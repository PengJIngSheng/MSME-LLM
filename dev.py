"""
dev.py — Auto-reload development server for MSME.AI
=====================================================
Usage:
    python dev.py

Watches all .py files in the project directory (recursively).
Automatically restarts the Uvicorn server whenever any .py file changes.
Works without installing extra packages (uses a subprocess + polling loop).
"""
import os
import sys
import time
import subprocess
import signal

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
WATCH_EXTS  = (".py",)
POLL_INTERVAL = 1.5   # seconds between file-change checks

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

def _start_server() -> subprocess.Popen:
    cmd = [
        sys.executable, "-m", "uvicorn",
        "server:app",
        "--host", "0.0.0.0",
        "--port", "8000",
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

def main():
    print("👀  MSME.AI dev server — watching for .py changes…")
    snapshot = _snapshot()
    proc = _start_server()

    try:
        while True:
            time.sleep(POLL_INTERVAL)

            # Restart if server died on its own
            if proc.poll() is not None:
                print("\n⚠️  Server exited unexpectedly, restarting…")
                proc = _start_server()
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
                proc = _start_server()
                snapshot = new_snap

    except KeyboardInterrupt:
        print("\n\n⛔  Dev server stopped.")
        _stop_server(proc)

if __name__ == "__main__":
    main()
