import subprocess
import sys
import unittest
from pathlib import Path


class ImportBoundaryTests(unittest.TestCase):
    def test_live_provider_and_engine_import_in_either_order(self):
        root = Path(__file__).resolve().parents[1]
        for modules in (
            ('genre_radio.live_song_provider', 'genre_radio.engine'),
            ('genre_radio.engine', 'genre_radio.live_song_provider'),
        ):
            with self.subTest(modules=modules):
                result = subprocess.run(
                    [sys.executable, '-c', '; '.join(f'import {module}' for module in modules)],
                    cwd=root, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
