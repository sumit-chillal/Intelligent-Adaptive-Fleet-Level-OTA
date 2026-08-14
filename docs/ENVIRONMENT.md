# Environment Setup — Python version, virtualenvs, and why it matters

## The rule

**Use Python 3.12 for this project.** Not 3.13, not 3.14.

Check before you create any virtualenv:

```bash
python3 --version
```

## Why

Half of this stack is not pure Python. `pydantic-core` is written in Rust and bound with PyO3. `asyncpg` and `cryptography` ship C extensions. Maintainers publish prebuilt binary wheels only for interpreter versions they officially support.

On a supported interpreter, `pip install` downloads a wheel and finishes in seconds. On an unsupported one, pip finds no wheel, falls back to building from source, downloads an entire Rust toolchain, compiles for several minutes, and then fails with:

```
error: the configured Python interpreter version (3.14) is newer than
       PyO3's maximum supported version (3.13)
```

The suggested `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1` escape hatch is a trap. It suppresses the check and builds anyway against an ABI the library was never tested on. You would be running cryptographic and database code on an untested binary interface, in a project whose entire premise is verifying firmware signatures. Use a supported interpreter instead.

This will keep biting as Phase 2B adds SQLAlchemy, asyncpg, and FastAPI, so fix it now rather than per-package.

## Installing Python 3.12 on macOS

```bash
brew install python@3.12
/opt/homebrew/bin/python3.12 --version      # confirm 3.12.x
```

## One virtualenv, at the repository root

Right now there are two: one at `convoy/.venv` and one at `convoy/backend/.venv`. Consolidate to a single environment at the repo root — the admin tooling and the backend share dependencies, and two environments means eventually installing a package into the one you are not currently using and spending twenty minutes confused.

```bash
cd ~/Documents/Major-Project/convoy

deactivate 2>/dev/null
rm -rf .venv backend/.venv

/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
python --version                            # must print 3.12.x

pip install --upgrade pip
pip install -r admin/requirements.txt
pip install -r backend/requirements.txt
```

Verify:

```bash
cd backend && python -m pytest -q            # 38 passed
cd ../admin && python link_check.py --ping   # roster appears
```

`.venv/` is gitignored, so nothing about this reaches the repository. Your teammates on Windows never create one — their code runs inside Docker, where the interpreter version is fixed by the image.

## Container interpreter

`device/Dockerfile` now pins `python:3.12-slim`, matching the host. Rebuild on each device laptop:

```powershell
docker compose -f docker-compose.laptopB.yml up --build -d
```

Keeping host and container on the same interpreter means a bug you reproduce locally behaves identically inside the container, which matters once backend and device share protocol schema code in Phase 2B.

## Quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `Building wheel for X ... error`, Rust/cargo output | Interpreter too new, no wheel published | Recreate the venv on 3.12 |
| `ModuleNotFoundError` for something you installed | Wrong venv active | `which python` — should be `convoy/.venv/bin/python` |
| Works in terminal A, fails in terminal B | Venv not activated in B | `source .venv/bin/activate` |
| `pytest` cannot import `app.*` | Running from the wrong directory | Run from `backend/`; `pytest.ini` sets `pythonpath = .` |