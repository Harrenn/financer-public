#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"


def _find_backend_python() -> str:
    preferred = [
        BACKEND_DIR / ".venv" / "bin" / "python",
        BACKEND_DIR / ".venv" / "bin" / "python3",
        ROOT_DIR / ".myenv" / "bin" / "python",
        ROOT_DIR / ".myenv" / "bin" / "python3",
    ]
    for candidate in preferred:
        if candidate.exists():
            return str(candidate)

    fallback = shutil.which("python3.13") or shutil.which("python3")
    if fallback:
        return fallback

    raise RuntimeError("No Python interpreter found. Install python3.13 or create backend/.venv first.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Financer in terminal-agent mode or API mode.")
    parser.add_argument(
        "--api",
        action="store_true",
        help="Run FastAPI server instead of the terminal agent.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for --api mode.")
    parser.add_argument("--port", type=int, default=8000, help="Port for --api mode.")
    return parser.parse_args()


def _ensure_dependencies(python_bin: str, modules: list[str]) -> bool:
    check = [
        python_bin,
        "-c",
        "; ".join([f"import {name}" for name in modules]),
    ]
    return subprocess.call(check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def _print_install_hint(python_bin: str) -> None:
    print("[launcher] Missing Python dependencies.", file=sys.stderr)
    print(f"[launcher] Install with: {python_bin} -m pip install -r {ROOT_DIR / 'requirements.txt'}", file=sys.stderr)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        os.environ.setdefault(key, value)


def _load_dotenv() -> None:
    candidates = [
        ROOT_DIR / ".env",
        ROOT_DIR / ".env.local",
        BACKEND_DIR / ".env",
        BACKEND_DIR / ".env.local",
    ]
    for path in candidates:
        _load_env_file(path)


def main() -> int:
    _load_dotenv()
    args = _parse_args()
    try:
        python_bin = _find_backend_python()
    except RuntimeError as exc:
        print(f"[launcher] {exc}", file=sys.stderr)
        return 2

    if args.api:
        if not _ensure_dependencies(python_bin, ["fastapi", "uvicorn", "sqlalchemy", "pydantic"]):
            _print_install_hint(python_bin)
            return 2

        if not (BACKEND_DIR / "main.py").exists():
            print("[launcher] API entrypoint not found. Expected backend/main.py", file=sys.stderr)
            return 2
        print(f"[launcher] starting API at http://{args.host}:{args.port}")
        print("[launcher] docs: /docs")
        cmd = [
            python_bin,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--reload",
        ]
        return subprocess.call(cmd, cwd=str(BACKEND_DIR))

    if not (BACKEND_DIR / "agent_cli.py").exists():
        print("[launcher] Terminal agent not found. Expected backend/agent_cli.py", file=sys.stderr)
        return 2

    if not _ensure_dependencies(python_bin, ["fastapi", "sqlalchemy", "pydantic"]):
        _print_install_hint(python_bin)
        return 2

    print("[launcher] starting Financer full-agent terminal mode")
    return subprocess.call([python_bin, "agent_cli.py"], cwd=str(BACKEND_DIR))


if __name__ == "__main__":
    raise SystemExit(main())
