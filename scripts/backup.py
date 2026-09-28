"""Take a native SQLite or PostgreSQL backup and write a SHA-256 sidecar.

Credentials are passed through process environment, never command arguments.
This is a backup helper, not an immutable archive or a retention policy.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import unquote, urlparse

from run_local import ROOT, load_environment


def postgres_environment(url: str) -> dict:
    parsed = urlparse(url.replace("postgresql+psycopg://", "postgresql://"))
    values = {"PGHOST": parsed.hostname or "localhost", "PGPORT": str(parsed.port or 5432),
              "PGDATABASE": unquote(parsed.path.lstrip("/")), "PGUSER": unquote(parsed.username or ""),
              "PGPASSWORD": unquote(parsed.password or "")}
    if parsed.query:
        raise ValueError("For TLS/options use standard PGSSL* environment variables instead of URL query parameters in this helper.")
    return {**os.environ, **values}


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    load_environment()
    url = os.environ.get("DATABASE_URL", "sqlite:///./data/logforge.db")
    target = args.output.resolve()
    if target.exists():
        raise FileExistsError("Refusing to overwrite an existing backup")
    target.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("sqlite:///"):
        source = Path(url.removeprefix("sqlite:///"))
        if not source.is_absolute():
            source = ROOT / "backend" / source
        if not source.is_file():
            raise FileNotFoundError("Source database does not exist")
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as database, sqlite3.connect(target) as backup:
            database.backup(backup)
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        kind = "sqlite-online-backup"
    elif url.startswith("postgresql"):
        subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--file", str(target)], env=postgres_environment(url), check=True)
        kind = "postgresql-custom-dump"
    else:
        raise ValueError("Unsupported database scheme")
    target.with_suffix(target.suffix + ".sha256").write_text(digest_file(target) + "\n", encoding="ascii")
    target.with_suffix(target.suffix + ".json").write_text(json.dumps({"format": kind, "bytes": target.stat().st_size,
        "warning": "Back up signing secrets separately. Verify restoration and /api/integrity before relying on this backup."}, indent=2), encoding="utf-8")
    print(f"Backup and checksum created at {target}")


if __name__ == "__main__":
    main()
