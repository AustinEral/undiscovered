# Undiscovered

**A radio station that makes the next track while you listen.**

Pick a genre. Let the station roam. Turn familiar words into a different sound.

Undiscovered runs a local production loop: Qwen plans the track, Krea paints the cover, and MiniMax Music 3 makes the music. A dark, lightweight player keeps five fresh tracks ahead, with starred recordings, fullscreen artwork, and the exact prompts behind each song.

No accounts. No cloud database. No frontend build. Your models, your station.

## What stays in the mix

- **Auto or your own direction.** Choose from 72 genres, or type a custom style.
- **Genius included.** Auto discovers chart songs and their lyrics, then rotates saved inputs into fresh interpretations. Paste your own lyrics or plug in another source.
- **Fresh recordings, not replay loops.** Reused lyrics get a new plan, seed, cover, and audio. An empty queue waits for a new track.
- **Listening and generation are separate.** Pause playback without stopping production. Pause production without losing the queue.
- **Keep your favorites.** Star a recording and play it from the local library without changing the station.
- **No hidden prompt rewriting.** Lyrics pass unchanged to the music model. Inspect the music and cover prompts in the player.

## Bring the studio

The supported starting point is **Linux with an NVIDIA GPU**. Python 3.11+, `nvidia-smi`, FFmpeg with MP3 support, and `ffprobe` must be available. Generation is GPU-heavy; memory needs depend on the model files and song length. CPU-only and Apple/AMD GPU setups are not supported by the current admission check.

You also need:

1. **ComfyUI** running locally with native **Krea 2 Turbo** and **MiniMax Music 3** support. Follow the upstream [Krea setup](https://docs.comfy.org/tutorials/image/krea/krea-2) and [MiniMax setup](https://docs.comfy.org/tutorials/audio/minimax/minimax-music-3), and confirm their example workflows run.
2. **llama.cpp** with a `llama-server` build supporting Qwen 3.8, chat templates, and JSON-schema responses. See the [server documentation](https://github.com/ggml-org/llama.cpp/tree/master/tools/server). Undiscovered launches and stops its own planner process; don't start a separate server on its configured port.
3. A local **Qwen 3.8 GGUF**. [Qwen3.8-27B GGUF files](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF) are one available source. Set the actual file path in your configuration.

The default Comfy model filenames and configuration options are in [Setup](docs/setup.md). Model weights and runtime data are not bundled.

## Tune in

Run these commands from the source checkout. Keep the environment and configuration outside it:

```bash
python3 -m venv "$HOME/.venvs/undiscovered"
source "$HOME/.venvs/undiscovered/bin/activate"
python -m pip install -r requirements.txt
mkdir -p "$HOME/.config/undiscovered"
cp config.example.json "$HOME/.config/undiscovered/config.json"
```

Edit that config: set `qwen_model`, `llama_command`, and `comfy_url` to your installation, and select `gpu_device` if needed. Then start the player:

```bash
python -m genre_radio --config "$HOME/.config/undiscovered/config.json" --port 3210
```

Open **http://127.0.0.1:3210/radio**. Startup prints the full player link for the selected port. If port 3210 is occupied, use `--port 3211`. Select **Start generation**, then **Play** when the first recording is ready.

For phone access, use `--host` with your private VPN interface IP; see [Phone access](docs/setup.md#phone-access). Loopback remains the default.

The Python module is named `genre_radio`; the app is Undiscovered. Run it from the checkout root so Python can find the module and its static assets.

### While you listen

- Open **Controls** to change genre or source. Changes save automatically for future jobs; ready tracks and the active job keep their settings.
- **View prompts** belongs to the recording currently playing, not the next track being generated.
- The optional manual form queues one supplied song, then returns the source to Auto after it completes.
- **Skip** moves to a ready track. **Starred** plays your saved recordings independently of the radio queue.
- Restart restores recordings and the queue, but leaves generation paused until you start it.
- The planned duration is a model budget, not an exact edit point. The player also shows measured audio length.

## Make it yours

[Write a lyrics provider](docs/lyrics-providers.md) to connect another source. Genius and manual input remain built in; the player and model pipeline do not need to change for every source.

[Setup](docs/setup.md) covers GPU selection, model filenames, data storage, and private phone access. The optional [DSH bridge](docs/dsh.md) serves the same player through an existing DSH installation; DSH is not required for local use.

## Develop

The runtime dependency is `aiohttp`. Tests use Python's standard `unittest`; browser checks use Node 22+ and installed Chrome, without npm packages.

```bash
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/ui.test.mjs tests/navigation.test.mjs
npm run check:js
```

Set `CHROME_BIN` if Chrome is not at `/usr/bin/google-chrome`. The automated suites use temporary data and fake model adapters. Real GPU smoke checks are optional and described in [Contributing](CONTRIBUTING.md).

The architecture is intentionally small: one backend, one worker, plain HTML/CSS/JavaScript. See [Architecture](docs/architecture.md).

## License

[MIT](LICENSE) for the application code. Model weights are separate downloads with their own licenses.
