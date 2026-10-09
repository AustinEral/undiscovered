import asyncio
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from genre_radio.engine import LibraryExhausted, RadioEngine
from genre_radio.planning import allowed_genres


async def wait_for(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


class LocalProducer:
    def __init__(self, engine):
        self.engine = engine
        self.calls = []
        self.gates = []
        self.failures = {}
        self.automatic = False

    async def __call__(self):
        index = len(self.calls)
        request = {
            'id': str(uuid.uuid4()),
            'title': f'Local fixture {index}',
            'artist': 'Fixture',
            'lyrics': f'Local test payload {index}',
            'lyrics_id': f'fixture-{index}',
            'genre': self.engine.preferences['genre'],
            'seed': index,
        }
        gate = asyncio.Event()
        if self.automatic:
            gate.set()
        self.calls.append(request)
        self.gates.append(gate)
        await gate.wait()
        if index in self.failures:
            raise self.failures[index]
        return request


class LocalPipeline:
    def __init__(self):
        self.calls = []
        self.gate = asyncio.Event()
        self.closed = False
        self.active = 0
        self.max_active = 0

    async def generate(self, request, directory, progress):
        self.calls.append(dict(request))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await progress('fixture-model', None)
            await self.gate.wait()
            (directory / 'audio.mp3').write_bytes(b'fixture audio')
            (directory / 'cover.png').write_bytes(b'fixture cover')
            return {'duration': 60, 'planned_duration': 90, 'bpm': 120, 'vocal_gender': 'male', 'duration_reason': 'Fixture plan, not model output', 'genre': allowed_genres(request)[0], 'caption': 'Fixture', 'cover_prompt': 'Fixture'}
        finally:
            self.active -= 1

    async def close(self):
        self.closed = True


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.network_patch = patch(
            'aiohttp.ClientSession',
            side_effect=AssertionError('Production retrieval is forbidden in worker tests'),
        )
        self.network = self.network_patch.start()
        self.addCleanup(self.network_patch.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pipeline = LocalPipeline()
        self.request_patch = patch.object(RadioEngine, '_request', new_callable=AsyncMock)
        self.request = self.request_patch.start()
        self.addCleanup(self.request_patch.stop)
        self.engine = RadioEngine(self.directory.name, self.pipeline)
        self.producer = LocalProducer(self.engine)
        self.request.side_effect = self.producer.__call__
        await self.engine.initialize()

    async def asyncTearDown(self):
        await self.engine.close()
        self.network.assert_not_called()

    async def begin_preparation(self):
        self.engine.start()
        await wait_for(lambda: len(self.producer.calls) == 1)

    async def begin_generation(self):
        await self.begin_preparation()
        self.producer.gates[0].set()
        await wait_for(lambda: len(self.pipeline.calls) == 1)

    async def test_preparation_and_model_phases_are_visible(self):
        events = self.engine.subscribe()
        initial = events.get_nowait()
        self.assertIs(initial['preparing'], False)
        self.assertIsNone(initial['generating'])
        await self.begin_preparation()
        preparing = events.get_nowait()
        self.assertIs(preparing['preparing'], True)
        self.assertIsNone(preparing['generating'])
        self.assertIsNone(preparing['current'])
        self.assertGreater(preparing['version'], initial['version'])
        self.producer.gates[0].set()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        generating = events.get_nowait()
        self.assertIs(generating['preparing'], False)
        self.assertEqual(generating['generating']['stage'], 'fixture-model')
        self.assertIsNone(generating['current'])
        self.assertGreater(generating['version'], preparing['version'])
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        finished = events.get_nowait()
        self.assertIs(finished['preparing'], False)
        self.assertIsNone(finished['generating'])
        self.assertIsNotNone(finished['current'])
        self.assertGreater(finished['version'], generating['version'])

    async def test_worker_does_not_mutate_the_producer_request(self):
        await self.begin_preparation()
        original = dict(self.producer.calls[0])
        self.producer.gates[0].set()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        self.assertEqual(self.producer.calls[0], original)
        self.assertNotIn('input_reused', original)
        self.assertFalse(self.pipeline.calls[0]['input_reused'])

    async def test_pause_during_preparation_does_not_start_model(self):
        await self.begin_preparation()
        paused = self.engine.stop()
        self.assertIs(paused['preparing'], True)
        self.producer.gates[0].set()
        await wait_for(lambda: not self.engine.preparing)
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.engine.generating)
        self.assertEqual(self.pipeline.calls, [])
        self.assertEqual(list((self.engine.root / 'tracks').iterdir()), [])
        self.assertFalse(self.engine._worker.done())
        self.engine.start()
        await wait_for(lambda: len(self.producer.calls) == 2)
        self.assertEqual(self.pipeline.calls, [])
        self.producer.gates[1].set()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.assertEqual(self.pipeline.calls[0]['id'], self.producer.calls[1]['id'])

    async def test_changed_preferences_preserve_preparation_and_apply_to_next_job(self):
        self.engine.update_preferences({'genre': 'Original fixture'})
        await self.begin_preparation()
        original = dict(self.producer.calls[0])
        self.engine.update_preferences({'genre': 'Replacement fixture'})
        self.producer.gates[0].set()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.assertEqual(self.pipeline.calls[0]['id'], original['id'])
        self.assertEqual(self.pipeline.calls[0]['genre'], 'Original fixture')
        self.assertEqual(len(self.producer.calls), 1)
        self.pipeline.gate.set()
        await wait_for(lambda: len(self.producer.calls) == 2)
        self.assertEqual(self.engine.current['id'], original['id'])
        self.assertEqual(self.engine.current['genre'], 'Original fixture')
        self.producer.gates[1].set()
        await wait_for(lambda: self.engine.next is not None)
        self.assertEqual(self.pipeline.calls[1]['genre'], 'Replacement fixture')
        self.assertEqual(self.engine.next['genre'], 'Replacement fixture')
        self.assertNotIn('duration', self.pipeline.calls[1])
        self.assertNotEqual(self.pipeline.calls[1]['id'], original['id'])

    async def test_preparation_error_is_actionable_and_start_retries(self):
        self.producer.failures[0] = RuntimeError('Local input unavailable; retry Start')
        await self.begin_preparation()
        self.producer.gates[0].set()
        await wait_for(lambda: self.engine.error is not None)
        failed = self.engine.snapshot()
        self.assertEqual(failed['error'], 'Local input unavailable; retry Start')
        self.assertFalse(failed['running'])
        self.assertFalse(failed['preparing'])
        self.assertIsNone(failed['generating'])
        self.assertFalse(self.engine._worker.done())
        self.assertEqual(self.pipeline.calls, [])
        worker = self.engine._worker
        self.assertIsNone(self.engine.start()['error'])
        await wait_for(lambda: len(self.producer.calls) == 2)
        self.producer.gates[1].set()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.assertIs(self.engine._worker, worker)
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        self.assertIsNone(self.engine.error)

    async def test_close_cancels_preparation_and_publishes_cleanup(self):
        events = self.engine.subscribe()
        await self.begin_preparation()
        await self.engine.close()
        state = events.get_nowait()
        self.assertFalse(state['preparing'])
        self.assertFalse(state['running'])
        self.assertIsNone(state['generating'])
        self.assertTrue(self.engine._worker.cancelled())
        self.assertTrue(self.pipeline.closed)
        self.assertEqual(self.pipeline.calls, [])
        self.request.assert_awaited_once()

    async def test_close_cancels_model_and_publishes_cleanup(self):
        events = self.engine.subscribe()
        await self.begin_generation()
        await self.engine.close()
        state = events.get_nowait()
        self.assertFalse(state['preparing'])
        self.assertIsNone(state['generating'])
        self.assertIsNone(state['current'])
        self.assertEqual(state['queue'], [])
        self.assertEqual(self.pipeline.active, 0)
        self.assertTrue(self.pipeline.closed)

    async def test_pause_allows_existing_model_to_finish(self):
        await self.begin_generation()
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        self.assertEqual(len(self.pipeline.calls), 1)
        self.assertEqual(len(self.producer.calls), 1)
        self.assertFalse(self.engine.preparing)
        self.assertIsNone(self.engine.generating)
        self.assertFalse(self.engine.running)

    async def test_changed_preferences_keep_inflight_model_and_use_new_style_next(self):
        self.engine.update_preferences({'genre': 'Original fixture'})
        await self.begin_generation()
        original_id = self.pipeline.calls[0]['id']
        generating = dict(self.engine.generating)
        self.engine.update_preferences({'genre': 'Replacement fixture'})
        self.assertEqual(self.engine.generating, generating)
        self.pipeline.gate.set()
        await wait_for(lambda: len(self.producer.calls) == 2)
        self.assertEqual(self.engine.current['id'], original_id)
        self.assertEqual(self.engine.current['genre'], 'Original fixture')
        self.assertEqual(self.engine.queue, [])
        self.producer.gates[1].set()
        await wait_for(lambda: self.engine.next is not None)
        self.assertEqual(self.engine.next['genre'], 'Replacement fixture')
        self.assertNotEqual(self.engine.next['id'], original_id)

    async def test_exhausted_local_producer_waits_without_error(self):
        self.producer.failures[0] = LibraryExhausted()
        await self.begin_preparation()
        self.producer.gates[0].set()
        await wait_for(lambda: not self.engine.preparing)
        self.assertIsNone(self.engine.error)
        self.assertIsNone(self.engine.generating)
        self.assertTrue(self.engine.running)
        self.assertFalse(self.engine._worker.done())
        self.assertEqual(self.pipeline.calls, [])
        self.engine.start()
        await wait_for(lambda: len(self.producer.calls) == 2)

    async def seed_history(self):
        self.producer.automatic = True
        self.pipeline.gate.set()
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.engine.stop()
        self.engine.advance()

    async def assert_request_rejected(self, original, error, **changes):
        request = dict(original, id=str(uuid.uuid4()), lyrics_id=str(uuid.uuid4()), genre='Another genre')
        request.update(changes)
        self.request.side_effect = None
        self.request.return_value = request
        calls, preparations = len(self.pipeline.calls), self.request.await_count
        current, queue = self.engine.current, list(self.engine.queue)
        files = {path: path.read_bytes() for path in self.engine.root.rglob('*') if path.is_file()}
        events = self.engine.subscribe()
        version = events.get_nowait()['version']
        self.engine.start()
        await wait_for(lambda: self.engine.error is not None)
        await asyncio.sleep(0)
        state = events.get_nowait()
        self.engine.unsubscribe(events)
        self.assertIn(error, state['error'])
        self.assertIn('then press Start', state['error'])
        self.assertFalse(state['running'])
        self.assertFalse(state['preparing'])
        self.assertIsNone(state['generating'])
        self.assertGreater(state['version'], version)
        self.assertEqual(len(self.pipeline.calls), calls)
        self.assertEqual(self.request.await_count, preparations + 1)
        self.assertFalse((self.engine.root / 'tracks' / request['id']).exists())
        self.assertEqual(self.engine.current, current)
        self.assertEqual(self.engine.queue, queue)
        self.assertEqual({path: path.read_bytes() for path in self.engine.root.rglob('*') if path.is_file()}, files)
        self.assertFalse(self.engine._worker.done())

    async def assert_request_reused(self, original, **changes):
        request = dict(original, id=str(uuid.uuid4()), seed=len(self.pipeline.calls))
        request.update(changes)
        original_request = dict(request)
        self.request.side_effect = None
        self.request.return_value = request
        before = set(self.engine.tracks)
        self.pipeline.gate.set()
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.engine.stop()
        self.assertIsNone(self.engine.error)
        self.assertEqual(self.engine.queue[-1]['id'], request['id'])
        self.assertTrue(self.engine.queue[-1]['input_reused'])
        self.assertEqual(self.pipeline.calls[-1]['lyrics'], request['lyrics'])
        self.assertEqual(set(self.engine.tracks), before | {request['id']})
        self.assertEqual(request, original_request)
        self.engine.advance()

    async def test_prepared_same_identity_is_allowed_for_past_current_and_queued(self):
        await self.seed_history()
        for index, location in enumerate(('past', 'current', 'queued')):
            with self.subTest(location=location):
                original = self.pipeline.calls[index]
                await self.assert_request_reused(
                    original, artist='  ＦＩＸＴＵＲＥ ',
                    title='  ' + original['title'].upper().replace(' ', '\n') + ' ',
                    lyrics=f'Different edition of the words {index}',
                )

    async def test_prepared_same_words_are_allowed_with_new_metadata(self):
        await self.seed_history()
        for index, location in enumerate(('past', 'current', 'queued')):
            with self.subTest(location=location):
                original = self.pipeline.calls[index]
                await self.assert_request_reused(
                    original, artist='Another artist', title=f'Another title {index}',
                    lyrics='  ' + original['lyrics'].upper().replace(' ', '\n  ') + ' ',
                )

    async def prepare_reused_input(self):
        await self.seed_history()
        self.producer.automatic = False
        self.engine.start()
        await wait_for(lambda: len(self.producer.calls) == 7)
        duplicate = self.producer.calls[-1]
        duplicate.update({key: self.pipeline.calls[0][key] for key in ('title', 'artist', 'lyrics')})
        return duplicate

    async def test_paused_reuse_preparation_is_discarded_before_models(self):
        duplicate = await self.prepare_reused_input()
        self.engine.stop()
        self.producer.gates[-1].set()
        await wait_for(lambda: not self.engine.preparing)
        self.assertIsNone(self.engine.error)
        self.assertEqual(len(self.pipeline.calls), 6)
        self.assertFalse((self.engine.root / 'tracks' / duplicate['id']).exists())

    async def test_preference_change_keeps_reuse_preparation_and_queue(self):
        duplicate = await self.prepare_reused_input()
        queue = list(self.engine.queue)
        current = dict(self.engine.current)
        self.engine.update_preferences({'genre': 'Future selection'})
        self.producer.gates[-1].set()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertIsNone(self.engine.error)
        self.assertEqual(self.engine.current, current)
        self.assertEqual(self.engine.queue[:-1], queue)
        self.assertEqual(self.engine.queue[-1]['id'], duplicate['id'])
        self.assertTrue(self.engine.queue[-1]['input_reused'])
        self.assertEqual(len(self.pipeline.calls), 7)
        self.assertEqual(len(self.producer.calls), 7)
        self.assertEqual(self.pipeline.calls[-1]['genre'], duplicate['genre'])

    async def test_prepared_new_versions_are_allowed_after_restart(self):
        await self.seed_history()
        original = self.pipeline.calls[0]
        await self.engine.close()
        self.pipeline = LocalPipeline()
        self.engine = RadioEngine(self.directory.name, self.pipeline)
        await self.engine.initialize()
        await self.assert_request_reused(original, lyrics='Revised edition')
        await self.assert_request_reused(original, title='Renamed', artist='New artist')

    async def test_malformed_prepared_identity_fails_before_models_and_directory(self):
        await self.seed_history()
        original = dict(self.pipeline.calls[0], title='Unused title', lyrics='Unused words')
        for field in ('title', 'artist', 'lyrics'):
            values = [None, 42, [], {}, '', ' \n\t']
            for value in values:
                with self.subTest(field=field, value=value):
                    await self.assert_request_rejected(original, error='Prepared song needs', **{field: value})
            with self.subTest(missing=field):
                request = dict(original)
                del request[field]
                await self.assert_request_rejected(request, error='Prepared song needs')

    async def test_placeholder_and_oversized_lyrics_fail_before_models_and_directory(self):
        await self.seed_history()
        original = dict(self.pipeline.calls[0], id=str(uuid.uuid4()), title='Unused title', artist='Unused artist')
        for lyrics, error in (
            ('Lyrics Here', 'placeholder lyrics'),
            ('x' * 14001, 'Prepared song needs'),
            ('bad\x00text', 'Prepared song needs'),
            ('\ud800', 'Prepared song needs'),
        ):
            with self.subTest(lyrics=repr(lyrics)):
                await self.assert_request_rejected(original, error=error, lyrics=lyrics)

    async def test_distinct_prepared_request_keeps_lyrics_and_generates(self):
        await self.seed_history()
        text = '[Verse]\nDistinct supplied lyrics.\n'
        request = dict(self.pipeline.calls[0], id=str(uuid.uuid4()), title='Distinct song', artist='Distinct artist', lyrics=text)
        self.request.side_effect = None
        self.request.return_value = request
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(len(self.pipeline.calls), 7)
        self.assertEqual(self.pipeline.calls[-1]['lyrics'], text)
        self.assertEqual(self.engine.queue[-1]['id'], request['id'])
        self.assertIsNone(self.engine.error)

    async def test_single_worker_fills_five_slots_and_refills_after_advance(self):
        self.producer.automatic = True
        self.pipeline.gate.set()
        worker = self.engine._worker
        self.engine.start()
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertIs(self.engine._worker, worker)
        self.assertEqual(len(self.pipeline.calls), 6)
        self.assertEqual(self.pipeline.max_active, 1)
        self.assertFalse(self.engine.preparing)
        self.assertIsNone(self.engine.generating)
        next_id = self.engine.next['id']
        result = self.engine.advance()
        self.assertTrue(result['advanced'])
        self.assertEqual(result['state']['current']['id'], next_id)
        self.assertEqual(len(result['state']['queue']), 4)
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(len(self.pipeline.calls), 7)
        self.assertEqual(self.engine.current['id'], next_id)
        self.assertEqual(self.pipeline.max_active, 1)
