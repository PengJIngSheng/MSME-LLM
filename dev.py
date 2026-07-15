"""
dev.py — Auto-reload development server for bisnes.ai
=====================================================
Usage:
    python dev.py                 # starts on 127.0.0.1:8001 by default
    python dev.py --port 8001

Watches all .py files in the project directory (recursively).
Automatically restarts the Uvicorn server whenever any .py file changes.
Works without installing extra packages (uses a subprocess + polling loop).
"""
import argparse
import os
import socket
import sys
import time
import subprocess
import signal

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
WATCH_EXTS   = (".py",)
POLL_INTERVAL = 1.5   # seconds between file-change checks
DEFAULT_PORT = int(os.getenv("BISNES_DEV_PORT", "8001"))

def _snapshot() -> dict[str, float]:
    """Return {filepath: mtime} for every watched file in the project."""
    state = {}
    for root, dirs, files in os.walk(PROJECT_DIR):
        # Skip hidden dirs, __pycache__, .git
        dirs[:] = [d for d in dirs if not d.startswith(('.', '__pycache__'))]
        for f in files:
            if any(f.endswith(ext) for ext in WATCH_EXTS):
                full = os.path.join(root, f)
                try:
                    state[full] = os.path.getmtime(full)
                except OSError:
                    pass
    return state

def _port_is_in_use(port: int) -> bool:
    """Return whether a local listener already owns the requested port."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True, text=True
        )
        return bool(result.stdout.strip())
    except FileNotFoundError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            return sock.connect_ex(("127.0.0.1", port)) == 0

def _start_server(port: int) -> subprocess.Popen:
    cmd = [
        sys.executable, "-m", "uvicorn",
        "server:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "info",
        "--no-access-log",
    ]
    print(f"\n🟢  Starting server: {' '.join(cmd)}\n{'─'*60}")
    proc = subprocess.Popen(cmd, cwd=PROJECT_DIR)
    return proc

def _stop_server(proc: subprocess.Popen):
    if proc and proc.poll() is None:
        print("\n🔴  Stopping server for reload…")
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

def main() -> int:
    parser = argparse.ArgumentParser(description="Run bisnes.ai with local auto-reload polling.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Local development port (default: {DEFAULT_PORT})")
    args = parser.parse_args()
    port = args.port
    if not 1 <= port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if _port_is_in_use(port):
        print(f"❌ Port {port} is already in use. Choose another port with --port; no process was stopped.")
        return 2

    print(f"👀  bisnes.ai dev server — watching for .py changes on http://127.0.0.1:{port}…")
    snapshot = _snapshot()
    proc = _start_server(port)

    try:
        while True:
            time.sleep(POLL_INTERVAL)

            # Restart if server died on its own
            if proc.poll() is not None:
                print("\n⚠️  Server exited unexpectedly, restarting…")
                if _port_is_in_use(port):
                    print(f"❌ Port {port} is now in use; stopping the development watcher.")
                    return 2
                proc = _start_server(port)
                snapshot = _snapshot()
                continue

            new_snap = _snapshot()
            changed = [
                f for f in new_snap
                if new_snap[f] != snapshot.get(f)
            ]
            added   = [f for f in new_snap if f not in snapshot]
            removed = [f for f in snapshot if f not in new_snap]

            if changed or added or removed:
                for f in changed:
                    print(f"  📝 Changed: {os.path.relpath(f, PROJECT_DIR)}")
                for f in added:
                    print(f"  ➕ Added:   {os.path.relpath(f, PROJECT_DIR)}")
                for f in removed:
                    print(f"  ➖ Removed: {os.path.relpath(f, PROJECT_DIR)}")

                _stop_server(proc)
                time.sleep(0.5)   # brief pause so old sockets close
                proc = _start_server(port)
                snapshot = new_snap

    except KeyboardInterrupt:
        print("\n\n⛔  Dev server stopped.")
        _stop_server(proc)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
