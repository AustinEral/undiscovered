import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from genre_radio.engine import RadioEngine
from genre_radio.server import create_app
from test_engine import FakePipeline, wait_for, seed_imports
from input_fixture import use_imported_input_fixture


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = RadioEngine(self.directory.name, FakePipeline(), buffer_size=2)
        use_imported_input_fixture(self.engine)
        seed_imports(self.engine)
        self.client = TestClient(TestServer(create_app(self.engine)))
        await self.client.start_server()
        network = patch('aiohttp.ClientSession', side_effect=AssertionError('External sessions are forbidden in server tests'))
        network.start()
        self.addCleanup(network.stop)

    async def asyncTearDown(self):
        await self.client.close()
        self.directory.cleanup()

    async def test_frontend_and_state(self):
        response = await self.client.get('/radio')
        self.assertEqual(response.status, 200)
        self.assertIn('html', response.headers['Content-Type'])
        state = await (await self.client.get('/radio/state')).json()
        self.assertFalse(state['running'])
        self.assertEqual(state['buffer_target'], 2)
        self.assertIn('instance_id', state)
        self.assertIsInstance(state['version'], int)
        for asset in ['app.js', 'styles.css']:
            self.assertEqual((await self.client.get('/radio/' + asset)).status, 200)

    async def test_controls_prefs_and_saved_lyrics(self):
        response = await self.client.post('/radio/lyrics', json={'title': 'Example', 'artist': 'Example Artist', 'text': '[Chorus]\nOriginal words.\n', 'original_duration': 245})
        body = await response.json()
        identity = body['lyrics']['id']
        self.assertEqual(response.status, 200)
        self.assertEqual(body['lyrics']['text'], '[Chorus]\nOriginal words.\n')
        changed = await (await self.client.post('/radio/preferences', json={'genre': 'UK garage', 'lyrics_id': identity})).json()
        self.assertEqual(changed['preferences']['genre'], 'UK garage')
        self.assertGreater(changed['version'], body['state']['version'])
        saved = await (await self.client.get('/radio/lyrics', params={'id': identity})).json()
        self.assertEqual(saved['text'], body['lyrics']['text'])
        self.assertEqual(saved['original_duration'], 245)
        self.assertEqual(next(record for record in changed['lyrics'] if record['id'] == identity)['original_duration'], 245)
        self.assertNotIn('duration', changed['preferences'])
        self.assertTrue((await (await self.client.post('/radio/start')).json())['running'])
        await wait_for(lambda: len(self.engine.queue) == 2)
        state = await (await self.client.get('/radio/state')).json()
        self.assertTrue(state['running'])
        self.assertIsNone(state['error'])
        self.assertEqual(state['preferences']['lyrics_id'], 'auto')
        self.assertEqual(sum(call['lyrics_id'] == identity for call in self.engine.pipeline.calls), 1)
        self.assertEqual(self.engine.pipeline.calls[0]['original_duration'], 245)
        self.assertNotIn('duration', self.engine.pipeline.calls[0])
        self.assertEqual(state['current']['duration'], 60.5)
        self.assertEqual(state['current']['planned_duration'], 90)
        self.assertEqual(state['current']['bpm'], 120)
        advanced = await (await self.client.post('/radio/advance')).json()
        self.assertTrue(advanced['advanced'])
        self.assertNotEqual(advanced['state']['current']['id'], state['current']['id'])
        await self.client.post('/radio/preferences', json={'lyrics_id': 'auto'})
        await self.client.post('/radio/start')
        await wait_for(lambda: len(self.engine.queue) == 2)
        self.assertTrue((await (await self.client.post('/radio/advance')).json())['advanced'])
        self.assertFalse((await (await self.client.post('/radio/stop')).json())['running'])

    async def test_preferences_preserve_ready_queue_and_do_not_start_paused_generation(self):
        changed = await (await self.client.post('/radio/preferences', json={'genre': 'Original style'})).json()
        self.assertFalse(changed['running'])
        self.assertFalse(changed['preparing'])
        self.assertEqual(self.engine.pipeline.calls, [])
        await self.client.post('/radio/start')
        await wait_for(lambda: len(self.engine.queue) == 2)
        before = await (await self.client.post('/radio/stop')).json()
        queue = before['queue']
        selected = list(self.engine.lyrics)[-1]
        changed = await (await self.client.post('/radio/preferences', json={'genre': 'Future style', 'lyrics_id': selected})).json()
        self.assertEqual(changed['queue'], queue)
        self.assertEqual(changed['next'], before['next'])
        self.assertEqual(changed['current'], before['current'])
        self.assertFalse(changed['running'])
        self.assertEqual(len(self.engine.pipeline.calls), 3)
        self.assertGreater(changed['version'], before['version'])
        for track in queue:
            for kind in ('audio', 'cover'):
                self.assertEqual((await self.client.get(f"/radio/media?id={track['id']}&kind={kind}")).status, 200)
        state = await (await self.client.get('/radio/state')).json()
        self.assertEqual(state['queue'], queue)
        self.assertFalse(state['running'])
        self.assertEqual(len(self.engine.pipeline.calls), 3)

    async def test_preferences_and_pause_retain_active_model_then_queue_its_style(self):
        await self.client.post('/radio/preferences', json={'genre': 'Original style'})
        await self.client.post('/radio/start')
        await wait_for(lambda: len(self.engine.queue) == 2)
        self.engine.pipeline.gate.clear()
        await self.client.post('/radio/advance')
        await wait_for(lambda: len(self.engine.pipeline.calls) == 4)
        before = await (await self.client.get('/radio/state')).json()
        selected = list(self.engine.lyrics)[-1]
        changed = await (await self.client.post('/radio/preferences', json={'genre': 'Future style', 'lyrics_id': selected})).json()
        self.assertEqual(changed['generating'], before['generating'])
        self.assertNotIn('superseded', changed['generating'])
        self.assertEqual(changed['current'], before['current'])
        self.assertEqual(changed['queue'], before['queue'])
        await self.client.post('/radio/stop')
        self.engine.pipeline.gate.set()
        await wait_for(lambda: len(self.engine.queue) == 2)
        state = await (await self.client.get('/radio/state')).json()
        self.assertEqual(state['queue'][:-1], before['queue'])
        self.assertEqual(state['queue'][-1]['id'], before['generating']['id'])
        self.assertEqual(state['queue'][-1]['genre'], 'Original style')
        self.assertEqual(state['current'], before['current'])
        self.assertEqual(state['preferences']['lyrics_id'], selected)
        self.assertFalse(state['running'])
        self.assertEqual(len(self.engine.pipeline.calls), 4)

    async def test_cached_fallback_notice_and_reuse_metadata_reach_state_api(self):
        saved = self.engine.save_lyrics({'title': 'Cached chart input', 'artist': 'Fixture chart artist', 'text': '[Verse]\nActual saved chart fixture words.', 'source_name': 'Genius', 'source_url': 'https://genius.com/fixture-lyrics'})
        self.engine._request = RadioEngine._request.__get__(self.engine)
        with patch('genre_radio.live_song_provider.LiveSongProvider.acquire', new_callable=AsyncMock, side_effect=ValueError('Chart unavailable')) as acquire:
            await self.client.post('/radio/start')
            await wait_for(lambda: len(self.engine.queue) == 2)
            await self.client.post('/radio/stop')
            state = await (await self.client.get('/radio/state')).json()
            self.assertIsNone(state['error'])
            self.assertIn('Using saved lyrics', state['notice'])
            self.assertFalse(state['current']['input_reused'])
            self.assertTrue(all(track['input_reused'] for track in state['queue']))
            self.assertTrue(all(call['lyrics_id'] == saved['id'] for call in self.engine.pipeline.calls))
            before = state['current']['id']
            after = await (await self.client.post('/radio/advance')).json()
            self.assertTrue(after['advanced'])
            self.assertNotEqual(after['state']['current']['id'], before)
            self.assertTrue(after['state']['current']['input_reused'])
            acquire.assert_awaited_once()

    async def test_media_range_and_unknown_paths(self):
        await self.client.post('/radio/start')
        await wait_for(lambda: self.engine.current is not None)
        identity = self.engine.current['id']
        url = f'/radio/media?id={identity}&kind=audio'
        full = await self.client.get(url)
        data = await full.read()
        self.assertEqual(full.headers['Content-Type'], 'audio/mpeg')
        ranged = await self.client.get(url, headers={'Range': 'bytes=0-2'})
        self.assertEqual(ranged.status, 206)
        self.assertEqual(await ranged.read(), data[:3])
        self.assertEqual((await self.client.get('/radio/media?id=../../etc/passwd&kind=audio')).status, 404)
        self.assertEqual((await self.client.get(f'/radio/media?id={identity}&kind=manifest')).status, 404)
        self.assertEqual((await self.client.get('/radio/no-such-file')).status, 404)

    async def test_favorites_and_track_details_are_read_only_for_playback_and_generation(self):
        await self.client.post('/radio/start')
        await wait_for(lambda: len(self.engine.queue) == 2)
        before = await (await self.client.post('/radio/stop')).json()
        identity = before['next']['id']
        response = await self.client.post('/radio/favorites', json={'id': identity, 'starred': True})
        self.assertEqual(response.status, 200)
        starred = (await response.json())['state']
        self.assertEqual(starred['starred_ids'], [identity])
        response = await self.client.get('/radio/favorites')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        favorites = await response.json()
        self.assertEqual(favorites, {'tracks': [before['next']], 'missing_ids': []})
        response = await self.client.get('/radio/track', params={'id': identity})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        details = (await response.json())['track']
        self.assertEqual(details['id'], identity)
        self.assertEqual(details['prompts'], {'music': 'Test caption', 'cover': 'Test cover'})
        repeat = await (await self.client.post('/radio/favorites', json={'id': identity, 'starred': True})).json()
        self.assertEqual(repeat['state'], starred)
        after = await (await self.client.get('/radio/state')).json()
        for key in ('current', 'queue', 'preferences', 'running', 'generating', 'preparing'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(len(self.engine.pipeline.calls), 3)
        self.assertEqual((await self.client.get('/radio/track?id=../../outside')).status, 404)
        self.assertEqual((await self.client.get('/radio/track')).status, 404)
        for body in ({'id': identity, 'starred': 'true'}, {'id': '../outside', 'starred': True}, {'id': identity, 'starred': True, 'extra': 1}):
            self.assertEqual((await self.client.post('/radio/favorites', json=body)).status, 400)
        unstarred = (await (await self.client.post('/radio/favorites', json={'id': identity, 'starred': False})).json())['state']
        self.assertEqual(unstarred['starred_ids'], [])
        self.assertEqual((await self.client.post('/radio/favorites', json={'id': identity, 'starred': True}, headers={'Origin': 'https://example.com'})).status, 403)

    async def test_generation_next_validates_failure_and_returns_state(self):
        self.engine.pipeline.fail_at = 1
        selected = next(iter(self.engine.lyrics))
        self.engine.update_preferences({'lyrics_id': selected})
        await self.client.post('/radio/start')
        await wait_for(lambda: self.engine.failure is not None)
        failed = await (await self.client.get('/radio/state')).json()
        self.assertTrue(failed['failure']['can_skip'])
        self.assertNotIn('lyrics', failed['failure'])
        for body in ({}, {'failed_id': 'stale'}, {'failed_id': failed['failure']['id'], 'extra': 1}):
            self.assertEqual((await self.client.post('/radio/generation/next', json=body)).status, 400)
        self.assertEqual(self.engine.failure, failed['failure'])
        self.engine.pipeline.gate.clear()
        response = await self.client.post('/radio/generation/next', json={'failed_id': failed['failure']['id']})
        self.assertEqual(response.status, 200)
        state = (await response.json())['state']
        self.assertIsNone(state['failure'])
        self.assertTrue(state['running'])
        self.assertEqual(state['preferences']['lyrics_id'], 'auto')
        self.assertEqual(state['current'], failed['current'])
        self.assertEqual(state['queue'], failed['queue'])
        self.assertEqual((await self.client.post('/radio/generation/next', json={'failed_id': failed['failure']['id']})).status, 400)
        await self.client.post('/radio/stop')
        self.engine.pipeline.gate.set()

    async def test_live_events_and_validation(self):
        stream = await self.client.get('/radio/events')
        self.assertEqual(stream.status, 200)
        line = await stream.content.readline()
        first = json.loads(line.decode().removeprefix('data: '))
        self.assertEqual(first['buffer_target'], 2)
        stream.close()
        for duration in (30, 120, 180, 300, 900):
            response = await self.client.post('/radio/preferences', json={'duration': duration})
            self.assertEqual(response.status, 400)
            self.assertIn('Unknown preference', (await response.json())['error'])
        self.assertEqual((await self.client.post('/radio/preferences', data='bad', headers={'Content-Type': 'application/json'})).status, 400)
        self.assertEqual((await self.client.post('/radio/start', headers={'Origin': 'https://example.com'})).status, 403)
        self.assertEqual((await self.client.get('/radio/state', headers={'Host': 'example.com'})).status, 403)
        self.assertEqual((await self.client.post('/radio/lyrics', json={'title': 'Example', 'text': 'x' * 14001})).status, 400)


if __name__ == '__main__':
    unittest.main()
