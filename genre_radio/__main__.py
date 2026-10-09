import argparse
import json
import os
from pathlib import Path

from aiohttp import web

from .engine import RadioEngine
from .models import ModelPipeline
from .lyrics_sources import get_auto_provider
from .server import create_app


def main():
    parser = argparse.ArgumentParser(description='Personal local genre radio')
    parser.add_argument('--config', default=os.environ.get('GENRE_RADIO_CONFIG', str(Path.home() / '.config/genre-radio/config.json')))
    parser.add_argument('--port', type=int, default=3210)
    args = parser.parse_args()
    config_path = Path(args.config).expanduser()
    if not config_path.is_file():
        parser.error('Config missing. Copy config.example.json to your external config path and set the local model paths.')
    config = json.loads(config_path.read_text())
    root = Path(config.get('data_dir', '~/Music/GenreRadio')).expanduser()
    pipeline = ModelPipeline(config)
    engine = RadioEngine(root, pipeline, buffer_size=config.get('buffer_size', 5),
                         provider_factory=get_auto_provider(config.get('lyrics_provider', 'genius')))
    web.run_app(create_app(engine), host='127.0.0.1', port=args.port, print=lambda message: print(message, flush=True))


if __name__ == '__main__':
    main()
