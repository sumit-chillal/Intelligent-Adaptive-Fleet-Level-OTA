#!/usr/bin/env python3
"""
CONVOY — repository layout checker.

Run this after copying files in, before running anything else. It answers
"is every file where the tooling expects it?" in one second, instead of you
discovering a misplaced file via a ModuleNotFoundError three commands later.

Usage, from the repository root (the folder containing README.md):

    python tools/check_layout.py

Exit code 0 means the layout is correct.
"""

from __future__ import annotations

import sys
from pathlib import Path

# (path relative to repo root, required?, note shown if missing)
EXPECTED: list[tuple[str, bool, str]] = [
    ("README.md", True, ""),
    (".gitignore", True, "protects your secrets — do not commit without it"),

    # ---- admin (Mac only) -------------------------------------------------
    ("admin/.env.example", True, ""),
    ("admin/.env", False, "copy from .env.example and fill in your credentials"),
    ("admin/docker-compose.admin.yml", True, ""),
    ("admin/link_check.py", True, ""),
    ("admin/requirements.txt", True, ""),
    ("admin/secrets/.gitkeep", False, "empty placeholder, keeps the folder in git"),

    # ---- backend (Mac only) -----------------------------------------------
    ("backend/requirements.txt", True, ""),
    ("backend/pytest.ini", True, "without this, pytest cannot import the app package"),
    ("backend/app/__init__.py", True, "empty file — makes app/ an importable package"),
    ("backend/app/constants.py", True, ""),
    ("backend/app/core/__init__.py", True, "empty file"),
    ("backend/app/core/adaptive.py", True, ""),
    ("backend/app/core/crypto.py", True, ""),
    ("backend/app/core/firmware.py", True, ""),
    ("backend/app/config.py", True, "Phase 2B — typed settings"),
    ("backend/app/db/__init__.py", True, "empty file"),
    ("backend/app/db/models.py", True, "Phase 2B — schema"),
    ("backend/app/db/session.py", True, "Phase 2B — async engine"),
    ("backend/alembic.ini", True, "Phase 2B"),
    ("backend/alembic/env.py", True, "Phase 2B"),
    ("backend/alembic/script.py.mako", True, "Phase 2B"),
    ("backend/tests/__init__.py", True, "empty file"),
    ("backend/tests/test_adaptive.py", True, "must be inside backend/tests/, not backend/"),
    ("backend/tests/test_crypto.py", True, "must be inside backend/tests/, not backend/"),

    # ---- device (copied to the Windows laptops) ---------------------------
    ("device/Dockerfile", True, ""),
    ("device/requirements.txt", True, ""),
    ("device/tcu_agent.py", True, ""),
    ("device/.env.example", True, ""),
    ("device/docker-compose.laptopB.yml", True, ""),
    ("device/docker-compose.laptopC.yml", True, ""),
    ("device/docker-compose.laptopD.yml", True, ""),

    # ---- tools & docs -----------------------------------------------------
    ("tools/keygen.py", True, ""),
    ("docs/PRD.md", True, ""),
    ("docs/Architecture.md", True, ""),
    ("docs/Rules.md", True, ""),
    ("docs/Design.md", True, ""),
    ("docs/Memory.md", True, ""),
    ("docs/SETUP.md", True, ""),
    ("docs/DEPLOYMENT.md", True, ""),
    ("docs/ENVIRONMENT.md", True, ""),
    ("docs/PHASE1_RUNBOOK.md", True, ""),
]

# Files that are commonly dropped in the wrong place, and where they belong.
MISPLACEMENTS: list[tuple[str, str]] = [
    ("backend/test_adaptive.py", "backend/tests/test_adaptive.py"),
    ("backend/test_crypto.py", "backend/tests/test_crypto.py"),
    ("backend/constants.py", "backend/app/constants.py"),
    ("backend/adaptive.py", "backend/app/core/adaptive.py"),
    ("backend/crypto.py", "backend/app/core/crypto.py"),
    ("backend/firmware.py", "backend/app/core/firmware.py"),
    ("backend/app/adaptive.py", "backend/app/core/adaptive.py"),
    ("backend/app/crypto.py", "backend/app/core/crypto.py"),
    ("backend/app/firmware.py", "backend/app/core/firmware.py"),
    ("keygen.py", "tools/keygen.py"),
    ("link_check.py", "admin/link_check.py"),
    ("tcu_agent.py", "device/tcu_agent.py"),
    ("device/env.example", "device/.env.example"),
    ("admin/.env.example.env", "admin/.env.example"),
]

# Things that must NEVER be committed.
DANGER: list[tuple[str, str]] = [
    ("admin/secrets/convoy_ed25519_private.pem", "signing key — must be gitignored"),
    ("admin/.env", "contains live credentials — must be gitignored"),
    ("device/.env", "contains live credentials — must be gitignored"),
]

GREEN, RED, YELLOW, BLUE, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[0m"
)


def main() -> int:
    root = Path.cwd()
    if not (root / "README.md").exists() or not (root / "docs").is_dir():
        print(f"{RED}Not at the repository root.{RESET}")
        print(f"  You are in: {root}")
        print("  cd to the folder that contains README.md and docs/, then re-run:")
        print("      python tools/check_layout.py")
        return 2

    print(f"{BLUE}CONVOY layout check{RESET}  ({root})\n")

    missing_required: list[tuple[str, str]] = []
    missing_optional: list[tuple[str, str]] = []
    empty_files: list[str] = []

    for rel, required, note in EXPECTED:
        path = root / rel
        if path.exists():
            # Files that should have content but are zero bytes are a common
            # copy-paste casualty. __init__.py is legitimately empty.
            if (path.stat().st_size == 0
                    and not path.name.startswith("__init__")
                    and not path.name == ".gitkeep"):
                empty_files.append(rel)
        elif required:
            missing_required.append((rel, note))
        else:
            missing_optional.append((rel, note))

    # ---- misplaced ---------------------------------------------------------
    misplaced = [(bad, good) for bad, good in MISPLACEMENTS if (root / bad).exists()]

    # ---- report ------------------------------------------------------------
    if misplaced:
        print(f"{RED}MISPLACED FILES{RESET}")
        for bad, good in misplaced:
            print(f"  {bad}")
            print(f"    -> move to {good}")
            print(f"       mv {bad} {good}")
        print()

    if missing_required:
        print(f"{RED}MISSING (required){RESET}")
        for rel, note in missing_required:
            print(f"  {rel}" + (f"   # {note}" if note else ""))
        print()

    if empty_files:
        print(f"{YELLOW}EMPTY FILES (copy probably failed){RESET}")
        for rel in empty_files:
            print(f"  {rel}")
        print()

    if missing_optional:
        print(f"{YELLOW}NOT YET CREATED (expected at this stage){RESET}")
        for rel, note in missing_optional:
            print(f"  {rel}" + (f"   # {note}" if note else ""))
        print()

    # ---- secrets that exist: confirm they are ignored ----------------------
    gitignore = (root / ".gitignore").read_text() if (root / ".gitignore").exists() else ""
    for rel, why in DANGER:
        if (root / rel).exists():
            name = Path(rel).name
            covered = any(tok in gitignore for tok in (".env", "secrets/", "*.pem"))
            mark = f"{GREEN}ignored{RESET}" if covered else f"{RED}NOT IGNORED{RESET}"
            print(f"  secret present: {rel}  [{mark}]  {why}")
    print()

    ok = not (missing_required or misplaced)
    if ok:
        print(f"{GREEN}Layout OK.{RESET} Next:")
        print("    cd backend && python -m pytest -q")
    else:
        print(f"{RED}Fix the items above, then re-run this check.{RESET}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())