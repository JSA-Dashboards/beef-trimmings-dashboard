"""Proof that the Proclamation 11059 quota parser reads CBP correctly.

Every fixture below is a verbatim line from a real Commodity Status Report, so
a change in CBP's layout fails here rather than silently zeroing the tracker.

    .venv/Scripts/python.exe -m pytest tests/test_quota_tracker.py -q
"""

import os
import sys
from datetime import date

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.join(_HERE, "..", "apps", "beef_trimmings"),
                   os.path.join(_HERE, "..")):
    if os.path.isfile(os.path.join(_candidate, "quota_tracker.py")):
        sys.path.insert(0, _candidate)
        break
else:  # pragma: no cover
    raise RuntimeError("quota_tracker.py not found next to app.py")

from quota_tracker import (  # noqa: E402
    TRANCHES,
    Fill,
    pace,
    parse_fill,
    project_final,
    report_date,
    tranche_for,
)


def _report(as_of, *lines):
    """A report's extracted text: the header the date comes from, plus rows."""
    return "Commodity Status Report {} Quota/License ID Number\n".format(as_of) + "\n".join(lines)


# Verbatim from 26_0921_commodity_status_report.pdf.
SEP21 = ("0299035402BEEF Affordable Beef - OTHR 202601 09/01/2026 09/30/2026 - "
         "100000000 KG 31128697.86 31.13% OPEN -")
SEP14 = ("0299035402BEEF Affordable Beef - OTHR 202601 09/01/2026 09/30/2026 - "
         "100000000 KG 22156145.96 22.16% OPEN -")
SEP08 = ("0299035402BEEF Affordable Beef - OTHR 202601 09/01/2026 09/30/2026 - "
         "100000000 KG 15369800.91 15.37% OPEN -")

# Other beef lines from the same page, none of which is this quota.
ARGENTINA = ("0201101BEEF03 Beef ARGENTINA - 202601 01/01/2026 12/31/2026 - "
             "20000000 KG 17248548.4 86.24% OPEN -")
USTR_AR = ("025085ARBEEF USTR AR BEEF ARGENTINA - 202603 07/01/2026 09/30/2026 - "
           "20000000 KG 20000000 100.00% FILL 08/28/2026 00:00: AM")
OTHR_REGULAR = ("0201101BEEF03 Beef - OTHR 202601 01/01/2026 12/31/2026 - "
                "52005000 KG 52005000 100.00% FILL 01/06/2026 00:00: AM")
EXCLUDED = ("0299035402BEEF Affordable Beef CANADA - 202601 09/01/2026 09/30/2026 - "
            "0 KG - 0.00% EXCL -")


# ── Reading one report ───────────────────────────────────────────────────────

def test_reads_the_real_september_21_line():
    f = parse_fill(_report("September 21, 2026", ARGENTINA, SEP21, OTHR_REGULAR))
    assert f.as_of == date(2026, 9, 21)
    assert f.period_start == date(2026, 9, 1) and f.period_end == date(2026, 9, 30)
    assert f.limit_kg == pytest.approx(100_000_000)
    assert f.entered_kg == pytest.approx(31_128_697.86)
    assert f.fill_pct == pytest.approx(31.13)
    assert f.status == "OPEN"


def test_header_date_beats_the_filename():
    """File names disagree on format and one carries no year at all."""
    assert report_date(_report("August 31, 2026")) == date(2026, 8, 31)
    assert report_date("no header here") is None


def test_pre_proclamation_report_has_no_line():
    """Every report before 2026-09-01 predates the quota. None, not an error."""
    assert parse_fill(_report("August 31, 2026", ARGENTINA, OTHR_REGULAR)) is None


# ── The trap: three other beef quotas sit on the same page ───────────────────

@pytest.mark.parametrize("line", [ARGENTINA, USTR_AR, OTHR_REGULAR])
def test_other_beef_quotas_are_never_mistaken_for_this_one(line):
    """Argentina is NOT in this quota, and the ordinary OTHR beef TRQ filled in
    January. Matching either would report a wrong number with total confidence."""
    assert parse_fill(_report("September 21, 2026", line)) is None


def test_excluded_country_rows_are_not_the_quota():
    """Canada/Mexico appear under the same quota id with a 0 kg EXCL row."""
    assert parse_fill(_report("September 21, 2026", EXCLUDED)) is None


def test_picks_the_quota_out_of_a_full_page():
    f = parse_fill(_report("September 21, 2026", ARGENTINA, USTR_AR, EXCLUDED,
                           SEP21, OTHR_REGULAR))
    assert f.entered_kg == pytest.approx(31_128_697.86)


# ── Tranche boundaries ───────────────────────────────────────────────────────

def test_tranche_windows_match_the_bulletin():
    assert [(t.number, t.start, t.end) for t in TRANCHES] == [
        (1, date(2026, 9, 1), date(2026, 9, 30)),
        (2, date(2026, 10, 1), date(2026, 10, 30)),
        (3, date(2026, 10, 31), date(2026, 11, 30)),
    ]


def test_october_31_belongs_to_tranche_3_not_2():
    """The boundary is Oct 30 / Oct 31, not a month split. A naive
    month-based tranche would put Oct 31 in tranche 2, which closed."""
    assert tranche_for(date(2026, 10, 31)).number == 3
    assert tranche_for(date(2026, 10, 1)).number == 2
    assert tranche_for(date(2026, 11, 1)) is None, "matched on start date only"


def test_each_tranche_is_its_own_100000_mt():
    """Not one 300,000 mt pool. Unused quantity does not carry forward."""
    assert all(t.limit_kg == 100_000_000 for t in TRANCHES)
    assert sum(t.limit_kg for t in TRANCHES) == 300_000_000


# ── Pace and projection ──────────────────────────────────────────────────────

REAL = [parse_fill(_report(d, ln)) for d, ln in
        (("September 8, 2026", SEP08), ("September 14, 2026", SEP14),
         ("September 21, 2026", SEP21))]


def test_pace_matches_the_real_series():
    """(31,128,697.86 - 15,369,800.91) / 13 days."""
    assert pace(REAL) == pytest.approx(1_212_222.8, rel=1e-4)


def test_pace_spans_first_to_last_not_the_last_gap():
    """CBP's cadence slips around holidays; measuring only the last gap would
    read a short week as a collapse in pace."""
    assert pace(REAL) == pytest.approx(pace([REAL[0], REAL[-1]]))


def test_pace_needs_two_observations():
    assert pace([REAL[0]]) is None
    assert pace([]) is None


def test_projection_says_tranche_1_does_not_fill():
    """The finding this was built for: ~42% lands, the rest expires Sep 30."""
    p = project_final(REAL, TRANCHES[0])
    assert p == pytest.approx(42_038_703, rel=1e-3)
    assert p / TRANCHES[0].limit_kg < 0.5


def test_projection_cannot_exceed_the_tranche():
    """CBP stops accepting at the limit, so a straight line above 100% would
    describe volume that cannot legally enter."""
    fast = [Fill(date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 30),
                 100_000_000.0, 0.0, 0.0, "OPEN"),
            Fill(date(2026, 9, 2), date(2026, 9, 1), date(2026, 9, 30),
                 100_000_000.0, 50_000_000.0, 50.0, "OPEN")]
    assert project_final(fast, TRANCHES[0]) == pytest.approx(100_000_000)


def test_projection_after_the_window_closes_adds_nothing():
    late = [Fill(date(2026, 10, 5), date(2026, 9, 1), date(2026, 9, 30),
                 100_000_000.0, 44_000_000.0, 44.0, "OPEN"),
            Fill(date(2026, 10, 12), date(2026, 9, 1), date(2026, 9, 30),
                 100_000_000.0, 44_000_000.0, 44.0, "OPEN")]
    assert project_final(late, TRANCHES[0]) == pytest.approx(44_000_000)
