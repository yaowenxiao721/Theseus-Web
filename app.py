import os
import sys
import json
import time
import queue
import signal
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterable

from flask import (
    Flask,
    render_template,
    request,
    Response,
    stream_with_context,
    jsonify,
    send_from_directory,
    abort,
)


app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
RESULTS_DIR = BASE_DIR / "results"


class ProcessRunner:
    """Manage a single running crawl.py process and stream its output."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, url: str):
        with self._lock:
            if self.is_running():
                return False

            # Build command: use current Python, unbuffered (-u) to stream prints immediately
            project_root = Path(__file__).resolve().parent
            crawl_py = project_root / "crawl.py"
            python_exe = sys.executable
            cmd = [python_exe, "-u", str(crawl_py), "--url", url]

            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")

            creationflags = 0
            # On Windows, create a new process group so we can signal/terminate more cleanly
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                universal_newlines=True,
                cwd=str(project_root),
                env=env,
                creationflags=creationflags,
            )

            # Reader thread to forward stdout to queue
            def _reader(proc: subprocess.Popen):
                try:
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        # Normalize line endings and push to queue
                        self._q.put(line.rstrip("\n"))
                except Exception as e:
                    self._q.put(f"[web] reader error: {e}")
                finally:
                    # Signal end of stream
                    self._q.put(None)

            self._reader_thread = threading.Thread(target=_reader, args=(self._proc,), daemon=True)
            self._reader_thread.start()
            return True

    def stop(self):
        with self._lock:
            if not self.is_running():
                return False
            assert self._proc is not None
            proc = self._proc
            try:
                if os.name == "nt":
                    # Best-effort: send CTRL-BREAK to process group; fall back to kill
                    try:
                        proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                        time.sleep(1.0)
                    except Exception:
                        pass
                    if proc.poll() is None:
                        proc.terminate()
                        time.sleep(1.0)
                    if proc.poll() is None:
                        proc.kill()
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            finally:
                self._proc = None
                # Push a sentinel to unblock any waiting generators
                try:
                    self._q.put_nowait(None)
                except Exception:
                    pass
            return True

    def sse_stream(self, start_if_needed: bool, url: str | None):
        """A generator that yields SSE messages from the running process."""
        if start_if_needed:
            if not url:
                yield f"event: error\ndata: URL is required\n\n"
                return
            started = self.start(url)
            if not started and not self.is_running():
                yield f"event: error\ndata: Failed to start process\n\n"
                return

        # Send an initial message
        yield f"event: info\ndata: Streaming started at {time.strftime('%H:%M:%S')}\n\n"

        last_heartbeat = 0.0
        while True:
            try:
                line = self._q.get(timeout=1.0)
            except queue.Empty:
                line = None if not self.is_running() else "__HEARTBEAT__"  # heartbeat when running

            now = time.time()
            if line is None:
                # End of stream
                yield "event: done\ndata: Process finished\n\n"
                break
            elif line == "__HEARTBEAT__":
                # SSE comment as heartbeat (keeps connection alive without client event)
                if now - last_heartbeat > 10:
                    yield ": ping\n\n"
                    last_heartbeat = now
            else:
                # Ensure it's valid JSON-safe text
                safe = line.replace("\r", "")
                yield f"data: {safe}\n\n"


runner = ProcessRunner()


@app.get("/")
def index():
    return render_template("index.html", nav_active="run")


@app.get("/stream")
def stream():
    url = request.args.get("url", type=str)

    # If already running, just attach to the stream; otherwise start a new process with given URL
    start_if_needed = not runner.is_running()

    @stream_with_context
    def generate():
        for chunk in runner.sse_stream(start_if_needed=start_if_needed, url=url):
            yield chunk

    headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(generate(), headers=headers)


@app.post("/stop")
def stop():
    stopped = runner.stop()
    return jsonify({"stopped": bool(stopped)})


@app.get("/status")
def status():
    return jsonify({"running": runner.is_running()})


# ----------------------- Logs and Results Browsing -----------------------

def _format_size(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.0f} PB"


def _list_logs() -> list[dict]:
    items = []
    if LOGS_DIR.is_dir():
        for p in LOGS_DIR.glob("*.log"):
            try:
                stat = p.stat()
                items.append(
                    {
                        "name": p.name,
                        "relpath": p.name,
                        "size": stat.st_size,
                        "size_h": _format_size(stat.st_size),
                        "mtime": stat.st_mtime,
                        "mtime_h": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
            except OSError:
                continue
    # sort by mtime desc
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _list_results() -> list[dict]:
    items = []
    if RESULTS_DIR.is_dir():
        for p in RESULTS_DIR.rglob("*.txt"):
            if not p.is_file():
                continue
            try:
                stat = p.stat()
                rel = p.relative_to(RESULTS_DIR).as_posix()
                items.append(
                    {
                        "name": p.name,
                        "relpath": rel,
                        "dir": p.parent.relative_to(RESULTS_DIR).as_posix(),
                        "size": stat.st_size,
                        "size_h": _format_size(stat.st_size),
                        "mtime": stat.st_mtime,
                        "mtime_h": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
            except OSError:
                continue
    items.sort(key=lambda x: (x["dir"], -x["mtime"]))
    return items


def _safe_join(base: Path, rel: str) -> Path:
    # prevent path traversal
    target = (base / rel).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        abort(400, description="Invalid path")
    return target


def _read_tail(path: Path, lines: int = 200, encoding: str = "utf-8") -> str:
    """Read last N lines of a text file efficiently."""
    try:
        if lines is not None and lines <= 0:
            with open(path, "r", encoding=encoding, errors="replace") as f:
                return f.read()
        # Simple, robust implementation for moderate files; for very large files, this is acceptable for N small
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            block = 4096
            data = bytearray()
            nl = 0
            while end > 0 and nl <= lines:
                start = max(0, end - block)
                f.seek(start)
                chunk = f.read(end - start)
                data[:0] = chunk
                nl = data.count(b"\n")
                end = start
            text = data.decode(encoding, errors="replace")
            parts = text.splitlines()
            return "\n".join(parts[-lines:])
    except Exception as e:
        return f"[error] Failed to read file: {e}"


@app.get("/logs")
def logs_index():
    items = _list_logs()
    return render_template("logs.html", items=items, nav_active="logs")


@app.get("/logs/view")
def logs_view():
    rel = request.args.get("file", type=str)
    if not rel:
        abort(400, description="Missing file")
    if not rel.endswith(".log"):
        abort(400, description="Only .log allowed")
    path = _safe_join(LOGS_DIR, rel)
    if not path.exists():
        abort(404)
    lines = request.args.get("lines", default=200, type=int)
    content = _read_tail(path, lines=lines)
    stat = path.stat()
    meta = {
        "name": path.name,
        "relpath": rel,
        "size_h": _format_size(stat.st_size),
        "mtime_h": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }
    return render_template("view_text.html", meta=meta, content=content, nav_active="logs", lines=lines, back_url="/logs")


@app.get("/logs/raw")
def logs_raw():
    rel = request.args.get("file", type=str)
    if not rel or not rel.endswith(".log"):
        abort(400)
    path = _safe_join(LOGS_DIR, rel)
    if not path.exists():
        abort(404)
    return send_from_directory(LOGS_DIR, rel, as_attachment=False)


@app.get("/results")
def results_index():
    items = _list_results()
    return render_template("results.html", items=items, nav_active="results")


@app.get("/results/view")
def results_view():
    rel = request.args.get("path", type=str)
    if not rel:
        abort(400, description="Missing path")
    if not rel.endswith(".txt"):
        abort(400, description="Only .txt allowed")
    path = _safe_join(RESULTS_DIR, rel)
    if not path.exists():
        abort(404)
    lines = request.args.get("lines", default=200, type=int)
    content = _read_tail(path, lines=lines)
    stat = path.stat()
    meta = {
        "name": path.name,
        "relpath": rel,
        "size_h": _format_size(stat.st_size),
        "mtime_h": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }
    return render_template("view_text.html", meta=meta, content=content, nav_active="results", lines=lines, back_url="/results")


@app.get("/results/raw")
def results_raw():
    rel = request.args.get("path", type=str)
    if not rel or not rel.endswith(".txt"):
        abort(400)
    path = _safe_join(RESULTS_DIR, rel)
    if not path.exists():
        abort(404)
    # send_from_directory requires directory and filename
    directory = (RESULTS_DIR / rel).parent
    filename = (RESULTS_DIR / rel).name
    # Ensure directory is within RESULTS_DIR
    _safe_join(RESULTS_DIR, rel)
    return send_from_directory(directory, filename, as_attachment=False)


def main():
    # Use threaded server to allow streaming while handling stop requests
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
