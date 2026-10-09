import argparse
import asyncio
import json

import aiohttp


async def run(base, count):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session:
        async with session.get(base + '/events') as events:
            async with session.post(base + '/start') as response:
                response.raise_for_status()
                print('Radio started.', flush=True)
            last = None
            async with asyncio.timeout(1800):
                async for raw in events.content:
                    if not raw.startswith(b'data: '):
                        continue
                    state = json.loads(raw[6:])
                    job = state.get('generating') or {}
                    status = (job.get('id'), job.get('stage'), len(state.get('queue', [])), bool(state.get('current')))
                    if status != last:
                        print(json.dumps({'stage': job.get('stage'), 'genre': job.get('genre'), 'current_ready': bool(state.get('current')), 'buffered': len(state.get('queue', []))}), flush=True)
                        last = status
                    if state.get('error'):
                        raise RuntimeError(state['error'])
                    ready = ([state['current']] if state.get('current') else []) + state.get('queue', [])
                    if len(ready) >= count:
                        for track in ready[:count]:
                            for field in ('audio_url', 'cover_url'):
                                async with session.get(base + '/' + track[field]) as media:
                                    media.raise_for_status()
                                    data = await media.read()
                                    if not data:
                                        raise RuntimeError('Generated media is empty')
                                    if field == 'cover_url' and not data.startswith(b'\x89PNG\r\n\x1a\n'):
                                        raise RuntimeError('Generated cover is not a PNG')
                        print('READY ' + json.dumps(ready[:count]), flush=True)
                        return
            raise RuntimeError('No ready tracks before the event stream ended')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:3210/radio')
    parser.add_argument('--tracks', type=int, default=2)
    args = parser.parse_args()
    asyncio.run(run(args.url.rstrip('/'), args.tracks))
