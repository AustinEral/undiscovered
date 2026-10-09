import asyncio
import json
import unittest
from unittest.mock import patch

import aiohttp

from genre_radio import genius
from genre_radio.lyrics_sources import GeniusScraper, ScrapingError
from genre_radio.live_song_provider import GENIUS_CHART_URL, LiveSongProvider, parse_chart, parse_lyrics
from test_live_song_provider import FakeSession, chart_page, lyrics_page

URL = 'https://genius.com/example-performer-paper-lanterns-lyrics'
LINES = [f'Original fixture line {number}, with words and punctuation.' for number in range(1, 7)]


def page(body=None):
    return '<meta content="Example Performer – Paper Lanterns Lyrics | Genius Lyrics" property="og:title">' + (body or '<div data-lyrics-container="true">' + '<br>'.join(LINES) + '</div>')


class ParserTests(unittest.TestCase):
    def test_both_callers_join_all_containers_in_order(self):
        html = page('<div class="Lyrics__Container" data-lyrics-container=\'true\'><span>' + LINES[0] + '</span><br/>' + '<br>'.join(LINES[1:]) + '</div><div data-lyrics-container="true">[Outro]<br>Last word.</div>')
        expected = '\n'.join(LINES) + '\n\n[Outro]\nLast word.'
        self.assertEqual(parse_lyrics(html), expected)
        self.assertEqual(GeniusScraper()._extract_lyrics(html), expected)

    def test_unlabelled_opening_and_sectionless_song_are_not_removed(self):
        html = page('<div data-lyrics-container="true">Opening words before any label.<br>[Chorus]<br>' + '<br>'.join(LINES) + '</div>')
        self.assertTrue(parse_lyrics(html).startswith('Opening words before any label.\n[Chorus]'))
        self.assertEqual(parse_lyrics(page()), '\n'.join(LINES))

    def test_structural_header_and_controls_are_excluded_not_lyric_words(self):
        html = page('<div class="LyricsHeader__Container" data-lyrics-container="true">Contributors title junk</div>'
                    '<div data-lyrics-container="true"><div class="LyricsHeader-x">Read More</div>'
                    '<span data-exclude-from-selection="true"><b>You might also like</b></span>'
                    '<script>do_not_include()</script><style>bad css</style>'
                    + '<br>'.join(LINES) + '<br>These Lyrics remain.<br>The final word is Embed</div>')
        text = parse_lyrics(html)
        for junk in ['Contributors', 'Read More', 'You might', 'do_not_include', 'bad css']:
            self.assertNotIn(junk, text)
        self.assertIn('These Lyrics remain.', text)
        self.assertTrue(text.endswith('The final word is Embed'))

    def test_nested_inline_tags_entities_blocks_and_repeated_chorus(self):
        html = page('<div data-lyrics-container="true"><p>[Verse]</p><p>Stay <a><b>right</b> here</a> &amp; listen.</p>'
                    + ''.join('<div>' + line + '</div>' for line in LINES) + '</div>'
                    '<div data-lyrics-container="true">[Chorus]<br>Again &#39;n again.</div>'
                    '<div data-lyrics-container="true">[Chorus]<br>Again &#39;n again.</div>')
        text = parse_lyrics(html)
        self.assertIn('Stay right here & listen.', text)
        self.assertEqual(text.count("Again 'n again."), 2)
        self.assertIn(LINES[0] + '\n' + LINES[1], text)

    def test_no_container_empty_header_and_truncated_html_fail(self):
        for html in ['<div>Access denied</div>', '<div data-lyrics-container="true"></div>', '<div data-lyrics-container="true">' + '<br>'.join(LINES), '<div data-lyrics-container="true" class="LyricsHeader">header</div>']:
            with self.subTest(html=html[:60]), self.assertRaises(genius.GeniusError):
                parse_lyrics(html)

    def test_oversize_lyrics_are_rejected_not_truncated(self):
        with self.assertRaisesRegex(genius.GeniusError, 'not truncated'):
            parse_lyrics(page('<div data-lyrics-container="true">' + '<br>'.join(['x' * 2500] * 6) + '</div>'))

    def test_metadata_artist_title_order_and_entities(self):
        metadata = GeniusScraper()._extract_metadata(page(), URL)
        self.assertEqual((metadata.artist, metadata.title), ('Example Performer', 'Paper Lanterns'))
        html = '<meta property="og:title" content="Example &amp; Friends — Paper &amp; Lanterns Lyrics">'
        self.assertEqual(genius.page_metadata(html)['title'], 'Paper & Lanterns')
        self.assertEqual(genius.page_metadata(html)['artist'], 'Example & Friends')

    def test_jsonld_metadata_wins_and_missing_metadata_does_not_invent_identity(self):
        html = '<script type="application/ld+json">' + json.dumps({'@graph': [{'@type': 'MusicRecording', 'name': 'Explicit Title', 'byArtist': [{'name': 'First Performer'}, {'name': 'Second Performer'}], 'genre': 'Jazz'}]}) + '</script>' + page()
        metadata = genius.page_metadata(html)
        self.assertEqual(metadata, {'title': 'Explicit Title', 'artist': 'First Performer, Second Performer', 'genre': 'Jazz'})
        with self.assertRaises(genius.GeniusError):
            genius.page_metadata('<title>Verification needed</title>')

    def test_genre_metadata_accepts_plain_and_escaped_forms(self):
        self.assertEqual(genius.original_genre('<script>{"primaryTag":"rap"}</script>'), 'rap')
        self.assertEqual(genius.original_genre('<script>{\\"primaryTag\\":\\"pop\\"}</script>'), 'pop')
        self.assertEqual(genius.original_genre('<meta name="description" content="probably jazz">'), '')

    def test_urls_are_exact_song_authorities_and_canonical_attribution(self):
        scraper = GeniusScraper()
        self.assertEqual(scraper.normalize_url(URL + '?tracking=yes#note'), URL)
        self.assertEqual(scraper.normalize_url('genius.com/example-lyrics'), 'https://genius.com/example-lyrics')
        for url in ['https://example.com/?ref=genius.com', 'https://genius.com.example.com/example-lyrics', 'https://genius.com//example.com/example-lyrics', 'https://user@genius.com/example-lyrics', 'https://genius.com:1234/example-lyrics', 'http://genius.com/example-lyrics', 'https://genius.com/artists/example', 'https://genius.com/example-lyrics\n']:
            self.assertFalse(scraper.supports_url(url), url)
            with self.assertRaises(ValueError):
                scraper.normalize_url(url)

    def test_chart_shapes_skip_bad_siblings_and_keep_recent_complete_entries(self):
        for songs in [None, {}, 'not a list']:
            with self.assertRaisesRegex(ValueError, 'malformed'):
                parse_chart(json.dumps({'response': {'songs': songs}}))
        payload = json.loads(chart_page({'title': 'Actual Title', 'artist': 'Performer', 'url': URL}))
        valid = payload['response']['songs'][0]
        valid['stats'] = None
        payload['response']['songs'] = [None, {}, {'stats': None}, valid, {**valid, 'release_date_components': {'year': 2099}}, {**valid, 'lyrics_state': 'unreleased'}]
        self.assertEqual(len(parse_chart(json.dumps(payload), current_year=2026)), 1)


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_scrape_preserves_source_and_all_text(self):
        session = FakeSession({URL: (200, page())})
        scraped = await GeniusScraper().scrape(session, URL + '?tracking=yes')
        self.assertEqual(scraped.metadata.source_url, URL)
        self.assertEqual(scraped.text, '\n'.join(LINES))
        self.assertEqual(session.requests, [URL])

    async def test_search_uses_real_metadata_not_url_slugs(self):
        song = {'title': 'Paper Lanterns', 'artist_names': 'Example Performer', 'url': URL, 'lyrics_state': 'complete'}
        payload = json.dumps({'response': {'sections': [{'type': 'top_hit', 'hits': [{'index': 'artist', 'result': {'name': 'ignored'}}, {'index': 'song', 'result': song}]}, {'type': 'song', 'hits': [{'index': 'song', 'result': song}, {'index': 'song', 'result': {**song, 'url': 'https://example.com/other-lyrics'}}]}]}})
        search_url = 'https://genius.com/api/search/multi?q=Paper+Lanterns&per_page=5'
        session = FakeSession({search_url: (200, payload)})
        result = await GeniusScraper().search(session, 'Paper Lanterns')
        self.assertEqual([(item.title, item.artist, item.source_url) for item in result], [('Paper Lanterns', 'Example Performer', URL)])

    async def test_redirects_are_checked_before_following(self):
        external = FakeSession({URL: (302, '', {'Location': 'https://example.com/fixture-lyrics'})})
        with self.assertRaises(genius.GeniusError):
            await genius.fetch_text(external, URL, song_page=True)
        self.assertEqual(external.requests, [URL])
        target = URL.replace('https://genius.com', 'https://www.genius.com')
        same_site = FakeSession({URL: (301, '', {'Location': target}), target: (200, page())})
        self.assertEqual(await genius.fetch_text(same_site, URL, song_page=True), page())
        self.assertEqual(same_site.requests, [URL, target])

    async def test_errors_are_actionable_and_do_not_bypass_or_retry(self):
        for status in [401, 403, 429, 500]:
            session = FakeSession({URL: (status, 'blocked')})
            with self.subTest(status=status), self.assertRaisesRegex(ScrapingError, str(status)) as caught:
                await GeniusScraper().scrape(session, URL)
            self.assertEqual(caught.exception.temporary, status in [429, 500])
            self.assertIsInstance(caught.exception, ValueError)
            self.assertEqual(session.requests, [URL])
        for error in [TimeoutError('timeout'), aiohttp.ClientConnectionError('offline')]:
            with self.assertRaises(ScrapingError) as caught:
                await GeniusScraper().scrape(FakeSession({URL: error}), URL)
            self.assertTrue(caught.exception.temporary)
        with self.assertRaises(asyncio.CancelledError):
            await GeniusScraper().scrape(FakeSession({URL: asyncio.CancelledError()}), URL)

    async def test_fetch_is_bounded(self):
        with patch('genre_radio.genius.MAX_RESPONSE_BYTES', 10):
            with self.assertRaisesRegex(genius.GeniusError, 'size limit'):
                await genius.fetch_text(FakeSession({URL: (200, 'x' * 11)}), URL, song_page=True)

    async def test_live_provider_stops_on_source_denial(self):
        second = 'https://genius.com/another-performer-another-song-lyrics'
        chart = chart_page({'title': 'First', 'artist': 'Performer', 'url': URL}, {'title': 'Second', 'artist': 'Another', 'url': second})
        for status in [403, 429, 503]:
            session = FakeSession({GENIUS_CHART_URL: (200, chart), URL: (status, ''), second: (200, lyrics_page())})
            provider = LiveSongProvider(lyrics={}, tracks={}, preferences={'genre': 'Auto'}, genre_choices=['Jazz'])
            with patch('genre_radio.live_song_provider.aiohttp.ClientSession', return_value=session):
                with self.assertRaisesRegex(ValueError, str(status)):
                    await provider.acquire()
            self.assertEqual(session.requests, [GENIUS_CHART_URL, URL])

    async def test_live_provider_skips_a_missing_song_but_keeps_chart_attribution(self):
        second = 'https://genius.com/another-performer-another-song-lyrics'
        chart = chart_page({'title': 'First', 'artist': 'Performer', 'url': URL}, {'title': 'Second', 'artist': 'Another', 'url': second})
        session = FakeSession({GENIUS_CHART_URL: (200, chart), URL: (404, ''), second: (200, lyrics_page())})
        provider = LiveSongProvider(lyrics={}, tracks={}, preferences={'genre': 'Auto'}, genre_choices=['Jazz'])
        with patch('genre_radio.live_song_provider.aiohttp.ClientSession', return_value=session):
            song = await provider.acquire()
        self.assertEqual((song.title, song.artist, song.source_url, song.source_name), ('Second', 'Another', second, 'Genius'))


if __name__ == '__main__':
    unittest.main()
