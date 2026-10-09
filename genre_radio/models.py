import asyncio
import contextlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import uuid

import aiohttp

from .gpu import require_gpu_memory
from .planning import (
    MAX_DURATION_SECONDS, LyricsFitError, QWEN_ALIAS, choice_payload, parse_choice_response,
    parse_plan_response, plan_payload, validate_planning_request,
)


KREA_MODEL = "krea2_turbo_bf16.safetensors"
KREA_CLIP = "qwen3vl_4b_bf16.safetensors"
KREA_VAE = "qwen_image_vae.safetensors"
MUSIC_MODEL = "minimax_music3_dit_fp16.safetensors"
MUSIC_CLIP = "minimax_music3_text_encoder_pruned_int8_convrot.safetensors"
MUSIC_VAE = "minimax_music3_dav.safetensors"
DEFAULT_MODELS = {
    "cover_model": KREA_MODEL, "cover_clip": KREA_CLIP, "cover_vae": KREA_VAE,
    "music_model": MUSIC_MODEL, "music_clip": MUSIC_CLIP, "music_vae": MUSIC_VAE,
}


def resolve_models(overrides):
    if not isinstance(overrides, dict) or overrides.keys() - DEFAULT_MODELS.keys():
        raise ValueError("models must be an object containing only: " + ", ".join(DEFAULT_MODELS))
    for key, value in overrides.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"models.{key} must be a nonempty exact Comfy model name.")
    return {**DEFAULT_MODELS, **overrides}


def validate_cover_size(size):
    if type(size) is not int or not 1024 <= size <= 2048 or size % 16:
        raise ValueError('Cover size must be 1024–2048 pixels in multiples of 16.')
    return size


def build_cover_graph(cover_prompt, seed, prefix, *, size=1536, models=None):
    size = validate_cover_size(size)
    names = resolve_models({} if models is None else models)
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": names["cover_model"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": names["cover_clip"], "type": "krea2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": names["cover_vae"]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": cover_prompt}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "ModelSamplingFlux", "inputs": {"model": ["1", 0], "max_shift": 1.15, "base_shift": 1.15, "width": size, "height": size}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["6", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["7", 0], "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix + "/cover"}},
    }


def build_music_graph(caption, lyrics, duration, seed, prefix, *, models=None):
    if type(duration) is not int or not 1 <= duration <= MAX_DURATION_SECONDS:
        raise ValueError(f"Music duration must be an integer from 1 to {MAX_DURATION_SECONDS} seconds.")
    names = resolve_models({} if models is None else models)
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": names["music_model"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": names["music_clip"], "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": names["music_vae"]}},
        "5": {"class_type": "MiniMaxMusic3TextEncode", "inputs": {"clip": ["2", 0], "caption": caption, "lyrics": lyrics, "seed": seed, "max_duration": duration, "cfg_scale": 1.7, "top_k": 50}},
        "6": {"class_type": "EmptyMiniMaxMusic3LatentAudio", "inputs": {"seconds": ["5", 1], "batch_size": 1}},
        "7": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": seed, "steps": 30, "cfg": 1.7, "sampler_name": "euler", "scheduler": "simple", "positive": ["5", 0], "negative": ["7", 0], "latent_image": ["6", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveAudioAdvanced", "inputs": {"audio": ["9", 0], "filename_prefix": prefix + "/music", "format": "flac"}},
    }


def validate_graph(graph, object_info):
    for node in graph.values():
        name = node["class_type"]
        if name not in object_info:
            raise RuntimeError(f"Configured Comfy server is missing node {name}.")
        schema = object_info[name]["input"]
        fields = {**schema.get("required", {}), **schema.get("optional", {})}
        for key in schema.get("required", {}):
            if key not in node["inputs"]:
                raise RuntimeError(f"Comfy node {name} requires input {key}; its contract changed.")
        for key, value in node["inputs"].items():
            if key not in fields:
                raise RuntimeError(f"Comfy node {name} does not support input {key}.")
            choices = fields[key][0]
            if isinstance(choices, list) and not isinstance(value, list) and value not in choices:
                raise RuntimeError(f"Configured Comfy server does not offer {name}.{key}={value}.")


async def read_json(response, limit=2 * 1024 * 1024):
    data = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        data.extend(chunk)
        if len(data) > limit:
            raise RuntimeError("Model server response exceeded its size limit.")
    if response.status >= 400:
        raise RuntimeError(f"Model server HTTP {response.status}: {data.decode(errors='replace')[:600]}")
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError("Model server returned invalid JSON.") from error


class ModelPipeline:
    def __init__(self, config):
        self.config = config
        self.comfy_url = config["comfy_url"].rstrip("/")
        self.qwen_port = config.get("qwen_port", 8192)
        if type(self.qwen_port) is not int or not 1 <= self.qwen_port <= 65535:
            raise ValueError("qwen_port must be an integer from 1 to 65535.")
        self.qwen_url = f"http://127.0.0.1:{self.qwen_port}"
        self.gpu_device = config.get("gpu_device", "0")
        if not isinstance(self.gpu_device, str) or re.fullmatch(r"[0-9]+|GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", self.gpu_device) is None:
            raise ValueError("gpu_device must be a single NVIDIA index string or full GPU UUID.")
        self.models = resolve_models(config.get("models", {}))
        self._process = None
        self._task = None
        self._closed = False

    async def close(self):
        self._closed = True
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._stop_qwen()

    async def _stop_qwen(self):
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=15)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
        self._process = None

    async def _wait_comfy_idle(self, session, progress):
        try:
            async with asyncio.timeout(1800):
                while True:
                    async with session.get(self.comfy_url + "/queue") as response:
                        queue = await read_json(response)
                    if not queue["queue_running"] and not queue["queue_pending"]:
                        return
                    await progress("waiting", None)
                    await asyncio.sleep(2)
        except TimeoutError as error:
            raise RuntimeError("Timed out waiting for the configured Comfy queue to become idle; retry later. No jobs were interrupted.") from error

    def _check_qwen_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", self.qwen_port))
            except OSError as error:
                raise RuntimeError(f"Qwen port {self.qwen_port} is already occupied; the app will not use or stop that process.") from error

    async def _admit_qwen(self, model):
        required_mib = math.ceil(model.stat().st_size / (1024 * 1024)) + 6144
        await require_gpu_memory(required_mib, device=self.gpu_device)

    async def _write_prompts(self, session, request, directory, progress):
        validate_planning_request(request)
        model = Path(self.config["qwen_model"]).expanduser()
        if not model.is_file():
            raise RuntimeError(f"Qwen model is missing: {model}. Check external config.")
        self._check_qwen_port()
        await self._wait_comfy_idle(session, progress)
        async with session.post(self.comfy_url + "/free", json={"unload_models": True, "free_memory": True}) as response:
            response.raise_for_status()
        await progress("gpu", None)
        await self._admit_qwen(model)
        await progress("qwen", None)
        command = [*self.config["llama_command"], "--model", str(model), "--host", "127.0.0.1", "--port", str(self.qwen_port), "--jinja", "--ctx-size", str(self.config["qwen_context"]), "--no-context-shift", "--n-gpu-layers", "99", "--parallel", "1", "--alias", QWEN_ALIAS]
        try:
            self._check_qwen_port()
            with (directory / "qwen.log").open("xb") as log:
                self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env={**os.environ, "CUDA_VISIBLE_DEVICES": self.gpu_device})
            async with asyncio.timeout(180):
                while True:
                    if self._process.poll() is not None:
                        raise RuntimeError(f"Owned Qwen server exited with code {self._process.returncode}; inspect {directory / 'qwen.log'}.")
                    try:
                        async with session.get(self.qwen_url + "/health", timeout=aiohttp.ClientTimeout(total=2)) as response:
                            if response.status == 200:
                                break
                    except (aiohttp.ClientError, TimeoutError):
                        pass
                    await asyncio.sleep(0.5)
            async with session.get(self.qwen_url + "/v1/models") as response:
                models = await read_json(response, 65536)
            if self._process.poll() is not None or [item["id"] for item in models["data"]] != [QWEN_ALIAS]:
                raise RuntimeError("Qwen server ownership/model check failed; inspect its launch log.")
            selected_genre = request["genre"]
            if selected_genre == "Auto":
                choice = await self._qwen_completion(session, choice_payload(request), directory / "qwen-choice-response.json")
                selected_genre = parse_choice_response(choice, request)
            await progress("planning", None)
            payload = plan_payload(request, selected_genre, self.config["qwen_tokens"])
            raw_response = await self._qwen_completion(session, payload, directory / "qwen-response.json")
            result = parse_plan_response(raw_response, selected_genre, request)
            await progress("planning", 1.0, {key: result[key] for key in ("genre", "planned_duration", "bpm", "vocal_gender")})
            await progress("qwen", 1.0)
            return result
        except TimeoutError as error:
            raise RuntimeError(f"Qwen timed out; inspect {directory / 'qwen.log'}.") from error
        finally:
            await self._stop_qwen()

    async def _qwen_completion(self, session, payload, path):
        try:
            async with session.post(self.qwen_url + "/v1/chat/completions", json=payload, timeout=aiohttp.ClientTimeout(total=180)) as response:
                raw_response = await read_json(response, 65536)
        except RuntimeError as error:
            raise RuntimeError(
                f"Qwen planning request failed: {error}. Full lyrics were not excerpted. "
                "If the context is too small, increase qwen_context in external config or choose another song."
            ) from error
        with path.open("x", encoding="utf-8") as output:
            json.dump(raw_response, output, indent=2)
        return raw_response

    async def _run_graph(self, session, graph, stage, progress):
        await self._wait_comfy_idle(session, progress)
        client_id = str(uuid.uuid4())
        await progress(stage, 0.0)
        async with session.ws_connect(self.comfy_url + "/ws", params={"clientId": client_id}, heartbeat=30, max_msg_size=2 * 1024 * 1024) as websocket:
            async with session.post(self.comfy_url + "/prompt", json={"prompt": graph, "client_id": client_id}) as response:
                submitted = await read_json(response)
            prompt_id = submitted.get("prompt_id")
            if not isinstance(prompt_id, str) or not prompt_id or submitted.get("node_errors"):
                raise RuntimeError(f"Comfy rejected {stage} graph: {str(submitted)[:600]}")
            try:
                async with asyncio.timeout(1800):
                    async for message in websocket:
                        if message.type != aiohttp.WSMsgType.TEXT:
                            if message.type == aiohttp.WSMsgType.ERROR:
                                raise RuntimeError(f"Comfy {stage} WebSocket failed.")
                            continue
                        event = json.loads(message.data)
                        data = event.get("data", {})
                        if data.get("prompt_id") != prompt_id:
                            continue
                        kind = event.get("type")
                        if kind in ("execution_error", "execution_interrupted"):
                            raise RuntimeError(f"Comfy {stage} {kind}: {str(data.get('exception_message', data))[:600]}")
                        if kind == "executing":
                            if data.get("node") is None:
                                break
                            await progress(stage, None)
                        elif kind == "progress" and data.get("max", 0) > 0:
                            await progress(stage, max(0.0, min(1.0, data["value"] / data["max"])))
                    else:
                        raise RuntimeError(f"Comfy disconnected before {stage} completed; outputs may remain on the server.")
            except TimeoutError as error:
                raise RuntimeError(f"Comfy {stage} exceeded 30 minutes; the shared server was not interrupted.") from error
            async with session.get(self.comfy_url + "/history/" + prompt_id) as response:
                history = await read_json(response)
        record = history.get(prompt_id, {})
        status = record.get("status", {})
        if status.get("status_str") != "success" or status.get("completed") is not True:
            raise RuntimeError(f"Comfy {stage} did not record a completed successful result: {str(status)[:600]}")
        outputs = record.get("outputs", {}).get("10", {}).get("images" if stage == "cover" else "audio", [])
        if len(outputs) != 1:
            raise RuntimeError(f"Comfy {stage} did not return exactly one output.")
        await progress(stage, 1.0)
        return outputs[0]

    async def _download(self, session, output, path, limit):
        if output.get("type") != "output" or not isinstance(output.get("filename"), str):
            raise RuntimeError("Comfy returned invalid output metadata.")
        params = {"filename": output["filename"], "subfolder": output.get("subfolder", ""), "type": "output"}
        size = 0
        async with session.get(self.comfy_url + "/view", params=params) as response:
            response.raise_for_status()
            with path.open("xb") as target:
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > limit:
                        raise RuntimeError(f"Comfy output exceeds the download limit for {path.name}.")
                    target.write(chunk)
        if size == 0:
            raise RuntimeError(f"Comfy returned an empty {path.name}.")
        if path.suffix == ".png":
            with path.open("rb") as source:
                if source.read(8) != b"\x89PNG\r\n\x1a\n":
                    raise RuntimeError("Comfy cover is not a PNG image.")

    async def _command(self, *command):
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(120):
                stdout, stderr = await process.communicate()
            if process.returncode:
                raise RuntimeError(f"{command[0]} failed: {stderr.decode(errors='replace')[-600:]}")
            return stdout
        except TimeoutError as error:
            raise RuntimeError(f"{command[0]} exceeded 120 seconds while encoding or probing audio.") from error
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    async def generate(self, request, directory, progress):
        if self._closed:
            raise RuntimeError("Model pipeline is closed.")
        if self._task is not None:
            raise RuntimeError("Only one model generation may run at a time.")
        self._task = asyncio.current_task()
        directory = Path(directory)
        timings = {}
        try:
            prefix = "genre-radio/" + str(uuid.UUID(request["id"]))
            cover_size = validate_cover_size(self.config.get('cover_size', 1536))
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                started = time.monotonic()
                prompts = await self._write_prompts(session, request, directory, progress)
                timings["qwen"] = round(time.monotonic() - started, 3)
                graphs = (
                    ("cover", build_cover_graph(prompts["cover_prompt"], request["seed"], prefix, size=cover_size, models=self.models), directory / "cover.png", 32 * 1024 * 1024),
                    ("music", build_music_graph(prompts["caption"], request["lyrics"], prompts["planned_duration"], request["seed"], prefix, models=self.models), directory / "audio.flac", 256 * 1024 * 1024),
                )
                async with session.get(self.comfy_url + "/object_info") as response:
                    object_info = await read_json(response, 32 * 1024 * 1024)
                for _, graph, _, _ in graphs:
                    validate_graph(graph, object_info)
                for stage, graph, path, limit in graphs:
                    started = time.monotonic()
                    output = await self._run_graph(session, graph, stage, progress)
                    await self._download(session, output, path, limit)
                    timings[stage] = round(time.monotonic() - started, 3)
            started = time.monotonic()
            await progress("encoding", None)
            await self._command("ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(directory / "audio.flac"), "-map_metadata", "-1", "-codec:a", "libmp3lame", "-b:a", "320k", str(directory / "audio.mp3"))
            probe = json.loads(await self._command("ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,codec_type", "-of", "json", str(directory / "audio.mp3")))
            duration = float(probe["format"]["duration"])
            if not math.isfinite(duration) or not 0 < duration <= prompts["planned_duration"] + 2 or not any(stream.get("codec_name") == "mp3" and stream.get("codec_type") == "audio" for stream in probe["streams"]):
                raise RuntimeError("Encoded music has an invalid duration or audio stream.")
            if not (directory / "audio.mp3").is_file() or not (directory / "cover.png").is_file():
                raise RuntimeError("Generation finished without both audio.mp3 and cover.png.")
            timings["encoding"] = round(time.monotonic() - started, 3)
            await progress("encoding", 1.0)
            return {"duration": duration, **prompts, "timings": timings}
        finally:
            await self._stop_qwen()
            self._task = None
