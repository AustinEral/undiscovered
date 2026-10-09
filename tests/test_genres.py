import json
import unittest
from dataclasses import FrozenInstanceError
from unicodedata import normalize

from genre_radio.genres import GENRES, _PROFILES, genre_family, genre_groups, genre_profile


class GenreCatalogTests(unittest.TestCase):
    def test_representative_catalog_has_unique_normalized_names(self):
        self.assertIsInstance(GENRES, list)
        self.assertGreaterEqual(len(GENRES), 60)
        self.assertLessEqual(len(GENRES), 80)
        keys = [' '.join(normalize('NFKC', name).casefold().split()) for name in GENRES]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(keys))
        self.assertTrue(all('×' not in name for name in GENRES))
        self.assertNotIn('Auto', GENRES)

    def test_all_profiles_have_the_exact_nonempty_string_schema(self):
        fields = {'name', 'family', 'tempo', 'rhythm', 'instruments', 'vocals', 'production', 'avoid'}
        for name in GENRES:
            with self.subTest(genre=name):
                profile = genre_profile(name)
                self.assertEqual(set(profile), fields)
                self.assertEqual(profile['name'], name)
                for field, value in profile.items():
                    with self.subTest(field=field):
                        self.assertIsInstance(value, str)
                        self.assertTrue(value.strip())
                        self.assertLessEqual(len(value), 160)
                self.assertEqual(json.loads(json.dumps(profile)), profile)

    def test_real_genre_coverage_and_family_identity(self):
        expected = {
            'Bluegrass': 'Country',
            'Salsa': 'Latin',
            'Bossa nova': 'Latin',
            'Roots reggae': 'Caribbean',
            'UK garage': 'Electronic',
            'Drum and bass': 'Electronic',
            'Detroit techno': 'Electronic',
            'Heavy metal': 'Metal',
            'Afrobeat': 'African',
            'Afrobeats': 'African',
            'Hindustani classical': 'Asian',
            'Gamelan': 'Asian',
        }
        for name, family in expected.items():
            with self.subTest(genre=name):
                self.assertIn(name, GENRES)
                self.assertEqual(genre_profile(name)['family'], family)
        self.assertNotEqual(genre_profile('Afrobeat')['rhythm'], genre_profile('Afrobeats')['rhythm'])

    def test_lookup_normalizes_unicode_case_and_whitespace(self):
        variants = {
            '  ＵＫ\u3000ＧＡＲＡＧＥ\t': 'UK garage',
            '\nDrUm\t AnD\u00a0  bAsS  ': 'Drum and bass',
            '　ＢＯＳＳＡ ＮＯＶＡ　': 'Bossa nova',
            'heavy\nmetal': 'Heavy metal',
        }
        for supplied, canonical in variants.items():
            with self.subTest(supplied=supplied):
                self.assertEqual(genre_profile(supplied), genre_profile(canonical))

    def test_unknown_custom_styles_have_no_profile(self):
        for name in ('My custom acoustic style', 'Jazz × bluegrass', 'Auto', '', ' \t\n', 'garage'):
            with self.subTest(genre=name):
                self.assertIsNone(genre_profile(name))

    def test_family_resolves_catalog_styles_and_exact_family_labels(self):
        for name in GENRES:
            with self.subTest(genre=name):
                self.assertEqual(genre_family(name), genre_profile(name)['family'])
        for group in genre_groups():
            with self.subTest(family=group['label']):
                self.assertEqual(genre_family('  ' + group['label'].upper() + '\t'), group['label'])
        self.assertEqual(genre_family('　ＰＯＰ　'), 'Pop')
        self.assertEqual(genre_family('  ＵＫ\u3000ＧＡＲＡＧＥ '), 'Electronic')
        self.assertIsNone(genre_profile('Pop'))

    def test_family_resolves_only_narrow_standard_aliases(self):
        aliases = {
            'rap': 'Hip-hop', 'hiphop': 'Hip-hop', 'hip hop': 'Hip-hop',
            'r-b': 'R&B', 'rhythm and blues': 'R&B',
            ' ＲＡＰ ': 'Hip-hop', ' RHYTHM\tAND\nBLUES ': 'R&B',
        }
        for name, family in aliases.items():
            with self.subTest(alias=name):
                self.assertEqual(genre_family(name), family)
                self.assertIsNone(genre_profile(name))
        for name in (None, '', ' \t\n', 'Auto', 'My custom acoustic style',
                     'Pop rock', 'Jazz × bluegrass', 'hip', 'garage', 'rhythm & blues', 'rb'):
            with self.subTest(unknown=name):
                self.assertIsNone(genre_family(name))

    def test_groups_cover_each_genre_once_and_all_families(self):
        expected_families = {
            'Pop', 'Rock', 'Punk', 'Metal', 'Hip-hop', 'R&B', 'Soul', 'Funk',
            'Jazz', 'Blues', 'Country', 'Folk', 'Electronic', 'Latin',
            'Caribbean', 'African', 'Asian', 'Classical', 'Ambient',
        }
        groups = genre_groups()
        self.assertIsInstance(groups, list)
        self.assertEqual(len(groups), len(expected_families))
        self.assertEqual({group['label'] for group in groups}, expected_families)
        self.assertEqual([name for group in groups for name in group['genres']], GENRES)
        self.assertEqual(json.loads(json.dumps(groups)), groups)
        for group in groups:
            with self.subTest(family=group['label']):
                self.assertEqual(set(group), {'label', 'genres'})
                self.assertIsInstance(group['label'], str)
                self.assertIsInstance(group['genres'], list)
                self.assertTrue(group['genres'])
                for name in group['genres']:
                    self.assertEqual(genre_profile(name)['family'], group['label'])

    def test_profiles_are_immutable_and_public_results_are_detached(self):
        with self.assertRaises(FrozenInstanceError):
            _PROFILES[0].name = 'Changed'
        baseline = genre_profile('Bluegrass')
        changed = genre_profile('Bluegrass')
        changed['instruments'] = 'Changed'
        changed['extra'] = 'Changed'
        self.assertEqual(genre_profile('Bluegrass'), baseline)
        groups = genre_groups()
        baseline_groups = genre_groups()
        groups[0]['label'] = 'Changed'
        groups[0]['genres'].clear()
        groups.append({'label': 'Changed', 'genres': ['Changed']})
        self.assertEqual(genre_groups(), baseline_groups)
        self.assertEqual(GENRES, [profile.name for profile in _PROFILES])

    def test_tempo_is_guidance_and_supports_alternative_counts(self):
        for name in GENRES:
            with self.subTest(genre=name):
                tempo = genre_profile(name)['tempo']
                self.assertTrue(tempo.startswith(('Often ', 'Tempo follows ', 'Pace follows ', 'Free-time ')))
        for name in ('Trap', 'Bluegrass', 'Drum and bass', 'Dubstep', 'Salsa', 'Roots reggae'):
            with self.subTest(genre=name):
                self.assertRegex(genre_profile(name)['tempo'], r'half-time|double-time')
        self.assertIn('unmetered', genre_profile('Ambient')['tempo'])


if __name__ == '__main__':
    unittest.main()
