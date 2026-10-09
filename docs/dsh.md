# Optional DSH bridge

The player runs on its own at `http://127.0.0.1:3210/radio`. If you already run DeepSeek Harness, the included bridge can expose the same player through that server's existing private origin.

Add this entry to your Web profile's `cordis.patch.yml`, replacing the path with your checkout:

```yaml
- insert:
    - id: undiscovered
      name: /ABSOLUTE/PATH/undiscovered/dsh-undiscovered.mjs
      config:
        upstream: http://127.0.0.1:3210
```

Open `/undiscovered` on that DSH origin, with no trailing slash. For example:

```text
https://YOUR-MACHINE.YOUR-TAILNET.ts.net:3080/undiscovered
```

Use your own private hostname. Connect the phone to the same tailnet if you use Tailscale. The bridge needs no separate Tailscale rule when DSH is already reachable there.

The bridge serves the page at `/undiscovered`, retains `/genre-radio` as an alias, and forwards same-origin API and media requests under `/api/genre-radio`. It inherits the DSH login and Host/Origin policy; it does not change authentication or expose a filesystem directory.

The radio backend still runs independently. The bridge does not launch the player, ComfyUI, or model servers. There are no Node package dependencies or browser bundles for this entry.
