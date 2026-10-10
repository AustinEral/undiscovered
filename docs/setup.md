# Set up your station

## Model files

Use a ComfyUI build that runs the native Krea 2 Turbo and MiniMax Music 3 example workflows. Undiscovered checks its workflow inputs against the server's `/object_info` before generation, so missing nodes or model choices produce an error rather than a guessed substitution.

Download the files from [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2) and [Comfy-Org/MiniMax-Music-3](https://huggingface.co/Comfy-Org/MiniMax-Music-3). The defaults are:

| Purpose | Comfy model directory | Filename |
| --- | --- | --- |
| Cover model | `diffusion_models` | `krea2_turbo_bf16.safetensors` |
| Cover text encoder | `text_encoders` | `qwen3vl_4b_bf16.safetensors` |
| Cover VAE | `vae` | `qwen_image_vae.safetensors` |
| Music model | `diffusion_models` | `minimax_music3_dit_fp16.safetensors` |
| Music text encoder | `text_encoders` | `minimax_music3_text_encoder_pruned_int8_convrot.safetensors` |
| Music VAE | `vae` | `minimax_music3_dav.safetensors` |

Different quantizations of the same supported model can use the `models` overrides below. Enter the exact loader name offered by Comfy, including a subdirectory if one appears in its selector. Changing a filename does not turn the workflow into an adapter for an unrelated model architecture.

Required specialized nodes include `MiniMaxMusic3TextEncode`, `EmptyMiniMaxMusic3LatentAudio`, `VAEDecodeAudio`, and `SaveAudioAdvanced`. The cover workflow uses `CLIPLoader` with type `krea2`; music uses type `minimax`.

## External configuration

Copy `config.example.json` outside the checkout. Pass it with `--config`, or set `GENRE_RADIO_CONFIG`. For compatibility, the fallback config path is `~/.config/genre-radio/config.json`.

| Setting | Meaning |
| --- | --- |
| `data_dir` | External directory for the library, queue, saved lyrics, and generated media. Defaults to `~/Music/GenreRadio`. |
| `comfy_url` | ComfyUI API base URL; typically `http://127.0.0.1:8188`. |
| `qwen_model` | Full path to a local Qwen GGUF file. |
| `llama_command` | Array containing the `llama-server` executable and any required launcher arguments. No shell-string expansion. |
| `qwen_port` | Free local port for the app-owned planner, an integer from `1` to `65535`. Defaults to `8192`. |
| `gpu_device` | NVIDIA device index as a string, or full GPU UUID. Defaults to `"0"`. |
| `qwen_context` | Planner context size, including input and output. The example uses `8192`. |
| `qwen_tokens` | Maximum planner response tokens. The example uses `1600`. |
| `lyrics_provider` | Registered Auto-source name. Defaults to `"genius"`. |
| `buffer_size` | Target number of ready upcoming recordings. The example uses `5`. |
| `cover_size` | Square cover dimension, `1024`–`2048` in multiples of `16`; default `1536`. |
| `models` | Optional object overriding any of the six model filenames. |

The model override keys are `cover_model`, `cover_clip`, `cover_vae`, `music_model`, `music_clip`, and `music_vae`. Unspecified entries keep their defaults.

For example, to use the upstream FP8 Krea Turbo files:

```json
{
  "models": {
    "cover_model": "krea2_turbo_fp8_scaled.safetensors",
    "cover_clip": "qwen3vl_4b_fp8_scaled.safetensors"
  }
}
```

Merge that object into your config rather than replacing the required planner settings.

## GPU behavior

The planner, cover, and music stages run sequentially. The app waits for the configured Comfy queue to become idle and asks Comfy to release cached models before starting its owned planner. It stops only the planner subprocess it created.

`gpu_device` selects the NVIDIA device checked by `nvidia-smi` and exposed to the planner through `CUDA_VISIBLE_DEVICES`. Prefer a full GPU UUID on multi-GPU machines to avoid device-index ambiguity; list UUIDs with `nvidia-smi --query-gpu=index,uuid --format=csv`. This setting does **not** select the device used by an independently running ComfyUI process; configure Comfy separately to match your setup.

Planner admission requires the GGUF file size rounded up to MiB plus 6,144 MiB of free memory. This is an admission estimate, not a whole-pipeline VRAM minimum or a reservation. Cover and music memory depend on their models, precision, and duration. No fixed minimum is claimed for every configuration.

## Keeping up with playback

The five-track queue is a target, not a startup guarantee. Generation must produce audio faster than it is consumed to maintain continuous playback. A full buffer absorbs occasional slow songs; it cannot compensate indefinitely for a backend that runs slower than real time.

Keep ComfyUI's CUDA-graph acceleration enabled for MiniMax Music 3. Its autoregressive model generates many frames per second of audio, so graph replay matters even on a large GPU. Flags such as `--disable-cuda-graphs`, `--disable-dynamic-vram`, and, in newer Comfy builds, `--disable-comfy-compiler` can disable this fast path. A graph that passes API validation can still run too slowly for radio playback.

If a shared Comfy server needs those flags for unrelated work, run a separate compatible Comfy backend on another loopback port and set `comfy_url` to that address. Keep its user, output, and temporary directories separate; existing model files can be shared. Do not interrupt another service's queue or disable its required settings. Avoid generating on both backends at once when they share a GPU.

To check throughput, compare the sum of a recording's manifest `timings` with its measured `duration`, accounting separately for any source-acquisition or queue wait outside those stages. For sustained playback, the average complete generation time must be lower than the audio duration. Check more than the model progress percentage, which is local to a model step rather than an end-to-end ETA. First-use initialization can be slower than later runs.

## Data and restart behavior

Each recording has its own directory with audio, cover, manifest, and generation diagnostics. The app never automatically deletes recordings. Keep `data_dir` outside the source checkout and back it up if you want to retain your station.

On restart the server restores the library and ready queue without starting GPU work. Select **Start generation** when ready. A service manager is optional; run the same command from the checkout root with your environment's Python executable. No machine-specific service file is required.

## Phone access

The backend defaults to loopback. For direct private-network access, bind it to a specific VPN interface IP. With Tailscale connected on both devices:

```bash
python -m genre_radio \
  --config "$HOME/.config/undiscovered/config.json" \
  --host "$(tailscale ip -4)" \
  --port 3211
```

Open the `http://IP:3211/radio` link printed at startup on your phone. This uses Tailscale's encrypted network directly; it does not need an HTTPS Serve rule or administrator access. Choose another free port if needed. Only the selected interface is bound, and the app accepts that Host while retaining Origin checks. Wildcard and multicast bind addresses are rejected.

The app has no standalone login. Bind only to an interface reachable by people you trust, and use your VPN access controls. Do not expose this listener to the public internet.

Alternatively, use the [DSH bridge](dsh.md) through an existing private DSH origin, or a private authenticated reverse proxy configured to forward the upstream Host and Origin consistently to the loopback backend. Do not disable the Host/Origin checks to make a proxy work.

Mobile browsers can require a Play tap after a long generation. Keep the page open for reliable handoff; uninterrupted background and lock-screen playback depends on the browser.

## Troubleshooting

- **Missing config:** pass the external file with `--config`.
- **Cannot connect to Comfy:** verify `comfy_url` and that the server is running. Undiscovered does not launch ComfyUI for you.
- **Missing node/model choice:** run the matching upstream workflow in Comfy first, then check configured filenames.
- **Planner port occupied:** choose a free `qwen_port`. The app will not use or stop the process already there.
- **GPU busy:** pause other work yourself or select the intended device. Undiscovered does not stop unrelated processes.
- **Planner output incomplete:** inspect that job's diagnostics; increase `qwen_tokens` or `qwen_context` when the respective budget is insufficient.
- **Source unavailable:** retry, use a saved input, or paste lyrics. When usable saved inputs exist, Auto rotates them while waiting before another source fetch.
