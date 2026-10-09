"""Offline inputs for worker tests, not coverage of the production acquisition path."""

import uuid

from genre_radio.engine import GENRES, LibraryExhausted


def use_imported_input_fixture(engine):
    async def request():
        identity = engine.preferences['lyrics_id']
        if identity == 'auto':
            available = engine._unused_lyrics()
            if not available:
                raise LibraryExhausted()
            identity = available[0]
        song = engine.lyrics[identity]
        previous = engine.queue[-1] if engine.queue else engine.current
        genre = engine.preferences['genre']
        if genre == 'Auto':
            genre = next(item for item in GENRES if not previous or item != previous['genre'])
        return {
            'id': str(uuid.uuid4()), 'title': song['title'], 'artist': song['artist'],
            'lyrics': song['text'], 'lyrics_id': identity, 'genre': genre,
            'original_duration': song.get('original_duration'), 'seed': 7,
        }

    engine._request = request
