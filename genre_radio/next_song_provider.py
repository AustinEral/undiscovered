"""Validated local-cache selection, not online discovery or acquisition.

Cache entries have no authoritative date or rank, so selection makes no claim
about recency or popularity. Source filters never fall back to another source.
Selection does not reserve a song; callers supply the used-track ledger.
"""

import random
import uuid
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from genre_radio.identity import lyrics_hash, song_key
from genre_radio.planning import validate_original_duration


def _text_field(value, name, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{name} must be text of at most {maximum} characters')
    if required and not value.strip():
        raise ValueError(f'{name} is required')
    value.encode('utf-8')


@dataclass(frozen=True, slots=True, kw_only=True)
class SongRecord:
    """Validated cached metadata and unchanged lyric text, not a model request."""

    lyrics_id: str
    title: str
    text: str
    artist: str = ''
    source_url: str = ''
    source_name: str = ''
    original_genre: str = ''
    original_duration: int | float | None = None

    def __post_init__(self):
        _text_field(self.lyrics_id, 'lyrics_id', 160, True)
        _text_field(self.title, 'title', 160, True)
        _text_field(self.artist, 'artist', 160)
        _text_field(self.text, 'lyrics', 14000, True)
        _text_field(self.source_url, 'source URL', 2000)
        _text_field(self.source_name, 'source name', 160)
        _text_field(self.original_genre, 'original genre', 160)
        validate_original_duration(self.original_duration)
        if self.identity_key == ('artist', 'song'):
            raise ValueError('Song / Artist is placeholder metadata')
        if ' '.join(self.text.casefold().split()) in {'lyrics', 'lyrics here', 'song lyrics'}:
            raise ValueError('Placeholder lyrics are not a song')
        if self.source_url:
            parsed = urlsplit(self.source_url)
            if parsed.scheme not in {'http', 'https'} or not parsed.netloc or any(ord(char) < 32 for char in self.source_url):
                raise ValueError('Source URL must be an http or https link')

    @property
    def identity_key(self) -> tuple[str, str]:
        return song_key({'artist': self.artist, 'title': self.title})

    @property
    def lyrics_hash(self) -> str:
        return lyrics_hash(self.text)

    def to_dict(self) -> dict:
        """Serialize record fields only; use prepare_request for model input."""
        return asdict(self)


class SongProvider:
    """Select an unused, valid cached record without fetching or substituting."""

    def __init__(
        self,
        *,
        lyrics: dict[str, dict],
        tracks: dict[str, dict],
        preferences: dict,
        lyrics_sources: list,
        genre_choices: list[str],
    ):
        if not isinstance(genre_choices, list) or not genre_choices:
            raise ValueError('genre_choices must be a nonempty list of named styles')
        for genre in genre_choices:
            _text_field(genre, 'genre choice', 160, True)
            if genre.strip().casefold() == 'auto':
                raise ValueError('genre_choices must contain named styles, not Auto')
        self.genre_choices = list(genre_choices)
        self.lyrics = lyrics
        self.tracks = tracks
        self.preferences = preferences
        self.lyrics_sources = lyrics_sources

    def _used_identities(self) -> tuple[set[tuple[str, str]], set[str]]:
        keys, hashes = set(), set()
        for track in self.tracks.values():
            if not isinstance(track, dict):
                continue
            fingerprint = track.get('lyrics_hash')
            if isinstance(fingerprint, str) and fingerprint:
                hashes.add(fingerprint)
            lyrics_id = track.get('lyrics_id')
            saved = self.lyrics.get(lyrics_id) if isinstance(lyrics_id, str) else None
            for record in (track, saved):
                if not isinstance(record, dict):
                    continue
                if all(isinstance(record.get(field, ''), str) for field in ('title', 'artist')):
                    keys.add(song_key(record))
                for field in ('lyrics', 'text'):
                    text = record.get(field)
                    if isinstance(text, str) and text.strip():
                        try:
                            hashes.add(lyrics_hash(text))
                        except UnicodeError:
                            continue
        return keys, hashes

    def select_from_available(self, *, preferred_source: str | None = None) -> SongRecord | None:
        """Randomly choose among validated, distinct, unused cache entries.

        A manual lyrics_id restricts selection to that exact unused record.
        An explicit source overrides the source preference; an empty source
        disables source filtering. Missing or exhausted matches return None.
        """
        source = self.preferences.get('lyrics_source', '') if preferred_source is None else preferred_source
        _text_field(source, 'source name', 160)
        selected_id = self.preferences.get('lyrics_id', 'auto')
        _text_field(selected_id, 'lyrics_id', 160, True)
        keys, hashes = self._used_identities()
        available = []
        entries = self.lyrics.items() if selected_id == 'auto' else [(selected_id, self.lyrics.get(selected_id))]
        for lyrics_id, cached in entries:
            if not isinstance(cached, dict):
                continue
            try:
                song = SongRecord(
                    lyrics_id=lyrics_id,
                    title=cached.get('title', ''),
                    artist=cached.get('artist', ''),
                    text=cached.get('text', ''),
                    source_url=cached.get('source_url', ''),
                    source_name=cached.get('source_name', ''),
                    original_genre=cached.get('original_genre', ''),
                    original_duration=cached.get('original_duration'),
                )
            except ValueError:
                continue
            if source and song.source_name != source:
                continue
            key, fingerprint = song.identity_key, song.lyrics_hash
            if key in keys or fingerprint in hashes:
                continue
            available.append(song)
            keys.add(key)
            hashes.add(fingerprint)
        return random.choice(available) if available else None

    def prepare_request(
        self,
        song: SongRecord,
        *,
        genre: str | None = None,
    ) -> dict:
        """Build a fresh job, preserving metadata and the selected genre mode.

        Omitted genre uses preferences, defaulting to Auto.
        Auto supplies the configured genre choices for downstream selection.
        Original duration is reference metadata, not a generation target.
        """
        genre = self.preferences.get('genre', 'Auto') if genre is None else genre
        _text_field(genre, 'genre', 160, True)
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

    def get_sources(self) -> list[str]:
        """Return source names from the platform's LyricSource catalog."""
        return [source.name for source in self.lyrics_sources]
