"""Cold raw vault (byte-for-byte preservation, content addressing, failure
handling) and the local WORM-style anchor store."""
import hashlib
import json
import os
import stat

import pytest

from app.evidence.anchor import AnchorError, LocalWormAnchorStore
from app.evidence.raw_vault import FilesystemRawVault, VaultError, VaultIntegrityError, object_key

PAYLOADS = [
    b"plain ascii syslog line",
    "CRLF line\r\nwith trailing spaces   \r\n".encode(),
    "unicode ✓ ü 日本語 🚀  nbsp".encode(),
    b"\ttabs\tand \\ backslashes \"quotes\" ' ",
    ("x" * 300_000).encode(),
    b"",
]


@pytest.mark.parametrize("data", PAYLOADS)
def test_vault_round_trip_is_byte_identical(tmp_path, data):
    vault = FilesystemRawVault(tmp_path)
    obj = vault.put(data)
    assert obj.sha256 == hashlib.sha256(data).hexdigest() and obj.size == len(data)
    assert obj.key == object_key(obj.sha256)
    assert vault.get(obj.key) == data
    assert vault.verify(obj.key, obj.sha256)


def test_put_is_idempotent_and_content_addressed(tmp_path):
    vault = FilesystemRawVault(tmp_path)
    a = vault.put(b"same")
    b = vault.put(b"same")
    assert a == b and len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1


def test_objects_are_written_read_only(tmp_path):
    vault = FilesystemRawVault(tmp_path)
    obj = vault.put(b"immutable")
    mode = os.stat(tmp_path.joinpath(*obj.key.split("/"))).st_mode
    assert not mode & stat.S_IWUSR or os.name == "nt"


def test_corrupted_object_is_detected(tmp_path):
    vault = FilesystemRawVault(tmp_path)
    obj = vault.put(b"original bytes")
    path = tmp_path.joinpath(*obj.key.split("/"))
    os.chmod(path, 0o644)
    path.write_bytes(b"tampered bytes")
    with pytest.raises(VaultIntegrityError):
        vault.get(obj.key)
    assert not vault.verify(obj.key, obj.sha256)
    with pytest.raises(VaultIntegrityError):
        vault.put(b"original bytes")  # never silently "repairs" or overwrites


def test_missing_object_and_invalid_keys_raise(tmp_path):
    vault = FilesystemRawVault(tmp_path)
    with pytest.raises(VaultError):
        vault.get(object_key("0" * 64))
    for bad in ("../etc/passwd", "sha256/aa/bb/notahash", "sha256/../../x"):
        with pytest.raises(VaultError):
            vault.get(bad)


def test_unwritable_vault_raises_vault_error(tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    with pytest.raises(VaultError):
        FilesystemRawVault(blocker).put(b"data")


def record(seq: int, root: str = "a" * 64) -> dict:
    return {"seq": seq, "batch_id": f"B{seq}", "root_hash": root, "prev_chain_hash": "0" * 64,
            "chain_hash": "c" * 64, "event_count": 3, "start_time": "t0", "end_time": "t1"}


def test_anchor_append_once_and_read_back(tmp_path):
    store = LocalWormAnchorStore(tmp_path)
    store.append(record(1))
    assert store.get(1) == record(1)
    assert store.append(record(1)) == "000000000001.anchor.json"  # identical re-anchor is idempotent
    with pytest.raises(AnchorError):
        store.append(record(1, root="b" * 64))  # never overwritten with different content
    assert store.get(2) is None
    assert [r["seq"] for r in store.list()] == [1]


def test_modified_anchor_file_is_detected(tmp_path):
    store = LocalWormAnchorStore(tmp_path)
    store.append(record(5))
    path = tmp_path / "000000000005.anchor.json"
    os.chmod(path, 0o644)
    envelope = json.loads(path.read_text())
    envelope["record"]["root_hash"] = "f" * 64
    path.write_text(json.dumps(envelope))
    with pytest.raises(AnchorError):
        store.get(5)
