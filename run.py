"""One-click launcher for the NJ School Student-Support Staff Finder."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections import deque
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
APP_FILE = PROJECT_DIR / "app.py"
VENV_PYTHON = PROJECT_DIR / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
LOG_FILE = PROJECT_DIR / "cache" / "streamlit.log"


def use_project_python() -> None:
    """Relaunch under the project environment when an editor uses system Python."""
    if not VENV_PYTHON.exists():
        print("The project environment is missing.")
        print("Run: python -m venv .venv")
        print("Then: .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        raise SystemExit(1)
    try:
        already_using_venv = Path(sys.executable).resolve() == VENV_PYTHON.resolve()
    except OSError:
        already_using_venv = False
    if not already_using_venv:
        raise SystemExit(subprocess.call([str(VENV_PYTHON), str(Path(__file__).resolve())], cwd=PROJECT_DIR))


def available_port(first: int = 8501, last: int = 8520) -> int:
    for port in range(first, last + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No available local port was found between 8501 and 8520.")


def capture_output(process: subprocess.Popen[str], recent: deque[str]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("w", encoding="utf-8") as log:
        if process.stdout is None:
            return
        for line in process.stdout:
            log.write(line)
            log.flush()
            recent.append(line.rstrip())


def wait_until_ready(process: subprocess.Popen[str], port: int, timeout: float = 30.0) -> bool:
    health_url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def main() -> int:
    use_project_python()
    if not APP_FILE.exists():
        print(f"Could not find the app: {APP_FILE}")
        return 1

    port = available_port()
    browser_url = f"http://localhost:{port}"
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP_FILE),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]

    print("Starting NJ School Student-Support Staff Finder…", flush=True)
    recent: deque[str] = deque(maxlen=20)
    process = subprocess.Popen(
        command,
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output_thread = threading.Thread(target=capture_output, args=(process, recent), daemon=True)
    output_thread.start()

    if not wait_until_ready(process, port):
        print("The app could not start. Recent technical details:")
        for line in recent:
            print(line)
        process.terminate()
        return 1

    print()
    print("The app is ready.")
    print("Open this link in your browser:")
    print(browser_url)
    print()
    print("Keep this window open while using the app. Press Ctrl+C here to stop it.")
    if os.getenv("NJ_SCHOOL_FINDER_NO_BROWSER") != "1":
        webbrowser.open(browser_url)

    try:
        return process.wait()
    except KeyboardInterrupt:
        print("\nStopping the app…")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

