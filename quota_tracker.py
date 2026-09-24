"""Fill tracking for the Proclamation 11059 tariff-free beef quota.

Proclamation 11059 (signed 2026-08-26, 91 FR 55989) added 300,000 mt to the
calendar-2026 in-quota quantity for beef -- overwhelmingly lean trimmings, the
product this dashboard prices -- for "other countries or areas" under AUSN 3(b)
to HTSUS chapter 2. CBP administers it as quota 0299035402BEEF over HTS
0201.30.5091, 0201.30.5097, 0202.30.5091 and 0202.30.5097.

Three facts about it are easy to get wrong and change what the numbers mean:

* It is THREE SEPARATE 100,000 mt tranches, not one 300,000 mt pool and not
  100,000 mt spread across three months. Each runs its own 30-day window, first
  come first served, and CBP prorates entries that exceed a tranche rather than
  carrying them forward. Whatever a tranche does not use is simply gone.

* The windows are Sep 1-30, Oct 1-30 and Oct 31-Nov 30. Tranche 2 ends on the
  30th, not the 31st, and tranche 3 starts on the 31st. A naive month boundary
  puts Oct 31 in the wrong tranche.

* ARGENTINA IS NOT IN THIS QUOTA. It has its own (025085ARBEEF, plus the
  country line under 0201101BEEF03), so Argentine volume never appears here and
  reading this as "the Argentina deal" overstates what it covers.

Worth knowing for context: the ordinary "other countries" beef TRQ
(0201101BEEF03, 52,005,000 kg) filled on 2026-01-06. Non-quota-country beef has
been paying the over-quota rate ever since, which is the gap this proclamation
opens.

DATA SOURCE. CBP publishes fill in the weekly Commodity Status Report, a PDF
posted the first business day of each week. There is no API and no CSV. The
file names are not derivable -- the same report has appeared as
`26_0921_commodity_status_report.pdf`, `26_0908_commodity_status_report_weekly.pdf`
and `commodity_status_report_weekly_aug_31.pdf` -- so the index page is scraped
for links rather than guessing a pattern. The index keeps only the current
report and four previous, so SEED_REPORTS holds the ones that have rolled off;
without it the series would silently truncate to the last five weeks partway
through the quota.
"""

import re
from datetime import date
from typing import NamedTuple, Optional

QUOTA_ID = "0299035402BEEF"

REPORT_INDEX = "https://www.cbp.gov/document/report/commodity-status-report"
CBP_ROOT = "https://www.cbp.gov"

# Reports that have rolled off the index page. Append as they age out; the
# index only ever carries the current one and four previous.
SEED_REPORTS = (
    "/sites/default/files/2026-09/26_0908_commodity_status_report_weekly.pdf",
    "/sites/default/files/2026-09/26_0914_commodity_status_report.pdf",
    "/sites/default/files/2026-09/26_0921_commodity_status_report.pdf",
)


class Tranche(NamedTuple):
    number: int
    start: date
    end: date
    limit_kg: float


# Per QB 26-230. Note the Oct 30 / Oct 31 boundary -- it is not a month split.
TRANCHES = (
    Tranche(1, date(2026, 9, 1), date(2026, 9, 30), 100_000_000.0),
    Tranche(2, date(2026, 10, 1), date(2026, 10, 30), 100_000_000.0),
    Tranche(3, date(2026, 10, 31), date(2026, 11, 30), 100_000_000.0),
)

MT_PER_KG = 0.001


class Fill(NamedTuple):
    """One observation of the quota's fill, as of a report date."""

    as_of: date
    period_start: date
    period_end: date
    limit_kg: float
    entered_kg: float
    fill_pct: float
    status: str            # OPEN / FILL / EXCL, verbatim from CBP


_NUM = r"[\d,]+(?:\.\d+)?"
_LINE = re.compile(
    QUOTA_ID
    + r"\s+.*?-\s*OTHR\s+\S+\s+"
    + r"(?P<start>\d{2}/\d{2}/\d{4})\s+(?P<end>\d{2}/\d{2}/\d{4})\s+"
    + r"\S+\s+(?P<limit>" + _NUM + r")\s+KG\s+"
    + r"(?P<entered>" + _NUM + r"|-)\s+"
    + r"(?P<pct>" + _NUM + r")%\s+(?P<status>[A-Z]+)"
)

_AS_OF = re.compile(r"Commodity Status Report\s+([A-Z][a-z]+ \d{1,2}, \d{4})")

_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def _num(s) -> Optional[float]:
    if s is None or s in ("-", ""):
        return None
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _mdy(s) -> Optional[date]:
    try:
        m, d, y = (int(p) for p in s.split("/"))
        return date(y, m, d)
    except (ValueError, AttributeError):
        return None


def report_date(text: str) -> Optional[date]:
    """The 'as of' date from the report header, not the file name.

    The file names disagree with each other about format and one of them
    (`..._aug_31.pdf`) carries no year at all, so the header is the only
    trustworthy source for which week a number belongs to.
    """
    m = _AS_OF.search(text or "")
    if not m:
        return None
    try:
        month, day, year = re.match(r"([A-Z][a-z]+) (\d{1,2}), (\d{4})", m.group(1)).groups()
        return date(int(year), _MONTHS[month], int(day))
    except (AttributeError, KeyError, ValueError):
        return None


def parse_fill(text: str) -> Optional[Fill]:
    """Pull the quota's OTHR line out of one report's extracted text.

    Returns None when the quota is absent, which is correct rather than
    exceptional: every report published before 2026-09-01 predates the
    proclamation and carries no such line.
    """
    as_of = report_date(text)
    m = _LINE.search(text or "")
    if as_of is None or m is None:
        return None
    limit = _num(m.group("limit"))
    entered = _num(m.group("entered")) or 0.0
    start, end = _mdy(m.group("start")), _mdy(m.group("end"))
    if limit is None or not limit or start is None or end is None:
        return None
    return Fill(as_of, start, end, limit, entered,
                _num(m.group("pct")) or 0.0, m.group("status"))


def tranche_for(period_start: date) -> Optional[Tranche]:
    """Which tranche a CBP period belongs to, matched on its start date."""
    for t in TRANCHES:
        if t.start == period_start:
            return t
    return None


def pace(fills) -> Optional[float]:
    """Kilograms per day across the observations, or None if under two.

    Measured from the first to the last observation rather than between the
    two most recent: CBP's weekly cadence slips around holidays, so a single
    short gap would otherwise read as a collapse in pace.
    """
    pts = sorted((f for f in fills if f is not None), key=lambda f: f.as_of)
    if len(pts) < 2:
        return None
    days = (pts[-1].as_of - pts[0].as_of).days
    if days <= 0:
        return None
    return (pts[-1].entered_kg - pts[0].entered_kg) / days


def project_final(fills, tranche: Tranche) -> Optional[float]:
    """Kilograms this tranche lands at if the current pace holds to its end.

    Capped at the tranche limit -- CBP stops accepting once it fills, so a
    straight-line projection above 100% would describe volume that cannot
    legally enter.
    """
    rate = pace(fills)
    if rate is None:
        return None
    latest = max((f for f in fills if f is not None), key=lambda f: f.as_of, default=None)
    if latest is None:
        return None
    days_left = (tranche.end - latest.as_of).days
    if days_left < 0:
        days_left = 0
    return min(latest.entered_kg + rate * days_left, tranche.limit_kg)
