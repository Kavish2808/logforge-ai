"""Environment snapshot + the dedicated benchmark database.

The benchmark never writes to the application's database: it derives
`<db name>_bench` from DATABASE_URL, creates it if needed, migrates it with
the real Alembic migrations, and refuses to reset any database whose name
does not end in `_bench`.

Values only visible from the host (git commit, Docker version, host OS) are
passed in by scripts/bench.sh through LOGFORGE_BENCH_* variables; without
them they are recorded as NOT RECORDED rather than guessed.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from logforge_bench import BENCH_VERSION
from logforge_bench.metrics import NOT_MEASURED

NOT_RECORDED = "NOT RECORDED"
BENCH_SUFFIX = "_bench"
BACKEND_DIR = Path(__file__).resolve().parent.parent


def app_database_url() -> str:
    url = os.environ.get("LOGFORGE_BENCH_APP_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        from app.config import Settings  # .env default

        url = Settings().database_url
    return url


def bench_database_url(app_url: str | None = None) -> str:
    base, _, name = (app_url or app_database_url()).rpartition("/")
    name = name.split("?")[0]
    return f"{base}/{name if name.endswith(BENCH_SUFFIX) else name + BENCH_SUFFIX}"


def configure_process_env(bench_url: str, workdir: Path, *, scheduler: bool = False) -> None:
    """Must run BEFORE any `app` module is imported (app.db.base binds its
    engine at import time)."""
    os.environ.setdefault("LOGFORGE_BENCH_APP_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
    os.environ["DATABASE_URL"] = bench_url
    os.environ["SCHEDULER_ENABLED"] = "true" if scheduler else "false"
    os.environ["RAW_VAULT_PATH"] = str(workdir / "raw_vault")
    os.environ["EVIDENCE_ANCHOR_PATH"] = str(workdir / "anchors")
    os.environ["DEBUG"] = "false"


def ensure_bench_database(app_url: str, bench_url: str) -> dict[str, Any]:
    from sqlalchemy import create_engine, text

    name = bench_url.rpartition("/")[-1]
    if not name.endswith(BENCH_SUFFIX):
        raise SystemExit(f"refusing: benchmark database '{name}' does not end with '{BENCH_SUFFIX}'")
    admin = create_engine(app_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            created = not conn.execute(text("SELECT 1 FROM pg_database WHERE datname=:n"), {"n": name}).scalar()
            if created:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
            # Benchmark database only (never the application's database or the server config): time WAL
            # writes/fsyncs so commit durability cost is measured, not guessed. Needs a superuser role;
            # without one the timing stays off and is reported as NOT MEASURED.
            try:
                conn.execute(text(f'ALTER DATABASE "{name}" SET track_wal_io_timing = on'))
            except Exception:  # noqa: BLE001
                pass
    finally:
        admin.dispose()
    proc = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR,
                          env={**os.environ, "DATABASE_URL": bench_url}, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"alembic upgrade on the benchmark database failed:\n{proc.stderr[-2000:]}")
    return {"database": name, "created": created, "migrated": "alembic upgrade head"}


def reset_bench_database(engine) -> list[str]:
    """TRUNCATE every application table of the benchmark database."""
    from sqlalchemy import text

    with engine.begin() as conn:
        name = conn.execute(text("SELECT current_database()")).scalar()
        if not name.endswith(BENCH_SUFFIX):
            raise SystemExit(f"refusing to reset '{name}': not a benchmark database")
        tables = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' "
                                   "AND tablename <> 'alembic_version' ORDER BY tablename")).scalars().all()
        if tables:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    return list(tables)


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def _cpu_model() -> str:
    info = _read("/proc/cpuinfo") or ""
    for line in info.splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.processor() or NOT_MEASURED


def _mem_total_mb() -> float | str:
    for line in (_read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            return round(int(line.split()[1]) / 1024, 1)
    return NOT_MEASURED


def _cgroup_limits() -> dict[str, Any]:
    cpu = (_read("/sys/fs/cgroup/cpu.max") or "").strip() or None
    mem = (_read("/sys/fs/cgroup/memory.max") or "").strip() or None
    return {"cpu.max": cpu or NOT_MEASURED, "memory.max": mem or NOT_MEASURED}


def _pkg_versions() -> dict[str, str]:
    from importlib import metadata

    out = {}
    for pkg in ("fastapi", "uvicorn", "SQLAlchemy", "psycopg", "pydantic", "alembic", "httpx"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = NOT_MEASURED
    return out


def _git() -> dict[str, Any]:
    commit = os.environ.get("LOGFORGE_BENCH_GIT_COMMIT")
    dirty = os.environ.get("LOGFORGE_BENCH_GIT_DIRTY")
    if not commit:
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BACKEND_DIR, capture_output=True, text=True,
                                    timeout=5).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            commit = None
    return {"commit": commit or NOT_RECORDED,
            "working_tree_dirty": {"1": True, "0": False}.get(dirty or "", NOT_RECORDED),
            "dirty_files": os.environ.get("LOGFORGE_BENCH_GIT_DIRTY_FILES", NOT_RECORDED)}


def snapshot(engine=None) -> dict[str, Any]:
    from app.config import get_settings

    s = get_settings()
    pg_version = NOT_MEASURED
    pg_settings: dict[str, Any] = {}
    if engine is not None:
        from sqlalchemy import text

        with engine.connect() as conn:
            pg_version = conn.execute(text("SELECT version()")).scalar()
            for name in ("shared_buffers", "synchronous_commit", "fsync", "wal_level", "max_connections",
                         "work_mem", "effective_cache_size", "checkpoint_timeout", "track_wal_io_timing",
                         "max_wal_size", "wal_buffers", "commit_delay"):
                pg_settings[name] = conn.execute(text(f"SHOW {name}")).scalar()
    return {
        "benchmark_version": BENCH_VERSION,
        "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
        "git": _git(),
        "runtime": {
            "where": "inside the backend container (python:3.11-slim) unless stated otherwise",
            "python": sys.version.split()[0], "implementation": platform.python_implementation(),
            "os_in_container": f"{platform.system()} {platform.release()}",
            "cpu_model": _cpu_model(), "cpu_count_visible": os.cpu_count(),
            "mem_total_mb_visible": _mem_total_mb(), "cgroup_limits": _cgroup_limits(),
            "packages": _pkg_versions(),
        },
        "host": {
            "os": os.environ.get("LOGFORGE_BENCH_HOST_OS", NOT_RECORDED),
            "docker_version": os.environ.get("LOGFORGE_BENCH_DOCKER_VERSION", NOT_RECORDED),
            "docker_info": os.environ.get("LOGFORGE_BENCH_DOCKER_INFO", NOT_RECORDED),
            "cpu": os.environ.get("LOGFORGE_BENCH_HOST_CPU", NOT_RECORDED),
            "ram": os.environ.get("LOGFORGE_BENCH_HOST_RAM", NOT_RECORDED),
        },
        "postgresql": {"version": pg_version, "settings": pg_settings,
                       "note": "the benchmark database shares the PostgreSQL server of the running dev stack"},
        "app_config": {
            "drift_enabled": s.drift_enabled, "drift_similarity_threshold": s.drift_similarity_threshold,
            "raw_vault_enabled": s.raw_vault_enabled, "raw_vault_backend": s.raw_vault_backend,
            "extension_inline_max_bytes": s.extension_inline_max_bytes,
            "extension_inline_max_fields": s.extension_inline_max_fields,
            "merkle_batch_max_events": s.merkle_batch_max_events, "merkle_seal_grace_seconds": s.merkle_seal_grace_seconds,
            "scheduler_enabled": os.environ.get("SCHEDULER_ENABLED"),
            "phase8_enabled": s.phase8_enabled, "rbac_mode": s.rbac_mode,
        },
    }
