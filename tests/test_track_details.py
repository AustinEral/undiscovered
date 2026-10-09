import asyncio
import tempfile
import unittest
from unittest.mock import patch

from genre_radio.engine import RadioEngine
from input_fixture import use_imported_input_fixture
from test_engine import FakePipeline, seed_imports, wait_for


class TrackDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        network = patch('aiohttp.ClientSession', side_effect=AssertionError('No production acquisition in track detail tests'))
        network.start()
        self.addCleanup(network.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pipeline = FakePipeline()
        self.engine = RadioEngine(self.directory.name, self.pipeline)
        use_imported_input_fixture(self.engine)
        seed_imports(self.engine)
        await self.engine.initialize()

    async def asyncTearDown(self):
        await self.engine.close()

    async def fill(self):
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.engine.stop()

    async def test_exact_prompts_only_follow_current_track(self):
        await self.fill()
        state = self.engine.snapshot()
        self.assertEqual(state['current']['prompts'], {'music': 'Test caption', 'cover': 'Test cover'})
        self.assertEqual(state['current']['vocal_gender'], 'female')
        self.assertNotIn('prompts', state['next'])
        self.assertTrue(all('prompts' not in track for track in state['queue'] + state['recent']))
        state['current']['prompts']['music'] = 'Changed client snapshot'
        self.assertEqual(self.engine.current['caption'], 'Test caption')
        self.engine.next['caption'] = 'Exact next music prompt\nwith line break'
        self.engine.next['cover_prompt'] = 'Exact next cover prompt'
        after = self.engine.advance()['state']
        self.assertEqual(after['current']['prompts']['music'], 'Exact next music prompt\nwith line break')
        self.assertEqual(after['current']['prompts']['cover'], 'Exact next cover prompt')

    async def test_older_track_does_not_get_invented_prompts(self):
        await self.fill()
        self.engine.current.pop('caption')
        self.engine.current.pop('cover_prompt')
        self.assertEqual(self.engine.snapshot()['current']['prompts'], {'music': '', 'cover': ''})

    async def test_manual_song_is_used_once_then_auto_continues(self):
        selected = list(self.engine.lyrics)[-1]
        self.engine.update_preferences({'lyrics_id': selected})
        await self.fill()
        self.assertEqual(self.pipeline.calls[0]['lyrics_id'], selected)
        self.assertEqual(sum(call['lyrics_id'] == selected for call in self.pipeline.calls), 1)
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')
        self.assertEqual(self.engine.current['source_selection'], 'manual')
        self.assertTrue(all(track['source_selection'] == 'auto' for track in self.engine.queue))
        self.assertIsNone(self.engine.error)

    async def test_paused_manual_completion_does_not_restart_radio(self):
        selected = next(iter(self.engine.lyrics))
        self.engine.update_preferences({'lyrics_id': selected})
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')
        self.assertEqual(len(self.pipeline.calls), 1)

    async def test_completed_manual_result_preserves_new_selection(self):
        first, second = list(self.engine.lyrics)[:2]
        self.engine.update_preferences({'lyrics_id': first})
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.update_preferences({'lyrics_id': second})
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.generating is None)
        self.assertEqual(self.engine.preferences['lyrics_id'], second)
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.current['lyrics_id'], first)
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(self.engine.next['lyrics_id'], second)
        self.assertEqual(self.engine.next['source_selection'], 'manual')
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')

    async def test_genre_change_during_manual_model_consumes_selection_only_once(self):
        selected = list(self.engine.lyrics)[-1]
        self.engine.update_preferences({'lyrics_id': selected, 'genre': 'Original style'})
        revision = self.engine._source_selection_revision
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.update_preferences({'genre': 'Future style'})
        self.assertEqual(self.engine._source_selection_revision, revision)
        self.pipeline.gate.set()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(self.engine.current['genre'], 'Original style')
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')
        self.assertEqual(self.engine._source_selection_revision, revision)
        self.assertEqual(sum(call['lyrics_id'] == selected for call in self.pipeline.calls), 1)
        self.assertTrue(all(track['genre'] == 'Future style' and track['source_selection'] == 'auto' for track in self.engine.queue))

    async def test_switch_away_and_back_to_same_manual_id_is_a_new_selection(self):
        first, second = list(self.engine.lyrics)[-2:]
        self.engine.update_preferences({'lyrics_id': first})
        revision = self.engine._source_selection_revision
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.update_preferences({'lyrics_id': second})
        self.engine.update_preferences({'lyrics_id': first})
        self.assertEqual(self.engine._source_selection_revision, revision + 2)
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        self.assertEqual(self.engine.preferences['lyrics_id'], first)
        self.assertEqual(len(self.pipeline.calls), 1)
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(self.engine.next['lyrics_id'], first)
        self.assertEqual(self.engine.next['source_selection'], 'manual')
        self.assertNotEqual(self.engine.current['id'], self.engine.next['id'])
        self.assertEqual(sum(call['lyrics_id'] == first for call in self.pipeline.calls), 2)
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')

    async def test_repeated_same_source_click_does_not_create_another_selection(self):
        selected = list(self.engine.lyrics)[-1]
        self.engine.update_preferences({'lyrics_id': selected})
        revision = self.engine._source_selection_revision
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.update_preferences({'lyrics_id': selected})
        self.engine.update_preferences({'lyrics_id': selected, 'genre': 'Future style'})
        self.assertEqual(self.engine._source_selection_revision, revision)
        self.pipeline.gate.set()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(sum(call['lyrics_id'] == selected for call in self.pipeline.calls), 1)
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')

    async def test_manual_source_change_during_preparation_keeps_original_job(self):
        first, second = list(self.engine.lyrics)[-2:]
        self.engine.update_preferences({'lyrics_id': first, 'genre': 'Original style'})
        original_request = self.engine._request
        release = asyncio.Event()

        async def request():
            prepared = await original_request()
            await release.wait()
            return prepared

        self.engine._request = request
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: self.engine.preparing)
        self.engine.update_preferences({'lyrics_id': second, 'genre': 'Future style'})
        release.set()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.assertEqual(self.pipeline.calls[0]['lyrics_id'], first)
        self.assertEqual(self.pipeline.calls[0]['genre'], 'Original style')
        self.pipeline.gate.set()
        await wait_for(lambda: len(self.engine.queue) == 5)
        self.assertEqual(self.engine.current['lyrics_id'], first)
        self.assertEqual(self.engine.current['source_selection'], 'manual')
        self.assertEqual(self.engine.next['lyrics_id'], second)
        self.assertEqual(self.engine.next['genre'], 'Future style')
        self.assertEqual(self.engine.next['source_selection'], 'manual')
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')
        self.assertEqual(sum(call['lyrics_id'] == first for call in self.pipeline.calls), 1)
        self.assertEqual(sum(call['lyrics_id'] == second for call in self.pipeline.calls), 1)

    async def test_switch_manual_to_auto_does_not_get_consumed_by_old_completion(self):
        selected = list(self.engine.lyrics)[-1]
        self.engine.update_preferences({'lyrics_id': selected})
        revision = self.engine._source_selection_revision
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.update_preferences({'lyrics_id': 'auto'})
        self.engine.update_preferences({'lyrics_id': selected})
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None)
        self.assertEqual(self.engine.preferences['lyrics_id'], selected)
        self.assertEqual(self.engine._source_selection_revision, revision + 2)
        self.assertEqual(self.engine.current['lyrics_id'], selected)
        self.assertEqual(len(self.pipeline.calls), 1)
        self.assertFalse(self.engine.running)

    async def test_manual_failure_does_not_consume_selection(self):
        selected = list(self.engine.lyrics)[-1]
        self.engine.update_preferences({'lyrics_id': selected})
        self.pipeline.fail_at = 1
        self.engine.start()
        await wait_for(lambda: self.engine.error is not None)
        self.assertEqual(self.engine.preferences['lyrics_id'], selected)
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.engine.current)

    async def test_vocal_plan_progress_is_visible_without_current_changes(self):
        self.pipeline.gate.clear()
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        await self.pipeline.progress('planning', 1.0, {'vocal_gender': 'female', 'planned_duration': 90, 'bpm': 120, 'genre': 'Bluegrass'})
        state = self.engine.snapshot()
        self.assertEqual(state['generating']['vocal_gender'], 'female')
        self.assertIsNone(state['current'])
        self.engine.stop()
        self.pipeline.gate.set()


if __name__ == '__main__':
    unittest.main()
