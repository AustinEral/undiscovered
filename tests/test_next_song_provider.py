"""Deterministic cache-only tests with original, neutral fixture text."""

import copy
import unittest
import uuid
from dataclasses import FrozenInstanceError, is_dataclass
from unittest.mock import patch

from genre_radio.engine import GENRES, LYRICS_SOURCES, lyrics_hash, song_key
from genre_radio import identity
from genre_radio.next_song_provider import SongProvider, SongRecord


def cached(**changes):
    return {
        'title': 'Paper Lantern',
        'artist': 'Fixture Ensemble',
        'text': '[Verse]\nA paper square rests on the table.\nWe fold the corners one by one.\n',
        'source_url': 'https://example.com/fixture',
        'source_name': 'genius.com',
        'original_genre': 'Chamber Pop',
        **changes,
    }


class SongRecordTests(unittest.TestCase):
    def test_engine_reexports_exact_shared_identity_helpers(self):
        self.assertIs(song_key, identity.song_key)
        self.assertIs(lyrics_hash, identity.lyrics_hash)

    def test_identity_uses_engine_normalization_and_actual_text_hash(self):
        first = SongRecord(lyrics_id='first', **cached())
        second = SongRecord(lyrics_id='second', **cached(
            title='  ＰＡＰＥＲ\tLANTERN ', artist='  FIXTURE   ensemble ',
            text=first.text.upper().replace('\n', '  '),
        ))
        self.assertEqual(first.identity_key, ('fixture ensemble', 'paper lantern'))
        self.assertEqual(first.identity_key, second.identity_key)
        self.assertEqual(first.lyrics_hash, second.lyrics_hash)
        self.assertEqual(first.lyrics_hash, lyrics_hash(first.text))
        self.assertEqual(len(first.lyrics_hash), 64)
        with self.assertRaises(FrozenInstanceError):
            first.text = 'Changed'

    def test_record_serialization_is_not_a_generation_request(self):
        raw = ' \n[Verse]\r\n  The blue tile marks a row.\t\n\n'
        record = SongRecord(lyrics_id='cached-id', **cached(text=raw))
        self.assertEqual(record.to_dict(), {'lyrics_id': 'cached-id', **cached(text=raw), 'original_duration': None})
        self.assertTrue({'id', 'genre', 'duration', 'seed', 'lyrics'}.isdisjoint(record.to_dict()))

    def test_original_duration_accepts_unknown_and_positive_finite_reference_seconds(self):
        for duration in (None, 0.5, 1, 180.5, 7200):
            with self.subTest(duration=duration):
                record = SongRecord(lyrics_id='cached-id', **cached(original_duration=duration))
                self.assertEqual(record.original_duration, duration)
                self.assertEqual(record.to_dict()['original_duration'], duration)

    def test_required_fields_and_metadata_are_validated_before_hashing(self):
        for changes in (
            {'title': None}, {'title': 3}, {'title': ''}, {'title': ' '},
            {'artist': []}, {'text': None}, {'text': 5}, {'text': {}},
            {'text': '\t\n'}, {'text': ' Lyrics   Here '},
            {'source_url': None}, {'source_url': 'not a URL'},
            {'source_name': {}}, {'original_genre': []},
            {'title': 'Song', 'artist': 'Artist'},
            {'title': 'x' * 161}, {'text': 'x' * 14001}, {'text': 'a\x00b'},
            {'text': '\ud800'},
            *({'original_duration': value} for value in (0, -1, 7201, True, False, '180', [], {}, float('nan'), float('inf'), float('-inf'))),
        ):
            with self.subTest(changes=changes):
                with patch('genre_radio.next_song_provider.lyrics_hash', side_effect=AssertionError('hashed invalid data')):
                    with self.assertRaises(ValueError):
                        SongRecord(lyrics_id='cache-id', **cached(**changes))


class SongProviderTests(unittest.TestCase):
    def setUp(self):
        self.lyrics = {'first': cached()}
        self.tracks = {}
        self.preferences = {'genre': 'Auto', 'lyrics_id': 'auto'}
        self.provider = SongProvider(
            lyrics=self.lyrics, tracks=self.tracks,
            preferences=self.preferences, lyrics_sources=LYRICS_SOURCES,
            genre_choices=GENRES,
        )
        self.choice_patch = patch('genre_radio.next_song_provider.random.choice', side_effect=lambda songs: songs[0])
        self.choose = self.choice_patch.start()
        self.addCleanup(self.choice_patch.stop)

    def selected(self, **kwargs):
        song = self.provider.select_from_available(**kwargs)
        self.assertIsNotNone(song)
        return song

    def test_all_invalid_and_empty_caches_finish_without_selection(self):
        self.lyrics.clear()
        self.assertIsNone(self.provider.select_from_available())
        invalid = [None, [], {}, cached(title='Song', artist='Artist'),
                   cached(text='Lyrics'), cached(text='Song Lyrics'), cached(text='')]
        self.lyrics.update({str(index): value for index, value in enumerate(invalid)})
        self.assertIsNone(self.provider.select_from_available())
        self.choose.assert_not_called()

    def test_valid_record_among_invalid_entries_is_selected(self):
        valid = self.lyrics.pop('first')
        self.lyrics.update({'placeholder': cached(title='Song', artist='Artist'),
                            'empty': cached(text=''), 'missing': {}, 'first': valid})
        self.assertEqual(self.selected().lyrics_id, 'first')
        self.assertEqual([song.lyrics_id for song in self.choose.call_args.args[0]], ['first'])
        self.choose.assert_called_once()

    def test_malformed_cache_metadata_is_skipped_without_type_errors(self):
        malformed = [None, 5, [], {}, {'title': 'Incomplete'},
                     cached(title=None), cached(artist=7), cached(text=['line']),
                     cached(source_name=[]), cached(source_url={}),
                     cached(original_genre=None), cached(source_url='https://[invalid')]
        valid = self.lyrics.pop('first')
        self.lyrics.update({str(index): value for index, value in enumerate(malformed)})
        self.lyrics[None] = cached()
        self.lyrics['first'] = valid
        self.assertEqual(self.selected().lyrics_id, 'first')
        self.assertEqual(len(self.choose.call_args.args[0]), 1)

    def test_optional_cache_metadata_defaults_follow_saved_record_contract(self):
        self.lyrics['first'] = {'title': 'Grid', 'text': 'Each square has four edges.'}
        song = self.selected()
        self.assertEqual((song.artist, song.source_name, song.source_url, song.original_genre), ('', '', '', ''))

    def test_selection_and_request_preserve_raw_lyrics_and_do_not_mutate_cache(self):
        raw = '\n  [Verse]\r\nThe ruler lies beside the page.  \t\n\n'
        self.lyrics['first']['text'] = raw
        before = copy.deepcopy(self.lyrics)
        song = self.selected()
        request = self.provider.prepare_request(song)
        self.assertEqual(song.text, raw)
        self.assertEqual(request['lyrics'], raw)
        self.assertEqual(self.lyrics, before)
        self.assertEqual(self.tracks, {})

    def test_used_normalized_title_artist_excluded_even_with_different_text(self):
        self.tracks['used'] = {'title': ' ＰＡＰＥＲ  lantern ', 'artist': ' FIXTURE\tENSEMBLE ', 'lyrics_hash': lyrics_hash('Different text.')}
        self.assertIsNone(self.provider.select_from_available())

    def test_true_stored_hash_excludes_renamed_cache_record(self):
        self.tracks['used'] = {'title': 'Old label', 'artist': 'Other', 'lyrics_hash': lyrics_hash(self.lyrics['first']['text'])}
        self.assertIsNone(self.provider.select_from_available())

    def test_supplied_fake_hash_cannot_disable_saved_text_identity(self):
        original = self.lyrics['first']
        self.lyrics['renamed'] = cached(title='New label', artist='Other', text=original['text'].upper().replace('\n', '  '), lyrics_hash='fake-cache-hash')
        self.tracks['used'] = {'title': 'Old label', 'artist': 'Past name', 'lyrics_id': 'first', 'lyrics_hash': 'fake-track-hash'}
        self.assertIsNone(self.provider.select_from_available())

    def test_actual_track_text_is_checked_despite_fake_hash(self):
        self.tracks['used'] = {'title': 'Other', 'artist': 'Other', 'lyrics': self.lyrics['first']['text'], 'lyrics_hash': 'fake'}
        self.assertIsNone(self.provider.select_from_available())

    def test_duplicate_cache_names_or_text_do_not_weight_random_selection(self):
        self.lyrics['same-name'] = cached(title=' PAPER   LANTERN ', artist='fixture ensemble', text='The second card is blank.')
        self.lyrics['same-text'] = cached(title='Different label', artist='Other', lyrics_hash='fake')
        self.lyrics['distinct'] = cached(title='Numbered Tiles', text='Three tiles line the windowsill.')
        self.assertEqual(self.selected().lyrics_id, 'first')
        candidates = self.choose.call_args.args[0]
        self.assertEqual([song.lyrics_id for song in candidates], ['first', 'distinct'])
        self.assertEqual(len({song.identity_key for song in candidates}), 2)
        self.assertEqual(len({song.lyrics_hash for song in candidates}), 2)

    def test_source_filter_is_applied_before_cache_deduplication(self):
        self.lyrics['other-source'] = cached(source_name='lyrics.com')
        self.assertEqual(self.selected(preferred_source='lyrics.com').lyrics_id, 'other-source')

    def test_explicit_source_and_source_preference_never_fall_back(self):
        self.preferences['lyrics_source'] = 'lyrics.com'
        self.assertIsNone(self.provider.select_from_available())
        self.assertEqual(self.selected(preferred_source='genius.com').lyrics_id, 'first')
        self.assertIsNone(self.provider.select_from_available(preferred_source='azlyrics.com'))
        self.assertEqual(self.selected(preferred_source='').lyrics_id, 'first')
        self.lyrics['other-source'] = cached(source_name='lyrics.com', text='Lyrics Here')
        self.assertIsNone(self.provider.select_from_available())

    def test_source_matching_entries_all_used_do_not_fall_back(self):
        self.lyrics['other-source'] = cached(title='Numbered Tiles', text='Three tiles line the windowsill.', source_name='lyrics.com')
        self.tracks['used'] = {'title': self.lyrics['first']['title'], 'artist': self.lyrics['first']['artist']}
        self.assertIsNone(self.provider.select_from_available(preferred_source='genius.com'))
        self.assertEqual(self.selected(preferred_source='lyrics.com').lyrics_id, 'other-source')

    def test_manual_selection_returns_exact_unused_record_even_if_duplicate_is_earlier(self):
        self.lyrics['requested'] = cached()
        self.preferences['lyrics_id'] = 'requested'
        self.assertEqual(self.selected().lyrics_id, 'requested')
        self.assertEqual([song.lyrics_id for song in self.choose.call_args.args[0]], ['requested'])

    def test_manual_missing_invalid_used_or_source_mismatch_never_substitutes(self):
        self.lyrics['other'] = cached(title='Numbered Tiles', text='Three tiles line the windowsill.')
        self.preferences['lyrics_id'] = 'missing'
        self.assertIsNone(self.provider.select_from_available())
        self.preferences['lyrics_id'] = 'invalid'
        self.lyrics['invalid'] = cached(text='Lyrics')
        self.assertIsNone(self.provider.select_from_available())
        self.preferences['lyrics_id'] = 'first'
        self.assertIsNone(self.provider.select_from_available(preferred_source='lyrics.com'))
        self.tracks['used'] = {'title': 'Paper Lantern', 'artist': 'Fixture Ensemble'}
        self.assertIsNone(self.provider.select_from_available())

    def test_malformed_history_metadata_does_not_break_valid_selection(self):
        self.tracks.update({'null': None, 'bad': {'title': [], 'artist': 8, 'lyrics_hash': [], 'lyrics_id': {}, 'lyrics': None},
                            'bad-unicode': {'lyrics': '\ud800'}})
        self.assertEqual(self.selected().lyrics_id, 'first')

    def test_get_sources_uses_actual_platform_dataclasses(self):
        self.assertTrue(LYRICS_SOURCES)
        self.assertTrue(all(is_dataclass(source) for source in LYRICS_SOURCES))
        self.assertEqual(self.provider.get_sources(), [source.name for source in LYRICS_SOURCES])
        self.assertIn('genius.com', self.provider.get_sources())

    def test_prepare_request_has_fresh_job_uuid_and_valid_seed_each_call(self):
        song = self.selected()
        ids = [uuid.UUID(int=1, version=4), uuid.UUID(int=2, version=4)]
        with patch('genre_radio.next_song_provider.uuid.uuid4', side_effect=ids) as fresh_id:
            with patch('genre_radio.next_song_provider.random.randint', side_effect=[0, 2**31 - 1]) as seed:
                first = self.provider.prepare_request(song)
                second = self.provider.prepare_request(song)
        self.assertEqual([first['id'], second['id']], [str(value) for value in ids])
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['id'], song.lyrics_id)
        self.assertEqual(uuid.UUID(first['id']).version, 4)
        self.assertEqual(fresh_id.call_count, 2)
        self.assertEqual(seed.call_count, 2)
        seed.assert_called_with(0, 2**31 - 1)
        self.assertEqual((first['seed'], second['seed']), (0, 2**31 - 1))
        self.assertEqual(first['lyrics_id'], song.lyrics_id)
        self.assertEqual(first['original_genre'], song.original_genre)
        self.assertEqual(first['genre'], 'Auto')
        self.assertEqual(first['genre_choices'], GENRES)
        self.assertIsNot(first['genre_choices'], GENRES)

    def test_prepare_request_preserves_selected_or_explicit_genre_without_target_duration(self):
        self.preferences.update(genre='  Nu-JAZZ / Soul  ', duration=240)
        song = self.selected()
        request = self.provider.prepare_request(song)
        self.assertEqual(request['genre'], '  Nu-JAZZ / Soul  ')
        self.assertNotIn('duration', request)
        self.assertNotIn('original_duration', request)
        self.assertEqual(request['original_genre'], 'Chamber Pop')
        self.assertNotIn('genre_choices', request)
        overridden = self.provider.prepare_request(song, genre='Disco')
        self.assertEqual(overridden['genre'], 'Disco')
        self.assertNotIn('duration', overridden)
        self.assertEqual(self.preferences['genre'], '  Nu-JAZZ / Soul  ')

    def test_original_duration_survives_cached_record_and_request_without_becoming_target(self):
        self.lyrics['first']['original_duration'] = 420.5
        song = self.selected()
        self.assertEqual(song.original_duration, 420.5)
        self.assertEqual(song.to_dict()['original_duration'], 420.5)
        request = self.provider.prepare_request(song)
        self.assertEqual(request['original_duration'], 420.5)
        self.assertEqual(request['lyrics'], self.lyrics['first']['text'])
        self.assertNotIn('duration', request)

    def test_invalid_cached_original_duration_is_rejected_before_selection(self):
        self.lyrics['first']['original_duration'] = float('nan')
        self.assertIsNone(self.provider.select_from_available())
        self.choose.assert_not_called()

    def test_genre_catalog_is_validated_and_copied(self):
        for choices in (None, [], 'Jazz', [''], ['Auto'], ['Jazz', 3]):
            with self.subTest(choices=choices), self.assertRaises(ValueError):
                SongProvider(lyrics={}, tracks={}, preferences={}, lyrics_sources=[], genre_choices=choices)
        choices = ['Jazz', 'Disco']
        provider = SongProvider(lyrics={}, tracks={}, preferences={}, lyrics_sources=[], genre_choices=choices)
        choices.clear()
        request = provider.prepare_request(self.selected())
        self.assertEqual(request['genre_choices'], ['Jazz', 'Disco'])
        request['genre_choices'].clear()
        self.assertEqual(provider.genre_choices, ['Jazz', 'Disco'])

    def test_prepare_request_rejects_duration_argument_and_invalid_genre(self):
        song = self.selected()
        with self.assertRaises(TypeError):
            self.provider.prepare_request(song, duration=180)
        for genre in ('', ' ', 3, [], 'x' * 161):
            with self.subTest(genre=genre), self.assertRaises(ValueError):
                self.provider.prepare_request(song, genre=genre)
        self.preferences['duration'] = None
        self.assertNotIn('duration', self.provider.prepare_request(song))
        self.preferences['genre'] = None
        with self.assertRaises(ValueError):
            self.provider.prepare_request(song)


if __name__ == '__main__':
    unittest.main()
