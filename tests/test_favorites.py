import json
import tempfile
import unittest
from unittest.mock import patch

from genre_radio.engine import RadioEngine, atomic_json
from input_fixture import use_imported_input_fixture
from test_engine import FakePipeline, seed_imports, wait_for


class FavoritesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        network = patch('aiohttp.ClientSession', side_effect=AssertionError('No live acquisition in favorites tests'))
        network.start()
        self.addCleanup(network.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pipeline = FakePipeline()
        self.engine = RadioEngine(self.directory.name, self.pipeline, buffer_size=2)
        use_imported_input_fixture(self.engine)
        seed_imports(self.engine)
        await self.engine.initialize()
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 2)
        self.engine.stop()

    async def asyncTearDown(self):
        await self.engine.close()

    async def test_star_is_idempotent_and_browse_details_do_not_mutate_generation(self):
        before = self.engine.snapshot()
        files = {path: path.read_bytes() for path in self.engine.root.rglob('*') if path.is_file()}
        selected = self.engine.next
        selected['caption'] = 'Exact music\nprompt <not markup>'
        selected['cover_prompt'] = 'Exact cover prompt'
        events = self.engine.subscribe()
        events.get_nowait()
        state = self.engine.set_favorite({'id': selected['id'], 'starred': True})['state']
        self.assertEqual(events.get_nowait(), state)
        self.assertGreater(state['version'], before['version'])
        self.assertEqual(state['starred_ids'], [selected['id']])
        self.assertEqual(self.engine.set_favorite({'id': selected['id'], 'starred': True})['state'], state)
        self.assertTrue(events.empty())
        favorites = self.engine.favorites()
        self.assertEqual(favorites['missing_ids'], [])
        self.assertEqual([track['id'] for track in favorites['tracks']], [selected['id']])
        self.assertNotIn('prompts', favorites['tracks'][0])
        details = self.engine.track_details(selected['id'])['track']
        self.assertEqual(details['prompts'], {'music': selected['caption'], 'cover': selected['cover_prompt']})
        details['prompts']['music'] = 'Client mutation'
        favorites['tracks'][0]['title'] = 'Client mutation'
        self.assertEqual(self.engine.track_details(selected['id'])['track']['title'], selected['title'])
        for key in ('current', 'queue', 'preferences', 'running', 'preparing', 'generating', 'error', 'failure'):
            self.assertEqual(self.engine.snapshot()[key], before[key])
        self.assertEqual(len(self.pipeline.calls), 3)
        self.assertEqual({path: path.read_bytes() for path in files}, files)
        self.assertEqual(json.loads((self.engine.root / 'favorites.json').read_text()), [selected['id']])
        self.engine.unsubscribe(events)

    async def test_restart_keeps_favorites_and_unknown_missing_references(self):
        current_id = self.engine.current['id']
        other_id = self.engine.next['id']
        atomic_json(self.engine.root / 'favorites.json', [current_id, 'missing-old-recording'])
        fresh = RadioEngine(self.directory.name, FakePipeline())
        try:
            self.assertEqual(fresh.snapshot()['starred_ids'], sorted([current_id, 'missing-old-recording']))
            self.assertEqual(fresh.favorites()['missing_ids'], ['missing-old-recording'])
            self.assertEqual(fresh.favorites()['tracks'][0]['id'], current_id)
            fresh.set_favorite({'id': other_id, 'starred': True})
            self.assertIn('missing-old-recording', json.loads((fresh.root / 'favorites.json').read_text()))
            fresh.set_favorite({'id': 'missing-old-recording', 'starred': False})
            self.assertEqual(fresh.favorites()['missing_ids'], [])
            self.assertEqual(fresh.pipeline.calls, [])
            self.assertFalse(fresh.running)
            self.assertEqual(fresh.track_details(current_id)['track'], fresh.snapshot()['current'])
        finally:
            await fresh.close()

    async def test_invalid_mutations_and_failed_write_preserve_favorites(self):
        identity = self.engine.current['id']
        before = self.engine.snapshot()
        for values in ({}, {'id': identity}, {'id': identity, 'starred': 1}, {'id': identity, 'starred': 'true'}, {'id': [], 'starred': True}, {'id': '../escape', 'starred': True}, {'id': 'unknown', 'starred': False}, {'id': identity, 'starred': True, 'extra': True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.engine.set_favorite(values)
            self.assertEqual(self.engine.snapshot(), before)
        with patch('genre_radio.engine.atomic_json', side_effect=OSError('Fixture disk unavailable')):
            with self.assertRaises(OSError):
                self.engine.set_favorite({'id': identity, 'starred': True})
        self.assertEqual(self.engine.snapshot(), before)
        self.assertFalse((self.engine.root / 'favorites.json').exists())
        self.engine.set_favorite({'id': identity, 'starred': False})
        self.assertEqual(self.engine.snapshot(), before)

    async def test_current_and_historical_tracks_share_exact_details_shape(self):
        identity = self.engine.current['id']
        expected = self.engine.snapshot()['current']
        self.engine.advance()
        before = self.engine.snapshot()
        self.assertEqual(self.engine.track_details(identity), {'track': expected})
        self.assertEqual(self.engine.snapshot(), before)
        self.engine.tracks[identity].pop('caption')
        self.engine.tracks[identity].pop('cover_prompt')
        self.assertEqual(self.engine.track_details(identity)['track']['prompts'], {'music': '', 'cover': ''})
        with self.assertRaises(KeyError):
            self.engine.track_details('../../outside')


if __name__ == '__main__':
    unittest.main()
