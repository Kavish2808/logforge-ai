"""LogForge AI benchmark + scale-evidence tooling (Phase 8 Step 10).

Isolated from the production code: nothing under `app/` imports this package,
and it is excluded from the backend image (.dockerignore). It drives the REAL
ingestion code (in-process) or the REAL HTTP API (a separate uvicorn process)
against a dedicated `<db>_bench` database, and records what it measured.

Run inside the backend container:  python -m logforge_bench --help
"""

BENCH_VERSION = "1.0.0"
