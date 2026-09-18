import json
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


if __name__ == "__main__":
    unittest.main()

