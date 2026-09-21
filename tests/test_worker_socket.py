"""Exercise the real Unix socket, process lifecycle and singleton without target traffic."""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from cpk_worker import request

SERVER = """
import sys
from pathlib import Path
import cpk_session as cs
import cpk_worker as worker
class IdleEngine:
    def idle(self): pass
    def close(self): pass
def run(query, trace, engine, publish, **kwargs):
    publish("autocomplete", "ok", {"ok": True, "items": ["first"]})
    publish("search", "ok", {"outcome": "ok", "items": []})
    publish("sourcing", "load_error", {})
    return {}
original = worker.JobManager
worker.JobManager = lambda home, **kw: original(home, engine_factory=IdleEngine, runner=run, **kw)
cs.HOME = Path(sys.argv[1])
worker.serve(cs.HOME)
"""


@unittest.skipUnless(os.name == "posix" and hasattr(socket, "AF_UNIX"), "worker runs on Unix")
class WorkerSocket(unittest.TestCase):
    def test_socket_permissions_idempotency_singleton_and_shutdown(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = home / "worker.sock"
            process = subprocess.Popen(
                [sys.executable, "-c", SERVER, temp], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            try:
                deadline = time.monotonic() + 5
                while not path.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(path.exists())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(request({"op": "health"}, path), {"ok": True})
                self.assertEqual(request({"op": "unknown"}, path), {"error": "badinput"})
                request_id = str(uuid.uuid4())
                payload = {"op": "start", "query": "q", "request_id": request_id}
                self.assertEqual(request(payload, path)["request_id"], request_id)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    job = request({"op": "get", "request_id": request_id}, path)
                    if job["state"] == "partial":
                        break
                    time.sleep(0.02)
                self.assertEqual(job["state"], "partial")
                self.assertEqual(request(payload, path), job)
                second = subprocess.run([sys.executable, "-c", SERVER, temp], capture_output=True, timeout=5)
                self.assertNotEqual(second.returncode, 0)
                self.assertTrue(request({"op": "health"}, path)["ok"])
                process.terminate()
                process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0)
                self.assertFalse(path.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
