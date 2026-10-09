import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp

from genre_radio.engine import CHART_RETRY_SECONDS, RadioEngine, SAVED_INPUT_NOTICE, atomic_json
from genre_radio.identity import lyrics_hash
from genre_radio.live_song_provider import LiveSong, LiveSongProvider
from genre_radio.rotation import select_cached_lyrics
from test_engine import FakePipeline, wait_for


class RotationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.network_patch = patch('aiohttp.ClientSession', side_effect=AssertionError('External acquisition is forbidden in rotation tests'))
        self.network = self.network_patch.start()
        self.addCleanup(self.network_patch.stop)
        self.acquisition_patch = patch.object(LiveSongProvider, 'acquire', new_callable=AsyncMock)
        self.acquire = self.acquisition_patch.start()
        self.addCleanup(self.acquisition_patch.stop)
        self.acquire.side_effect = ValueError('Genius chart unavailable')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pipeline = FakePipeline()
        self.engine = RadioEngine(self.directory.name, self.pipeline)
        await self.engine.initialize()

    async def asyncTearDown(self):
        await self.engine.close()
        self.network.assert_not_called()

    def add(self, index, **changes):
        values = dict(title=f'Cached {index}', artist='Fixture artist', text=f'[Verse]\nActual saved fixture words {index}.\n', source_name='Genius', source_url=f'https://genius.com/fixture-{index}-lyrics')
        values.update(changes)
        return self.engine.save_lyrics(values)

    async def fill(self):
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5 or self.engine.error is not None)
        self.assertIsNone(self.engine.error)
        self.engine.stop()

    async def test_one_song_pool_fills_five_with_fresh_generation_requests(self):
        song = self.add(0)
        with patch('genre_radio.engine.secrets.randbelow', side_effect=range(100, 106)):
            await self.fill()
        self.acquire.assert_awaited_once()
        self.assertEqual(len(self.pipeline.calls), 6)
        self.assertEqual(len({call['id'] for call in self.pipeline.calls}), 6)
        self.assertEqual({call['seed'] for call in self.pipeline.calls}, set(range(100, 106)))
        self.assertEqual(len(self.engine.lyrics), 1)
        self.assertEqual(self.engine.notice, SAVED_INPUT_NOTICE)
        self.assertFalse(self.engine.current['input_reused'])
        self.assertTrue(all(track['input_reused'] for track in self.engine.queue))
        for call in self.pipeline.calls:
            self.assertEqual(call['lyrics'], song['text'])
            self.assertEqual(call['lyrics_id'], song['id'])
            self.assertNotIn('caption', call)
            self.assertNotIn('cover_prompt', call)
            self.assertNotIn('audio', call)
            self.assertNotIn('audio_url', call)
            self.assertNotIn('notice', call)
            self.assertIn('genre_choices', call)
            self.assertTrue((self.engine.root / 'tracks' / call['id'] / 'audio.mp3').is_file())
            self.assertTrue((self.engine.root / 'tracks' / call['id'] / 'cover.png').is_file())
        self.assertEqual(len({track['audio_url'] for track in self.engine.tracks.values()}), 6)
        self.assertTrue(self.engine.advance()['state']['current']['input_reused'])

    async def test_two_song_pool_alternates_while_filling_and_refilling(self):
        self.add(0)
        self.add(1)
        await self.fill()
        self.engine.advance()
        await self.fill()
        ids = [call['lyrics_id'] for call in self.pipeline.calls]
        self.assertEqual(len(ids), 7)
        self.assertTrue(all(left != right for left, right in zip(ids, ids[1:])))
        self.assertEqual(ids[0], ids[2])
        self.acquire.assert_awaited_once()

    async def test_six_song_pool_avoids_current_and_queue_then_rotates_after_restart(self):
        for index in range(6):
            self.add(index)
        await self.fill()
        ids = [call['lyrics_id'] for call in self.pipeline.calls]
        self.assertEqual(len(set(ids)), 6)
        old_tracks = dict(self.engine.tracks)
        await self.engine.close()
        self.pipeline = FakePipeline()
        self.engine = RadioEngine(self.directory.name, self.pipeline)
        await self.engine.initialize()
        self.engine.advance()
        old_open = Path.open

        def metadata_only(path, mode='r', *args, **kwargs):
            if path.suffix in {'.mp3', '.png'} and 'r' in mode:
                raise AssertionError('Old media must not be read to generate a new interpretation')
            return old_open(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', metadata_only):
            await self.fill()
        self.assertEqual(self.pipeline.calls[0]['lyrics_id'], ids[0])
        self.assertNotIn(self.pipeline.calls[0]['id'], old_tracks)
        self.assertEqual({key: self.engine.tracks[key] for key in old_tracks}, old_tracks)
        self.assertTrue(self.engine.queue[-1]['input_reused'])

    async def test_fresh_acquisition_is_preferred_and_clears_notice(self):
        cached = self.add(0)
        fresh = LiveSong(lyrics_id='fresh-source-id', title='Fresh chart song', artist='Fixture chart artist', text='[Verse]\nFresh acquired words.', source_name='Genius', source_url='https://genius.com/fresh-lyrics')
        self.acquire.side_effect = None
        self.acquire.return_value = fresh
        self.pipeline.gate.clear()
        self.engine.start()
        self.engine.notice = SAVED_INPUT_NOTICE
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        self.assertIsNone(self.engine.notice)
        self.assertIsNone(self.engine.error)
        self.assertFalse(self.engine.current['input_reused'])
        self.assertNotEqual(self.engine.current['lyrics_id'], cached['id'])
        self.assertEqual(self.pipeline.calls[0]['lyrics'], fresh.text)
        self.acquire.assert_awaited_once()

    async def test_unused_cached_input_is_not_marked_as_prior_generation_reuse(self):
        saved = self.add(0)
        request = await self.engine._request()
        self.assertEqual(request['lyrics_id'], saved['id'])
        self.assertFalse(request['input_reused'])
        self.assertEqual(request['notice'], SAVED_INPUT_NOTICE)
        self.assertIsNone(self.engine.notice)

    async def test_failure_cooldown_uses_cache_until_expiry_then_attempts_fresh_once(self):
        self.add(0)
        with patch('genre_radio.engine.time.monotonic', return_value=100) as clock:
            first = await self.engine._request()
            self.assertEqual(self.engine._chart_retry_at, 100 + CHART_RETRY_SECONDS)
            clock.return_value = 100 + CHART_RETRY_SECONDS - 0.001
            second = await self.engine._request()
            self.acquire.assert_awaited_once()
            self.assertNotEqual(first['id'], second['id'])
            clock.return_value = 100 + CHART_RETRY_SECONDS
            await self.engine._request()
            self.assertEqual(self.acquire.await_count, 2)
            self.assertEqual(self.engine._chart_retry_at, 100 + 2 * CHART_RETRY_SECONDS)

    async def test_cooldown_with_invalid_cache_preserves_original_failure(self):
        saved = self.add(0)
        await self.engine._request()
        original = self.acquire.side_effect
        self.engine.lyrics[saved['id']]['text'] = ''
        with self.assertRaises(ValueError) as raised:
            await self.engine._request()
        self.assertIs(raised.exception, original)
        self.acquire.assert_awaited_once()

    async def test_cooldown_resets_on_restart_and_fresh_success(self):
        self.add(0)
        await self.engine._request()
        await self.engine.close()
        self.engine = RadioEngine(self.directory.name, FakePipeline())
        await self.engine.initialize()
        self.assertEqual(self.engine._chart_retry_at, 0)
        self.assertIsNone(self.engine._chart_failure)
        fresh = LiveSong(lyrics_id='fresh-source-id', title='Fresh after restart', artist='Fixture chart artist', text='[Verse]\nFresh acquired words after restart.', source_name='Genius', source_url='https://genius.com/fresh-lyrics')
        self.acquire.side_effect = None
        self.acquire.return_value = fresh
        request = await self.engine._request()
        self.assertEqual(request['title'], fresh.title)
        self.assertEqual(self.acquire.await_count, 2)
        self.assertIsNone(self.engine._chart_failure)
        self.assertEqual(self.engine._chart_retry_at, 0)

    async def test_empty_cache_preserves_each_documented_acquisition_failure(self):
        for error in (ValueError('Original chart failure'), aiohttp.ClientError('Original client failure'), TimeoutError('Original timeout')):
            with self.subTest(error=error):
                self.acquire.reset_mock()
                self.acquire.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    await self.engine._request()
                self.assertIs(raised.exception, error)
                self.acquire.assert_awaited_once()
        self.assertEqual(self.pipeline.calls, [])

    async def test_empty_cache_stops_with_original_error_and_no_notice(self):
        self.engine.start()
        await wait_for(lambda: self.engine.error is not None)
        self.assertEqual(self.engine.error, 'Genius chart unavailable')
        self.assertIsNone(self.engine.notice)
        self.assertFalse(self.engine.running)
        self.assertEqual(self.pipeline.calls, [])
        self.acquire.assert_awaited_once()

    async def test_fresh_save_validation_failure_uses_valid_cache_and_cooldown(self):
        saved = self.add(0)
        fresh = LiveSong(lyrics_id='fresh-input', title='Fresh input', artist='Fixture artist', text='Fresh fixture words', source_url='https://genius.com/fresh-lyrics', source_name='Genius')
        self.acquire.side_effect = None
        self.acquire.return_value = fresh
        error = ValueError('Fresh saved-input validation failed')
        with patch.object(self.engine, 'save_lyrics', side_effect=error) as save:
            request = await self.engine._request()
            self.assertEqual(request['lyrics_id'], saved['id'])
            self.assertEqual(request['notice'], SAVED_INPUT_NOTICE)
            self.assertIs(self.engine._chart_failure, error)
            self.assertGreater(self.engine._chart_retry_at, 0)
            await self.engine._request()
            self.acquire.assert_awaited_once()
            save.assert_called_once()

    async def test_fresh_save_validation_failure_without_cache_preserves_error(self):
        fresh = LiveSong(lyrics_id='fresh-input', title='Fresh input', artist='Fixture artist', text='Fresh fixture words', source_url='https://genius.com/fresh-lyrics', source_name='Genius')
        self.acquire.side_effect = None
        self.acquire.return_value = fresh
        error = ValueError('Fresh saved-input validation failed')
        with patch.object(self.engine, 'save_lyrics', side_effect=error):
            with self.assertRaises(ValueError) as raised:
                await self.engine._request()
        self.assertIs(raised.exception, error)
        self.assertEqual(self.engine._chart_retry_at, 0)
        self.acquire.assert_awaited_once()

    async def test_duplicate_fresh_input_reuses_existing_saved_id_without_reimport(self):
        saved = self.add(0)
        self.acquire.side_effect = None
        self.acquire.return_value = LiveSong(lyrics_id='new-source-id', **{key: value for key, value in saved.items() if key != 'id'})
        request = await self.engine._request()
        self.assertEqual(request['lyrics_id'], saved['id'])
        self.assertEqual(list(self.engine.lyrics), [saved['id']])
        self.assertEqual(request['notice'], SAVED_INPUT_NOTICE)
        self.acquire.assert_awaited_once()

    async def test_fresh_saved_record_and_prepare_validation_failures_use_cache(self):
        saved = self.add(0)
        fresh = LiveSong(lyrics_id=saved['id'], **{key: value for key, value in saved.items() if key != 'id'})
        self.acquire.side_effect = None
        self.acquire.return_value = fresh
        with patch.object(self.engine._live_provider, 'prepare_request', side_effect=ValueError('Prepared input invalid')):
            self.assertEqual((await self.engine._request())['lyrics_id'], saved['id'])
        self.engine._chart_retry_at = 0
        self.acquire.return_value = LiveSong(lyrics_id='fresh-source-id', **{key: value for key, value in saved.items() if key != 'id'})
        with patch.object(LiveSong, 'saved_record', side_effect=ValueError('Saved input invalid')):
            self.assertEqual((await self.engine._request())['lyrics_id'], saved['id'])
        self.assertEqual(self.acquire.await_count, 2)

    async def test_fresh_save_disk_failures_are_not_hidden_by_cache(self):
        saved = self.add(0)
        self.acquire.side_effect = None
        self.acquire.return_value = LiveSong(lyrics_id='fresh-source-id', **{key: value for key, value in saved.items() if key != 'id'})
        with patch.object(self.engine, 'save_lyrics', side_effect=OSError('Disk unavailable')):
            with self.assertRaisesRegex(OSError, 'Disk unavailable'):
                await self.engine._request()
        self.assertEqual(self.engine._chart_retry_at, 0)
        self.acquire.assert_awaited_once()

    async def test_programming_errors_do_not_trigger_fallback(self):
        self.add(0)
        self.acquire.side_effect = RuntimeError('Unexpected programming failure')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected programming failure'):
            await self.engine._request()

    async def test_invalid_or_non_genius_cache_records_are_excluded(self):
        valid = self.add(0)
        self.engine.lyrics.clear()
        invalid = [
            None,
            dict(valid, text=''), dict(valid, text=None), dict(valid, text=42),
            dict(valid, title=[]), dict(valid, artist=''), dict(valid, text='Lyrics Here'),
            dict(valid, title='Song', artist='Artist'), dict(valid, text='x' * 14001),
            dict(valid, source_url='javascript:alert(1)'), dict(valid, source_url=42),
            dict(valid, source_name=42), dict(valid, original_duration=True),
            dict(valid, source_url='https://genius.com\n/unsafe'),
            dict(valid, source_name='Original', source_url=''),
            dict(valid, source_name='RoomServiceAI', source_url='https://example.com/demo'),
            dict(valid, source_name='', source_url='https://genius.com.example.com/fake'),
        ]
        for index, record in enumerate(invalid):
            self.engine.lyrics[str(index)] = record
        with self.assertRaisesRegex(ValueError, 'Genius chart unavailable'):
            await self.engine._request()
        self.assertEqual(self.engine.snapshot()['library_remaining'], 1)
        self.assertEqual(self.pipeline.calls, [])

    async def test_invalid_persisted_cache_rows_do_not_hide_valid_saved_input(self):
        saved = self.add(0)
        atomic_json(self.engine.root / 'lyrics.json', [None, 42, {}, dict(saved, id=42), dict(saved, id='invalid', text=None), saved])
        await self.engine.close()
        self.engine = RadioEngine(self.directory.name, FakePipeline())
        await self.engine.initialize()
        self.assertEqual((await self.engine._request())['lyrics_id'], saved['id'])
        self.assertEqual(self.engine.snapshot()['library_remaining'], 1)
        self.assertTrue((self.engine.root / 'lyrics.json').is_file())

    async def test_each_documented_failure_can_use_valid_cached_input(self):
        saved = self.add(0)
        for error in (ValueError('Chart failed'), aiohttp.ClientError('Client failed'), TimeoutError('Timed out')):
            with self.subTest(error=error):
                self.engine._chart_retry_at = 0
                self.acquire.reset_mock()
                self.acquire.side_effect = error
                request = await self.engine._request()
                self.assertEqual(request['lyrics_id'], saved['id'])
                self.assertEqual(request['notice'], SAVED_INPUT_NOTICE)
                self.acquire.assert_awaited_once()

    async def test_legacy_hashless_manifest_matches_cached_input_fingerprint(self):
        first, second = self.add(0), self.add(1)
        self.engine.lyrics['old-alias'] = dict(first, id='old-alias', title='Old title', source_name='Manual', source_url='')
        old = dict(id='old-track', lyrics_id='old-alias', title='Old title', artist=first['artist'], created_at='2026-02-01', genre='Jazz')
        self.engine.tracks[old['id']] = old
        self.engine.current = old
        self.assertEqual((await self.engine._request())['lyrics_id'], second['id'])

    async def test_source_name_or_valid_genius_url_qualifies_cache(self):
        for index, fields in enumerate((dict(source_name='genius.com', source_url=''), dict(source_name='', source_url='https://www.genius.com/fixture-lyrics'), dict(source_name='Genius', source_url='https://example.com/attribution'))):
            with self.subTest(fields=fields):
                self.engine.lyrics.clear()
                saved = self.add(index, **fields)
                self.assertEqual((await self.engine._request())['lyrics_id'], saved['id'])

    async def test_explicit_manual_saved_selection_can_reuse_non_genius_input(self):
        saved = self.add(0, source_name='Manual', source_url='')
        self.engine.update_preferences({'lyrics_id': saved['id']})
        request = await self.engine._request()
        self.assertFalse(request['input_reused'])
        self.engine.tracks['old'] = dict(request, lyrics_hash=lyrics_hash(saved['text']), created_at='2026-01-01')
        request = await self.engine._request()
        self.assertTrue(request['input_reused'])
        self.assertIsNone(request['notice'])
        self.acquire.assert_not_awaited()

    async def test_paused_fallback_preparation_does_not_publish_notice(self):
        self.add(0)
        gate = asyncio.Event()

        async def unavailable():
            await gate.wait()
            raise ValueError('Unavailable')

        self.acquire.side_effect = unavailable
        self.engine.start()
        await wait_for(lambda: self.engine.preparing)
        self.engine.update_preferences({'genre': 'Superseding genre'})
        self.engine.stop()
        gate.set()
        await wait_for(lambda: not self.engine.preparing)
        self.assertIsNone(self.engine.notice)
        self.assertIsNone(self.engine.error)
        self.assertEqual(self.pipeline.calls, [])

    async def test_request_snapshots_provider_and_style_before_acquisition(self):
        for existing in (True, False):
            with self.subTest(existing=existing):
                saved = self.add(f'snapshot-{existing}')
                song = LiveSong(lyrics_id=saved['id'], **{key: value for key, value in saved.items() if key != 'id'}) if existing else LiveSong(lyrics_id='fresh-snapshot', title='Fresh snapshot', artist='Fixture artist', text='Fresh snapshot words', source_name='Genius', source_url='https://genius.com/fresh-snapshot-lyrics')
                self.engine.update_preferences({'genre': 'Original style', 'lyrics_id': 'auto'})
                provider = self.engine._live_provider
                started, release = asyncio.Event(), asyncio.Event()

                async def acquire():
                    started.set()
                    await release.wait()
                    return song

                with patch.object(provider, 'acquire', side_effect=acquire), patch.object(provider, 'prepare_request', wraps=provider.prepare_request) as prepare:
                    task = asyncio.create_task(self.engine._request())
                    try:
                        await started.wait()
                        self.engine.update_preferences({'genre': 'Future style', 'lyrics_id': saved['id']})
                        with patch.object(self.engine._live_provider, 'prepare_request', side_effect=AssertionError('Future provider must not prepare active input')):
                            release.set()
                            request = await task
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                prepare.assert_called_once_with(song, genre='Original style')
                self.assertEqual(request['genre'], 'Original style')
                self.assertEqual(request['lyrics'], song.text)
                self.assertEqual(self.engine.preferences['genre'], 'Future style')
                self.assertEqual(self.engine.lyrics[request['lyrics_id']]['source_url'], song.source_url)

    async def test_fallback_request_uses_original_genre_after_async_failure(self):
        saved = self.add(0)
        self.engine.update_preferences({'genre': 'Original style'})
        started, release = asyncio.Event(), asyncio.Event()

        async def unavailable():
            started.set()
            await release.wait()
            raise ValueError('Unavailable')

        self.acquire.side_effect = unavailable
        task = asyncio.create_task(self.engine._request())
        try:
            await started.wait()
            self.engine.update_preferences({'genre': 'Future style', 'lyrics_id': saved['id']})
            release.set()
            request = await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(request['genre'], 'Original style')
        self.assertEqual(request['lyrics_id'], saved['id'])
        self.assertEqual(request['notice'], SAVED_INPUT_NOTICE)

    async def test_source_and_style_change_during_acquisition_apply_only_to_next_job(self):
        manual = self.add('manual', source_name='Manual', source_url='')
        fresh = LiveSong(lyrics_id='fresh-source', title='Original acquired input', artist='Fixture artist', text='Original acquisition words', source_name='Genius', source_url='https://genius.com/original-input-lyrics')
        release = asyncio.Event()

        async def acquire():
            await release.wait()
            return fresh

        self.acquire.side_effect = acquire
        self.engine.update_preferences({'genre': 'Original style'})
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: self.acquire.await_count == 1)
        self.engine.update_preferences({'genre': 'Future style', 'lyrics_id': manual['id']})
        release.set()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.assertEqual(self.pipeline.calls[0]['lyrics'], fresh.text)
        self.assertEqual(self.pipeline.calls[0]['genre'], 'Original style')
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        self.assertEqual(self.engine.current['genre'], 'Original style')
        self.assertEqual(self.engine.current['source_selection'], 'auto')
        self.assertEqual(self.engine.preferences['lyrics_id'], manual['id'])
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 2)
        self.assertEqual(self.pipeline.calls[1]['lyrics_id'], manual['id'])
        self.assertEqual(self.pipeline.calls[1]['genre'], 'Future style')
        self.acquire.assert_awaited_once()
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.next is not None)
        self.assertEqual(self.engine.next['source_selection'], 'manual')
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')

    async def test_future_settings_do_not_clear_active_fallback_notice_or_queue(self):
        self.add(0)
        await self.fill()
        self.engine.advance()
        current = dict(self.engine.current)
        queue = list(self.engine.queue)
        self.pipeline.gate.clear()
        self.engine.update_preferences({'genre': 'Original style'})
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 7)
        generating = dict(self.engine.generating)
        self.assertEqual(self.engine.notice, SAVED_INPUT_NOTICE)
        manual = self.add('manual', source_name='Manual', source_url='')
        state = self.engine.update_preferences({'genre': 'Future style', 'lyrics_id': manual['id']})
        self.assertEqual(state['notice'], SAVED_INPUT_NOTICE)
        self.assertEqual(state['generating'], generating)
        self.assertEqual(self.engine.current, current)
        self.assertEqual(self.engine.queue, queue)
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertFalse(self.engine.running)
        self.assertEqual(len(self.pipeline.calls), 7)
        self.assertEqual(self.engine.queue[:-1], queue)
        self.assertEqual(self.engine.queue[-1]['genre'], 'Original style')
        self.assertEqual(self.engine.current, current)
        self.assertEqual(self.engine.preferences['lyrics_id'], manual['id'])
        self.assertEqual(self.engine.notice, SAVED_INPUT_NOTICE)
        self.acquire.assert_awaited_once()

    async def test_restart_prunes_exact_job_ids_but_preserves_versions_and_files(self):
        self.add(0)
        await self.fill()
        current, first, second = self.engine.current, *self.engine.queue[:2]
        self.engine.queue = [current, first, first, second]
        self.engine._save()
        fresh = RadioEngine(self.directory.name, FakePipeline())
        self.assertEqual([track['id'] for track in fresh.queue], [first['id'], second['id']])
        self.assertEqual(len(fresh.tracks), 6)
        self.assertGreater(fresh.version, 0)
        self.assertFalse(fresh.running)
        self.assertIsNone(fresh.notice)
        await fresh.close()


class PureRotationTests(unittest.TestCase):
    def test_identity_and_hash_aliases_share_recency_and_pool_slot(self):
        records = [
            dict(id='a', title='Same song', artist='Artist', text='Words A'),
            dict(id='b', title=' SAME SONG ', artist='ARTIST', text='Words B'),
            dict(id='c', title='Renamed', artist='Artist', text=' WORDS B '),
            dict(id='d', title='Other song', artist='Artist', text='Words D'),
        ]
        recent = dict(id='track', title='Renamed', artist='Artist', lyrics_hash=lyrics_hash('WORDS B'), created_at='2026-02-02')
        older = dict(id='older', title='Other song', artist='Artist', lyrics_hash=lyrics_hash('Words D'), created_at='2026-01-01')
        self.assertEqual(select_cached_lyrics(records, [older, recent], [])['id'], 'd')
        self.assertEqual(select_cached_lyrics(records, [older, recent], [older])['id'], 'a')

    def test_queue_tail_avoidance_wins_when_entire_pool_is_queued(self):
        records = [dict(id=str(index), title=f'Song {index}', artist='Artist', text=f'Words {index}') for index in range(2)]
        tracks = [dict(record, lyrics_id=record['id'], created_at=f'2026-01-0{index + 1}') for index, record in enumerate(records)]
        self.assertEqual(select_cached_lyrics(records, tracks, tracks)['id'], '0')
        self.assertEqual(select_cached_lyrics(records, tracks, list(reversed(tracks)))['id'], '1')

    def test_empty_pool_returns_none(self):
        self.assertIsNone(select_cached_lyrics([], [], []))
