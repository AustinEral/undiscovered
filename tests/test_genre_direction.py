import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from genre_radio.engine import GENRES, RadioEngine
from test_engine import FakePipeline, wait_for


class GenreDirectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        network = patch('aiohttp.ClientSession', side_effect=AssertionError('No production input acquisition in genre tests'))
        network.start()
        self.addCleanup(network.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pipeline = FakePipeline(gate=True)
        self.engine = RadioEngine(self.directory.name, self.pipeline)
        self.song = self.engine.save_lyrics({'title': 'Fixture title', 'artist': 'Fixture performer', 'text': 'Original local fixture words.', 'original_genre': 'Hip-hop'})
        self.prepared = {
            'id': str(uuid.uuid4()), 'title': self.song['title'], 'artist': self.song['artist'],
            'lyrics': self.song['text'], 'lyrics_id': self.song['id'],
            'genre': 'Producer guess', 'seed': 4,
        }
        self.engine._request = AsyncMock(return_value=self.prepared)
        await self.engine.initialize()

    async def asyncTearDown(self):
        await self.engine.close()

    async def complete_one(self):
        self.engine.start()
        await wait_for(lambda: len(self.pipeline.calls) == 1)
        self.engine.stop()
        self.pipeline.gate.set()
        await wait_for(lambda: self.engine.current is not None or self.engine.error is not None)

    async def test_auto_reaches_prompt_writer_and_selected_genre_is_persisted(self):
        await self.complete_one()
        request = self.pipeline.calls[0]
        self.assertEqual(request['genre'], 'Auto')
        self.assertEqual(request['genre_choices'], GENRES)
        self.assertEqual(request['original_genre'], 'Hip-hop')
        self.assertEqual(request['lyrics'], self.song['text'])
        self.assertEqual(self.prepared['genre'], 'Producer guess')
        self.assertNotIn('duration', request)
        self.assertEqual(self.engine.current['planned_duration'], 90)
        self.assertEqual(self.engine.current['bpm'], 120)
        self.assertNotEqual(self.engine.current['genre'], 'Auto')
        self.assertNotEqual(self.engine.current['genre'], 'Producer guess')
        self.assertEqual(self.engine.current['genre_selection'], 'auto')
        self.assertEqual(self.engine.current['original_genre'], 'Hip-hop')

    async def test_legacy_producer_duration_cannot_override_model_plan(self):
        self.prepared['duration'] = 180
        await self.complete_one()
        self.assertEqual(self.prepared['duration'], 180)
        self.assertNotIn('duration', self.pipeline.calls[0])
        self.assertEqual(self.engine.current['planned_duration'], 90)
        self.assertEqual(self.engine.current['duration'], 60.5)

    async def test_manual_genre_overrides_the_input_producers_guess(self):
        self.engine.update_preferences({'genre': 'Manual jazz style'})
        await self.complete_one()
        self.assertEqual(self.pipeline.calls[0]['genre'], 'Manual jazz style')
        self.assertEqual(self.engine.current['genre'], 'Manual jazz style')
        self.assertEqual(self.engine.current['genre_selection'], 'manual')

    async def test_placeholder_identity_rejected_before_model_or_directory(self):
        self.prepared.update(title='  SONG ', artist=' ＡＲＴＩＳＴ ')
        self.engine.start()
        await wait_for(lambda: self.engine.error is not None)
        self.assertIn('placeholder', self.engine.error)
        self.assertEqual(self.pipeline.calls, [])
        self.assertFalse((self.engine.root / 'tracks' / self.prepared['id']).exists())

    async def test_manual_metadata_validated_and_unknown_genre_not_invented(self):
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            self.engine.save_lyrics({'title': 'Song', 'artist': 'Artist', 'text': 'Provided words'})
        valid = self.engine.save_lyrics({'title': 'Song', 'artist': 'Real fixture performer', 'text': 'Other fixture words'})
        self.assertEqual(valid['original_genre'], '')
        with self.assertRaises(ValueError):
            self.engine.save_lyrics({'title': 'Valid title', 'artist': 'Fixture', 'text': 'Words', 'original_genre': []})


if __name__ == '__main__':
    unittest.main()
