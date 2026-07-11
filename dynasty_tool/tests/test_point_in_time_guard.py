"""R3: point_in_time must fail loudly and never fall back to current values."""
from __future__ import annotations

import pytest

from ..value import make_provider, PointInTimeUnavailable
from ..value.provider import REALIZED
from ..value.historical_provider import HistoricalValueProvider
from .fixtures import make_test_provider


def test_point_in_time_without_source_raises():
    with pytest.raises(PointInTimeUnavailable):
        make_provider("point_in_time")


def test_point_in_time_does_not_fall_back_to_current_values():
    current = make_test_provider()
    # Even when a perfectly good current provider is available, point_in_time
    # must refuse it rather than silently mislabel current values.
    with pytest.raises(PointInTimeUnavailable):
        make_provider("point_in_time", current_provider=current)


def test_guard_message_names_what_is_missing():
    try:
        make_provider("point_in_time")
    except PointInTimeUnavailable as e:
        msg = str(e).lower()
        assert "historical" in msg
        assert "current" in msg  # explains the refusal to fall back
    else:
        pytest.fail("expected PointInTimeUnavailable")


def test_realized_basis_still_works():
    prov = make_provider("realized", current_provider=make_test_provider())
    assert prov.basis == REALIZED
    val, matched = prov.player_value("200")
    assert matched and val == 5000


def test_historical_provider_stub_raises_not_implemented():
    prov = HistoricalValueProvider(source=object(), qb_format=1)
    with pytest.raises(NotImplementedError):
        prov.player_value("200")
    with pytest.raises(NotImplementedError):
        prov.pick_value("2027", 1)
