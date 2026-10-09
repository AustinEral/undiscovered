import argparse
import json
import os
from pathlib import Path

from aiohttp import web

from .engine import RadioEngine
from .models import ModelPipeline
from .lyrics_sources import get_auto_provider
from .server import bind_address, create_app


def main():
    parser = argparse.ArgumentParser(description='Personal local genre radio')
    parser.add_argument('--config', default=os.environ.get('GENRE_RADIO_CONFIG', str(Path.home() / '.config/genre-radio/config.json')))
    parser.add_argument('--port', type=int, default=3210)
    parser.add_argument('--host', type=bind_address, default='127.0.0.1',
                        help='Specific interface IP; defaults to loopback. Use a private VPN IP for phone access.')
    args = parser.parse_args()
    config_path = Path(args.config).expanduser()
    if not config_path.is_file():
        parser.error('Config missing. Copy config.example.json to your external config path and set the local model paths.')
    config = json.loads(config_path.read_text())
    root = Path(config.get('data_dir', '~/Music/GenreRadio')).expanduser()
    pipeline = ModelPipeline(config)
    engine = RadioEngine(root, pipeline, buffer_size=config.get('buffer_size', 5),
                         provider_factory=get_auto_provider(config.get('lyrics_provider', 'genius')))
    authority = f'[{args.host}]' if ':' in args.host else args.host
    player_url = f'http://{authority}:{args.port}/radio'
    web.run_app(create_app(engine, host=args.host), host=args.host, port=args.port,
                print=lambda message: print(f'{message}\nOpen Undiscovered: {player_url}', flush=True))


if __name__ == '__main__':
    main()
