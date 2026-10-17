"""Create local dependencies when needed, then launch the browser."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".venv"
PYTHON = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = ENV / ".browser-studio-setup"


def main():
    requirements = ROOT / "requirements.txt"
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not PYTHON.exists():
        print("Creating the local Python environment...", flush=True)
        venv.EnvBuilder(with_pip=True).create(ENV)

    if "--setup" in sys.argv or not STAMP.exists() or STAMP.read_text() != fingerprint:
        print("Installing Python dependencies and Chromium...", flush=True)
        subprocess.run([str(PYTHON), "-m", "pip", "install", "-r", str(requirements)], check=True)
        subprocess.run([str(PYTHON), "-m", "playwright", "install", "chromium"], check=True)
        STAMP.write_text(fingerprint)

    if "--setup" not in sys.argv:
        return subprocess.call([str(PYTHON), "-m", "browserkit"], cwd=ROOT)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        print("Setup failed. Check your connection, then retry: python run.py --setup", file=sys.stderr)
        sys.exit(error.returncode)
