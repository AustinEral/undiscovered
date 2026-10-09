# Undiscovered

A local music player, not a platform. Keep one aiohttp backend, one generation worker, and a plain HTML/CSS/JavaScript player.

- Inspect Git status before editing. Keep changes focused.
- Keep runtime data, model weights, environments, and generated media outside this repository.
- Preserve original lyrics, attribution, generated recordings, and queued tracks. Never delete media automatically.
- Keep listening and generation independent. Preference changes apply to future jobs, not ready or active tracks.
- Run the planner, cover model, and music model sequentially. Never stop unrelated model processes or interrupt shared queues.
- Keep source acquisition behind the lyrics-provider interface. Genius is the default adapter; manual input remains supported.
- Keep machine paths and device settings in external configuration. Do not commit private run notes, endpoints, credentials, or user data.
- Use safe media IDs, same-origin APIs, and textContent for user/model text.
- Reuse existing tests. Run the focused Python tests for a change and the full Python suite before release. Run browser tests for UI changes.
- Do not publish, push, or rewrite history without an explicit request.
