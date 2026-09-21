"""Reading the trimmings series without misreading it.

Two separate ways this page has printed a number that meant something other
than its label. Both are handled here so both can be tested.


1. A daily print that is not a clean read of the market
--------------------------------------------------------------------------
The daily LM_XB401 national line is a weighted average of whatever happened
to trade that afternoon, and one off-market cluster can drag it a long way
with nothing in the file looking wrong.

On 2026-09-18 the national line printed $345.42 -- down $82.58 on the day,
the largest move in the series since at least 2023-07 and 2.6x the next
largest -- while the Central line in the same report printed $436.15 and rose
$8.35. Backing Central out of the national weighted average leaves ~319k lb
trading outside the Central states at an implied ~$309.55 while the Central
states traded $434.00-$440.15. The weekly LM_XB460 average for that same week
was $408.40. The break was real; a drop of that size was not.

Nothing here rewrites USDA's published number -- that number is the product,
and the dashboard keeps showing it. `assess_print` only decides when to SAY
that a day's print is unrepresentative.

Thresholds are measured against every priced day LM_XB401 carries from
2023-07-31 through 2026-09-16, i.e. everything before the day that prompted
this:

    divergence  |national - central|, days where BOTH are priced (n=364)
        min -15.40   p50 -0.07   max +31.88   -> limit 25.00, 1 baseline trip
    dispersion  national high - low                          (n=623)
        p50  10.82   p99 49.90   max  68.10   -> limit 75.00, 0 baseline trips

    2026-09-18 scored divergence -90.73 and dispersion 181.15.

Two traps worth keeping in mind before adjusting any of this:

* Central is priced on only 364 of 623 days. On the other 259 the Central
  average comes back as 0/NaN, and a naive `national - central` reads ~430 --
  which would fire the guard on 40% of all days. `divergence` returns None
  unless both lines carry a real price.

* Daily VOLUME is deliberately NOT a check. 2026-09-18 carried 444,658 lb,
  154% of the baseline median, so a thin-sample test would have stayed silent
  on the exact day this was written for.


2. A change tile compared against the wrong prior point
--------------------------------------------------------------------------
`changes` used to locate the comparison point by date offset alone: "day
change" looked back 2 days, "week change" 8 days, and took the most recent
observation at or before that. Both offsets are one period too long, so on an
evenly spaced series they step over the period they name:

* Weekly series are spaced exactly 7 days. An 8-day lookback lands before the
  previous report and skips it, so every "week change" on this page was
  really a two-week change. On 2026-09-18 the LM_XB460 tile read -$40.89
  (vs 09/04) when the week-over-week move was -$28.34 (vs 09/11). The two
  import tiles had the same bug.

* The daily series prints on consecutive sessions often enough to matter. A
  2-day lookback from 2026-08-27 lands on 08/25, not 08/26, giving -$4.06
  when the day change was +$9.66 -- the wrong sign, not just the wrong size.

The offset is the wrong tool for "the previous one". `PREV` asks for the
preceding observation directly and is immune to spacing. Month and year
tiles still use real date offsets, because there "roughly 30 days ago" is
genuinely what is meant.
"""

from typing import NamedTuple, Optional

# ── 1. Is this print a clean read of the market? ─────────────────────────────

# Measured above; see the module docstring for the distributions behind them.
DIVERGENCE_LIMIT = 25.0
DISPERSION_LIMIT = 75.0


class Assessment(NamedTuple):
    """Verdict on one day's national print."""

    flagged: bool
    reasons: tuple                # human-readable strings, empty when quiet
    divergence: Optional[float]   # national - central, None if not comparable
    dispersion: Optional[float]   # national high - low, None if not comparable


def _price(x) -> Optional[float]:
    """Coerce to a usable price. AMS writes 0.00 for 'nothing traded'."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def divergence(national_avg, central_avg) -> Optional[float]:
    """national - central, or None when either side has no real price."""
    n, c = _price(national_avg), _price(central_avg)
    if n is None or c is None:
        return None
    return n - c


def dispersion(low, high) -> Optional[float]:
    """Width of the day's national price range, or None if not reported."""
    lo, hi = _price(low), _price(high)
    if lo is None or hi is None or hi < lo:
        return None
    return hi - lo


def _pounds(x) -> Optional[float]:
    """Coerce a reported weight. AMS writes these thousands-separated."""
    try:
        v = float(str(x).replace(",", "").strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return v if v > 0 else None       # NaN fails this too, which is the point


# A load is 40,000 lb and AMS reports to the pound, so a remainder smaller than
# this is rounding noise in the published averages rather than a real trade.
# Dividing by it would turn a fraction of a cent into a wild price.
MIN_RESIDUAL_POUNDS = 1_000.0


def implied_outside_central(national_avg, national_pounds,
                            central_avg, central_pounds):
    """Weighted average of the trade that happened OUTSIDE the Central states.

    National covers all states and therefore INCLUDES Central, so the rest of
    the country is what is left when Central's pounds are backed out of the
    national weighted average. This is the number that EXPLAINS a National /
    Central gap instead of merely reporting it: on 2026-09-18, National $345.42
    against Central $436.15 implies ~$309.55 across ~319k lb everywhere else.
    USDA does not publish that line; it is derived here and labelled as such.

    Returns (avg, pounds), or None when either line is unpriced, a weight is
    missing, or too little is left over to divide by safely.
    """
    n_avg, c_avg = _price(national_avg), _price(central_avg)
    n_lb, c_lb = _pounds(national_pounds), _pounds(central_pounds)
    if n_avg is None or c_avg is None or n_lb is None or c_lb is None:
        return None
    rest = n_lb - c_lb
    if rest < MIN_RESIDUAL_POUNDS:
        return None
    return ((n_avg * n_lb - c_avg * c_lb) / rest, rest)


def assess_print(national_avg, central_avg, low, high) -> Assessment:
    """Decide whether a day's national print should carry a caveat."""
    div = divergence(national_avg, central_avg)
    dsp = dispersion(low, high)
    reasons = []

    if div is not None and abs(div) > DIVERGENCE_LIMIT:
        direction = "below" if div < 0 else "above"
        reasons.append(
            f"National is ${abs(div):,.2f} {direction} the Central line in the same "
            f"report (${_price(national_avg):,.2f} vs ${_price(central_avg):,.2f}). "
            f"Since 2023 that gap has stayed inside -$15.40 to +$31.88."
        )

    if dsp is not None and dsp > DISPERSION_LIMIT:
        reasons.append(
            f"The day's national range is ${dsp:,.2f} wide "
            f"(${_price(low):,.2f}-${_price(high):,.2f}). The widest range on any "
            f"day since 2023 was $68.10, so the average is blending trades that "
            f"are not really the same market."
        )

    return Assessment(bool(reasons), tuple(reasons), div, dsp)


# ── 2. Comparing against the right prior point ───────────────────────────────

class _Prev:
    """Sentinel asking for the preceding observation rather than a date offset."""

    def __repr__(self):
        return "PREV"


PREV = _Prev()


def changes(df, date_col: str, val_col: str, deltas):
    """Current value, plus one change per entry in `deltas`.

    Each entry is either `PREV` -- the observation immediately before the
    current one, whatever its spacing -- or a `timedelta`, which takes the most
    recent observation at or before `current_date - delta`.

    Use `PREV` for "since last time" tiles (day, week). Use a timedelta only
    when the label really means elapsed time ("month", "year"); see the module
    docstring for what an offset does to an evenly spaced series.
    """
    valid = df[df[val_col].notna()]
    if valid.empty:
        return (None,) + (None,) * len(deltas)

    cur = valid.iloc[-1]
    cval = cur[val_col]
    cdt = cur[date_col]

    def prior(delta):
        if isinstance(delta, _Prev):
            return valid.iloc[-2][val_col] if len(valid) >= 2 else None
        sub = valid[valid[date_col] <= cdt - delta]
        return sub.iloc[-1][val_col] if not sub.empty else None

    out = [cval]
    for d in deltas:
        p = prior(d)
        out.append(cval - p if p is not None else None)
    return tuple(out)
