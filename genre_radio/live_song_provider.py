import json
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import aiohttp

from genre_radio.identity import lyrics_hash, song_key
from genre_radio.genius import GeniusError, extract_lyrics as parse_lyrics, fetch_text, original_genre, song_url
from genre_radio.planning import validate_original_duration

GENIUS_CHART_URL = 'https://genius.com/api/home/chart_items?chart_type=songs&chart_genre=all&time_period=day&page=1&per_page=50'
SOURCE_NAME = 'Genius'
_PLACEHOLDER_TEXT = {'lyrics', 'lyrics here', 'song lyrics'}


def _text(value, name, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{name} must be text of at most {maximum} characters')
    if required and not value.strip():
        raise ValueError(f'{name} is required')
    value.encode('utf-8')
    return value


def parse_chart(payload, *, current_year=None):
    try:
        songs = json.loads(payload)['response']['songs']
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('Genius returned malformed current song chart data') from error
    if not isinstance(songs, list):
        raise ValueError('Genius returned malformed current song chart data')
    year = current_year if current_year is not None else datetime.now(timezone.utc).year
    minimum_year = year - 2
    records = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        try:
            release_year = song['release_date_components']['year']
            stats = song.get('stats') or {}
            record = ChartSong(
                title=song['title'],
                artist=song['artist_names'],
                source_url=song['url'],
                release_year=release_year,
                views=stats.get('pageviews') if isinstance(stats, dict) else None,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if type(release_year) is int and minimum_year <= release_year <= year and not song.get('instrumental', False) and song.get('lyrics_state') in (None, 'complete'):
            records.append(record)
    if not records:
        raise ValueError('Genius has no recent non-instrumental songs in its current chart')
    return records


@dataclass(frozen=True, slots=True, kw_only=True)
class ChartSong:
    title: str
    artist: str
    source_url: str
    release_year: int
    views: int | None = None

    def __post_init__(self):
        _text(self.title, 'title', 160, True)
        _text(self.artist, 'artist', 160, True)
        _text(self.source_url, 'source URL', 2000, True)
        song_url(self.source_url)
        if type(self.release_year) is not int:
            raise ValueError('release year must be an integer')
        if song_key({'title': self.title, 'artist': self.artist}) == ('artist', 'song'):
            raise ValueError('Song / Artist is placeholder metadata')


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveSong:
    lyrics_id: str
    title: str
    artist: str
    text: str
    source_url: str
    source_name: str
    original_genre: str = ''
    original_duration: int | float | None = None
    views: int | None = None

    def __post_init__(self):
        _text(self.lyrics_id, 'lyrics ID', 160, True)
        _text(self.title, 'title', 160, True)
        _text(self.artist, 'artist', 160, True)
        _text(self.text, 'lyrics', 14000, True)
        _text(self.source_url, 'source URL', 2000, True)
        _text(self.source_name, 'source name', 160, True)
        _text(self.original_genre, 'original genre', 160)
        validate_original_duration(self.original_duration)
        if self.identity_key == ('artist', 'song'):
            raise ValueError('Song / Artist is placeholder metadata')
        if ' '.join(self.text.casefold().split()) in _PLACEHOLDER_TEXT:
            raise ValueError('Placeholder lyrics are not a song')
        parsed = urlsplit(self.source_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc or any(ord(char) < 32 for char in self.source_url):
            raise ValueError('Source URL must be an http or https link')

    @property
    def identity_key(self):
        return song_key({'artist': self.artist, 'title': self.title})

    @property
    def lyrics_hash(self):
        return lyrics_hash(self.text)

    def saved_record(self):
        return {
            'id': self.lyrics_id,
            'title': self.title,
            'artist': self.artist,
            'text': self.text,
            'source_url': self.source_url,
            'source_name': self.source_name,
            'original_genre': self.original_genre,
            **({'original_duration': self.original_duration} if self.original_duration is not None else {}),
        }


class LiveSongProvider:
    source_id = 'genius.com'

    @staticmethod
    def accepts_cached(song):
        source = urlsplit(song['source_url'])
        return song['source_name'].casefold() in {'genius', 'genius.com'} or (source.scheme == 'https' and source.netloc.casefold() in {'genius.com', 'www.genius.com'})

    def __init__(self, *, lyrics, tracks, preferences, genre_choices):
        if not isinstance(genre_choices, list) or not genre_choices or any(not isinstance(item, str) or not item.strip() or item.strip().casefold() == 'auto' for item in genre_choices):
            raise ValueError('genre_choices must be a nonempty list of named styles')
        self.genre_choices = list(genre_choices)
        self.lyrics = lyrics
        self.tracks = tracks
        self.preferences = preferences

    def _used_identities(self):
        keys = set()
        hashes = set()
        for track in self.tracks.values():
            if not isinstance(track, dict):
                continue
            if isinstance(track.get('title'), str) and isinstance(track.get('artist'), str):
                keys.add(song_key(track))
            fingerprint = track.get('lyrics_hash')
            if isinstance(fingerprint, str) and fingerprint:
                hashes.add(fingerprint)
            saved = self.lyrics.get(track.get('lyrics_id'))
            if isinstance(saved, dict) and isinstance(saved.get('text'), str) and saved['text'].strip():
                hashes.add(lyrics_hash(saved['text']))
        return keys, hashes

    def _saved_chart_song(self, candidate, keys, hashes):
        for lyrics_id, record in self.lyrics.items():
            if not isinstance(record, dict) or song_key(record) != song_key({'title': candidate.title, 'artist': candidate.artist}):
                continue
            try:
                song = LiveSong(
                    lyrics_id=lyrics_id,
                    title=record.get('title', ''),
                    artist=record.get('artist', ''),
                    text=record.get('text', ''),
                    source_url=record.get('source_url', ''),
                    source_name=record.get('source_name', ''),
                    original_genre=record.get('original_genre', ''),
                    original_duration=record.get('original_duration'),
                    views=candidate.views,
                )
            except (TypeError, ValueError, UnicodeError):
                return None
            if song.identity_key not in keys and song.lyrics_hash not in hashes:
                return song
        return None

    async def acquire(self):
        keys, hashes = self._used_identities()
        async with aiohttp.ClientSession() as session:
            candidates = parse_chart(await fetch_text(session, GENIUS_CHART_URL))
            for candidate in candidates:
                identity = song_key({'title': candidate.title, 'artist': candidate.artist})
                if identity in keys:
                    continue
                saved = self._saved_chart_song(candidate, keys, hashes)
                if saved:
                    return saved
                try:
                    html = await fetch_text(session, candidate.source_url, song_page=True)
                except GeniusError as error:
                    if error.status == 404:
                        continue
                    raise
                try:
                    text = parse_lyrics(html)
                    song = LiveSong(
                        lyrics_id=str(uuid.uuid4()),
                        title=candidate.title,
                        artist=candidate.artist,
                        text=text,
                        source_url=song_url(candidate.source_url),
                        source_name=SOURCE_NAME,
                        original_genre=original_genre(html),
                        views=candidate.views,
                    )
                except (TypeError, ValueError, UnicodeError):
                    continue
                if song.identity_key not in keys and song.lyrics_hash not in hashes:
                    return song
        raise ValueError('Genius did not provide a different valid recent popular song with usable lyrics')

    def prepare_request(self, song, *, genre=None):
        genre = self.preferences.get('genre', 'Auto') if genre is None else genre
        _text(genre, 'genre', 160, True)
        request = {
            'id': str(uuid.uuid4()),
            'title': song.title,
            'artist': song.artist,
            'lyrics': song.text,
            'lyrics_id': song.lyrics_id,
            'genre': genre,
            'seed': random.randint(0, 2**31 - 1),
            'original_genre': song.original_genre,
        }
        if song.original_duration is not None:
            request['original_duration'] = song.original_duration
        if genre == 'Auto':
            request['genre_choices'] = list(self.genre_choices)
        return request
