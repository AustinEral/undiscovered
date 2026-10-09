import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from genre_radio.engine import RadioEngine, SAVED_INPUT_NOTICE
from genre_radio.live_song_provider import LiveSong, LiveSongProvider
from genre_radio.lyrics_sources import AUTO_PROVIDERS, get_auto_provider
from genre_radio.next_song_provider import SongProvider
from test_engine import FakePipeline, wait_for


class FixtureProvider:
    source_id = 'fixture'

    def __init__(self, **context):
        self.context = context
        self.requests = SongProvider(**context, lyrics_sources=[])

    async def acquire(self):
        raise ValueError('Fixture discovery unavailable')

    def prepare_request(self, song, *, genre):
        return self.requests.prepare_request(song, genre=genre)

    def accepts_cached(self, song):
        return song['source_name'] == 'Fixture'


class ProviderSelectionTests(unittest.TestCase):
    def test_genius_is_default_and_explicit_selection(self):
        self.assertIs(get_auto_provider(), LiveSongProvider)
        self.assertIs(get_auto_provider('genius'), LiveSongProvider)

    def test_unknown_provider_is_not_silently_replaced(self):
        for name in ('missing', '', None, {}, []):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unknown lyrics provider'):
                get_auto_provider(name)

    def test_explicit_registry_entry_is_selectable(self):
        with patch.dict(AUTO_PROVIDERS, fixture=FixtureProvider):
            self.assertIs(get_auto_provider('fixture'), FixtureProvider)

    def test_cli_selects_configured_provider_factory(self):
        from genre_radio.__main__ import main

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps({'lyrics_provider': 'fixture', 'data_dir': directory}))
            with patch.dict(AUTO_PROVIDERS, fixture=FixtureProvider), \
                    patch('sys.argv', ['genre_radio', '--config', str(config)]), \
                    patch('genre_radio.__main__.ModelPipeline'), \
                    patch('genre_radio.__main__.RadioEngine') as engine, \
                    patch('genre_radio.__main__.create_app'), \
                    patch('genre_radio.__main__.web.run_app'):
                main()
            self.assertIs(engine.call_args.kwargs['provider_factory'], FixtureProvider)

    def test_genius_cache_matching_preserves_existing_attribution_rules(self):
        for name, url in [('Genius', ''), ('genius.com', ''), ('Other', 'https://www.genius.com/example-lyrics')]:
            self.assertTrue(LiveSongProvider.accepts_cached({'source_name': name, 'source_url': url}))
        self.assertFalse(LiveSongProvider.accepts_cached({'source_name': 'Fixture', 'source_url': 'https://example.com/song'}))


class ProviderEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.network_patch = patch('aiohttp.ClientSession', side_effect=AssertionError('Unexpected network access'))
        self.network_patch.start()
        self.addCleanup(self.network_patch.stop)
        self.pipeline = FakePipeline()
        self.engine = RadioEngine(directory.name, self.pipeline, provider_factory=FixtureProvider)
        await self.engine.initialize()

    async def asyncTearDown(self):
        await self.engine.close()

    def save(self, title, source_name='Fixture'):
        return self.engine.save_lyrics({'title': title, 'artist': 'Fixture artist',
                                       'text': f'[Verse]\nOriginal fixture words for {title}.',
                                       'source_name': source_name, 'source_url': 'https://example.com/song'})

    async def test_injected_provider_fills_buffer_from_its_cache_with_fresh_jobs(self):
        selected = self.save('Selected source')
        self.save('Unselected source', 'Genius')
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 5 or self.engine.error is not None)
        self.engine.stop()
        self.assertIsNone(self.engine.error)
        self.assertEqual(len(self.pipeline.calls), 6)
        self.assertEqual({call['lyrics_id'] for call in self.pipeline.calls}, {selected['id']})
        self.assertEqual(len({call['id'] for call in self.pipeline.calls}), 6)
        self.assertEqual(self.engine.notice, SAVED_INPUT_NOTICE)
        self.assertEqual(self.engine.snapshot()['preferences']['lyrics_source'], 'fixture')
        self.assertEqual(self.engine.lyrics[self.engine.current['lyrics_id']]['source_name'], 'Fixture')

    async def test_fresh_adapter_record_preserves_text_and_attribution(self):
        song = LiveSong(lyrics_id='fixture-live', title='Fresh fixture', artist='Fixture artist',
                        text='[Verse]\n  Every original fixture word stays.\n',
                        source_name='Fixture', source_url='https://example.com/fresh', original_genre='Jazz')
        with patch.object(FixtureProvider, 'acquire', new_callable=AsyncMock, return_value=song):
            request = await self.engine._request()
        self.assertEqual(request['lyrics'], song.text)
        record = self.engine.lyrics[request['lyrics_id']]
        self.assertEqual(record['source_url'], song.source_url)
        self.assertEqual(record['source_name'], song.source_name)
        self.assertEqual(record['original_genre'], song.original_genre)
        self.assertFalse(request['input_reused'])

    async def test_manual_selection_bypasses_adapter_and_source_filter(self):
        manual = self.save('Manual selection', '')
        self.engine.update_preferences({'lyrics_id': manual['id'], 'genre': 'Jazz'})
        with patch.object(FixtureProvider, 'acquire', new_callable=AsyncMock) as acquire:
            request = await self.engine._request()
        acquire.assert_not_awaited()
        self.assertEqual(request['lyrics_id'], manual['id'])
        self.assertEqual(request['lyrics'], manual['text'])
        self.assertEqual(request['genre'], 'Jazz')

    async def test_provider_factory_survives_preferences_and_reload(self):
        self.save('Persistent cache')
        self.engine.update_preferences({'genre': 'Jazz'})
        self.assertIsInstance(self.engine._live_provider, FixtureProvider)
        self.assertEqual(self.engine._live_provider.context['preferences']['genre'], 'Jazz')
        restored = RadioEngine(self.engine.root, FakePipeline(), provider_factory=FixtureProvider)
        self.assertIsInstance(restored._live_provider, FixtureProvider)
        self.assertEqual(restored.preferences['lyrics_source'], 'fixture')
        self.assertEqual(restored._cached_lyrics()['title'], 'Persistent cache')

    async def test_failed_acquisition_uses_captured_provider_cache_after_preferences_change(self):
        original = self.save('Original source selection', 'Jazz')
        self.save('Future source selection', 'Rock')
        self.engine.update_preferences({'genre': 'Jazz'})
        captured = self.engine._live_provider
        entered = asyncio.Event()
        release = asyncio.Event()

        async def acquire(provider):
            entered.set()
            await release.wait()
            raise ValueError('Fixture discovery unavailable')

        def accepts_cached(provider, song):
            return song['source_name'] == provider.context['preferences']['genre']

        with patch.object(FixtureProvider, 'acquire', acquire), patch.object(FixtureProvider, 'accepts_cached', accepts_cached):
            task = asyncio.create_task(self.engine._request())
            await entered.wait()
            self.engine.update_preferences({'genre': 'Rock'})
            self.assertIsNot(self.engine._live_provider, captured)
            release.set()
            request = await task

        self.assertEqual(request['genre'], 'Jazz')
        self.assertEqual(request['lyrics_id'], original['id'])
        self.assertEqual(request['lyrics'], original['text'])
        self.assertEqual(self.engine.preferences['genre'], 'Rock')

    async def test_other_sources_do_not_hide_adapter_failure(self):
        self.save('Different source', 'Genius')
        with self.assertRaisesRegex(ValueError, 'Fixture discovery unavailable'):
            await self.engine._request()
