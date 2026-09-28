"""Run the API, persistent worker, and Vite UI as native child processes."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def load_environment() -> None:
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not key.replace("_", "").isalnum():
                raise ValueError(f"Invalid environment line for {key!r}")
            os.environ.setdefault(key, value.strip().strip("\"'"))
    (ROOT / "backend" / "data").mkdir(parents=True, exist_ok=True)


def package_manager_command() -> list[str]:
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm:
        return [npm]
    pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    if pnpm:
        return [pnpm]
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm.cmd"
    if os.name == "nt" and bundled.is_file():
        return [str(bundled)]
    raise RuntimeError("Node.js with npm or pnpm is unavailable. Install Node.js 22 LTS, or use --backend-only.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--create-user")
    parser.add_argument("--role", choices=["admin", "analyst", "reviewer"], default="analyst")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    load_environment()
    subprocess.run([sys.executable, "-m", "app.cli", "migrate"], cwd=ROOT / "backend", check=True)
    if args.create_user:
        subprocess.run([sys.executable, "-m", "app.cli", "create-user", "--username", args.create_user,
                        "--role", args.role, "--password-env", "LOGFORGE_BOOTSTRAP_PASSWORD"], cwd=ROOT / "backend", check=True)
        return 0
    if args.migrate_only:
        return 0
    commands = [
        ([sys.executable, "-m", "uvicorn", "app.main:app", "--host", args.host, "--port", str(args.port),
          "--timeout-graceful-shutdown", "50"], ROOT / "backend"),
        ([sys.executable, "-m", "app.worker"], ROOT / "backend"),
    ]
    if not args.backend_only:
        manager = package_manager_command()
        forwarded = ["run", "dev", "--host", "127.0.0.1"] if "pnpm" in Path(manager[0]).name else ["run", "dev", "--", "--host", "127.0.0.1"]
        commands.append((manager + forwarded, ROOT / "frontend"))
    processes: list[subprocess.Popen] = []
    stop_requested = False

    def stop(*_args: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        for command, directory in commands:
            processes.append(subprocess.Popen(command, cwd=directory, creationflags=flags))
        print(f"API: http://{args.host}:{args.port}/docs", flush=True)
        if not args.backend_only:
            print("Workspace: http://127.0.0.1:5173 — press Ctrl+C to stop.", flush=True)
        while not stop_requested:
            for process in processes:
                if process.poll() is not None:
                    print(f"Process {process.pid} exited with status {process.returncode}; stopping sibling processes.", flush=True)
                    return process.returncode or 1
            time.sleep(0.5)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
                except OSError:
                    pass
        deadline = time.monotonic() + 55
        for process in reversed(processes):
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
