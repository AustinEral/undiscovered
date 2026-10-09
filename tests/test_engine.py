import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from genre_radio.engine import RadioEngine
from genre_radio.planning import allowed_genres
from input_fixture import use_imported_input_fixture


class FakePipeline:
    def __init__(self, gate=False, fail_at=None, partial=False):
        self.calls = []
        self.closed = False
        self.gate = asyncio.Event()
        if not gate:
            self.gate.set()
        self.fail_at = fail_at
        self.partial = partial

    async def generate(self, request, directory, progress):
        self.calls.append(dict(request))
        self.progress = progress
        await progress('qwen', None)
        await self.gate.wait()
        await asyncio.sleep(0)
        if len(self.calls) == self.fail_at:
            raise RuntimeError('Test generation failed')
        (directory / 'audio.mp3').write_bytes(b'ID3 test audio')
        if not self.partial:
            (directory / 'cover.png').write_bytes(b'PNG test cover')
        return {'duration': 60.5, 'planned_duration': 90, 'bpm': 120, 'vocal_gender': 'female', 'duration_reason': 'Fixture plan, not model output', 'genre': allowed_genres(request)[0], 'caption': 'Test caption', 'cover_prompt': 'Test cover'}

    async def close(self):
        self.closed = True


def seed_imports(engine, count=12):
    for index in range(count):
        engine.save_lyrics({'title': f'Fixture song {index}', 'artist': 'Fixture artist', 'text': f'[Verse]\nUnique local fixture words {index}.\n'})


async def wait_for(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        network = patch('aiohttp.ClientSession', side_effect=AssertionError('No production acquisition in engine tests'))
        network.start()
        self.addCleanup(network.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.pipeline = FakePipeline()
        self.engine = RadioEngine(self.directory.name, self.pipeline)
        use_imported_input_fixture(self.engine)
        seed_imports(self.engine)
        await self.engine.initialize()

    async def asyncTearDown(self):
        await self.engine.close()
        self.directory.cleanup()

    async def test_starts_paused_and_fills_five_ready_slots_with_one_current(self):
        self.assertFalse(self.engine.running)
        self.assertEqual(self.pipeline.calls, [])
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(len(self.pipeline.calls), 6)
        self.assertEqual(self.engine.snapshot()['buffer_target'], 5)
        self.assertEqual(self.engine.next, self.engine.queue[0])
        self.assertEqual(len({track['id'] for track in self.engine.queue}), 5)
        self.assertNotIn(self.engine.current['id'], {track['id'] for track in self.engine.queue})

    async def test_skip_is_immediate_and_refills_the_buffer(self):
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        next_id = self.engine.next['id']
        result = self.engine.advance()
        self.assertTrue(result['advanced'])
        self.assertEqual(result['state']['current']['id'], next_id)
        self.assertEqual(len(result['state']['queue']), 4)
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(len(self.pipeline.calls), 7)

    async def test_no_next_keeps_current_unchanged(self):
        before = self.engine.current
        result = self.engine.advance()
        self.assertFalse(result['advanced'])
        self.assertEqual(self.engine.current, before)

    async def test_pause_allows_inflight_to_finish_but_starts_no_more(self):
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        self.assertEqual(len(self.pipeline.calls), 1)
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.queue, [])

    async def test_generation_keeps_captured_style_and_next_job_uses_new_style(self):
        self.engine.update_preferences({'genre': 'Original style'})
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        old_id = self.pipeline.calls[0]['id']
        generating = dict(self.engine.generating)
        self.engine.update_preferences({'genre': 'Test new genre'})
        self.assertEqual(self.engine.generating, generating)
        self.assertNotIn('superseded', self.engine.generating)
        self.pipeline.gate.set()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(self.engine.current['id'], old_id)
        self.assertEqual(self.engine.current['genre'], 'Original style')
        self.assertEqual(self.pipeline.calls[0]['genre'], 'Original style')
        self.assertTrue(all(track['genre'] == 'Test new genre' for track in self.engine.queue))

    async def test_preference_changes_preserve_current_queue_ids_and_all_media(self):
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.engine.stop()
        current = dict(self.engine.current)
        queue = list(self.engine.queue)
        old_ids = set(self.engine.tracks)
        files = {path: path.read_bytes() for path in (self.engine.root / 'tracks').rglob('*') if path.is_file()}
        events = self.engine.subscribe()
        before = events.get_nowait()
        for values in ({'genre': 'New style'}, {'lyrics_id': list(self.engine.lyrics)[-1]}, {'lyrics_id': 'auto'}):
            state = self.engine.update_preferences(values)
            self.assertEqual(self.engine.current, current)
            self.assertEqual(self.engine.queue, queue)
            self.assertEqual(set(self.engine.tracks), old_ids)
            self.assertGreater(state['version'], before['version'])
            self.assertEqual(events.get_nowait(), state)
            self.assertEqual(json.loads((self.engine.root / 'state.json').read_text())['queue_ids'], [track['id'] for track in queue])
            before = state
        await asyncio.sleep(0)
        self.engine.unsubscribe(events)
        self.assertFalse(self.engine.running)
        self.assertEqual(len(self.pipeline.calls), 6)
        self.assertEqual({path: path.read_bytes() for path in (self.engine.root / 'tracks').rglob('*') if path.is_file()}, files)

    async def test_preferences_do_not_start_paused_generation_with_empty_queue(self):
        await asyncio.sleep(0)
        self.assertFalse(self.engine._wake.is_set())
        self.engine.update_preferences({'genre': 'Future style', 'lyrics_id': next(iter(self.engine.lyrics))})
        await asyncio.sleep(0)
        self.assertFalse(self.engine._wake.is_set())
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.preparing)
        self.assertIsNone(self.engine.generating)
        self.assertEqual(self.pipeline.calls, [])

    async def test_restart_restores_ready_media_but_does_not_start_gpu(self):
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        fresh = RadioEngine(self.directory.name, FakePipeline())
        self.assertFalse(fresh.running)
        self.assertEqual(fresh.current['id'], self.engine.current['id'])
        self.assertEqual([track['id'] for track in fresh.queue], [track['id'] for track in self.engine.queue])

    async def test_legacy_duration_preference_is_dropped_without_losing_saved_tracks(self):
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.engine.stop()
        state_path = self.engine.root / 'state.json'
        saved = json.loads(state_path.read_text())
        saved['preferences']['duration'] = 180
        state_path.write_text(json.dumps(saved))
        pipeline = FakePipeline()
        fresh = RadioEngine(self.directory.name, pipeline)
        self.assertNotIn('duration', fresh.preferences)
        self.assertNotIn('duration', fresh.snapshot()['preferences'])
        self.assertEqual(fresh.current['id'], self.engine.current['id'])
        self.assertEqual([track['id'] for track in fresh.queue], [track['id'] for track in self.engine.queue])
        self.assertFalse(fresh.running)
        self.assertEqual(pipeline.calls, [])
        fresh.update_preferences({'genre': 'Migration fixture'})
        self.assertNotIn('duration', json.loads(state_path.read_text())['preferences'])
        await fresh.close()

    async def test_progress_plan_updates_generating_without_changing_current(self):
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.engine.stop()
        self.engine.advance()
        current = dict(self.engine.current)
        self.pipeline.gate.clear()
        self.engine.update_preferences({'genre': 'Plan fixture genre'})
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 7)
        events = self.engine.subscribe()
        before = events.get_nowait()
        await self.pipeline.progress('cover', 0.4, {'genre': 'Plan fixture genre', 'planned_duration': 90, 'bpm': 120, 'current': None, 'duration_reason': 'Not a public progress field'})
        snapshot = events.get_nowait()
        self.engine.unsubscribe(events)
        self.assertGreater(snapshot['version'], before['version'])
        self.assertEqual(snapshot['current'], before['current'])
        self.assertEqual(self.engine.current, current)
        self.assertEqual(snapshot['generating']['genre'], 'Plan fixture genre')
        self.assertEqual(snapshot['generating']['planned_duration'], 90)
        self.assertEqual(snapshot['generating']['bpm'], 120)
        self.assertEqual(snapshot['generating']['progress'], 0.4)
        self.assertNotIn('current', snapshot['generating'])
        self.assertNotIn('duration_reason', snapshot['generating'])
        await self.pipeline.progress('audio', 0.5)
        self.assertEqual(self.engine.generating['planned_duration'], 90)
        self.assertEqual(self.engine.generating['bpm'], 120)
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(self.engine.current, current)

    async def test_actual_and_planned_duration_are_distinct_and_persisted(self):
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        track = self.engine.current
        manifest = json.loads((self.engine.root / 'tracks' / track['id'] / 'manifest.json').read_text())
        for record in (track, manifest, self.engine.snapshot()['current']):
            self.assertEqual(record['duration'], 60.5)
            self.assertEqual(record['planned_duration'], 90)
            self.assertEqual(record['bpm'], 120)
        self.assertEqual(manifest['duration_reason'], 'Fixture plan, not model output')
        self.assertNotIn('requested_duration', manifest)
        self.assertNotIn('duration', self.pipeline.calls[0])
        fresh = RadioEngine(self.directory.name, FakePipeline())
        self.assertEqual(fresh.current['duration'], 60.5)
        self.assertEqual(fresh.current['planned_duration'], 90)
        await fresh.close()

    async def test_original_duration_is_saved_reference_not_target(self):
        text = '[Verse]\nUnchanged reference fixture words.\n'
        record = self.engine.save_lyrics({'title': 'Reference length', 'artist': 'Fixture reference artist', 'text': text, 'original_duration': 245})
        self.engine.update_preferences({'lyrics_id': record['id']})
        direct = await RadioEngine._request(self.engine)
        self.assertEqual(direct['original_duration'], 245)
        self.assertNotIn('duration', direct)
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(sum(call['lyrics_id'] == record['id'] for call in self.pipeline.calls), 1)
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')
        self.assertIsNone(self.engine.error)
        request = self.pipeline.calls[0]
        self.assertEqual(request['original_duration'], 245)
        self.assertEqual(request['lyrics'], text)
        self.assertNotIn('duration', request)
        self.assertEqual(self.engine.current['original_duration'], 245)
        self.assertEqual(self.engine.current['planned_duration'], 90)
        self.assertEqual(self.engine.current['duration'], 60.5)
        persisted = json.loads((self.engine.root / 'lyrics.json').read_text())
        self.assertEqual(next(item for item in persisted if item['id'] == record['id'])['original_duration'], 245)
        for invalid in (0, -1, 7201, True, '245', float('inf')):
            with self.subTest(original_duration=invalid), self.assertRaises(ValueError):
                self.engine.save_lyrics({'title': 'Invalid reference', 'artist': 'Fixture', 'text': 'Different invalid fixture', 'original_duration': invalid})
        self.assertIsNone(next(item for item in self.engine.lyrics.values() if item['title'] == 'Fixture song 0')['original_duration'])

    async def test_generation_error_keeps_current_and_stops_new_work(self):
        self.pipeline.fail_at = 2
        self.engine.start()
        await wait_for(lambda: self.engine.error is not None)
        self.assertIsNotNone(self.engine.current)
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.error, 'Test generation failed')
        self.assertEqual(len(self.pipeline.calls), 2)

    async def test_partial_media_is_never_published_ready(self):
        self.pipeline.partial = True
        self.engine.start()
        await wait_for(lambda: self.engine.error is not None)
        self.assertIsNone(self.engine.current)
        self.assertEqual(self.engine.tracks, {})

    async def test_pasted_lyrics_stay_unchanged_and_source_links_are_not_fetched(self):
        text = '[Verse]\nSome original words.\n'
        record = self.engine.save_lyrics({'title': 'Example', 'artist': 'Example Artist', 'text': text, 'source_url': 'https://example.com/lyrics'})
        self.assertEqual(record['text'], text)
        self.engine.update_preferences({'lyrics_id': record['id']})
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(sum(call['lyrics_id'] == record['id'] for call in self.pipeline.calls), 1)
        self.assertEqual(self.pipeline.calls[0]['lyrics'], text)
        self.assertEqual(self.engine.lyrics[record['id']]['text'], text)
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')
        self.assertIsNone(self.engine.error)
        with self.assertRaises(ValueError):
            self.engine.save_lyrics({'title': 'Bad', 'text': 'words', 'source_url': 'javascript:alert(1)'})

    async def test_rejects_placeholder_and_duplicate_records_before_saving(self):
        before = (Path(self.directory.name) / 'lyrics.json').read_text()
        for values in (
            {'title': 'Song', 'artist': 'Artist', 'text': '[Verse]\nActual words.'},
            {'title': 'Another', 'artist': 'Artist', 'text': 'Lyrics Here'},
            {'title': 'Fixture song 0', 'artist': 'Fixture artist', 'text': '[Verse]\nDifferent words.'},
            {'title': 'Different title', 'artist': 'Different artist', 'text': '[Verse]\nUnique local fixture words 0.\n'},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.engine.save_lyrics(values)
        self.assertEqual((Path(self.directory.name) / 'lyrics.json').read_text(), before)

    async def test_input_limits_and_media_paths(self):
        for value in [29, 30, 120, 180, 300, 301, True, '120', None]:
            with self.subTest(duration=value), self.assertRaisesRegex(ValueError, 'Unknown preference'):
                self.engine.update_preferences({'duration': value})
        with self.assertRaises(ValueError):
            self.engine.update_preferences({'lyrics_id': 'unknown'})
        with self.assertRaises(ValueError):
            self.engine.save_lyrics({'title': 'Example', 'text': 'x' * 14001})
        with self.assertRaises(KeyError):
            self.engine.media_path('../../outside', 'audio')

    async def test_directory_failure_does_not_kill_worker_and_start_can_retry(self):
        with patch('genre_radio.engine.Path.mkdir', side_effect=OSError('Test disk full')):
            self.engine.start()
            await wait_for(lambda: self.engine.error is not None)
        self.assertFalse(self.engine._worker.done())
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.error, 'Test disk full')
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertIsNone(self.engine.error)

    async def test_state_stream_is_bounded_and_reports_ready_count(self):
        queue = self.engine.subscribe()
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(queue.qsize(), 1)
        state = queue.get_nowait()
        self.assertEqual(len(state['queue']), 5)
        self.engine.unsubscribe(queue)
        self.assertEqual(len(self.engine._subscribers), 0)


if __name__ == '__main__':
    unittest.main()
