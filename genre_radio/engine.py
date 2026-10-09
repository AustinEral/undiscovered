import asyncio
import json
import os
import secrets
import tempfile
import time
import uuid
from collections import ChainMap
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import aiohttp
from genre_radio import lyrics_sources
from genre_radio.identity import lyrics_hash, song_key
from genre_radio.genres import GENRES, genre_groups
from genre_radio.gpu import GpuAdmissionError
from genre_radio.planning import MAX_DURATION_SECONDS, validate_original_duration
from genre_radio.rotation import select_cached_lyrics

SAVED_INPUT_NOTICE = 'Using saved lyrics for a new interpretation; the source will be checked again later'
CHART_RETRY_SECONDS = 3600

LYRICS_SOURCES = lyrics_sources.get_all_sources()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def text_field(value, name, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{name} must be text of at most {maximum} characters')
    if required and not value.strip():
        raise ValueError(f'{name} is required')
    value.encode('utf-8')
    return value


class LibraryExhausted(Exception):
    pass


class RadioEngine:
    def __init__(self, root, pipeline, buffer_size=5, *, provider_factory=None):
        if type(buffer_size) is not int or not 1 <= buffer_size <= 10:
            raise ValueError('Buffer size must be 1–10 tracks')
        self.buffer_size = buffer_size
        self.root = Path(root).expanduser().resolve()
        self.pipeline = pipeline
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / 'tracks').mkdir(exist_ok=True, mode=0o700)
        self.lyrics = {}
        self.tracks = {}
        self.preferences = {'genre': 'Auto', 'lyrics_id': 'auto', 'lyrics_source': 'genius.com'}
        self.current = None
        self.queue = []
        self.preparing = False
        self.generating = None
        self.error = None
        self.failure = None
        self._failed_input = None
        self._skipped_inputs = {}
        self.starred_ids = set()
        self.notice = None
        self.running = False
        self._source_selection_revision = 0
        self.version = 0
        self.instance_id = str(uuid.uuid4())
        self._wake = asyncio.Event()
        self._worker = None
        self._subscribers = set()
        self._provider_factory = lyrics_sources.get_auto_provider() if provider_factory is None else provider_factory
        self._live_provider: lyrics_sources.AutoLyricsProvider | None = None
        self._chart_retry_at = 0
        self._chart_failure = None
        self._load()

    def _load(self):
        lyrics_path = self.root / 'lyrics.json'
        if lyrics_path.exists():
            self.lyrics = {item['id']: item for item in json.loads(lyrics_path.read_text()) if isinstance(item, dict) and isinstance(item.get('id'), str)}
        for path in (self.root / 'tracks').glob('*/manifest.json'):
            try:
                record = json.loads(path.read_text())
                if record['id'] != path.parent.name:
                    continue
                if (path.parent / 'audio.mp3').is_file() and (path.parent / 'cover.png').is_file():
                    self.tracks[record['id']] = record
            except (OSError, ValueError, KeyError):
                continue
        favorites_path = self.root / 'favorites.json'
        if favorites_path.exists():
            favorites = json.loads(favorites_path.read_text())
            if not isinstance(favorites, list) or any(not isinstance(identity, str) for identity in favorites):
                raise ValueError('favorites.json must contain a list of track IDs')
            self.starred_ids = set(favorites)
        state_path = self.root / 'state.json'
        if state_path.exists():
            state = json.loads(state_path.read_text())
            self.preferences.update(state.get('preferences', {}))
            self.preferences.pop('duration', None)
            if self.preferences['lyrics_id'] not in self.lyrics:
                self.preferences['lyrics_id'] = 'auto'
            self.current = self.tracks.get(state.get('current_id'))
            self.queue = [self.tracks[identity] for identity in state.get('queue_ids', []) if identity in self.tracks]

        self._prune_repeated_queue()
        self.queue = self.queue[:self.buffer_size]
        self._update_live_provider()

    def _update_live_provider(self):
        """Recreate the live provider with current lyrics and tracks."""
        self._live_provider = self._provider_factory(
            lyrics=self.lyrics,
            tracks=ChainMap(self.tracks, self._skipped_inputs),
            preferences=self.preferences,
            genre_choices=list(GENRES),
        )
        self.preferences['lyrics_source'] = self._live_provider.source_id

    def _track_identities(self, tracks):
        names, hashes = set(), set()
        for track in tracks:
            names.add(song_key(track))
            fingerprint = track.get('lyrics_hash')
            saved = self.lyrics.get(track.get('lyrics_id'))
            if isinstance(fingerprint, str) and fingerprint:
                hashes.add(fingerprint)
            elif isinstance(saved, dict) and isinstance(saved.get('text'), str):
                hashes.add(lyrics_hash(saved['text']))
        return names, hashes

    def _unused_lyrics(self):
        names, hashes = self._track_identities(self.tracks.values())
        candidates = []
        for identity in self.lyrics:
            try:
                song = self._saved_lyrics(identity)
            except (TypeError, ValueError, UnicodeError):
                continue
            name, fingerprint = song_key(song), lyrics_hash(song['text'])
            if name in names or fingerprint in hashes:
                continue
            candidates.append(identity)
            names.add(name)
            hashes.add(fingerprint)
        return candidates

    def _prune_repeated_queue(self):
        identities = {self.current['id']} if self.current else set()
        kept = []
        for track in self.queue:
            if track['id'] in identities:
                continue
            kept.append(track)
            identities.add(track['id'])
        if len(kept) != len(self.queue):
            self.queue = kept
            self._save()
            self._broadcast()

    @property
    def next(self):
        return self.queue[0] if self.queue else None

    def _save(self):
        atomic_json(self.root / 'state.json', {
            'preferences': self.preferences,
            'current_id': self.current['id'] if self.current else None,
            'queue_ids': [track['id'] for track in self.queue],
        })

    def _visible_track(self, track, include_prompts=False):
        if not track:
            return None
        keys = ['id', 'title', 'artist', 'genre', 'duration', 'created_at', 'audio_url', 'cover_url', 'lyrics_id', 'planned_duration', 'bpm', 'duration_reason', 'original_duration', 'vocal_gender', 'input_reused']
        result = {key: track[key] for key in keys if key in track}
        if include_prompts:
            result['prompts'] = {name: track.get(field, '') if isinstance(track.get(field, ''), str) else '' for name, field in [('music', 'caption'), ('cover', 'cover_prompt')]}
        return result

    def track_details(self, identity):
        return {'track': self._visible_track(self.tracks[identity], include_prompts=True)}

    def favorites(self):
        tracks = [self.tracks[identity] for identity in self.starred_ids if identity in self.tracks]
        return {
            'tracks': [self._visible_track(track) for track in sorted(tracks, key=lambda track: (track['created_at'], track['id']), reverse=True)],
            'missing_ids': sorted(self.starred_ids - self.tracks.keys()),
        }

    def set_favorite(self, values):
        if not isinstance(values, dict) or set(values) != {'id', 'starred'}:
            raise ValueError('Provide id and starred')
        identity = text_field(values['id'], 'track ID', 160, True)
        if type(values['starred']) is not bool:
            raise ValueError('starred must be a boolean')
        if identity not in self.tracks and (values['starred'] or identity not in self.starred_ids):
            raise ValueError('Track not found')
        candidate = self.starred_ids | {identity} if values['starred'] else self.starred_ids - {identity}
        if candidate != self.starred_ids:
            atomic_json(self.root / 'favorites.json', sorted(candidate))
            self.starred_ids = candidate
            self._broadcast()
        return {'state': self.snapshot()}

    def snapshot(self):
        remaining = len(self._unused_lyrics())
        visible = self._visible_track
        return {
            'version': self.version, 'instance_id': self.instance_id,
            'running': self.running, 'current': visible(self.current, include_prompts=True), 'next': visible(self.next),
            'queue': [visible(track) for track in self.queue], 'buffer_target': self.buffer_size,
            'preparing': self.preparing,
            'generating': dict(self.generating) if self.generating else None,
            'preferences': dict(self.preferences),
            'library_remaining': remaining,
            'library_exhausted': False,
            'lyrics': [{key: item.get(key, '') for key in ['id', 'title', 'artist', 'source_url', 'source_name', 'original_genre', 'original_duration']} for item in self.lyrics.values() if isinstance(item, dict)],
            'genres': list(GENRES),
            'genre_groups': genre_groups(),
            'recent': [visible(track) for track in sorted(self.tracks.values(), key=lambda item: item['created_at'], reverse=True)[:12]],
            'error': self.error,
            'failure': dict(self.failure) if self.failure else None,
            'starred_ids': sorted(self.starred_ids),
            'notice': self.notice,
        }

    def _broadcast(self):
        self.version += 1
        state = self.snapshot()
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(state)

    def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        queue.put_nowait(self.snapshot())
        return queue

    def unsubscribe(self, queue):
        self._subscribers.discard(queue)

    def end_subscriptions(self):
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)

    async def initialize(self):
        self._worker = asyncio.create_task(self._run(), name='genre-radio-generation')

    async def close(self):
        self.running = False
        try:
            if self._worker:
                self._worker.cancel()
                try:
                    await self._worker
                except asyncio.CancelledError:
                    pass
        finally:
            await self.pipeline.close()

    def _capture_failure(self, error, request):
        request = request if isinstance(request, dict) else {}
        metadata = {}
        for key in ('title', 'artist', 'lyrics_id'):
            try:
                metadata[key] = text_field(request.get(key), key, 160, True)
            except (ValueError, UnicodeError):
                metadata[key] = ''
        try:
            fingerprint = lyrics_hash(text_field(request.get('lyrics'), 'lyrics', 14000, True))
        except (ValueError, UnicodeError):
            fingerprint = None
        known = all(metadata.values()) and fingerprint is not None
        self._failed_input = dict(metadata, lyrics_hash=fingerprint) if known else None
        try:
            identity = text_field(request.get('id'), 'job ID', 160, True)
        except (ValueError, UnicodeError):
            identity = str(uuid.uuid4())
        self.failure = dict(metadata, id=identity, can_skip=bool(known and not isinstance(error, (GpuAdmissionError, OSError))))

    def generation_next(self, values):
        if not isinstance(values, dict) or set(values) != {'failed_id'} or not isinstance(values['failed_id'], str):
            raise ValueError('Provide failed_id')
        if not self.failure or values['failed_id'] != self.failure['id']:
            raise ValueError('Generation failure is no longer current')
        if self.running or self.preparing or self.generating is not None:
            raise ValueError('Wait for the active generation to finish')
        if not self.failure['can_skip'] or self._failed_input is None:
            raise ValueError('This failure has no input to skip; retry generation instead')
        self._skipped_inputs[self.failure['id']] = dict(self._failed_input)
        if self.preferences['lyrics_id'] != 'auto':
            self.preferences = dict(self.preferences, lyrics_id='auto')
            self._source_selection_revision += 1
            self._save()
            self._update_live_provider()
        return {'state': self.start()}

    def start(self):
        if not self.running and not self.preparing and self.generating is None:
            self.failure = None
            self._failed_input = None
        self.running = True
        self.error = None
        self.notice = None
        self._wake.set()
        self._broadcast()
        return self.snapshot()

    def stop(self):
        self.running = False
        self._broadcast()
        return self.snapshot()

    def update_preferences(self, values):
        if not isinstance(values, dict) or set(values) - {'genre', 'lyrics_id'}:
            raise ValueError('Unknown preference fields')
        candidate = dict(self.preferences)
        if 'genre' in values:
            candidate['genre'] = text_field(values['genre'], 'genre', 160, True).strip()
        if 'lyrics_id' in values:
            if values['lyrics_id'] != 'auto' and values['lyrics_id'] not in self.lyrics:
                raise ValueError('Choose a saved lyric set')
            candidate['lyrics_id'] = values['lyrics_id']
        if candidate != self.preferences or ('lyrics_id' in values and candidate['lyrics_id'] != 'auto'):
            self._skipped_inputs.clear()
        if candidate != self.preferences:
            if candidate['lyrics_id'] != self.preferences['lyrics_id']:
                self._source_selection_revision += 1
            self.preferences = candidate
            self._save()
            self._update_live_provider()
            if self.running:
                self._wake.set()
            self._broadcast()
        return self.snapshot()

    def _validate_lyrics(self, values):
        if not isinstance(values, dict) or set(values) - {'title', 'artist', 'text', 'source_url', 'source_name', 'original_genre', 'original_duration'}:
            raise ValueError('Unknown lyric fields')
        title = text_field(values.get('title', ''), 'title', 160, True).strip()
        artist = text_field(values.get('artist', ''), 'artist', 160, True).strip()
        lyrics = text_field(values.get('text', ''), 'lyrics', 14000, True)
        source = text_field(values.get('source_url', ''), 'source URL', 2000).strip()
        if source:
            parsed = urlsplit(source)
            if parsed.scheme not in {'http', 'https'} or not parsed.netloc or any(ord(char) < 32 for char in source):
                raise ValueError('Source URL must be an http or https link')
        source_name = text_field(values.get('source_name', ''), 'source name', 160).strip()
        original_genre = text_field(values.get('original_genre', ''), 'original genre', 160).strip()
        original_duration = validate_original_duration(values.get('original_duration'))
        identity = song_key({'title': title, 'artist': artist})
        if identity == ('artist', 'song'):
            raise ValueError('Song / Artist is a placeholder identity. Supply the actual title and artist.')
        normalized_text = ' '.join(lyrics.casefold().split())
        if normalized_text in {'lyrics', 'lyrics here', 'song lyrics'}:
            raise ValueError('Placeholder lyrics are not a song.')
        return {'title': title, 'artist': artist, 'text': lyrics, 'source_url': source, 'source_name': source_name, 'original_genre': original_genre, 'original_duration': original_duration}

    def save_lyrics(self, values):
        record = self._validate_lyrics(values)
        identity = song_key(record)
        fingerprint = lyrics_hash(record['text'])
        existing_names, existing_hashes = set(), set()
        for saved in self.lyrics.values():
            if not isinstance(saved, dict):
                continue
            if isinstance(saved.get('title'), str) and isinstance(saved.get('artist', ''), str):
                existing_names.add(song_key(saved))
            saved_text = saved.get('text')
            if isinstance(saved_text, str) and saved_text.strip():
                existing_hashes.add(lyrics_hash(saved_text))
        if identity in existing_names or fingerprint in existing_hashes:
            raise ValueError('Song is already saved (same artist/title or lyrics).')
        record['id'] = str(uuid.uuid4())
        self.lyrics[record['id']] = record
        self._update_live_provider()
        atomic_json(self.root / 'lyrics.json', list(self.lyrics.values()))
        self._wake.set()
        self._broadcast()
        return record

    async def scrape_lyrics(self, source_name: str, url: str) -> dict:
        """Scrape lyrics from a lyrics source URL."""
        scraper_class = lyrics_sources.get_all_scrapers().get(source_name)
        if not scraper_class:
            raise ValueError(f'Unknown lyrics source: {source_name}')
        scraper = scraper_class()
        async with aiohttp.ClientSession() as session:
            scraped = await scraper.scrape(session, url)
        return {
            'title': scraped.metadata.title,
            'artist': scraped.metadata.artist,
            'text': scraped.text,
            'source_url': scraped.metadata.source_url,
            'source_name': source_name,
        }

    async def search_lyrics(self, source_name: str, query: str) -> list[dict]:
        """Search a lyrics source for songs."""
        scraper_class = lyrics_sources.get_all_scrapers().get(source_name)
        if not scraper_class:
            raise ValueError(f'Unknown lyrics source: {source_name}')
        scraper = scraper_class()
        async with aiohttp.ClientSession() as session:
            results = await scraper.search(session, query)
        return [
            {
                'title': song.title,
                'artist': song.artist,
                'source_url': song.source_url,
                'genre': song.genre,
                'views': song.views,
            }
            for song in results
        ]

    def advance(self):
        self._prune_repeated_queue()
        advanced = self.next is not None
        if advanced:
            self.current = self.queue.pop(0)
            self._save()
            self._wake.set()
            self._broadcast()
        return {'state': self.snapshot(), 'advanced': advanced}

    def media_path(self, identity, kind):
        if identity not in self.tracks or kind not in {'audio', 'cover'}:
            raise KeyError('Media not found')
        return self.root / 'tracks' / identity / ('audio.mp3' if kind == 'audio' else 'cover.png')

    def _saved_lyrics(self, lyric_id):
        song = self.lyrics.get(lyric_id)
        if not isinstance(song, dict):
            raise ValueError('Choose a saved lyric set')
        text_field(lyric_id, 'lyrics ID', 160, True)
        fields = {'title', 'artist', 'text', 'source_url', 'source_name', 'original_genre', 'original_duration'}
        return dict(self._validate_lyrics({key: value for key, value in song.items() if key in fields}), id=lyric_id)

    def _cached_lyrics(self, provider=None):
        provider = self._live_provider if provider is None else provider
        skipped_names, skipped_hashes = self._track_identities(self._skipped_inputs.values())
        records = []
        for lyric_id in self.lyrics:
            try:
                song = self._saved_lyrics(lyric_id)
            except (TypeError, ValueError, UnicodeError):
                continue
            if song_key(song) in skipped_names or lyrics_hash(song['text']) in skipped_hashes:
                continue
            if provider.accepts_cached(song):
                records.append(song)
        def metadata(track):
            saved = self.lyrics.get(track.get('lyrics_id'))
            if not track.get('lyrics_hash') and isinstance(saved, dict) and isinstance(saved.get('text'), str):
                return dict(track, lyrics_hash=lyrics_hash(saved['text']))
            return track

        upcoming = ([self.current] if self.current else []) + self.queue
        return select_cached_lyrics(records, [metadata(track) for track in self.tracks.values()], [metadata(track) for track in upcoming])

    async def _request(self):
        preferences = dict(self.preferences)
        provider = self._live_provider
        lyric_id = preferences['lyrics_id']
        if lyric_id == 'auto':
            if time.monotonic() < self._chart_retry_at:
                saved = self._cached_lyrics(provider)
                if saved is None:
                    raise self._chart_failure
                return self._saved_request(saved, genre=preferences['genre'], notice=SAVED_INPUT_NOTICE)
            try:
                song = await provider.acquire()
                if song.lyrics_id not in self.lyrics:
                    values = song.saved_record()
                    values.pop('id')
                    saved = self.save_lyrics(values)
                    request = provider.prepare_request(song, genre=preferences['genre'])
                    request['lyrics_id'] = saved['id']
                else:
                    request = provider.prepare_request(song, genre=preferences['genre'])
            except (ValueError, aiohttp.ClientError, TimeoutError) as error:
                saved = self._cached_lyrics(provider)
                if saved is None:
                    raise
                self._chart_retry_at = time.monotonic() + CHART_RETRY_SECONDS
                self._chart_failure = error
                return self._saved_request(saved, genre=preferences['genre'], notice=SAVED_INPUT_NOTICE)
            self._chart_retry_at = 0
            self._chart_failure = None
            return dict(request, input_reused=False)
        return self._saved_request(self._saved_lyrics(lyric_id), genre=preferences['genre'])

    def _saved_request(self, song, *, genre, notice=None):
        names, hashes = self._track_identities(self.tracks.values())
        input_reused = song_key(song) in names or lyrics_hash(song['text']) in hashes
        return {
            'id': str(uuid.uuid4()),
            'title': song['title'],
            'artist': song['artist'],
            'lyrics': song['text'],
            'lyrics_id': song['id'],
            'genre': genre,
            'seed': secrets.randbelow(2**31),
            'original_genre': song['original_genre'],
            'original_duration': song['original_duration'],
            'input_reused': input_reused,
            'notice': notice,
        }

    async def _run(self):
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self.running and (self.current is None or len(self.queue) < self.buffer_size):
                request = None
                try:
                    source_selection_revision = self._source_selection_revision
                    genre_mode = self.preferences['genre']
                    lyrics_mode = self.preferences['lyrics_id']
                    self.preparing = True
                    self._broadcast()
                    try:
                        request = await self._request()
                    finally:
                        self.preparing = False
                    if not self.running:
                        self.generating = None
                        self._broadcast()
                        continue
                    try:
                        text_field(request.get('title'), 'title', 160, True)
                        text_field(request.get('artist'), 'artist', 160, True)
                        prepared_lyrics = text_field(request.get('lyrics'), 'lyrics', 14000, True)
                    except (UnicodeError, ValueError) as error:
                        raise ValueError(f'Prepared song needs valid title, artist, and lyrics fields: {error}. Choose a valid song, then press Start.') from error
                    if song_key(request) == ('artist', 'song'):
                        raise ValueError('Input returned placeholder Song / Artist metadata. Supply the actual title and artist; no model job was started.')
                    if ' '.join(prepared_lyrics.casefold().split()) in {'lyrics', 'lyrics here', 'song lyrics'}:
                        raise ValueError('Input returned placeholder lyrics. Supply usable lyrics for the actual song, then press Start; no model job was started.')
                    names, hashes = self._track_identities(self.tracks.values())
                    input_reused = song_key(request) in names or lyrics_hash(request['lyrics']) in hashes
                    saved = self.lyrics.get(request['lyrics_id'], {})
                    original_genre = text_field(request.get('original_genre', saved.get('original_genre', '')), 'original genre', 160).strip()
                    original_duration = validate_original_duration(request.get('original_duration', saved.get('original_duration')))
                    recent = ([self.current] if self.current else []) + self.queue
                    request = {key: value for key, value in request.items() if key != 'duration'}
                    request.update(genre=genre_mode, genre_choices=list(GENRES), original_genre=original_genre, original_duration=original_duration, recent_genres=[track['genre'] for track in recent[-3:]], input_reused=input_reused)
                    self.notice = request.pop('notice', None)
                    self.generating = {'id': request['id'], 'title': request['title'], 'genre': request['genre'], 'stage': 'waiting', 'progress': None, 'input_reused': request['input_reused']}
                    self._broadcast()
                    directory = self.root / 'tracks' / request['id']
                    directory.mkdir(mode=0o700)

                    async def progress(stage, fraction, details=None):
                        self.generating['stage'] = stage
                        self.generating['progress'] = max(0, min(1, fraction)) if fraction is not None else None
                        if details is not None:
                            self.generating.update({key: details[key] for key in ('genre', 'planned_duration', 'bpm', 'vocal_gender') if key in details})
                        self._broadcast()

                    generated = await self.pipeline.generate(request, directory, progress)
                    if not (directory / 'audio.mp3').is_file() or not (directory / 'cover.png').is_file():
                        raise RuntimeError('Generation did not produce both audio and cover art')
                    duration = float(generated['duration'])
                    planned_duration = generated.get('planned_duration')
                    if type(planned_duration) is not int or not 1 <= planned_duration <= MAX_DURATION_SECONDS:
                        raise RuntimeError('Prompt generator did not return a supported duration plan')
                    if not 0 < duration <= planned_duration + 2:
                        raise RuntimeError('Generated audio has an invalid duration')
                    selected_genre = text_field(generated.get('genre'), 'selected genre', 160, True).strip()
                    if selected_genre.casefold() == 'auto' or (genre_mode != 'Auto' and selected_genre != genre_mode):
                        raise RuntimeError('Prompt generator did not return the selected genre correctly.')
                    vocal_gender = generated.get('vocal_gender')
                    if not isinstance(vocal_gender, str) or vocal_gender not in {'male', 'female', 'mixed', 'instrumental'}:
                        raise RuntimeError('Prompt generator did not return a vocal presentation.')
                    track = {
                        'id': request['id'], 'title': request['title'], 'artist': request['artist'],
                        'genre': selected_genre, 'genre_selection': 'auto' if genre_mode == 'Auto' else 'manual', 'original_genre': original_genre,
                        'lyrics_id': request['lyrics_id'], 'duration': duration,
                        'created_at': datetime.now(timezone.utc).isoformat(),
                        'audio_url': f"media?id={request['id']}&kind=audio",
                        'cover_url': f"media?id={request['id']}&kind=cover",
                        'seed': request['seed'], 'planned_duration': planned_duration, 'bpm': generated['bpm'],
                        'duration_reason': generated['duration_reason'], 'original_duration': original_duration,
                        'vocal_gender': vocal_gender, 'source_selection': 'manual' if lyrics_mode != 'auto' else 'auto',
                        'lyrics_hash': lyrics_hash(request['lyrics']), 'input_reused': request['input_reused'],
                        'caption': generated['caption'], 'cover_prompt': generated['cover_prompt'],
                        'timings': generated.get('timings', {}),
                    }
                    atomic_json(directory / 'manifest.json', track)
                    self.tracks[track['id']] = track
                    if self.current is None:
                        self.current = track
                    else:
                        self.queue.append(track)
                    if lyrics_mode != 'auto' and request['lyrics_id'] == lyrics_mode and source_selection_revision == self._source_selection_revision and self.preferences['lyrics_id'] == lyrics_mode:
                        self.preferences = dict(self.preferences, lyrics_id='auto')
                        self._update_live_provider()
                    self.generating = None
                    self._save()
                    self._broadcast()
                except LibraryExhausted:
                    self.generating = None
                    self._broadcast()
                    break
                except asyncio.CancelledError:
                    self.generating = None
                    self._broadcast()
                    raise
                except Exception as error:
                    self._capture_failure(error, request)
                    self.error = str(error)[:1000] or type(error).__name__
                    self.running = False
                    self.generating = None
                    self._broadcast()
                    break
