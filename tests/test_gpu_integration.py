import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from genre_radio.models import ModelPipeline
from test_models import CONFIG


class GpuBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_file_budget_rounds_up_and_adds_headroom_on_gpu_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'fixture.gguf'
            with model.open('wb') as stream:
                stream.truncate(2 * 1024 * 1024 + 1)
            with patch('genre_radio.models.require_gpu_memory', new_callable=AsyncMock) as admission:
                await ModelPipeline(CONFIG)._admit_qwen(model)
            admission.assert_awaited_once_with(6147, device='0')

    async def test_missing_model_never_queries_gpu(self):
        with tempfile.TemporaryDirectory() as directory, patch('genre_radio.models.require_gpu_memory', new_callable=AsyncMock) as admission:
            with self.assertRaises(FileNotFoundError):
                await ModelPipeline(CONFIG)._admit_qwen(Path(directory) / 'missing.gguf')
            admission.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
