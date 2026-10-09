import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from genre_radio.engine import RadioEngine
from genre_radio.gpu import GpuAdmissionError
from genre_radio.identity import lyrics_hash, song_key
from genre_radio.live_song_provider import LiveSongProvider
from test_engine import FakePipeline, wait_for


class GenerationNextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        network = patch('aiohttp.ClientSession', side_effect=AssertionError('No live acquisition in recovery tests'))
        network.start()
        self.addCleanup(network.stop)
        acquisition = patch.object(LiveSongProvider, 'acquire', new_callable=AsyncMock, side_effect=ValueError('Fixture chart unavailable'))
        self.acquire = acquisition.start()
        self.addCleanup(acquisition.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pipeline = FakePipeline()
        self.engine = RadioEngine(self.directory.name, self.pipeline, buffer_size=2)
        self.songs = [self.engine.save_lyrics({'title': f'Saved song {index}', 'artist': 'Fixture singer', 'text': f'[Verse]\nExact private fixture lyrics {index}.', 'source_name': 'Genius', 'source_url': f'https://genius.com/fixture-{index}-lyrics'}) for index in range(3)]
        await self.engine.initialize()

    async def asyncTearDown(self):
        await self.engine.close()

    async def fail_manual(self):
        self.engine.update_preferences({'lyrics_id': self.songs[0]['id']})
        self.pipeline.fail_at = len(self.pipeline.calls) + 1
        self.engine.start()
        await wait_for(lambda: self.engine.failure is not None)
        return dict(self.engine.failure)

    async def test_failure_exposes_known_metadata_without_lyrics_and_retry_keeps_input(self):
        failed = await self.fail_manual()
        self.assertEqual(failed, {'id': self.pipeline.calls[0]['id'], 'title': self.songs[0]['title'], 'artist': self.songs[0]['artist'], 'lyrics_id': self.songs[0]['id'], 'can_skip': True})
        self.assertNotIn('lyrics', failed)
        self.assertNotIn('lyrics_hash', failed)
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.tracks, {})
        self.assertEqual(self.engine._skipped_inputs, {})
        self.pipeline.gate.clear()
        state = self.engine.start()
        self.assertIsNone(state['failure'])
        self.assertIsNone(state['error'])
        await wait_for(lambda: len(self.pipeline.calls) == 2)
        self.assertEqual(self.pipeline.calls[1]['lyrics_id'], self.songs[0]['id'])
        self.assertNotEqual(self.pipeline.calls[1]['id'], failed['id'])
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)

    async def test_generate_another_preserves_current_queue_and_media_and_avoids_failed_input(self):
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 2)
        self.engine.stop()
        self.engine.advance()
        failed = await self.fail_manual()
        before = self.engine.snapshot()
        tracks = dict(self.engine.tracks)
        files = {path: path.read_bytes() for path in (self.engine.root / 'tracks').rglob('*') if path.is_file()}
        self.pipeline.gate.clear()
        state = self.engine.generation_next({'failed_id': failed['id']})['state']
        self.assertEqual(state['current'], before['current'])
        self.assertEqual(state['queue'], before['queue'])
        self.assertEqual(state['preferences']['lyrics_id'], 'auto')
        self.assertIsNone(state['failure'])
        self.assertTrue(state['running'])
        with self.assertRaises(ValueError):
            self.engine.generation_next({'failed_id': failed['id']})
        await wait_for(lambda: len(self.pipeline.calls) == 5)
        self.assertNotEqual(self.pipeline.calls[-1]['lyrics_id'], failed['lyrics_id'])
        self.assertNotEqual(lyrics_hash(self.pipeline.calls[-1]['lyrics']), lyrics_hash(self.songs[0]['text']))
        self.assertEqual(self.engine.tracks, tracks)
        self.assertEqual({path: path.read_bytes() for path in files}, files)
        self.engine.update_preferences({'genre': 'Future fixture genre'})
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.generating is None)
        self.assertEqual(self.engine.snapshot()['current'], before['current'])
        self.assertEqual(self.engine.snapshot()['queue'][:-1], before['queue'])
        self.assertEqual(self.engine.queue[-1]['id'], self.pipeline.calls[-1]['id'])
        self.assertNotIn(failed['id'], self.engine.tracks)
        self.assertFalse(self.engine.running)

    async def test_skip_metadata_filters_provider_and_cached_aliases_without_registry_records(self):
        failed = await self.fail_manual()
        self.engine.generation_next({'failed_id': failed['id']})
        self.engine.stop()
        alias = dict(self.songs[0], id='alias', artist='Alias artist', title='Alias title')
        self.engine.lyrics['alias'] = alias
        same_name = dict(self.songs[0], id='same-name', text='Another transcription of the failed song')
        self.engine.lyrics['same-name'] = same_name
        names, hashes = self.engine._live_provider._used_identities()
        self.assertIn(song_key(self.songs[0]), names)
        self.assertIn(lyrics_hash(alias['text']), hashes)
        for index in (1, 2):
            self.engine.lyrics[self.songs[index]['id']]['source_name'] = ''
            self.engine.lyrics[self.songs[index]['id']]['source_url'] = ''
        self.assertIsNone(self.engine._cached_lyrics())
        self.assertEqual(self.engine.tracks, {})
        self.assertEqual(self.engine.favorites(), {'tracks': [], 'missing_ids': []})
        with self.assertRaises(KeyError):
            self.engine.media_path(failed['id'], 'audio')
        with self.assertRaises(KeyError):
            self.engine.track_details(failed['id'])

    async def test_skips_clear_on_settings_change_explicit_manual_selection_and_restart(self):
        failed = await self.fail_manual()
        self.engine.generation_next({'failed_id': failed['id']})
        self.engine.stop()
        skipped = dict(self.engine._skipped_inputs)
        self.engine.update_preferences({'genre': self.engine.preferences['genre']})
        self.assertEqual(self.engine._skipped_inputs, skipped)
        self.engine.update_preferences({'genre': 'New fixture genre'})
        self.assertEqual(self.engine._skipped_inputs, {})
        self.engine._skipped_inputs.update(skipped)
        self.engine.update_preferences({'lyrics_id': self.songs[0]['id']})
        self.assertEqual(self.engine._skipped_inputs, {})
        self.engine._skipped_inputs.update(skipped)
        revision = self.engine._source_selection_revision
        self.engine.update_preferences({'lyrics_id': self.songs[0]['id']})
        self.assertEqual(self.engine._source_selection_revision, revision)
        self.assertEqual(self.engine._skipped_inputs, {})
        self.engine._skipped_inputs.update(skipped)
        fresh = RadioEngine(self.directory.name, FakePipeline())
        try:
            self.assertEqual(fresh._skipped_inputs, {})
            self.assertIsNone(fresh.failure)
            self.assertIsNone(fresh.error)
            self.assertFalse(fresh.running)
        finally:
            await fresh.close()

    async def test_resource_failures_are_not_skippable_and_failed_ids_are_not_reused(self):
        self.engine.update_preferences({'lyrics_id': self.songs[0]['id']})
        previous = None
        for error in (GpuAdmissionError('Fixture GPU busy'), OSError('Fixture disk unavailable')):
            with patch.object(self.pipeline, 'generate', new_callable=AsyncMock, side_effect=error) as generate:
                self.engine.start()
                await wait_for(lambda: self.engine.failure is not None)
                failure = dict(self.engine.failure)
                self.assertFalse(failure['can_skip'])
                self.assertEqual(failure['lyrics_id'], self.songs[0]['id'])
                self.assertNotEqual(failure['id'], previous)
                previous = failure['id']
                with self.assertRaises(ValueError):
                    self.engine.generation_next({'failed_id': failure['id']})
                self.assertEqual(self.engine.failure, failure)
                generate.assert_awaited_once()
        self.assertEqual(self.engine._skipped_inputs, {})

    async def test_no_prepared_request_failure_does_not_reuse_successful_job_metadata(self):
        self.songs[0]['source_name'] = ''
        self.songs[0]['source_url'] = ''
        for song in self.songs[1:]:
            self.engine.lyrics[song['id']]['text'] = ''
        self.engine.update_preferences({'lyrics_id': self.songs[0]['id']})
        self.engine.start()
        await wait_for(lambda: self.engine.failure is not None)
        self.assertIsNotNone(self.engine.current)
        self.assertFalse(self.engine.failure['can_skip'])
        self.assertEqual(self.engine.failure['title'], '')
        self.assertEqual(self.engine.failure['lyrics_id'], '')
        self.assertNotEqual(self.engine.failure['id'], self.engine.current['id'])
        self.assertEqual(len(self.pipeline.calls), 1)

    async def test_each_generic_failure_waits_for_explicit_action_and_latest_failure_survives(self):
        self.engine.update_preferences({'lyrics_id': self.songs[0]['id']})
        with patch.object(self.pipeline, 'generate', new_callable=AsyncMock, side_effect=RuntimeError('Fixture plan failed')) as generate:
            self.engine.start()
            await wait_for(lambda: self.engine.failure is not None)
            first = dict(self.engine.failure)
            self.engine.generation_next({'failed_id': first['id']})
            await wait_for(lambda: self.engine.failure is not None)
            latest = dict(self.engine.failure)
            self.assertNotEqual(latest['id'], first['id'])
            self.assertNotEqual(latest['lyrics_id'], first['lyrics_id'])
            for body in ({'failed_id': first['id']}, {}, {'failed_id': latest['id'], 'extra': True}):
                with self.assertRaises(ValueError):
                    self.engine.generation_next(body)
            self.engine.stop()
            self.engine.update_preferences({'genre': 'Future fixture style'})
            await asyncio.sleep(0)
            self.assertEqual(generate.await_count, 2)
            self.assertEqual(self.engine.failure, latest)
            self.assertFalse(self.engine.running)
            self.assertEqual(self.engine.tracks, {})

    async def test_no_alternative_input_stops_once_without_retrying_failed_song(self):
        for song in self.songs[1:]:
            self.engine.lyrics[song['id']]['text'] = ''
        failed = await self.fail_manual()
        self.engine.generation_next({'failed_id': failed['id']})
        await wait_for(lambda: self.engine.failure is not None)
        self.assertFalse(self.engine.failure['can_skip'])
        self.assertNotEqual(self.engine.failure['id'], failed['id'])
        self.assertEqual(self.engine.error, 'Fixture chart unavailable')
        self.assertEqual(len(self.pipeline.calls), 1)
        self.acquire.assert_awaited_once()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.tracks, {})
        await asyncio.sleep(0)
        self.assertEqual(len(self.pipeline.calls), 1)

    async def test_generate_another_rejects_active_preparation_or_generation(self):
        failed = await self.fail_manual()
        for attribute, active in (('preparing', True), ('generating', {'id': 'active'}), ('running', True)):
            setattr(self.engine, attribute, active)
            with self.assertRaises(ValueError):
                self.engine.generation_next({'failed_id': failed['id']})
            setattr(self.engine, attribute, None if attribute == 'generating' else False)
            self.assertEqual(self.engine.failure, failed)
        self.assertEqual(self.engine._skipped_inputs, {})


if __name__ == '__main__':
    unittest.main()
