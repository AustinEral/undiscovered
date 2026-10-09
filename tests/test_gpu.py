import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from genre_radio.gpu import GpuAdmissionError, require_gpu_memory


class GpuAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def process(self, stdout=b"26624\n", returncode=0):
        process = MagicMock()
        process.returncode = returncode
        process.communicate = AsyncMock(return_value=(stdout, b""))
        process.wait = AsyncMock(return_value=returncode)
        return process

    async def test_exact_threshold_and_above_are_admitted(self):
        for free_mib in (26624, 26625):
            with self.subTest(free_mib=free_mib):
                process = self.process(f"{free_mib}\n".encode())
                with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
                    await require_gpu_memory(26624)
                spawn.assert_awaited_once_with(
                    "nvidia-smi", "--id=0", "--query-gpu=memory.free",
                    "--format=csv,noheader,nounits",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                process.kill.assert_not_called()
                process.wait.assert_awaited_once()

    async def test_below_threshold_fails_without_retry(self):
        process = self.process(b"26623\n")
        with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            with self.assertRaisesRegex(GpuAdmissionError, "GPU busy.*26,623 MiB free.*26,624 MiB required"):
                await require_gpu_memory(26624)
        spawn.assert_awaited_once()
        process.kill.assert_not_called()

    async def test_exact_device_is_queried(self):
        process = self.process()
        with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            await require_gpu_memory(26624, device="GPU-test-device")
        self.assertEqual(spawn.call_args.args[1], "--id=GPU-test-device")

    async def test_invalid_output_fails_closed(self):
        for stdout in (b"", b"\n", b"1\n2\n", b"1\n\n", b"-1\n", b"N/A\n", b"1.5\n", b"+1\n", b"1 MiB\n", b"\xff\n"):
            with self.subTest(stdout=stdout):
                process = self.process(stdout)
                with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
                    with self.assertRaisesRegex(GpuAdmissionError, "Cannot verify GPU memory.*invalid"):
                        await require_gpu_memory(0)
                process.wait.assert_awaited_once()

    async def test_zero_is_valid_but_insufficient_for_positive_requirement(self):
        for required_mib in (0, 1):
            with self.subTest(required_mib=required_mib):
                process = self.process(b"0\n")
                with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
                    if required_mib:
                        with self.assertRaisesRegex(GpuAdmissionError, "GPU busy"):
                            await require_gpu_memory(required_mib)
                    else:
                        await require_gpu_memory(required_mib)

    async def test_nonzero_exit_fails_even_with_enough_memory(self):
        process = self.process(returncode=1)
        with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            with self.assertRaisesRegex(GpuAdmissionError, "Cannot verify GPU memory.*failed"):
                await require_gpu_memory(26624)
        process.wait.assert_awaited_once()

    async def test_missing_executable_fails_closed(self):
        with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError)):
            with self.assertRaisesRegex(GpuAdmissionError, "Cannot verify GPU memory.*could not run"):
                await require_gpu_memory(26624)

    async def test_timeout_kills_and_reaps_only_owned_probe(self):
        process = self.process(returncode=None)
        process.communicate.side_effect = TimeoutError
        timeout = asyncio.timeout
        with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            with patch("genre_radio.gpu.asyncio.timeout", wraps=timeout) as deadline:
                with self.assertRaisesRegex(GpuAdmissionError, "Cannot verify GPU memory.*2 seconds"):
                    await require_gpu_memory(26624)
        deadline.assert_called_once_with(2)
        spawn.assert_awaited_once()
        process.kill.assert_called_once_with()
        process.wait.assert_awaited_once_with()

    async def test_cancellation_propagates_and_reaps_owned_probe(self):
        process = self.process(returncode=None)
        started = asyncio.Event()

        async def communicate():
            started.set()
            await asyncio.Future()

        process.communicate.side_effect = communicate
        with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            task = asyncio.create_task(require_gpu_memory(26624))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        spawn.assert_awaited_once()
        process.kill.assert_called_once_with()
        process.wait.assert_awaited_once_with()

    async def test_process_exit_race_still_reaps_probe(self):
        process = self.process(returncode=None)
        process.communicate.side_effect = TimeoutError
        process.kill.side_effect = ProcessLookupError
        with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            with self.assertRaises(GpuAdmissionError):
                await require_gpu_memory(26624)
        process.wait.assert_awaited_once()

    async def test_invalid_required_memory_never_starts_probe(self):
        for required_mib in (-1, 1.5, True, "26624", None):
            with self.subTest(required_mib=required_mib):
                with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock()) as spawn:
                    with self.assertRaises(ValueError):
                        await require_gpu_memory(required_mib)
                spawn.assert_not_called()

    async def test_invalid_device_never_starts_probe(self):
        for device in ("", " ", None, 0):
            with self.subTest(device=device):
                with patch("genre_radio.gpu.asyncio.create_subprocess_exec", AsyncMock()) as spawn:
                    with self.assertRaises(ValueError):
                        await require_gpu_memory(26624, device)
                spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
