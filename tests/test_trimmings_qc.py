"""Proof that the Fresh 90s checks fire on bad input and stay quiet on good.

A guard nobody has watched fail is not a guard. Every case below is a real row
from LM_XB401 or LM_XB460, not a hypothetical -- the bad day is 2026-09-18, the
day that printed -$82.58 and prompted all of this, and the quiet cases are the
historical extremes the checks have to tolerate without crying wolf.

    .venv/Scripts/python.exe -m pytest tests/test_trimmings_qc.py -q
"""

import os
import sys
from datetime import timedelta

import pandas as pd
import pytest

# This file is byte-identical in the livestock-portal repo and in the
# standalone beef-trimmings-dashboard repo, where the page sits at the repo
# root instead of under apps/. Find trimmings_qc wherever it lives rather than
# forking the test.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.join(_HERE, "..", "apps", "beef_trimmings"),
                   os.path.join(_HERE, "..")):
    if os.path.isfile(os.path.join(_candidate, "trimmings_qc.py")):
        sys.path.insert(0, _candidate)
        break
else:  # pragma: no cover
    raise RuntimeError("trimmings_qc.py not found next to app.py")

from trimmings_qc import (  # noqa: E402
    DISPERSION_LIMIT,
    DIVERGENCE_LIMIT,
    PREV,
    assess_print,
    changes,
    dispersion,
    divergence,
)


# ═══ 1. Is the print a clean read of the market? ═════════════════════════════

# LM_XB401 2026-09-18, Chemical Lean Fresh 90%:
#   National  15 trades  444,658 lb  259.00-440.15  wtd avg 345.42
#   Central    3 trades  125,996 lb  434.00-440.15  wtd avg 436.15
BAD_DAY = dict(national_avg=345.42, central_avg=436.15, low=259.00, high=440.15)

# LM_XB401 2026-09-16, the previous priced day -- an ordinary print.
#   National  24 trades  878,691 lb  412.00-450.75  wtd avg 428.00
#   Central                          412.00-450.75  wtd avg 427.80
GOOD_DAY = dict(national_avg=428.00, central_avg=427.80, low=412.00, high=450.75)


def test_fires_on_the_day_that_prompted_it():
    a = assess_print(**BAD_DAY)
    assert a.flagged
    assert a.divergence == pytest.approx(-90.73, abs=0.01)
    assert a.dispersion == pytest.approx(181.15, abs=0.01)
    assert len(a.reasons) == 2, "both divergence and dispersion should trip"


def test_quiet_on_the_ordinary_day_before_it():
    a = assess_print(**GOOD_DAY)
    assert not a.flagged
    assert a.reasons == ()
    assert a.divergence == pytest.approx(0.20, abs=0.01)
    assert a.dispersion == pytest.approx(38.75, abs=0.01)


# ── The trap: Central is unpriced on 259 of 623 days ─────────────────────────

@pytest.mark.parametrize("central", [0, 0.0, "0.00", "", None, "NA"])
def test_unpriced_central_never_fires(central):
    """A naive national-minus-central reads ~430 here and would fire on 40% of days."""
    a = assess_print(national_avg=430.00, central_avg=central, low=428.00, high=433.00)
    assert a.divergence is None
    assert not a.flagged


def test_unpriced_national_is_not_assessed():
    a = assess_print(national_avg=0.00, central_avg=0.00, low=0.00, high=0.00)
    assert a.divergence is None and a.dispersion is None
    assert not a.flagged


# ── Historical extremes it must tolerate ─────────────────────────────────────

def test_widest_ordinary_dispersion_stays_quiet():
    """2023-12-18 held the record range at $68.10 -- under the $75 limit."""
    assert dispersion(192.40, 260.50) == pytest.approx(68.10, abs=0.01)
    assert dispersion(192.40, 260.50) < DISPERSION_LIMIT


def test_most_negative_ordinary_divergence_stays_quiet():
    """2025-01-07: national 336.65 vs central 352.05, the widest negative gap."""
    assert divergence(336.65, 352.05) == pytest.approx(-15.40, abs=0.01)
    assert not assess_print(336.65, 352.05, 330.00, 355.00).flagged


def test_known_single_baseline_trip_is_deliberate():
    """2023-12-18 diverged +31.88 and was itself the 2nd-largest move on record.

    It is the only day in 364 that trips the limit. Documented, not accidental --
    if a threshold change makes this quiet, the guard got looser on purpose.
    """
    a = assess_print(226.62, 194.74, 192.40, 260.50)
    assert a.divergence == pytest.approx(31.88, abs=0.01)
    assert a.flagged


def test_limits_are_exclusive():
    """Exactly at the limit is not a trip; a cent past it is."""
    base = 400.00
    assert not assess_print(base, base - DIVERGENCE_LIMIT, 399.0, 401.0).flagged
    assert assess_print(base, base - DIVERGENCE_LIMIT - 0.01, 399.0, 401.0).flagged

    lo = 300.00
    assert not assess_print(base, base, lo, lo + DISPERSION_LIMIT).flagged
    assert assess_print(base, base, lo, lo + DISPERSION_LIMIT + 0.01).flagged


def test_inverted_range_is_rejected_not_negative():
    assert dispersion(440.15, 259.00) is None


# ═══ 2. Comparing against the right prior point ══════════════════════════════

def _frame(dates, values):
    return pd.DataFrame({"d": pd.to_datetime(dates), "v": values})


# LM_XB460 national Fresh 90% weekly average, the three most recent weeks.
WEEKLY = _frame(["2026-09-04", "2026-09-11", "2026-09-18"], [449.29, 436.74, 408.40])

# LM_XB401 national Fresh 90%, three CONSECUTIVE priced sessions.
DAILY_RUN = _frame(["2026-08-25", "2026-08-26", "2026-08-27"], [459.88, 446.16, 455.82])


def test_weekly_change_is_week_over_week():
    """-28.34 against 09/11, not -40.89 against 09/04."""
    cur, chg = changes(WEEKLY, "d", "v", [PREV])
    assert cur == pytest.approx(408.40)
    assert chg == pytest.approx(-28.34, abs=0.01)


def test_eight_day_offset_really_does_skip_a_week():
    """The old behaviour, pinned so the regression stays visible.

    A weekly series is spaced exactly 7 days, so an 8-day lookback lands before
    the previous report and steps over it entirely.
    """
    _, skipped = changes(WEEKLY, "d", "v", [timedelta(days=8)])
    assert skipped == pytest.approx(-40.89, abs=0.01)
    assert skipped != pytest.approx(-28.34, abs=0.01)


def test_day_change_on_consecutive_sessions_had_the_wrong_sign():
    """2026-08-27 vs 08-26 is +9.66; the old 2-day offset gave -4.06 vs 08-25."""
    _, correct = changes(DAILY_RUN, "d", "v", [PREV])
    assert correct == pytest.approx(9.66, abs=0.01)

    _, old = changes(DAILY_RUN, "d", "v", [timedelta(days=2)])
    assert old == pytest.approx(-4.06, abs=0.01)
    assert (correct > 0) and (old < 0), "the offset inverted the direction"


def test_prev_skips_unpriced_rows():
    """PREV means the previous OBSERVATION, not the previous row."""
    df = _frame(["2026-09-14", "2026-09-15", "2026-09-16"], [435.27, None, 428.00])
    _, chg = changes(df, "d", "v", [PREV])
    assert chg == pytest.approx(428.00 - 435.27, abs=0.01)


def test_prev_needs_two_observations():
    cur, chg = changes(_frame(["2026-09-18"], [408.40]), "d", "v", [PREV])
    assert cur == pytest.approx(408.40)
    assert chg is None


def test_empty_frame_returns_the_right_arity():
    assert changes(_frame([], []), "d", "v", [PREV, timedelta(days=30)]) == (None, None, None)


def test_timedelta_still_means_elapsed_time():
    """Month/year tiles keep offset semantics -- PREV must not have replaced them."""
    df = _frame(["2026-06-18", "2026-08-19", "2026-09-18"], [460.00, 450.00, 408.40])
    _, month = changes(df, "d", "v", [timedelta(days=30)])
    assert month == pytest.approx(408.40 - 450.00, abs=0.01)


def test_prev_and_offset_mix_in_one_call():
    df = _frame(["2025-09-18", "2026-08-19", "2026-09-11", "2026-09-18"],
                [494.39, 450.00, 436.74, 408.40])
    cur, prev, month, year = changes(df, "d", "v", [PREV, timedelta(days=30), timedelta(days=365)])
    assert cur == pytest.approx(408.40)
    assert prev == pytest.approx(-28.34, abs=0.01)
    assert month == pytest.approx(-41.60, abs=0.01)
    assert year == pytest.approx(-85.99, abs=0.01)
