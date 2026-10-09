# Architecture

One aiohttp backend. One generation worker. One plain browser player.

## Data flow

1. The worker captures the next job's genre and source settings.
2. A lyrics provider supplies an attributed input, or the user selects saved/manual lyrics.
3. Qwen chooses a genre in Auto mode and writes a production plan for the complete lyrics. A custom genre bypasses genre selection, not planning.
4. Krea generates the cover. MiniMax generates the audio from the caption and unchanged lyrics.
5. Both artifacts must exist before the worker publishes the recording to the ready queue.
6. The browser streams the current track while the worker fills the upcoming buffer.

Saved lyrics can rotate into new recordings, each with a fresh ID, seed, plan, cover, and audio. Old audio is never a model input. Duplicate recording IDs are excluded from the queue; an empty queue waits rather than looping a finished track.

## Responsibilities

- `engine.py`: queue, preferences, source acquisition, persistence, and worker lifecycle.
- `lyrics_sources.py` and provider modules: source contracts, acquisition, and source attribution.
- `planning.py` and `genres.py`: genre profiles, model requests, and plan validation.
- `models.py` and `gpu.py`: model workflow graphs, owned planner process, GPU admission, and artifact checks.
- `server.py`: loopback HTTP API, same-origin boundary, events, and media ranges.
- `static/`: UI, playback, starred library, and device-local transport state.
- `dsh-undiscovered.mjs`: optional page and API bridge for an existing DSH server.

## Invariants

- Playback and generation have separate controls. Closing a tab does not stop the backend worker.
- Settings apply to future jobs; they never discard queued or in-flight work.
- Generation pause stops starting new model jobs. A model job already running may finish and enter the queue.
- Source text is model input, not planner instructions. Lyrics are never silently shortened or rewritten.
- The planner emits a bounded duration and an explicit fit verdict. A negative verdict stops before media generation.
- Text validation remains defensive. The duration explanation's 1,000-character bound is also enforced in the planner schema. Larger prose bounds stay in Python because large grammar repetitions can exceed llama.cpp limits.
- Finished audio is neither padded nor trimmed to match planned duration.
- Shared Comfy queues are allowed to finish; unrelated model processes are not stopped.
- Ready recordings and saved inputs survive restart. Restart does not start GPU work.
- Media URLs use safe recording IDs, not arbitrary filesystem paths. User and model text is rendered with `textContent`.

## API

The standalone API is relative to `/radio`; the optional DSH bridge maps it under `/api/genre-radio`.

| Route | Purpose |
| --- | --- |
| `GET /state` | Station state, current track, queue, preferences, progress, and failures. |
| `GET /events` | Server-sent state snapshots with instance ID and version. |
| `POST /start`, `POST /stop` | Start or pause generation, independently of browser audio. |
| `POST /preferences` | Save future genre/source settings. |
| `GET /lyrics`, `POST /lyrics` | Retrieve or save a lyric input. |
| `POST /advance` | Advance to the next ready recording. |
| `GET /media` | Audio or cover by recording ID; supports byte ranges. |
| `GET /favorites`, `POST /favorites` | Persistent starred-recording library. |
| `GET /track` | Recording details and exact stored prompts. |
| `POST /generation/next` | Skip the latest failed input and request another one. |

Failures remain visible and actionable. The app does not create an unbounded automatic retry loop. Starred-library playback is local to the browser and does not rearrange the station queue.
