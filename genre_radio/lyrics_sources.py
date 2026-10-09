"""Lyric scraping from popular online sources.

Scrapes real, recent, popular songs from major lyrics websites.
Each scraper implements a common interface and returns structured lyric data.
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import aiohttp

from genre_radio import genius
from genre_radio.live_song_provider import LiveSong, LiveSongProvider

class AutoLyricsProvider(Protocol):
    source_id: str

    async def acquire(self) -> LiveSong:
        ...

    def prepare_request(self, song: LiveSong, *, genre: str) -> dict:
        ...

    def accepts_cached(self, song: dict) -> bool:
        ...


AUTO_PROVIDERS = {'genius': LiveSongProvider}


def get_auto_provider(name='genius'):
    if not isinstance(name, str) or name not in AUTO_PROVIDERS:
        raise ValueError(f'Unknown lyrics provider: {name!r}. Available: {", ".join(AUTO_PROVIDERS)}')
    return AUTO_PROVIDERS[name]


LYRICS_COM = "https://www.lyrics.com"
GENIUS_COM = "https://genius.com"
AZLYRICS_COM = "https://www.azlyrics.com"


@dataclass(frozen=True)
class LyricSource:
    """A lyric source provider."""
    name: str
    label: str
    description: str


@dataclass(frozen=True)
class SongMetadata:
    """Metadata for a song from a lyrics source."""
    title: str
    artist: str
    source_url: str
    genre: str | None = None
    description: str | None = None
    views: int | None = None
    uploader: str | None = None
    uploaded_at: datetime | None = None


@dataclass(frozen=True)
class ScrapedLyrics:
    """Complete scraped lyric data."""
    metadata: SongMetadata
    text: str


class ScrapingError(ValueError):
    """A lyric scraping failure."""
    def __init__(self, message: str, *, temporary: bool = False):
        super().__init__(message)
        self.temporary = temporary


@runtime_checkable
class LyricScraper(Protocol):
    """Protocol for lyric scrapers."""

    async def search(self, session: aiohttp.ClientSession, query: str) -> list[SongMetadata]:
        """Search for songs matching the query."""
        ...

    async def scrape(self, session: aiohttp.ClientSession, url: str) -> ScrapedLyrics:
        """Scrape lyrics from a song page URL."""
        ...

    def supports_url(self, url: str) -> bool:
        """Return True if this scraper handles the given URL."""
        ...


class LyricsComScraper:
    """Scraper for lyrics.com."""

    BASE = LYRICS_COM

    def supports_url(self, url: str) -> bool:
        return "lyrics.com" in url

    def normalize_url(self, url: str) -> str:
        """Normalize a lyrics.com URL to its canonical form."""
        parsed = urlparse(url)
        if parsed.path.startswith("/lyric/"):
            path = parsed.path
        elif parsed.path.startswith("/"):
            path = parsed.path
        else:
            path = parsed.path
        return urljoin(self.BASE, path)

    async def search(self, session: aiohttp.ClientSession, query: str) -> list[SongMetadata]:
        """Search lyrics.com for songs."""
        params = {"q": query}
        url = f"{self.BASE}/search.php?{urlencode(params)}"
        songs: list[SongMetadata] = []

        async with session.get(url) as response:
            if response.status != 200:
                raise ScrapingError(f"Lyrics.com search failed: HTTP {response.status}")
            html = await response.text()

        # Parse search results
        # Lyrics.com search returns links like /lyric/artist/title
        pattern = r'<a\s+href="(/lyric/\d+/[^\"]+)"[^>]*>([^<]+)</a>'
        for match in re.finditer(pattern, html):
            href = match.group(1)
            title_artist = match.group(2)

            # Extract title and artist from format like "Title - Artist"
            if " - " in title_artist:
                parts = title_artist.split(" - ", 1)
                title, artist = parts[0].strip(), parts[1].strip()
            else:
                title = title_artist.strip()
                artist = ""

            songs.append(SongMetadata(
                title=title,
                artist=artist,
                source_url=urljoin(self.BASE, href),
                genre=None,
            ))

        return songs[:12]  # Return top 12 results

    async def scrape(self, session: aiohttp.ClientSession, url: str) -> ScrapedLyrics:
        """Scrape lyrics from a lyrics.com song page."""
        normalized = self.normalize_url(url)

        async with session.get(normalized) as response:
            if response.status != 200:
                raise ScrapingError(f"Lyrics.com page fetch failed: HTTP {response.status}")
            html = await response.text()

        # Extract metadata from page
        metadata = self._extract_metadata(html, url)

        # Extract lyrics text
        lyrics_text = self._extract_lyrics(html)

        return ScrapedLyrics(metadata=metadata, text=lyrics_text)

    def _extract_metadata(self, html: str, url: str) -> SongMetadata:
        """Extract song metadata from lyrics.com page HTML."""
        title = ""
        artist = ""
        genre = None
        views = None

        # Try to extract from title tag: "Song Title Lyrics - Artist | Lyrics.com"
        title_match = re.search(r"<title>([^<]+)</title>", html)
        if title_match:
            title_text = title_match.group(1)
            # Format: "Song Title Lyrics - Artist" or "Artist - Song Title Lyrics"
            if " - " in title_text:
                parts = title_text.split(" - ")
                if len(parts) >= 2:
                    # Common format: "Title Lyrics - Artist"
                    title = parts[0].replace("Lyrics", "").strip()
                    artist = parts[1].strip()

        # Look for artist name in common meta or link tags
        artist_match = re.search(r'<meta[^>]+content="[^"]*(?:Artist|by\s+)([^"]+)"[^>]*>', html)
        if artist_match:
            artist = artist_match.group(1).strip()

        # Extract views if present
        views_match = re.search(r"(\d+)\s*views?", html, re.IGNORECASE)
        if views_match:
            views = int(views_match.group(1).replace(",", ""))

        # Extract genre from breadcrumbs or meta
        genre_match = re.search(r'genre[^"/]+["\']?[^>]*>([^<]+)</a>', html, re.IGNORECASE)
        if genre_match:
            genre = genre_match.group(1).strip()

        return SongMetadata(
            title=title or "Song",
            artist=artist or "Artist",
            source_url=url,
            genre=genre,
            views=views,
        )

    def _extract_lyrics(self, html: str) -> str:
        """Extract lyrics text from lyrics.com page HTML."""
        # lyrics.com typically has lyrics in <pre id="lyric-body-text"> or similar
        patterns = [
            r'<pre[^>]*id="lyric-body-text"[^>]*>(.*?)</pre>',
            r'<div[^>]*class="lyrics[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*id="lyrics[^"]*"[^>]*>(.*?)</div>',
            r'<pre[^>]*class="lyric-body[^"]*"[^>]*>(.*?)</pre>',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                raw = match.group(1)
                # Clean up the lyrics
                text = self._clean_lyrics(raw)
                if text:
                    return text

        # Fallback: extract all text between common lyric markers
        lyrics_match = re.search(
            r'<(?:pre|div)[^>]*>(.*?)</(?:pre|div)>',
            html,
            re.DOTALL,
        )
        if lyrics_match:
            return self._clean_lyrics(lyrics_match.group(1))

        return ""

    def _clean_lyrics(self, html: str) -> str:
        """Clean up raw lyrics HTML."""
        # Remove HTML tags
        text = re.sub(r"<[^>]+>", "", html)
        # Unescape HTML entities
        text = unescape(text)
        # Clean up whitespace
        text = re.sub(r"\n\s*\n", "\n\n", text.strip())
        return text


class GeniusScraper:
    BASE = GENIUS_COM

    def supports_url(self, url: str) -> bool:
        try:
            self.normalize_url(url)
            return True
        except ValueError:
            return False

    def normalize_url(self, url: str) -> str:
        return genius.song_url(url)

    async def search(self, session: aiohttp.ClientSession, query: str) -> list[SongMetadata]:
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise ScrapingError('Search query must contain 1–300 characters')
        url = f'{self.BASE}/api/search/multi?{urlencode({"q": query.strip(), "per_page": 5})}'
        try:
            payload = await genius.fetch_text(session, url)
            return [SongMetadata(**item) for item in genius.search_results(payload)]
        except genius.GeniusError as error:
            raise ScrapingError(str(error), temporary=error.temporary) from error
        except (aiohttp.ClientError, TimeoutError) as error:
            raise ScrapingError('Genius could not be reached. Try again later.', temporary=True) from error

    async def scrape(self, session: aiohttp.ClientSession, url: str) -> ScrapedLyrics:
        try:
            normalized = self.normalize_url(url)
            html = await genius.fetch_text(session, normalized, song_page=True)
            return ScrapedLyrics(metadata=self._extract_metadata(html, normalized), text=self._extract_lyrics(html))
        except genius.GeniusError as error:
            raise ScrapingError(str(error), temporary=error.temporary) from error
        except (aiohttp.ClientError, TimeoutError) as error:
            raise ScrapingError('Genius could not be reached. Try again later.', temporary=True) from error

    def _extract_metadata(self, html: str, url: str) -> SongMetadata:
        return SongMetadata(**genius.page_metadata(html), source_url=genius.song_url(url))

    def _extract_lyrics(self, html: str) -> str:
        return genius.extract_lyrics(html)


class AZLyricsScraper:
    """Scraper for azlyrics.com."""

    BASE = AZLYRICS_COM

    def supports_url(self, url: str) -> bool:
        return "azlyrics.com" in url

    def normalize_url(self, url: str) -> str:
        """Normalize an azlyrics.com URL."""
        parsed = urlparse(url)
        path = parsed.path
        if not path.startswith("/"):
            path = "/" + path
        return urljoin(self.BASE, path)

    async def search(self, session: aiohttp.ClientSession, query: str) -> list[SongMetadata]:
        """Search azlyrics.com for songs."""
        params = {"q": query}
        url = f"{self.BASE}/search.php?{urlencode(params)}"
        songs: list[SongMetadata] = []

        async with session.get(url) as response:
            if response.status != 200:
                raise ScrapingError(f"AZLyrics search failed: HTTP {response.status}")
            html = await response.text()

        # AZLyrics search results table
        pattern = r'<div[^>]*class="search-match[^"]*"[^>]*>(.*?)</div>'
        for match in re.finditer(pattern, html, re.DOTALL):
            div = match.group(1)
            # Extract artist and title
            artist_match = re.search(r'<b[^>]*>([^<]+)</b>', div)
            title_match = re.search(r'<b[^>]*>([^<]+)</b>', div, re.DOTALL)

            if artist_match and title_match:
                artist = artist_match.group(1).strip()
                title = title_match.group(1).strip()

                # Find the link
                link_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>[^<]*</a>', div)
                if link_match:
                    href = link_match.group(1)
                    songs.append(SongMetadata(
                        title=title,
                        artist=artist,
                        source_url=urljoin(self.BASE, href),
                        genre=None,
                    ))

        return songs[:12]

    async def scrape(self, session: aiohttp.ClientSession, url: str) -> ScrapedLyrics:
        """Scrape lyrics from an azlyrics.com song page."""
        normalized = self.normalize_url(url)

        async with session.get(normalized) as response:
            if response.status != 200:
                raise ScrapingError(f"AZLyrics page fetch failed: HTTP {response.status}")
            html = await response.text()

        metadata = self._extract_metadata(html, url)
        lyrics_text = self._extract_lyrics(html)

        return ScrapedLyrics(metadata=metadata, text=lyrics_text)

    def _extract_metadata(self, html: str, url: str) -> SongMetadata:
        """Extract song metadata from azlyrics.com page."""
        title = ""
        artist = ""

        # AZLyrics format: <title>Artist - Song Title Lyrics</title>
        title_match = re.search(r"<title>([^<]+)</title>", html)
        if title_match:
            title_text = title_match.group(1)
            if " - " in title_text:
                parts = title_text.split(" - ", 1)
                artist = parts[0].strip()
                title = parts[1].replace("Lyrics", "").strip()

        return SongMetadata(
            title=title or "Song",
            artist=artist or "Artist",
            source_url=url,
            genre=None,
        )

    def _extract_lyrics(self, html: str) -> str:
        """Extract lyrics text from azlyrics.com page."""
        # AZLyrics lyrics are typically in a div with specific class
        patterns = [
            r'<div[^>]*class="song lyrics-body"[^>]*>(.*?)</div>',
            r'<div[^>]*class="col-xs-12"[^>]*>(.*?)</div>',  # main container
            r'<div[^>]*id="lyric-body-text"[^>]*>(.*?)</div>',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                raw = match.group(1)
                return self._clean_lyrics(raw)

        return ""

    def _clean_lyrics(self, html: str) -> str:
        """Clean up azlyrics.com lyrics HTML."""
        # Remove tags
        text = re.sub(r"<[^>]+>", "", html)
        text = unescape(text)
        # Clean up spacing
        text = re.sub(r"\n\s*\n", "\n\n", text.strip())
        return text


def get_all_scrapers() -> dict[str, type[LyricScraper]]:
    """Return all available lyric scrapers."""
    return {
        "lyrics.com": LyricsComScraper,
        "genius.com": GeniusScraper,
        "azlyrics.com": AZLyricsScraper,
    }


def get_all_sources() -> list[LyricSource]:
    """Return all lyric source definitions."""
    return [
        LyricSource(
            name="lyrics.com",
            label="Lyrics.com",
            description="Large collection of lyrics from various artists and genres.",
        ),
        LyricSource(
            name="genius.com",
            label="Genius",
            description="Crowdsourced lyrics with annotations and artist information.",
        ),
        LyricSource(
            name="azlyrics.com",
            label="AZLyrics",
            description="Extensive lyrics database with search and artist browsable content.",
        ),
    ]
