"""R1: identity is user_id, never display name."""
from __future__ import annotations

from ..ingest.chain import walk_chain
from ..ingest.identity import build_identities, alias_warnings
from .fixtures import FakeClient, U1, U2, U3


def _identities():
    client = FakeClient()
    chain = walk_chain(client, "L2021")
    return build_identities(client, chain)


def test_one_account_two_names_collapses_to_one():
    idents = _identities()
    # U1 was LACrams then TheGrimGaffer -> a single account with both names.
    assert set(idents[U1].names) == {"LACrams", "TheGrimGaffer"}
    assert idents[U1].canonical == "TheGrimGaffer"  # most-recent season name
    assert U1 in alias_warnings(idents)


def test_near_identical_names_different_ids_stay_separate():
    idents = _identities()
    # "daAverageJoes" (U2) and "DaAverageJoes65" (U3) must NOT be merged.
    assert U2 in idents and U3 in idents
    assert idents[U2].canonical == "daAverageJoes"
    assert idents[U3].canonical == "DaAverageJoes65"
    assert idents[U2].user_id != idents[U3].user_id
    # neither is an alias (each used exactly one name)
    assert U2 not in alias_warnings(idents)
    assert U3 not in alias_warnings(idents)


def test_exactly_one_alias_account():
    idents = _identities()
    assert set(alias_warnings(idents).keys()) == {U1}
