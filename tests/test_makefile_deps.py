#!/usr/bin/env python3
"""
RED test: verify the canonical /opt/data/cqd_venv has all dependencies from requirements.txt.
This test fails when dependencies are missing (the original broken state).
After the Makefile is implemented, `make install-deps` should fix this.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = Path("/opt/data/cqd_venv/bin/python")
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"


def python_check(module: str) -> bool:
    """Return True if the module imports successfully under the venv."""
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", f"import {module}"],
        capture_output=True,
        timeout=30,
    )
    return result.returncode == 0


def test_venv_python_exists():
    assert VENV_PYTHON.exists(), f"Canonical venv Python not found at {VENV_PYTHON}"


def test_requirements_file_exists():
    assert REQUIREMENTS.exists(), f"requirements.txt not found at {REQUIREMENTS}"


def test_ccxt_installed():
    assert python_check("ccxt"), "ccxt not importable — run: make install-deps"


def test_dotenv_installed():
    assert python_check("dotenv"), "python-dotenv not importable — run: make install-deps"


def test_pandas_installed():
    assert python_check("pandas"), "pandas not importable — run: make install-deps"


def test_pandas_ta_installed():
    assert python_check("pandas_ta"), "pandas-ta not importable — run: make install-deps"


def test_numpy_installed():
    assert python_check("numpy"), "numpy not importable — run: make install-deps"


def test_requests_installed():
    assert python_check("requests"), "requests not importable — run: make install-deps"


if __name__ == "__main__":
    # Run all checks and report
    deps = ["ccxt", "dotenv", "pandas", "pandas_ta", "numpy", "requests"]
    missing = [d for d in deps if not python_check(d)]

    if missing:
        print(f"❌ Missing dependencies: {', '.join(missing)}")
        print(f"   Fix with: make install-deps")
        sys.exit(1)
    else:
        print(f"✅ All dependencies present")
        sys.exit(0)
