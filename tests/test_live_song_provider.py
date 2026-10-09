import json
import unittest
import uuid
from unittest.mock import patch

from genre_radio.engine import GENRES
from genre_radio.live_song_provider import GENIUS_CHART_URL, LiveSong, LiveSongProvider, parse_chart, parse_lyrics


def chart_page(*songs):
    records = []
    for song in songs:
        records.append({
            'title': song['title'],
            'artist_names': song['artist'],
            'url': song['url'],
            'release_date_components': {'year': song.get('year', 2026)},
            'stats': {'pageviews': song.get('views', 1000)},
            'instrumental': song.get('instrumental', False),
        })
    return json.dumps({'response': {'songs': records}}, ensure_ascii=False)


def lyrics_page(label='First Song', genre='pop'):
    lines = '\n'.join(f'Line {index} for {label}' for index in range(1, 9))
    return (
        '<div data-lyrics-container="true">ContributorsTranslations'
        f'{label} LyricsRead More&nbsp;[Verse 1]<br/>{lines.replace(chr(10), "<br/>")}</div>'
        '<div data-lyrics-container="true">[Chorus]<br/>A distinct chorus line<br/>Another chorus line</div>'
        f'<script>{{\\"primaryTag\\":\\"{genre}\\"}}</script>'
    )


class FakeContent:
    def __init__(self, data):
        self.data = data

    async def iter_chunked(self, size):
        for offset in range(0, len(self.data), size):
            yield self.data[offset:offset + size]


class FakeResponse:
    def __init__(self, status, text, headers=None):
        self.status = status
        self._text = text
        self.headers = headers or {}
        self.charset = 'utf-8'
        self.content = FakeContent(text.encode('utf-8'))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self, responses, **kwargs):
        self.responses = responses
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, url, **kwargs):
        self.requests.append(url)
        response = self.responses[url]
        if isinstance(response, BaseException):
            raise response
        return FakeResponse(*response)


class LiveSongTests(unittest.TestCase):
    def test_rejects_missing_placeholder_and_malformed_records(self):
        base = {
            'lyrics_id': 'one',
            'title': 'Actual title',
            'artist': 'Actual artist',
            'text': '[Verse]\nOne actual line\nTwo actual lines',
            'source_url': 'https://genius.com/actual-title-lyrics',
            'source_name': 'Genius',
        }
        for changes in (
            {'title': ''}, {'artist': ''}, {'text': ''},
            {'title': 'Song', 'artist': 'Artist'}, {'text': 'Lyrics Here'},
            {'source_url': ''}, {'source_url': 'not-a-url'}, {'source_name': ''},
            *({'original_duration': value} for value in (0, -1, 7201, True, False, '180', [], {}, float('nan'), float('inf'), float('-inf'))),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                LiveSong(**{**base, **changes})

    def test_saved_record_has_required_fields_and_preserves_text(self):
        raw = '[Verse]\n  Keep spacing.\n'
        song = LiveSong(
            lyrics_id='one', title='Actual title', artist='Actual artist', text=raw,
            source_url='https://genius.com/actual-title-lyrics', source_name='Genius', original_genre='pop',
        )
        self.assertEqual(song.saved_record(), {
            'id': 'one', 'title': 'Actual title', 'artist': 'Actual artist', 'text': raw,
            'source_url': 'https://genius.com/actual-title-lyrics', 'source_name': 'Genius', 'original_genre': 'pop',
        })

    def test_original_duration_accepts_unknown_and_persists_finite_reference_seconds(self):
        for duration in (None, 0.5, 1, 180.5, 7200):
            with self.subTest(duration=duration):
                song = LiveSong(
                    lyrics_id='one', title='Actual title', artist='Actual artist', text='[Verse]\n  Keep spacing.\n',
                    source_url='https://genius.com/actual-title-lyrics', source_name='Genius', original_duration=duration,
                )
                self.assertEqual(song.original_duration, duration)
                self.assertEqual(song.saved_record().get('original_duration'), duration)
                self.assertNotIn('duration', song.saved_record())


class GeniusParsingTests(unittest.TestCase):
    def test_chart_requires_recent_actual_noninstrumental_songs(self):
        songs = parse_chart(chart_page(
            {'title': 'Recent', 'artist': 'Artist A', 'url': 'https://genius.com/a-lyrics', 'year': 2026},
            {'title': 'Old', 'artist': 'Artist B', 'url': 'https://genius.com/b-lyrics', 'year': 2020},
            {'title': 'Instrumental', 'artist': 'Artist C', 'url': 'https://genius.com/c-lyrics', 'year': 2026, 'instrumental': True},
        ), current_year=2026)
        self.assertEqual([(song.title, song.artist) for song in songs], [('Recent', 'Artist A')])

    def test_lyrics_parser_removes_page_header_and_keeps_sections(self):
        text = parse_lyrics(lyrics_page())
        self.assertTrue(text.startswith('[Verse 1]\nLine 1'))
        self.assertIn('[Chorus]\nA distinct chorus line', text)
        self.assertNotIn('Contributors', text)
        self.assertNotIn('Read More', text)


class LiveSongProviderTests(unittest.IsolatedAsyncioTestCase):
    def make_provider(self, lyrics=None, tracks=None):
        return LiveSongProvider(
            lyrics={} if lyrics is None else lyrics,
            tracks={} if tracks is None else tracks,
            preferences={'genre': 'Auto'},
            genre_choices=list(GENRES),
        )

    async def test_acquire_uses_chart_metadata_lyrics_attribution_and_genre(self):
        url = 'https://genius.com/artist-first-song-lyrics'
        responses = {
            GENIUS_CHART_URL: (200, chart_page({'title': 'First Song', 'artist': 'Artist', 'url': url})),
            url: (200, lyrics_page(genre='rap')),
        }
        session = FakeSession(responses)
        with patch('genre_radio.live_song_provider.aiohttp.ClientSession', return_value=session):
            song = await self.make_provider().acquire()
        self.assertEqual((song.title, song.artist), ('First Song', 'Artist'))
        self.assertEqual((song.source_url, song.source_name, song.original_genre), (url, 'Genius', 'rap'))
        self.assertGreater(len(song.text), 100)
        self.assertEqual(session.requests, [GENIUS_CHART_URL, url])

    async def test_acquire_skips_generated_duplicate_before_fetching_lyrics(self):
        first = 'https://genius.com/artist-first-lyrics'
        second = 'https://genius.com/artist-second-lyrics'
        responses = {
            GENIUS_CHART_URL: (200, chart_page(
                {'title': 'First', 'artist': 'Artist', 'url': first},
                {'title': 'Second', 'artist': 'Artist', 'url': second},
            )),
            second: (200, lyrics_page('Second')),
        }
        session = FakeSession(responses)
        tracks = {'used': {'title': 'First', 'artist': 'Artist', 'lyrics_hash': 'used'}}
        with patch('genre_radio.live_song_provider.aiohttp.ClientSession', return_value=session):
            song = await self.make_provider(tracks=tracks).acquire()
        self.assertEqual(song.title, 'Second')
        self.assertEqual(session.requests, [GENIUS_CHART_URL, second])

    def test_prepare_request_maps_saved_text_to_lyrics(self):
        song = LiveSong(
            lyrics_id='one', title='Actual', artist='Artist', text='\n[Verse]\r\n  Words for the request.\t\n',
            source_url='https://genius.com/actual-lyrics', source_name='Genius', original_genre='pop',
        )
        with patch('genre_radio.live_song_provider.uuid.uuid4', return_value=uuid.UUID(int=1)):
            request = self.make_provider().prepare_request(song)
        self.assertEqual(request['lyrics'], song.text)
        self.assertEqual(request['original_genre'], 'pop')
        self.assertEqual(request['genre'], 'Auto')
        self.assertEqual(request['genre_choices'], GENRES)
        self.assertNotIn('duration', request)
        self.assertNotIn('original_duration', request)
        self.assertEqual(request['id'], str(uuid.UUID(int=1)))
        self.assertGreaterEqual(request['seed'], 0)
        self.assertLessEqual(request['seed'], 2**31 - 1)

    def test_prepare_request_preserves_genre_preference_and_reference_not_target(self):
        song = LiveSong(
            lyrics_id='one', title='Actual', artist='Artist', text='[Verse]\n  Words for the request.\n',
            source_url='https://genius.com/actual-lyrics', source_name='Genius', original_duration=420.5,
        )
        provider = self.make_provider()
        provider.preferences.update(genre='Jazz', duration=180)
        request = provider.prepare_request(song)
        self.assertEqual(request['genre'], 'Jazz')
        self.assertNotIn('genre_choices', request)
        self.assertEqual(request['original_duration'], 420.5)
        self.assertEqual(request['lyrics'], song.text)
        self.assertNotIn('duration', request)
        self.assertEqual(provider.prepare_request(song, genre='Disco')['genre'], 'Disco')
        with self.assertRaises(TypeError):
            provider.prepare_request(song, duration=180)

    async def test_saved_chart_record_keeps_original_duration_without_lyrics_fetch(self):
        url = 'https://genius.com/artist-first-song-lyrics'
        saved = LiveSong(
            lyrics_id='one', title='First Song', artist='Artist', text='[Verse]\n  Keep cached spacing.\n',
            source_url=url, source_name='Genius', original_duration=420.5,
        ).saved_record()
        session = FakeSession({
            GENIUS_CHART_URL: (200, chart_page({'title': 'First Song', 'artist': 'Artist', 'url': url})),
        })
        provider = self.make_provider(lyrics={'one': saved})
        with patch('genre_radio.live_song_provider.aiohttp.ClientSession', return_value=session):
            song = await provider.acquire()
        self.assertEqual(song.original_duration, 420.5)
        self.assertEqual(song.text, saved['text'])
        self.assertEqual(provider.prepare_request(song)['original_duration'], 420.5)
        self.assertEqual(session.requests, [GENIUS_CHART_URL])


if __name__ == '__main__':
    unittest.main()
