import threading
import unittest
from urllib.request import urlopen

from x_sidechain.ui import build_ui_server


class LocalUITests(unittest.TestCase):
    def test_ui_is_loopback_only_and_sends_security_headers(self) -> None:
        server = build_ui_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            self.assertEqual(host, "127.0.0.1")
            with urlopen(f"http://{host}:{port}/", timeout=2) as response:
                body = response.read().decode("utf-8")
                self.assertIn("X-SIDECHAIN", body)
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_invalid_port_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 0 and 65535"):
            build_ui_server(port=70000)


if __name__ == "__main__":
    unittest.main()
