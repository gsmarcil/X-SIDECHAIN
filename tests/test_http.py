import unittest
import unittest.mock

from x_sidechain.http import ProviderHTTPError, post_json


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


if __name__ == "__main__":
    unittest.main()
