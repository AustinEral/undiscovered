# Lyrics providers

Genius is the default automatic lyrics adapter. Auto fetches current chart inputs, prefers fresh songs, and rotates matching saved lyrics into new recordings when fresh acquisition is unavailable. Manual pasted lyrics and one-time saved-song selection work independently of the automatic adapter.

## Select an automatic adapter

The external configuration accepts:

```json
{
  "lyrics_provider": "genius"
}
```

Omitting `lyrics_provider` selects `genius`. The only bundled automatic adapter is Genius. Unknown names fail rather than silently selecting a different source. Configuration selects the adapter when the application starts; genre and manual-song preferences continue to apply to future jobs through the existing player controls.

`lyrics_source` in player state identifies the configured adapter. It is not a writable player preference and is separate from each saved song's `source_name` and `source_url` attribution.

## Adapter contract

`genre_radio.lyrics_sources.AutoLyricsProvider` describes the automatic adapter interface:

- `source_id: str`: a stable source identifier for state, such as `genius.com`.
- `async acquire() -> LiveSong`: return one complete, validated input. `LiveSong` carries `lyrics_id`, `title`, `artist`, unchanged `text`, `source_name`, `source_url`, and optional original-genre/duration metadata.
- `prepare_request(song, *, genre) -> dict`: create a fresh model request containing a new job ID and seed, the supplied genre, the complete unchanged lyrics, and the input identity. Auto genre requests include the configured genre choices. `SongProvider.prepare_request` in `next_song_provider.py` is an existing reusable request builder for the same record fields.
- `accepts_cached(song: dict) -> bool`: identify which validated saved records belong to this adapter's automatic fallback pool. This predicate must not fetch data or mutate the record. Manual selection bypasses this predicate.

Factories receive keyword arguments `lyrics`, `tracks`, `preferences`, and `genre_choices`, matching the existing Genius adapter constructor. `tracks` includes the generated-track ledger and temporarily skipped inputs. The adapter can consult this context to prefer inputs not previously generated or skipped. Factories are recreated when saved lyrics or preferences change. They must not start background work in their constructors; acquisition happens only when awaited.

An in-progress request captures its adapter and genre before acquisition. Changing preferences does not replace the adapter already preparing that request or alter queued recordings. Adapters do not write station state, publish tracks, run models, or manage the generation worker. The engine validates and saves returned lyrics, retains attribution, and owns generation and publication.

## Add another adapter

Implement the interface in a regular Python module and add its factory to the explicit `AUTO_PROVIDERS` dictionary in `lyrics_sources.py`. Its dictionary key becomes the `lyrics_provider` configuration value. There is no package discovery, dynamic module loading, or plugin lifecycle.

For embedding or tests, pass a factory directly without registration:

```python
from genre_radio.engine import RadioEngine

engine = RadioEngine(data_dir, pipeline, provider_factory=MyLyricsProvider)
```

The injected factory has the same constructor and method contract as a registered adapter. It need not inherit from the Genius implementation. `tests/test_lyrics_providers.py` demonstrates a synthetic independent adapter, fresh acquisition, manual selection, attributed cache fallback, and buffer filling.

## Acquisition and rotation

Genius retains its existing chart selection, canonical source links, shared HTML parser, and validated lyric extraction. Its fallback pool recognizes saved Genius attribution names and Genius URLs exactly as before.

When acquisition raises `ValueError`, `aiohttp.ClientError`, or `TimeoutError`, the engine checks the selected adapter's saved pool. With valid cached inputs, it creates new recordings and waits one hour before another acquisition attempt. Without a matching pool, it reports the source failure. Rotation prefers alternatives to current and queued inputs, then least recently generated inputs. A single valid saved song can produce fresh interpretations with new job IDs and seeds. Existing audio is never substituted for generation or used as a model input.

Attribution stays on saved records and remains available to the player. Manual inputs can omit source metadata; imported inputs preserve the adapter's source name and URL.

## Search and URL import

The existing `LyricScraper` interface in `lyrics_sources.py` is separate from automatic discovery. Its `search`, `scrape`, and `supports_url` methods support the engine's search and URL-import methods. Existing Genius, Lyrics.com, and AZLyrics scraper implementations remain available through `get_all_scrapers()` and `get_all_sources()`. They do not automatically become Auto discovery adapters. The player retains its manual lyrics form and saved-input selection.

## Verify

Run the provider integration tests without network requests or model generation:

```bash
python -m unittest discover -s tests -p 'test_lyrics_providers.py' -v
```

The existing Genius parsing, live-provider, cache-provider, rotation, engine, worker, and HTTP tests also cover the unchanged default and manual paths. All test adapters and lyric fixtures are synthetic.
