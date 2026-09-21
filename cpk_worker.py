"""On-demand jobs over an owner-only Unix socket; one thread owns the browser."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import copy
import json
import os
import queue
import re
import signal
import socket
import socketserver
import threading
import time
import uuid
from pathlib import Path

from cpk_metrics import Trace

__all__ = ["JobManager", "run_job", "request", "serve", "valid_keyword"]
KEYWORD_RE = re.compile(r"[\w가-힣ㄱ-ㅎㅏ-ㅣ0-9 ().,+&/-]{1,60}")
TERMINAL = {"complete", "partial", "failed"}


def valid_keyword(query: str) -> bool:
    """Apply the same bounded keyword contract as the web frontend."""
    return isinstance(query, str) and bool(KEYWORD_RE.fullmatch(query.strip()))


def run_job(query: str, trace: Trace, engine, publish, *, db_path: Path) -> dict:
    """Publish independently completed sections, then persist a complete search snapshot."""
    import cpk_domeggook as dg
    import cpk_engine
    import cpk_market as market
    import cpk_market_db as mdb
    import cpk_market_report as report
    import cpk_market_score as score

    def fetch_source():
        with trace.stage("sourcing"):
            return dg.check_existence(query, timeout=trace.remaining(20))

    result: dict = {"query": query, "autocomplete": [], "autocomplete_ok": None}
    source = None
    # The browser stays on this thread. HTTP sessions and SQLite are never shared between threads.
    executor = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="cpk-aux")
    ac_future = executor.submit(getattr(engine, "autocomplete", cpk_engine.autocomplete), query, trace)
    source_future = executor.submit(fetch_source)

    def show_ac(future):
        try:
            ac = future.result()
        except Exception:
            ac = {"ok": False, "items": [], "outcome": "load_error", "used": query}
        publish("autocomplete", "ok" if ac["ok"] else ac["outcome"], ac)

    def show_source(future):
        try:
            publish("sourcing", "ok", future.result())
        except Exception as exc:
            publish("sourcing", "timeout" if isinstance(exc, TimeoutError) else "load_error", {})

    ac_future.add_done_callback(show_ac)
    source_future.add_done_callback(show_source)

    def await_future(future):
        while not future.done():
            cf.wait([future], timeout=trace.remaining(0.05))
            if not future.done() and hasattr(engine, "idle"):
                engine.idle()
        return future.result()

    try:
        browser_result = engine.search(query, trace)
        result.update(browser_result)
        publish("search", result["outcome"], browser_result)
        try:
            ac = await_future(ac_future)
            result.update(autocomplete=ac["items"], autocomplete_ok=ac["ok"], autocomplete_query=ac["used"])
            publish("autocomplete", "ok" if ac["ok"] else ac["outcome"], ac)
        except Exception as exc:
            result["autocomplete_ok"] = False
            publish(
                "autocomplete", "timeout" if isinstance(exc, TimeoutError) else "load_error", {"ok": False, "items": []}
            )
        if result.get("outcome") in ("ok", "no_results") and result["autocomplete_ok"]:
            trace.mark("essential_ready_ms")
        try:
            source = await_future(source_future)
            publish("sourcing", "ok", source)
        except Exception as exc:
            publish("sourcing", "timeout" if isinstance(exc, TimeoutError) else "load_error", {})
        response = {"result": result}
        if result.get("outcome") == "ok":
            with trace.stage("storage"):
                conn = mdb.connect(str(db_path))
                try:
                    kid = mdb.upsert_keyword(conn, query, "ondemand", status="tracked")
                    mdb.set_status(conn, kid, "tracked")
                    today = time.strftime("%Y-%m-%d")
                    mdb.insert_snapshot(conn, kid, market.snapshot_from_result(result), day=today)
                    mdb.save_scores(conn, kid, today, score.opportunity(mdb.snapshots_for(conn, kid)))
                    if source is not None:
                        mdb.save_sourcing(conn, kid, source)
                    response["report"] = report.report(conn)
                finally:
                    conn.close()
        return response
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


class JobManager:
    """Bound pending jobs, persist states atomically, and deduplicate active requests."""

    def __init__(
        self,
        home: Path,
        *,
        engine_factory=None,
        runner=run_job,
        max_pending: int = 8,
        timeout: float = 60,
        start: bool = True,
    ):
        self.home = home
        self.directory = home / "jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.pending: queue.Queue = queue.Queue(maxsize=max_pending)
        self.timeout = timeout
        self.runner = runner
        self.engine_factory = engine_factory
        self.stopping = threading.Event()
        self.thread = None
        # A restarted process cannot resume its former in-memory browser operation.
        for path in self.directory.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job.get("created", 0) < time.time() - 7 * 86400:
                    path.unlink()
                    continue
                if job.get("state") not in TERMINAL:
                    job.update(state="failed", error="worker_restarted")
                    self._write(job)
            except (ValueError, OSError, KeyError):
                continue
        if start:
            self.thread = threading.Thread(target=self._work, name="cpk-browser", daemon=True)
            self.thread.start()

    def _write(self, job: dict) -> None:
        import cpk_session as cs

        cs.atomic_write(self.directory / (job["request_id"] + ".json"), json.dumps(job, ensure_ascii=False))

    def start(self, query: str, request_id: str) -> dict:
        """Start once, or return the existing job for repeated IDs/active keywords."""
        request_id = str(uuid.UUID(request_id))
        if not valid_keyword(query):
            return {"error": "badinput"}
        query = query.strip()
        with self.lock:
            existing = self.get(request_id)
            if existing.get("error") != "not_found":
                return existing if existing["query"] == query else {"error": "request_id_conflict"}
            for job in self.jobs.values():
                if job["query"] == query and job["state"] not in TERMINAL:
                    return copy.deepcopy(job)
            if self.pending.full():
                return {"error": "busy"}
            job = {
                "request_id": request_id,
                "query": query,
                "state": "queued",
                "created": time.time(),
                "sections": {"search": "pending", "autocomplete": "pending", "sourcing": "pending"},
                "result": {},
                "autocomplete": {},
                "sourcing": {},
            }
            self.jobs[request_id] = job
            self._write(job)
            self.pending.put_nowait((request_id, time.monotonic()))
            return copy.deepcopy(job)

    def get(self, request_id: str) -> dict:
        """Read status without creating network traffic to the target website."""
        request_id = str(uuid.UUID(request_id))
        with self.lock:
            if request_id in self.jobs:
                return copy.deepcopy(self.jobs[request_id])
            path = self.directory / (request_id + ".json")
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {"error": "not_found"}

    def close(self) -> None:
        """Ask the worker to stop after its bounded current operation."""
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=self.timeout + 5)

    def _work(self) -> None:
        if self.engine_factory is None:
            from cpk_engine import BrowserSearch

            engine = BrowserSearch()
        else:
            engine = self.engine_factory()
        try:
            while not self.stopping.is_set():
                try:
                    request_id, enqueued = self.pending.get(timeout=0.2)
                except queue.Empty:
                    engine.idle()
                    continue
                trace = Trace(request_id, timeout=max(0, self.timeout - (time.monotonic() - enqueued)))
                with self.lock:
                    job = self.jobs[request_id]
                    job.update(state="running", queue_ms=round((time.monotonic() - enqueued) * 1000, 1))
                    self._write(job)

                def publish(section, status, data, job=job, trace=trace):
                    with self.lock:
                        if job["state"] in TERMINAL:
                            return
                        job["sections"][section] = status
                        job["result" if section == "search" else section] = data
                        if status in ("ok", "no_results") and section in ("search", "autocomplete"):
                            trace.mark("first_result_ms")
                        if (
                            job["sections"]["search"] in ("ok", "no_results")
                            and job["sections"]["autocomplete"] == "ok"
                        ):
                            trace.mark("essential_ready_ms")
                        job["timing"] = trace.snapshot()
                        self._write(job)

                try:
                    trace.remaining()
                    response = self.runner(job["query"], trace, engine, publish, db_path=self.home / "market.db")
                    with self.lock:
                        job.update(response)
                        essential = (
                            job["sections"]["search"] in ("ok", "no_results")
                            and job["sections"]["autocomplete"] == "ok"
                        )
                        job["state"] = "complete" if essential and job["sections"]["sourcing"] == "ok" else "partial"
                        if not essential and job["sections"]["search"] not in ("ok", "no_results"):
                            job["state"] = "failed"
                except Exception as exc:
                    with self.lock:
                        job.update(state="failed", error="timeout" if isinstance(exc, TimeoutError) else "worker_error")
                    engine.close()
                finally:
                    with self.lock:
                        for section, status in job["sections"].items():
                            if status == "pending":
                                job["sections"][section] = "timeout"
                        job["timing"] = trace.snapshot()
                        self._write(job)
                        # Finished jobs remain available on disk without an unbounded memory cache.
                        self.jobs.pop(request_id, None)
                    trace.save(
                        self.home / "performance.jsonl",
                        backend=getattr(engine, "backend", "browser_reuse"),
                        outcome=job["state"],
                    )
                    self.pending.task_done()
        finally:
            engine.close()


def request(payload: dict, path: Path) -> dict:
    """Send a small framed JSON request to the local worker."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path))
        connection.sendall(json.dumps(payload).encode() + b"\n")
        with connection.makefile("rb") as stream:
            response = stream.readline(2_000_000)
    return json.loads(response)


def serve(home: Path) -> None:
    """Run the owner-only socket server. There is no TCP listener or scheduled crawl."""
    import cpk_session as cs

    home.mkdir(parents=True, exist_ok=True)
    path = home / "worker.sock"
    with cs._flock("worker.lock", timeout=0, blocking=False) as owned:
        if not owned:
            raise RuntimeError("worker already running")
        path.unlink(missing_ok=True)
        manager = JobManager(home, timeout=float(os.environ.get("CPK_REQUEST_TIMEOUT", "60")))

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(5)
                try:
                    payload = json.loads(self.rfile.readline(8192))
                    if payload.get("op") == "start":
                        result = manager.start(payload.get("query", ""), payload["request_id"])
                    elif payload.get("op") == "get":
                        result = manager.get(payload["request_id"])
                    elif payload.get("op") == "health":
                        result = {"ok": bool(manager.thread and manager.thread.is_alive())}
                    else:
                        result = {"error": "badinput"}
                except Exception:
                    result = {"error": "badinput"}
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        try:
            with Server(str(path), Handler) as server:
                os.chmod(path, 0o600)

                def stop(signum, frame):
                    # shutdown() must run outside serve_forever's thread.
                    threading.Thread(target=server.shutdown, daemon=True).start()

                previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
                try:
                    server.serve_forever(poll_interval=0.2)
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)
        finally:
            manager.close()
            path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("serve", "request"))
    args = parser.parse_args()
    import cpk_session as cs

    if args.action == "serve":
        serve(cs.HOME)
        return
    try:
        raw = os.environ.get("SSH_ORIGINAL_COMMAND", "").removeprefix("__worker__ ")
        if len(raw) > 4096:
            raise ValueError("payload too large")
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
        result = request(payload, cs.HOME / "worker.sock")
    except Exception:
        result = {"error": "worker_unavailable"}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
