import unittest
import unittest.mock

from x_sidechain.http import MAX_ERROR_BYTES, ProviderHTTPError, post_json, scrub


class RetryingTransportTests(unittest.TestCase):
    def test_transient_status_is_retried_until_success(self) -> None:
        attempts = []
        slept = []

        def fake_attempt(url, headers, payload, timeout):
            attempts.append(url)
            if len(attempts) < 3:
                raise ProviderHTTPError("provider returned HTTP 429: slow down", status=429, retryable=True)
            return '{"id": "ok"}'

        with unittest.mock.patch("x_sidechain.http._attempt", side_effect=fake_attempt):
            data = post_json("https://example.invalid/v1/chat", {}, {}, 30, sleep=slept.append)

        self.assertEqual(data, {"id": "ok"})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(slept), 2)
        self.assertLess(slept[0], slept[1])

    def test_client_error_is_not_retried(self) -> None:
        calls = []

        def fake_attempt(url, headers, payload, timeout):
            calls.append(url)
            raise ProviderHTTPError("provider returned HTTP 401: bad key", status=401, retryable=False)

        with unittest.mock.patch("x_sidechain.http._attempt", side_effect=fake_attempt):
            with self.assertRaisesRegex(ProviderHTTPError, "401"):
                post_json("https://example.invalid/v1/chat", {}, {}, 30, sleep=lambda _s: None)

        self.assertEqual(len(calls), 1)

    def test_retries_stop_at_the_attempt_limit(self) -> None:
        def fake_attempt(url, headers, payload, timeout):
            raise ProviderHTTPError("provider connection failed: reset", retryable=True)

        with unittest.mock.patch("x_sidechain.http._attempt", side_effect=fake_attempt):
            with self.assertRaisesRegex(ProviderHTTPError, "connection failed"):
                post_json("https://example.invalid/v1/chat", {}, {}, 30, attempts=2, sleep=lambda _s: None)

    def test_non_json_body_is_rejected(self) -> None:
        with unittest.mock.patch("x_sidechain.http._attempt", return_value="<html>gateway</html>"):
            with self.assertRaisesRegex(ProviderHTTPError, "non-JSON"):
                post_json("https://example.invalid/v1/chat", {}, {}, 30, sleep=lambda _s: None)


    def test_connection_reset_and_early_disconnect_are_retried(self) -> None:
        import http.client

        for error in (
            ConnectionResetError("reset by peer"),
            http.client.RemoteDisconnected("closed without response"),
            http.client.IncompleteRead(b"partial"),
        ):
            with self.subTest(error=type(error).__name__):
                attempts = []

                def fake_urlopen(*_args, _error=error, **_kwargs):
                    attempts.append(1)
                    raise _error

                with unittest.mock.patch("x_sidechain.http._urlopen", side_effect=fake_urlopen):
                    with self.assertRaises(ProviderHTTPError) as caught:
                        post_json(
                            "https://example.invalid/v1/chat", {}, {}, 30, sleep=lambda _s: None
                        )
                # urlopen only wraps what it sees while connecting; these arrive raw
                # from getresponse() and are exactly the transient cases to retry.
                self.assertEqual(len(attempts), 3)
                self.assertTrue(caught.exception.retryable)

    def test_error_body_is_bounded_and_kept_out_of_the_message(self) -> None:
        import urllib.error

        class HugeError(urllib.error.HTTPError):
            code = 500
            headers = None

            def __init__(self) -> None:
                self.read_amount = None

            def read(self, amt=None):
                self.read_amount = amt
                return b"secret-body " * 4096

        error = HugeError()
        with unittest.mock.patch("x_sidechain.http._urlopen", side_effect=error):
            with self.assertRaises(ProviderHTTPError) as caught:
                post_json("https://example.invalid/v1/chat", {}, {}, 30, sleep=lambda _s: None)

        self.assertEqual(error.read_amount, MAX_ERROR_BYTES)
        self.assertEqual(str(caught.exception), "provider returned HTTP 500")
        self.assertNotIn("secret-body", str(caught.exception))
        self.assertIn("secret-body", caught.exception.detail)
        self.assertLessEqual(len(caught.exception.detail), MAX_ERROR_BYTES)


    def test_outgoing_credentials_are_scrubbed_from_an_echoed_error_body(self) -> None:
        import urllib.error

        key = "sk-live-ABCDEF1234567890"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        class EchoError(urllib.error.HTTPError):
            code = 401
            headers = None

            def __init__(self) -> None:
                pass

            def read(self, amt=None):
                return f"rejected Authorization: Bearer {key}".encode()

        with unittest.mock.patch("x_sidechain.http._urlopen", side_effect=EchoError()):
            with self.assertRaises(ProviderHTTPError) as caught:
                post_json(
                    "https://example.invalid/v1/chat", headers, {}, 30, sleep=lambda _s: None
                )

        # A gateway echoing the request must not hand our own key back into a file.
        self.assertNotIn(key, caught.exception.detail)
        self.assertIn("[REDACTED]", caught.exception.detail)

    def test_scrub_removes_token_shapes_it_did_not_send(self) -> None:
        sent = {"Authorization": "Bearer sk-live-ABCDEF1234567890"}

        for original, forbidden in (
            ('{"access_token": "OTHER-TENANT-SECRET-9876"}', "OTHER-TENANT-SECRET-9876"),
            ("downstream used xai-AAAABBBBCCCCDDDD", "xai-AAAABBBBCCCCDDDD"),
            ('{"password":"hunter2-and-more"}', "hunter2-and-more"),
        ):
            with self.subTest(original=original):
                cleaned = scrub(original, sent)
                self.assertNotIn(forbidden, cleaned)
                self.assertIn("[REDACTED]", cleaned)

    def test_scrub_leaves_ordinary_diagnostics_alone(self) -> None:
        text = "model overloaded, retry in 3s; content-type application/json"
        self.assertEqual(scrub(text, {"Content-Type": "application/json"}), text)


if __name__ == "__main__":
    unittest.main()
