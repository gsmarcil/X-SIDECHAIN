import json
import stat
import tempfile
import unittest
from pathlib import Path

from x_sidechain.audit import AuditLog


class AuditLogTests(unittest.TestCase):
    def test_round_trip_and_secret_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = AuditLog("session", Path(directory))
            log.append("started", {"api_key": "never-store-this", "task": "test"})
            log.append("completed", {"ok": True})

            valid, message = AuditLog.verify(log.path)
            self.assertTrue(valid, message)
            contents = log.path.read_text(encoding="utf-8")
            self.assertNotIn("never-store-this", contents)
            self.assertIn("[REDACTED]", contents)

    def test_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = AuditLog("session", Path(directory))
            log.append("started", {"task": "original"})
            record = json.loads(log.path.read_text(encoding="utf-8"))
            record["payload"]["task"] = "changed"
            log.path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            valid, message = AuditLog.verify(log.path)
            self.assertFalse(valid)
            self.assertIn("hash mismatch", message)


    def test_log_is_owner_only_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            log = AuditLog("session", root)
            log.append("started", {"task": "private task text"})

            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(log.path.stat().st_mode), 0o600)

    def test_token_shaped_fields_are_redacted_but_usage_survives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = AuditLog("session", Path(directory))
            log.append(
                "reply",
                {
                    "access_token": "leak-1",
                    "refresh_token": "leak-2",
                    "client_secret": "leak-3",
                    "usage": {"input_tokens": 11, "output_tokens": 3},
                },
            )
            contents = log.path.read_text(encoding="utf-8")

            for secret in ("leak-1", "leak-2", "leak-3"):
                self.assertNotIn(secret, contents)
            self.assertIn('"input_tokens":11', contents.replace(" ", ""))

    def test_reopening_a_log_continues_the_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = AuditLog("session", Path(directory))
            first.append("started", {"task": "one"})
            second = AuditLog("session", Path(directory))
            second.append("continued", {"task": "two"})

            valid, message = AuditLog.verify(first.path)
            self.assertTrue(valid, message)
            self.assertIn("verified 2 events", message)

    def test_broken_log_is_not_extended(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = AuditLog("session", Path(directory))
            log.append("started", {"task": "original"})
            record = json.loads(log.path.read_text(encoding="utf-8"))
            record["payload"]["task"] = "changed"
            log.path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "broken audit log"):
                AuditLog("session", Path(directory))


class CorruptLogTests(unittest.TestCase):
    """verify exists to answer whether a log holds; it must always answer."""

    def _verdict(self, contents: bytes) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt.jsonl"
            path.write_bytes(contents)
            return AuditLog.verify(path)

    def test_every_shape_of_corruption_returns_a_verdict(self) -> None:
        for contents in (
            b'["not", "an", "object"]\n',
            b'"just a string"\n',
            b"42\n",
            b"null\n",
            b"true\n",
            b'{"sequence": 0}\n',
            b"not json at all\n",
            b"\xff\xfe\x00binary\n",
            b'{"event_hash": "x", "sequence": 5, "previous_hash": "y"}\n',
        ):
            with self.subTest(contents=contents[:24]):
                ok, message = self._verdict(contents)
                self.assertFalse(ok)
                self.assertTrue(message, "a refusal must say why")

    def test_a_missing_file_is_reported_not_raised(self) -> None:
        ok, message = AuditLog.verify(Path("/nonexistent/audit.jsonl"))
        self.assertFalse(ok)
        self.assertIn("invalid audit log", message)


if __name__ == "__main__":
    unittest.main()

