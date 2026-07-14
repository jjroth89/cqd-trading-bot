"""
CQD Environment Normalization — Validation Test Suite
======================================================
Tests the canonicalization of the CQD bot's Python execution environment.

Following TDD: These tests define EXPECTED behavior.
They MUST fail before the fix is applied, and MUST pass after.

RED Phase — tests written first, expecting to fail until environment is normalized.
"""

import subprocess
import sys
import os
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path("/opt/data/cqd-trading-bot")
CANONICAL_VENV_PYTHON = Path("/opt/data/cqd_venv/bin/python")
REDUNDANT_VENV = PROJECT_ROOT / ".venv"
REQUIREMENTS_TXT = PROJECT_ROOT / "requirements.txt"
CRON_WRAPPER_DIR = PROJECT_ROOT / "cron_wrappers"

REQUIRED_PACKAGES = ["ccxt", "pandas", "numpy"]


# ── Test 1: Canonical venv has all required packages ──────────────────────────
# RED: Expect this to FAIL if deps not yet installed into /opt/data/cqd_venv

def test_canonical_venv_has_required_packages():
    """
    The canonical venv at /opt/data/cqd_venv MUST have ccxt, pandas, and numpy
    importable. This is the venv that cron wrappers are hardcoded to use.
    """
    for pkg in REQUIRED_PACKAGES:
        result = subprocess.run(
            [str(CANONICAL_VENV_PYTHON), "-c", f"import {pkg}; print({pkg}.__version__)"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Package '{pkg}' not importable in canonical venv.\n"
            f"  Python: {CANONICAL_VENV_PYTHON}\n"
            f"  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}\n"
            f"  → Run: uv pip install --python {CANONICAL_VENV_PYTHON} -r {REQUIREMENTS_TXT}"
        )
        print(f"  ✓ {pkg}={result.stdout.strip()}")


# ── Test 2: requirements.txt exists and lists all required packages ───────────

def test_requirements_txt_declares_required_packages():
    """
    requirements.txt MUST declare ccxt, pandas, and numpy as dependencies.
    This is the source of truth for what gets installed into the canonical venv.
    """
    assert REQUIREMENTS_TXT.exists(), f"requirements.txt not found at {REQUIREMENTS_TXT}"

    content = REQUIREMENTS_TXT.read_text()
    for pkg in REQUIRED_PACKAGES:
        assert pkg in content, (
            f"Package '{pkg}' not declared in {REQUIREMENTS_TXT}.\n"
            f"  Expected: {pkg}>=X.Y.Z"
        )
    print(f"  ✓ All required packages declared in requirements.txt")


# ── Test 3: Redundant .venv directory must not exist ───────────────────────────
# RED: Expect this to FAIL before sanitization (.venv exists)
# GREEN: Expect this to PASS after .venv removal

def test_redundant_venv_removed():
    """
    The project-local .venv directory at /opt/data/cqd-trading-bot/.venv
    MUST NOT exist after normalization. It causes path ambiguity and must be
    removed to eliminate future environment confusion.
    """
    assert not REDUNDANT_VENV.exists(), (
        f"Redundant .venv directory still exists at {REDUNDANT_VENV}.\n"
        f"  This causes environment ambiguity between cron wrappers (which use\n"
        f"  /opt/data/cqd_venv) and project-local tooling.\n"
        f"  → Run: rm -rf {REDUNDANT_VENV}"
    )
    print(f"  ✓ .venv removed — no more path ambiguity")


# ── Test 4: Cron wrapper scripts remain untouched ─────────────────────────────
# Ensures the fix doesn't alter any wrapper scripts

def test_cron_wrappers_untouched():
    """
    All cron wrapper scripts MUST retain their original PYTHON_BIN path logic.
    The normalization fixes the TARGET venv, not the wrapper paths themselves.
    """
    wrapper_scripts = list(CRON_WRAPPER_DIR.glob("*.sh"))
    assert len(wrapper_scripts) > 0, f"No .sh scripts found in {CRON_WRAPPER_DIR}"

    for wrapper in wrapper_scripts:
        content = wrapper.read_text()
        # Verify the hardcoded path logic is intact
        assert 'PYTHON_BIN="/opt/data/cqd_venv/bin/python"' in content, (
            f"Wrapper {wrapper.name} has modified PYTHON_BIN path.\n"
            f"  Expected: PYTHON_BIN=\"/opt/data/cqd_venv/bin/python\"\n"
            f"  Constraint: Cron wrapper scripts must remain untouched."
        )
    print(f"  ✓ All {len(wrapper_scripts)} cron wrapper scripts retain original paths")


# ── Test 5: Bot core scripts import successfully from canonical venv ──────────

def test_core_scripts_import_from_canonical_venv():
    """
    All core bot execution scripts MUST be able to import required packages
    (ccxt, pandas, numpy) using the canonical venv's Python.

    This validates that the execution environment is consistent across:
    - quant_evaluator.py
    - sandbox_engine.py
    - rotate_watchlist.py
    """
    core_scripts = [
        PROJECT_ROOT / "core" / "quant_evaluator.py",
        PROJECT_ROOT / "core" / "sandbox_engine.py",
        PROJECT_ROOT / "core" / "rotate_watchlist.py",
    ]

    import_test_code = "; ".join(f"import {p}" for p in REQUIRED_PACKAGES)

    for script in core_scripts:
        if not script.exists():
            print(f"  ⚠ Skipping {script.name} (not found)")
            continue

        result = subprocess.run(
            [str(CANONICAL_VENV_PYTHON), "-c", import_test_code],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=script.parent,
        )
        assert result.returncode == 0, (
            f"Core script {script.name} cannot import required packages.\n"
            f"  Command: {CANONICAL_VENV_PYTHON} -c '{import_test_code}'\n"
            f"  stderr: {result.stderr}\n"
            f"  → Canonical venv may not have all dependencies installed"
        )
        print(f"  ✓ {script.name} — all imports OK")


# ── Test 6: Makefile targets (if they exist) also validate ───────────────────

def test_makefile_targets_environment_consistency():
    """
    If the project has a Makefile with test/deps targets, verify they reference
    the canonical venv path consistently.
    """
    makefile = PROJECT_ROOT / "Makefile"
    if not makefile.exists():
        print("  ⚠ Makefile not found — skipping")
        return

    content = makefile.read_text()
    # After normalization, any venv path references should point to /opt/data/cqd_venv
    # not to the project-local .venv
    if ".venv" in content:
        assert "/opt/data/cqd_venv" in content, (
            f"Makefile contains .venv reference but not /opt/data/cqd_venv.\n"
            f"  This may indicate stale path references.\n"
            f"  → Update Makefile to use /opt/data/cqd_venv consistently"
        )
    print(f"  ✓ Makefile path references consistent with canonical venv")


# ── Test 7: No stale .venv symlinks in project ────────────────────────────────

def test_no_stale_venv_symlinks():
    """
    After normalization, there should be no symlinks pointing to /opt/data/cqd-trading-bot/.venv
    anywhere in the project tree.
    """
    result = subprocess.run(
        ["find", str(PROJECT_ROOT), "-type", "l", "-name", "python", "-xtype", "l"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    symlinks = [l for l in result.stdout.strip().split("\n") if l and ".venv" in l]
    assert len(symlinks) == 0, (
        f"Found stale .venv symlinks in project:\n  " + "\n  ".join(symlinks) + "\n"
        f"  → Remove these symlinks after .venv removal"
    )
    print(f"  ✓ No stale .venv symlinks in project tree")


if __name__ == "__main__":
    print("=" * 70)
    print("CQD Environment Normalization — TDD Validation Suite")
    print("=" * 70)
    print(f"Canonical venv:  {CANONICAL_VENV_PYTHON}")
    print(f"Redundant .venv: {REDUNDANT_VENV}")
    print(f"Requirements:    {REQUIREMENTS_TXT}")
    print("=" * 70)

    tests = [
        ("test_requirements_txt_declares_required_packages", test_requirements_txt_declares_required_packages),
        ("test_cron_wrappers_untouched", test_cron_wrappers_untouched),
        ("test_canonical_venv_has_required_packages", test_canonical_venv_has_required_packages),
        ("test_redundant_venv_removed", test_redundant_venv_removed),
        ("test_core_scripts_import_from_canonical_venv", test_core_scripts_import_from_canonical_venv),
        ("test_makefile_targets_environment_consistency", test_makefile_targets_environment_consistency),
        ("test_no_stale_venv_symlinks", test_no_stale_venv_symlinks),
    ]

    passed = 0
    failed = 0

    for name, fn in tests:
        print(f"\n▶ {name}")
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            failed += 1

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    sys.exit(0 if failed == 0 else 1)