import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from genre_radio.__main__ import main
from genre_radio.engine import RadioEngine
from genre_radio.server import bind_address, create_app
from test_engine import FakePipeline


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    def test_specific_bind_addresses(self):
        for address in ('127.0.0.1', '192.0.2.10', '::1', '2001:db8::10'):
            self.assertEqual(bind_address(address), address)
        for address in ('0.0.0.0', '::', '224.0.0.1', 'ff02::1', 'example.com', '*'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                bind_address(address)

    async def test_explicit_interface_preserves_host_and_origin_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            engine = RadioEngine(root, FakePipeline())
            client = TestClient(TestServer(create_app(engine, host='192.0.2.10')))
            await client.start_server()
            try:
                authority = '192.0.2.10:3211'
                response = await client.get('/radio/state', headers={'Host': authority})
                self.assertEqual(response.status, 200)
                self.assertFalse((await response.json())['running'])
                response = await client.post('/radio/preferences', json={'genre': 'Ambient'},
                                             headers={'Host': authority, 'Origin': 'http://' + authority})
                self.assertEqual(response.status, 200)
                for headers in ({'Host': 'evil.example'},
                                {'Host': authority, 'Origin': 'https://evil.example'},
                                {'Host': authority, 'Origin': 'http://192.0.2.10:9999'}):
                    response = await client.post('/radio/preferences', json={'genre': 'Trap'}, headers=headers)
                    self.assertEqual(response.status, 403)
                self.assertEqual(engine.preferences['genre'], 'Ambient')
            finally:
                await client.close()

    def test_cli_prints_player_link_for_selected_interface(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / 'config.json'
            config.write_text(json.dumps({'data_dir': root}))
            with patch('sys.argv', ['radio', '--config', str(config), '--host', '192.0.2.10', '--port', '3211']), \
                 patch('genre_radio.__main__.ModelPipeline'), patch('genre_radio.__main__.RadioEngine'), \
                 patch('genre_radio.__main__.create_app') as app, patch('genre_radio.__main__.web.run_app') as run:
                main()
                self.assertEqual(app.call_args.kwargs['host'], '192.0.2.10')
                self.assertEqual(run.call_args.kwargs['host'], '192.0.2.10')
                output = StringIO()
                with redirect_stdout(output):
                    run.call_args.kwargs['print']('Started')
                self.assertIn('http://192.0.2.10:3211/radio', output.getvalue())
