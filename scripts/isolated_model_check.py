"""Exercise the three local models with original fixture text, never station inputs."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from genre_radio.models import ModelPipeline


FIXTURE_LYRICS = '''[Verse 1]
Morning on the windows, rain along the road
Open up the doorway, let the daylight glow
Footsteps on the pavement, every turn is new
Moving through the morning, finding something true

[Chorus]
Keep a little light on, let the long road wind
Leave a little room for what we have to find
Keep a little light on, let the night go by
We will meet the morning underneath the open sky

[Verse 2]
Shadows on the hillside, clouds across the blue
Every little moment has a different view
Nothing here is borrowed, nothing stays the same
Carry on together through the sun and rain

[Chorus]
Keep a little light on, let the long road wind
Leave a little room for what we have to find
Keep a little light on, let the night go by
We will meet the morning underneath the open sky'''


async def check(config_path, output_root, genre, perspective):
    config = json.loads(Path(config_path).expanduser().read_text())
    identity = str(uuid.uuid4())
    directory = Path(output_root).expanduser() / identity
    directory.mkdir(parents=True, exist_ok=False)
    lyrics = FIXTURE_LYRICS
    if perspective != 'neutral':
        narrator = 'woman' if perspective == 'female' else 'man'
        lyrics = lyrics.replace('Morning on the windows, rain along the road', f'I am the {narrator} with the lamp beside the door')
    request = {
        'id': identity,
        'title': 'Isolated full-song planning check',
        'artist': 'Local test fixture',
        'lyrics': lyrics,
        'genre': genre,
        'genre_choices': ['Bluegrass', 'Detroit techno', 'Roots reggae'],
        'original_genre': 'Hip-hop',
        'original_duration': 60,
        'recent_genres': [],
        'seed': 91234,
    }
    previous = None
    async def progress(stage, fraction, details=None):
        nonlocal previous
        key = (stage, int(fraction * 10) if fraction is not None else None)
        if key != previous or details:
            print(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'stage': stage, 'progress': fraction, 'plan': details}), flush=True)
            previous = key
    print(json.dumps({'output': str(directory), 'scope': 'original vocal fixture; not a station song or acquired lyric input'}), flush=True)
    pipeline = ModelPipeline(config)
    try:
        result = await pipeline.generate(request, directory, progress)
        (directory / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
        if perspective != 'neutral' and result['vocal_gender'] != perspective:
            raise RuntimeError('The vocal casting did not match the explicit fixture narrator.')
        print(json.dumps({'complete': True, 'output': str(directory), 'genre': result['genre'], 'planned_duration': result['planned_duration'], 'bpm': result['bpm'], 'vocal_gender': result['vocal_gender'], 'duration': result['duration'], 'timings': result['timings']}), flush=True)
    finally:
        await pipeline.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--genre', default='Auto')
    parser.add_argument('--vocal-perspective', choices=['neutral', 'female', 'male'], default='neutral')
    args = parser.parse_args()
    asyncio.run(check(args.config, args.output_root, args.genre, args.vocal_perspective))
