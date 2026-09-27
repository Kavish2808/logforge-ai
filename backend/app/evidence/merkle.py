"""Deterministic Merkle trees over event hashes, and the batch chain hash.

Domain-separated SHA-256 (RFC 6962 style) so a leaf can never be confused
with an interior node:
    leaf  = SHA256(0x00 || "<event_id>:<raw_sha256>")
    node  = SHA256(0x01 || left || right)
An odd node at any level is promoted unchanged to the next level (no
duplication, which would let two different leaf lists share a root).

Batches are chained:
    chain_hash = SHA256("logforge-chain-v1|<seq>|<prev_chain_hash>|<root>|<event_count>")
so removing, reordering or altering any sealed batch breaks every later link.
"""
from __future__ import annotations

import hashlib

LEAF_ALGORITHM = "lf-merkle-v1:sha256:rfc6962-domain-sep:odd-promote"  # <= 64 chars (stored)
GENESIS = "0" * 64


def leaf_hash(event_id: str, raw_sha256: str) -> str:
    return hashlib.sha256(b"\x00" + f"{event_id}:{raw_sha256}".encode("utf-8")).hexdigest()


def _node(left: str, right: str) -> str:
    return hashlib.sha256(b"\x01" + bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()


def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        raise ValueError("a Merkle tree needs at least one leaf")
    level = list(leaves)
    while len(level) > 1:
        nxt = [_node(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0]


def inclusion_proof(leaves: list[str], index: int) -> list[dict[str, str]]:
    """Audit path for leaves[index]: [{"side": "L"|"R", "hash": sibling}, ...]."""
    if not 0 <= index < len(leaves):
        raise IndexError("leaf index out of range")
    proof: list[dict[str, str]] = []
    level = list(leaves)
    i = index
    while len(level) > 1:
        if i % 2 == 1:
            proof.append({"side": "L", "hash": level[i - 1]})
        elif i + 1 < len(level):
            proof.append({"side": "R", "hash": level[i + 1]})
        # else: odd last node, promoted without a sibling
        nxt = [_node(level[j], level[j + 1]) for j in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
        i //= 2
    return proof


def verify_inclusion(leaf: str, proof: list[dict[str, str]], root: str) -> bool:
    current = leaf
    for step in proof:
        if step.get("side") == "L":
            current = _node(step["hash"], current)
        elif step.get("side") == "R":
            current = _node(current, step["hash"])
        else:
            return False
    return current == root


def chain_hash(seq: int, prev_chain_hash: str, root: str, event_count: int) -> str:
    return hashlib.sha256(f"logforge-chain-v1|{seq}|{prev_chain_hash}|{root}|{event_count}".encode()).hexdigest()
