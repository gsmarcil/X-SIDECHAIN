import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from x_sidechain.http import ProviderHTTPError, post_json, scrub

SECRET = "sk-live-SECRET-0123456789"


class _Redirector(BaseHTTPRequestHandler):
    """A provider that answers a call by pointing somewhere else."""

    code = 302
    harvest_port = 0
    received: dict = {}

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(self.code)
        self.send_header("Location", f"http://127.0.0.1:{self.harvest_port}/v1/taken")
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


class _Harvester(BaseHTTPRequestHandler):
    received: dict = {}

    def _record(self) -> None:
        for name in ("Authorization", "x-api-key", "X-Company-Key"):
            if self.headers.get(name):
                _Harvester.received[name] = self.headers[name]
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _record
    do_POST = _record

    def log_message(self, *args) -> None:
        pass


class RedirectTests(unittest.TestCase):
    def setUp(self) -> None:
        _Harvester.received = {}
        self.harvester = HTTPServer(("127.0.0.1", 0), _Harvester)
        self.harvest_port = self.harvester.server_address[1]
        _Redirector.harvest_port = self.harvest_port
        self.redirector = HTTPServer(("127.0.0.1", 0), _Redirector)
        for server in (self.harvester, self.redirector):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
        self.url = f"http://127.0.0.1:{self.redirector.server_address[1]}/v1/chat"

    def test_a_redirect_never_carries_the_credential_to_another_host(self) -> None:
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                _Redirector.code = code
                _Harvester.received = {}
                with self.assertRaises(ProviderHTTPError) as caught:
                    post_json(
                        self.url,
                        {"Authorization": f"Bearer {SECRET}", "x-api-key": SECRET},
                        {"messages": []},
                        timeout=5,
                        attempts=1,
                    )
                self.assertEqual(_Harvester.received, {}, "the other host received a credential")
                self.assertEqual(caught.exception.status, code)

    def test_the_refusal_says_what_to_do_about_it(self) -> None:
        _Redirector.code = 302
        with self.assertRaises(ProviderHTTPError) as caught:
            post_json(self.url, {"Authorization": f"Bearer {SECRET}"},
                      {"messages": []}, timeout=5, attempts=1)
        self.assertIn("base_url", str(caught.exception))


class ScrubTests(unittest.TestCase):
    def test_a_configured_header_name_is_redacted_like_a_known_one(self) -> None:
        # Any header may be the one carrying a key, so the value is what matters.
        headers = {"X-Company-Key": "cmp-live-0123456789abcdef", "Content-Type": "application/json"}
        body = 'echo {"X-Company-Key": "cmp-live-0123456789abcdef"}'
        cleaned = scrub(body, headers)
        self.assertNotIn("cmp-live-0123456789abcdef", cleaned)
        self.assertIn("[REDACTED]", cleaned)

    def test_headers_that_carry_nothing_secret_survive(self) -> None:
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "anthropic-version": "2023-06-01", "User-Agent": "x-sidechain"}
        body = "content-type application/json, anthropic-version 2023-06-01, agent x-sidechain"
        self.assertEqual(scrub(body, headers), body)

    def test_a_short_value_is_not_treated_as_a_secret(self) -> None:
        # Redacting every short header value would gut the diagnostics.
        self.assertEqual(scrub("region eu-1", {"X-Region": "eu-1"}), "region eu-1")


if __name__ == "__main__":
    unittest.main()
