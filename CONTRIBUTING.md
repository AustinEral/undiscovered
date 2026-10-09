# Contributing

Keep Undiscovered small, direct, and fun to use. A change should make the station better without turning it into a service framework.

## Local checks

Create an external Python environment as described in the README, then run from the checkout root:

```bash
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/ui.test.mjs tests/navigation.test.mjs
npm run check:js
```

Most changes need only the relevant tests while you work. Run the full Python suite before sharing a change. Browser tests need Node 22+ and installed Chrome; set `CHROME_BIN` for a different executable location. They use generated fixture media and temporary profiles, not your station library.

## Real model checks are optional

For a model-pipeline change, the isolated check uses original synthetic lyrics and writes into a new external directory:

```bash
python -m scripts.isolated_model_check \
  --config "$HOME/.config/undiscovered/config.json" \
  --output-root "$HOME/Music/UndiscoveredChecks"
```

This uses your real GPU and models. It creates a new result directory and never needs a station's saved songs. Do not run it alongside unrelated GPU work without checking available resources.

The station smoke check is different: it starts generation on a running player and leaves generation enabled to fill the queue:

```bash
python scripts/smoke.py --url http://127.0.0.1:3210/radio --tracks 2
```

Use it only when you intend to run the station. Pause generation in the UI afterward if needed.

## Source and data

- Keep models, environments, private configuration, recordings, and personal run notes outside this repository.
- Use synthetic examples in tests and documentation. Keep paths generic and endpoints local or clearly marked placeholders.
- Do not reset queues, delete recordings, or restart unrelated services to make a test pass.
- Preserve source attribution and exact lyrics through the generation pipeline.
- Keep provider additions behind the [lyrics-provider interface](docs/lyrics-providers.md).
- Add only configuration and abstractions that have a concrete consumer.

## Pull requests

Describe the behavior changed and which tests you ran. For a UI change, include a screenshot using synthetic data. Do not include credentials, real config, private hostnames, generated libraries, or diagnostic files from someone else's machine.
