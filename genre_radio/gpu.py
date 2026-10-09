import asyncio
import contextlib
import re


class GpuAdmissionError(RuntimeError):
    pass


async def require_gpu_memory(required_mib, device="0"):
    if type(required_mib) is not int or required_mib < 0:
        raise ValueError("Required GPU memory must be a nonnegative integer in MiB.")
    if not isinstance(device, str) or not device.strip():
        raise ValueError("GPU device must be a nonempty identifier.")
    process = None
    try:
        async with asyncio.timeout(2):
            process = await asyncio.create_subprocess_exec(
                "nvidia-smi", f"--id={device}", "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
        if process.returncode != 0:
            raise GpuAdmissionError(
                "Cannot verify GPU memory: nvidia-smi failed. No model started. "
                "Check the NVIDIA driver, then press Start to retry."
            )
        rows = stdout.splitlines()
        if len(rows) != 1 or re.fullmatch(rb"[0-9]+", rows[0].strip()) is None:
            raise GpuAdmissionError(
                "Cannot verify GPU memory: nvidia-smi returned invalid free-memory data. "
                "No model started. Check the GPU device, then press Start to retry."
            )
        free_mib = int(rows[0].strip())
        if free_mib < required_mib:
            raise GpuAdmissionError(
                f"GPU busy: device {device} has {free_mib:,} MiB free; "
                f"{required_mib:,} MiB required. No model started and no other "
                "service was stopped. Free GPU memory, then press Start to retry."
            )
    except TimeoutError as error:
        raise GpuAdmissionError(
            "Cannot verify GPU memory: nvidia-smi exceeded 2 seconds. "
            "No model started. Check the NVIDIA driver, then press Start to retry."
        ) from error
    except OSError as error:
        raise GpuAdmissionError(
            "Cannot verify GPU memory: nvidia-smi could not run. "
            "No model started. Check nvidia-smi and the NVIDIA driver, "
            "then press Start to retry."
        ) from error
    finally:
        if process is not None:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
