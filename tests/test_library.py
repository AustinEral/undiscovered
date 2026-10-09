import tempfile
import unittest
import uuid
from unittest.mock import patch

from genre_radio.engine import RadioEngine, atomic_json, lyrics_hash
from test_engine import FakePipeline, wait_for
from input_fixture import use_imported_input_fixture


class LibraryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        network = patch('genre_radio.engine.aiohttp.ClientSession', side_effect=AssertionError('Imported-library tests must not access the network'))
        network.start()
        self.addCleanup(network.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.pipeline = FakePipeline()
        self.engine = RadioEngine(self.directory.name, self.pipeline)
        use_imported_input_fixture(self.engine)
        await self.engine.initialize()

    async def asyncTearDown(self):
        await self.engine.close()
        self.directory.cleanup()

    def add(self, title, text=None, artist='Fixture artist'):
        return self.engine.save_lyrics({'title': title, 'artist': artist, 'text': text or f'[Verse]\nOriginal fixture words for {title}.\n'})

    async def test_empty_library_does_not_seed_demo_or_call_models(self):
        self.assertEqual(self.engine.lyrics, {})
        self.engine.start()
        await wait_for(lambda: self.engine.version >= 2)
        self.assertFalse(self.engine.snapshot()['library_exhausted'])
        self.assertTrue(self.engine.running)
        self.assertIsNone(self.engine.error)
        self.assertEqual(self.pipeline.calls, [])
        self.assertFalse(self.engine._worker.done())

    async def test_imported_fixture_filters_generated_songs_then_waits(self):
        self.add('First')
        self.add('Second')
        self.engine.start()
        await wait_for(lambda: len(self.engine.tracks) == 2)
        self.assertEqual(len(self.pipeline.calls), 2)
        self.assertEqual(len(self.engine.queue), 1)
        self.assertEqual(self.engine.snapshot()['library_remaining'], 0)
        self.assertFalse(self.engine.snapshot()['library_exhausted'])
        self.assertIsNone(self.engine.error)
        self.assertEqual(self.engine._unused_lyrics(), [])

    async def test_duplicate_metadata_and_duplicate_words_are_rejected_before_saving(self):
        self.add('Same Song', 'Some fixture words.')
        with self.assertRaises(ValueError):
            self.add(' SAME SONG ', 'A differently formatted edition.', 'FIXTURE ARTIST')
        with self.assertRaises(ValueError):
            self.add('Renamed import', '  SOME\nfixture   words. ')
        self.assertEqual(len(self.engine.lyrics), 1)
        self.assertEqual(self.pipeline.calls, [])

    async def test_generated_song_can_be_reimported_when_saved_input_is_missing(self):
        song = self.add('Restore missing input')
        self.engine.start()
        await wait_for(lambda: self.engine.current is not None)
        self.engine.stop()
        history = dict(self.engine.tracks)
        current = self.engine.current
        files = {path: path.read_bytes() for path in (self.engine.root / 'tracks').rglob('*') if path.is_file()}
        del self.engine.lyrics[song['id']]
        atomic_json(self.engine.root / 'lyrics.json', [])
        restored = self.engine.save_lyrics({key: value for key, value in song.items() if key != 'id'})
        self.assertNotEqual(restored['id'], song['id'])
        self.assertEqual(restored['text'], song['text'])
        self.assertEqual(self.engine.tracks, history)
        self.assertEqual(self.engine.current, current)
        self.assertEqual({path: path.read_bytes() for path in (self.engine.root / 'tracks').rglob('*') if path.is_file()}, files)
        with self.assertRaisesRegex(ValueError, 'already saved'):
            self.engine.save_lyrics({key: value for key, value in song.items() if key != 'id'})

    async def test_successful_history_keeps_unused_input_count_after_restart(self):
        self.add('Example')
        self.engine.start()
        await wait_for(lambda: self.engine.current is not None)
        fresh = RadioEngine(self.directory.name, FakePipeline())
        self.assertEqual(fresh.snapshot()['library_remaining'], 0)
        self.assertEqual(fresh._unused_lyrics(), [])

    async def test_failed_generation_does_not_consume_imported_song(self):
        self.add('Retry this')
        self.pipeline.fail_at = 1
        self.engine.start()
        await wait_for(lambda: self.engine.error is not None)
        self.assertEqual(self.engine.snapshot()['library_remaining'], 1)
        self.pipeline.fail_at = None
        self.engine.start()
        await wait_for(lambda: self.engine.current is not None)
        self.assertEqual(len(self.pipeline.calls), 2)
        self.assertFalse(self.engine.snapshot()['library_exhausted'])

    async def test_importing_another_song_wakes_waiting_radio(self):
        self.engine.start()
        self.add('First import')
        await wait_for(lambda: self.engine.current is not None)
        self.add('Another import')
        await wait_for(lambda: len(self.engine.queue) == 1)
        self.assertEqual(len(self.pipeline.calls), 2)
        self.assertEqual(self.engine.next['title'], 'Another import')

    async def test_manual_selection_regenerates_saved_lyrics_once_then_returns_to_auto(self):
        song = self.add('Manual choice')
        self.engine.update_preferences({'lyrics_id': song['id']})
        self.engine.start()
        await wait_for(lambda: self.engine.current is not None)
        original = self.engine.current
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')
        self.engine.update_preferences({'lyrics_id': song['id']})
        self.engine.start()
        await wait_for(lambda: len(self.engine.queue) == 1)
        self.assertEqual(len(self.pipeline.calls), 2)
        self.assertEqual(self.engine.preferences['lyrics_id'], 'auto')
        self.assertIsNone(self.engine.error)
        self.assertEqual(self.engine.lyrics[song['id']], song)
        self.assertEqual(self.engine.current, original)
        self.assertNotEqual(self.engine.next['id'], original['id'])
        self.assertTrue(self.engine.next['input_reused'])
        self.assertEqual(len(self.engine.tracks), 2)

    def legacy_queue(self):
        tracks = []
        for title, text in [('Current', 'Current words'), (' CURRENT ', 'Different edition'),
                            ('Renamed', ' CURRENT WORDS '), ('Unique', 'Unique words'),
                            ('Renamed unique', ' UNIQUE WORDS ')]:
            song = {
                'id': str(uuid.uuid4()), 'title': title, 'artist': 'Fixture artist', 'text': text,
                'source_url': '', 'source_name': '', 'original_genre': '',
            }
            self.engine.lyrics[song['id']] = song
            identity = str(uuid.uuid4())
            track = {
                'id': identity, 'title': title, 'artist': song['artist'],
                'genre': 'Legacy', 'duration': 60, 'created_at': '2026-01-01T00:00:00Z',
                'lyrics_id': song['id'], 'lyrics_hash': lyrics_hash(text),
                'audio_url': f'media?id={identity}&kind=audio',
                'cover_url': f'media?id={identity}&kind=cover',
            }
            directory = self.engine.root / 'tracks' / identity
            directory.mkdir()
            (directory / 'audio.mp3').write_bytes(b'Preserved legacy audio')
            (directory / 'cover.png').write_bytes(b'Preserved legacy cover')
            atomic_json(directory / 'manifest.json', track)
            self.engine.tracks[identity] = track
            tracks.append(track)
        self.engine.current, self.engine.queue = tracks[0], tracks[1:]
        self.engine.preferences['lyrics_id'] = tracks[0]['lyrics_id']
        atomic_json(self.engine.root / 'lyrics.json', list(self.engine.lyrics.values()))
        self.engine._save()
        return tracks

    async def test_new_versions_remain_queued_in_auto_and_manual_modes(self):
        tracks = self.legacy_queue()
        preserved = {path: path.read_bytes() for path in self.engine.root.rglob('*') if path.is_file() and path.name != 'state.json'}
        for selection in (tracks[0]['lyrics_id'], 'auto'):
            with self.subTest(selection=selection):
                self.engine.preferences['lyrics_id'] = selection
                self.engine._save()
                fresh = RadioEngine(self.directory.name, FakePipeline())
                self.assertEqual(fresh.queue, tracks[1:])
                self.assertEqual(fresh.current, tracks[0])
                self.assertEqual(len(fresh.tracks), 5)
                self.assertEqual({path: path.read_bytes() for path in fresh.root.rglob('*') if path.is_file() and path.name != 'state.json'}, preserved)
        self.assertEqual(self.pipeline.calls, [])

    async def test_advance_prunes_only_current_and_duplicate_track_ids(self):
        tracks = self.legacy_queue()
        self.engine.queue = [tracks[0], tracks[1], tracks[1], tracks[2]]
        result = self.engine.advance()
        self.assertTrue(result['advanced'])
        self.assertEqual(self.engine.current, tracks[1])
        self.assertEqual(self.engine.queue, [tracks[2]])
        self.assertEqual(len(self.engine.tracks), 5)
        self.assertEqual(self.pipeline.calls, [])

    async def test_legacy_manifests_without_hashes_use_saved_lyrics(self):
        tracks = self.legacy_queue()
        for track in tracks:
            del track['lyrics_hash']
            atomic_json(self.engine.root / 'tracks' / track['id'] / 'manifest.json', track)
        fresh = RadioEngine(self.directory.name, FakePipeline())
        self.assertEqual(fresh.queue, tracks[1:])
        self.assertEqual(len(fresh.lyrics), 5)
        self.assertEqual(len(fresh.tracks), 5)

    async def test_advance_allows_new_versions_of_past_songs(self):
        tracks = self.legacy_queue()
        self.engine.current = tracks[3]
        self.engine.queue = tracks[1:3]
        self.engine._save()
        result = self.engine.advance()
        self.assertTrue(result['advanced'])
        self.assertEqual(self.engine.current, tracks[1])
        self.assertEqual(self.engine.queue, [tracks[2]])
        self.assertEqual(len(self.engine.tracks), 5)

    async def test_prune_only_advance_broadcasts_and_preserves_current(self):
        tracks = self.legacy_queue()
        self.engine.queue = [tracks[0], tracks[0]]
        self.engine._save()
        events = self.engine.subscribe()
        before = events.get_nowait()
        result = self.engine.advance()
        state = events.get_nowait()
        self.assertFalse(result['advanced'])
        self.assertEqual(state, result['state'])
        self.assertGreater(state['version'], before['version'])
        self.assertEqual(state['current']['id'], tracks[0]['id'])
        self.assertEqual(state['queue'], [])
        self.assertEqual(RadioEngine(self.directory.name, FakePipeline()).queue, [])
        self.assertEqual(len(self.engine.tracks), 5)
        self.assertEqual(self.pipeline.calls, [])

    async def test_invalid_import_source_label_has_a_validation_error(self):
        with self.assertRaises(ValueError):
            self.engine.save_lyrics({'title': 'Example', 'text': 'Original fixture', 'source_name': 42})
        with self.assertRaises(ValueError):
            self.engine.save_lyrics({'title': 'Example', 'text': 'Original fixture', 'source_name': 'x' * 161})


if __name__ == '__main__':
    unittest.main()
