"""Immutable anchor abstraction for sealed Merkle batch roots.

An anchor store receives each sealed batch's (seq, batch_id, root, chain
hash) exactly once and must never allow it to be changed afterwards. The
database copy of the chain is then verified against the anchors: tampering
with the database alone is detectable as long as the anchor store is not
writable by the same party.

Phase 7 ships LocalWormAnchorStore: one file per anchor, created with
O_CREAT|O_EXCL (never overwritten) and made read-only. It is WORM-*style*:
a local administrator can still delete or edit files, so it raises the bar
but is not a compliance-grade WORM device. A future implementation (S3
Object Lock, an immutable storage appliance, an RFC 3161 timestamping
service) only needs to implement `append` / `get` / `list`.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.evidence.canonical import canonical_bytes, canonical_sha256


class AnchorError(Exception):
    pass


class AnchorStore(ABC):
    backend = "abstract"
    # True only for providers whose immutability is enforced outside this
    # process's control (e.g. object-lock in COMPLIANCE mode). Never claimed locally.
    compliance_grade = False

    @abstractmethod
    def append(self, record: dict[str, Any]) -> str:
        """Persist an anchor record immutably; return its reference."""

    @abstractmethod
    def get(self, seq: int) -> dict[str, Any] | None: ...

    @abstractmethod
    def list(self) -> list[dict[str, Any]]: ...

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "compliance_grade": self.compliance_grade}


def _envelope(record: dict[str, Any]) -> dict[str, Any]:
    return {"record": record, "record_sha256": canonical_sha256(record)}


class LocalWormAnchorStore(AnchorStore):
    backend = "local_worm"

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def _path(self, seq: int) -> Path:
        return self.root / f"{int(seq):012d}.anchor.json"

    def append(self, record: dict[str, Any]) -> str:
        seq = int(record["seq"])
        path = self._path(seq)
        data = canonical_bytes(_envelope(record))
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        except FileExistsError as exc:
            existing = self.get(seq)
            if existing == record:
                return path.name  # idempotent re-anchor of identical content
            raise AnchorError(f"anchor {seq} already exists with different content; refusing to overwrite") from exc
        except OSError as exc:
            raise AnchorError(f"anchor write failed ({type(exc).__name__})") from exc
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise AnchorError(f"anchor write failed ({type(exc).__name__})") from exc
        return path.name

    def get(self, seq: int) -> dict[str, Any] | None:
        path = self._path(seq)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise AnchorError(f"anchor {seq} is unreadable") from exc
        record = envelope.get("record")
        if not isinstance(record, dict) or canonical_sha256(record) != envelope.get("record_sha256"):
            raise AnchorError(f"anchor {seq} failed its self-check (modified)")
        return record

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        out = []
        for path in sorted(self.root.glob("*.anchor.json")):
            try:
                out.append(self.get(int(path.name.split(".")[0])))
            except (AnchorError, ValueError):
                out.append({"seq": path.name, "error": "unreadable or modified"})
        return out

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "root": str(self.root), "compliance_grade": self.compliance_grade,
                "note": "local append-only files (O_EXCL, read-only); not a compliance-grade WORM device"}


_override: AnchorStore | None = None


def set_anchor_store(store: AnchorStore | None) -> None:
    global _override
    _override = store


def _local_worm(settings) -> AnchorStore:
    return LocalWormAnchorStore(settings.evidence_anchor_path)


# Anchor providers by backend name. A future object-lock / WORM provider is
# registered here; every sealed batch records the backend it was anchored to,
# and verification resolves that batch's store by name.
PROVIDERS: dict[str, Any] = {"local_worm": _local_worm}


def get_anchor_store(backend: str | None = None) -> AnchorStore:
    """The configured anchor store, or the store for a recorded backend name."""
    from app.config import get_settings

    settings = get_settings()
    name = backend or settings.evidence_anchor_backend
    if _override is not None and (backend is None or backend == _override.backend):
        return _override
    factory = PROVIDERS.get(name)
    if factory is None:
        raise AnchorError(f"anchor backend '{name}' is not available in this build")
    return factory(settings)
