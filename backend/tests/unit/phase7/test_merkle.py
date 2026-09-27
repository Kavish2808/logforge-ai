"""Merkle trees: deterministic roots, inclusion proofs for every leaf, tamper detection."""
import hashlib

import pytest

from app.evidence import merkle


def leaves(n: int) -> list[str]:
    return [merkle.leaf_hash(f"EVT{i:023d}", hashlib.sha256(str(i).encode()).hexdigest()) for i in range(n)]


def test_single_leaf_root_is_the_leaf():
    ls = leaves(1)
    assert merkle.merkle_root(ls) == ls[0]


def test_empty_tree_is_rejected():
    with pytest.raises(ValueError):
        merkle.merkle_root([])


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 33])
def test_every_leaf_has_a_valid_inclusion_proof(n):
    ls = leaves(n)
    root = merkle.merkle_root(ls)
    for i, leaf in enumerate(ls):
        assert merkle.verify_inclusion(leaf, merkle.inclusion_proof(ls, i), root)


def test_proof_fails_for_modified_leaf_or_root():
    ls = leaves(9)
    root = merkle.merkle_root(ls)
    proof = merkle.inclusion_proof(ls, 4)
    tampered = merkle.leaf_hash("EVT-tampered", "0" * 64)
    assert not merkle.verify_inclusion(tampered, proof, root)
    assert not merkle.verify_inclusion(ls[4], proof, "f" * 64)
    assert not merkle.verify_inclusion(ls[4], [{"side": "X", "hash": ls[0]}], root)


def test_root_changes_with_any_leaf_order_or_content():
    ls = leaves(6)
    root = merkle.merkle_root(ls)
    assert merkle.merkle_root(list(reversed(ls))) != root
    assert merkle.merkle_root(ls[:-1]) != root
    assert merkle.merkle_root(ls[:-1] + [ls[-1][::-1]]) != root
    assert merkle.merkle_root(list(ls)) == root  # deterministic


def test_leaf_and_node_are_domain_separated():
    a, b = leaves(2)
    # An interior node value can never be passed off as a leaf of the same content.
    assert merkle.merkle_root([a, b]) != merkle.leaf_hash(a, b)


def test_odd_promotion_does_not_equal_duplication():
    ls = leaves(3)
    assert merkle.merkle_root(ls) != merkle.merkle_root(ls + [ls[-1]])


def test_chain_hash_binds_sequence_previous_root_and_count():
    base = merkle.chain_hash(2, "a" * 64, "b" * 64, 10)
    assert base != merkle.chain_hash(3, "a" * 64, "b" * 64, 10)
    assert base != merkle.chain_hash(2, "c" * 64, "b" * 64, 10)
    assert base != merkle.chain_hash(2, "a" * 64, "d" * 64, 10)
    assert base != merkle.chain_hash(2, "a" * 64, "b" * 64, 11)
