"""Bounded live worker pilot in isolated storage, sharing production pause and budget."""

import argparse
import json
import random
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cpk_session as cs  # noqa: E402
from cpk_benchmark import contract, summarize  # noqa: E402
from cpk_worker import request, valid_keyword  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries-file", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if args.home.resolve() == cs.HOME.resolve() or not 1 <= args.limit <= 5:
        parser.error("use isolated job storage and a limit between 1 and 5")
    queries = [line.strip() for line in args.queries_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not queries or any(not valid_keyword(query) for query in queries):
        parser.error("valid nonempty keywords required")
    random.Random(20260922).shuffle(queries)
    args.home.mkdir(parents=True, exist_ok=True)
    path = args.home / "worker.sock"
    code = "import sys; from pathlib import Path; from cpk_worker import serve; serve(Path(sys.argv[1]))"
    rows = []
    with (args.home / "server.log").open("a", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, "-c", code, str(args.home.resolve())],
            cwd=Path(__file__).resolve().parents[1],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 10
            while not path.exists() and server.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if server.poll() is not None or not path.exists():
                raise RuntimeError("isolated worker did not start")
            for query in queries[: args.limit]:
                started = time.monotonic()
                job = request({"op": "start", "query": query, "request_id": str(uuid.uuid4())}, path)
                first = None
                while job.get("state") not in ("complete", "partial", "failed"):
                    if job.get("error") or time.monotonic() - started > 75:
                        raise RuntimeError("job did not reach a bounded terminal state")
                    time.sleep(0.25)
                    job = request({"op": "get", "request_id": job["request_id"]}, path)
                    if first is None and "ok" in (job["sections"]["search"], job["sections"]["autocomplete"]):
                        first = round((time.monotonic() - started) * 1000, 1)
                row = {
                    "backend": "resident_worker",
                    "query": query,
                    "state": job["state"],
                    "outcome": job.get("result", {}).get("outcome"),
                    "request_id": job["request_id"],
                    "session_reused": job.get("result", {}).get("session_reused"),
                    "sections": job["sections"],
                    "contract": contract(job.get("result", {})),
                    "timing": job["timing"],
                    "socket_first_ms": first,
                    "socket_total_ms": round((time.monotonic() - started) * 1000, 1),
                }
                rows.append(row)
                with (args.home / "samples.jsonl").open("a", encoding="utf-8") as output:
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(json.dumps(row, ensure_ascii=False), flush=True)
                if not row["contract"]["passed"]:
                    break
                time.sleep(5)
        finally:
            server.terminate()
            try:
                server.wait(timeout=70)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    print(json.dumps({"summary": summarize(rows)}), flush=True)


if __name__ == "__main__":
    main()
