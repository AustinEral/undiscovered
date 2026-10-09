import asyncio
import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genre_radio.engine import RadioEngine
from genre_radio.identity import song_key
from genre_radio.planning import allowed_genres


class RecordingPipeline:
    def __init__(self):
        self.requests = []

    async def generate(self, request, directory, progress):
        self.requests.append(dict(request))
        await progress('input-check', 1.0)
        (directory / 'audio.mp3').write_bytes(b'ID3 live input check')
        (directory / 'cover.png').write_bytes(b'PNG live input check')
        return {
            'duration': 30.0,
            'planned_duration': 30,
            'bpm': 120,
            'vocal_gender': 'female',
            'duration_reason': 'Recording fixture only, not an actual model plan',
            'genre': allowed_genres(request)[0],
            'caption': 'Live input check',
            'cover_prompt': 'Live input check',
        }

    async def close(self):
        return None


async def wait_for(predicate):
    async with asyncio.timeout(180):
        while not predicate():
            await asyncio.sleep(0.05)


async def main():
    with tempfile.TemporaryDirectory(prefix='genre-radio-live-input-') as directory:
        pipeline = RecordingPipeline()
        engine = RadioEngine(directory, pipeline, buffer_size=5)
        engine._update_live_provider()
        await engine.initialize()
        try:
            engine.start()
            await wait_for(lambda: engine.error is not None or (engine.current is not None and len(engine.queue) == 5))
            if engine.error:
                raise RuntimeError(engine.error)
            tracks = [engine.current, *engine.queue]
            if len(pipeline.requests) != 6 or len(tracks) != 6:
                raise RuntimeError('Expected one current and five queued provider inputs')
            identities = {song_key(track) for track in tracks}
            hashes = {track['lyrics_hash'] for track in tracks}
            if len(identities) != 6 or len(hashes) != 6:
                raise RuntimeError('Provider returned a duplicate song identity or lyric text')
            result = []
            for request, track in zip(pipeline.requests, tracks):
                saved = engine.lyrics[track['lyrics_id']]
                required = ('title', 'artist', 'text', 'source_url', 'source_name', 'original_genre')
                if any(field not in saved for field in required):
                    raise RuntimeError('Saved provider record is missing a required field')
                if any(not saved[field].strip() for field in ('title', 'artist', 'text', 'source_url', 'source_name')):
                    raise RuntimeError('Saved provider record contains an empty required value')
                if request['lyrics'] != saved['text']:
                    raise RuntimeError('Engine did not map saved text to generation lyrics')
                result.append({
                    'title': saved['title'],
                    'artist': saved['artist'],
                    'source_url': saved['source_url'],
                    'source_name': saved['source_name'],
                    'original_genre': saved['original_genre'],
                    'lyrics_characters': len(saved['text']),
                    'pipeline_request_id': request['id'],
                    'track_position': 'current' if track is engine.current else 'queued',
                })
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            await engine.close()


if __name__ == '__main__':
    asyncio.run(main())
