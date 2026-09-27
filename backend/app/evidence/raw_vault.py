"""Cold raw vault: a content-addressed object store for exact raw bytes.

The interface (RawVault) is what the rest of the system uses; Phase 7 ships
a local-filesystem implementation that works in Docker and air-gapped
deployments. An S3/MinIO-compatible implementation only has to implement
put/get/exists/delete-free semantics with the same object keys — ingestion
semantics do not change.

Object key = "sha256/<aa>/<bb>/<sha256>" of the exact bytes stored, so:
- the key *is* the integrity metadata (re-hashing the object must give it);
- identical raw payloads are stored once (idempotent, safe to retry);
- an object is never overwritten with different content.
"""
from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.evidence.canonical import sha256_bytes


class VaultError(Exception):
    """Any failure to store or read an object. Never silently ignored."""


class VaultIntegrityError(VaultError):
    """An object's bytes no longer hash to its key."""


@dataclass(frozen=True)
class VaultObject:
    backend: str
    key: str
    sha256: str
    size: int


def object_key(sha256: str) -> str:
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise VaultError("object keys are lowercase hex SHA-256 digests")
    return f"sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"


class RawVault(ABC):
    backend: str = "abstract"

    @abstractmethod
    def put(self, data: bytes) -> VaultObject:
        """Store `data`; return its content address. Idempotent."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return the exact bytes stored under `key` (integrity-checked)."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    def verify(self, key: str, expected_sha256: str) -> bool:
        try:
            return sha256_bytes(self.get(key)) == expected_sha256
        except VaultError:
            return False

    def describe(self) -> dict:
        return {"backend": self.backend}


class FilesystemRawVault(RawVault):
    backend = "filesystem"

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if len(parts) != 4 or parts[0] != "sha256" or object_key(parts[3]) != key:
            raise VaultError("invalid object key")
        return self.root.joinpath(*parts)

    def put(self, data: bytes) -> VaultObject:
        digest = sha256_bytes(data)
        key = object_key(digest)
        path = self._path(key)
        try:
            if path.exists():
                # Content-addressed: an existing object must already hold these bytes.
                if sha256_bytes(path.read_bytes()) != digest:
                    raise VaultIntegrityError(f"existing object {key} does not match its address")
                return VaultObject(self.backend, key, digest, len(data))
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)  # atomic publish; readers never see partial objects
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            try:
                os.chmod(path, 0o444)  # objects are immutable once written
            except OSError:
                pass
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError(f"filesystem vault write failed ({type(exc).__name__})") from exc
        return VaultObject(self.backend, key, digest, len(data))

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise VaultError(f"object {key} not found") from exc
        except OSError as exc:
            raise VaultError(f"filesystem vault read failed ({type(exc).__name__})") from exc
        if sha256_bytes(data) != key.rsplit("/", 1)[-1]:
            raise VaultIntegrityError(f"object {key} failed integrity verification")
        return data

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def describe(self) -> dict:
        return {"backend": self.backend, "root": str(self.root)}


_override: RawVault | None = None


def set_vault(vault: RawVault | None) -> None:
    """Swap the process-wide vault (tests, or a future S3 backend)."""
    global _override
    _override = vault


def _filesystem(settings) -> RawVault:
    return FilesystemRawVault(settings.raw_vault_path)


# Vault backends by name. A future S3/MinIO-compatible backend is registered
# here; every stored object records the backend it was written to, and
# recovery/verification resolve that object's vault by name.
BACKENDS: dict[str, object] = {"filesystem": _filesystem}


def get_vault(backend: str | None = None) -> RawVault:
    """The configured vault, or the vault for a recorded backend name."""
    from app.config import get_settings

    settings = get_settings()
    name = backend or settings.raw_vault_backend
    if _override is not None and (backend is None or backend == _override.backend):
        return _override
    factory = BACKENDS.get(name)
    if factory is None:
        raise VaultError(f"raw vault backend '{name}' is not available in this build")
    return factory(settings)
