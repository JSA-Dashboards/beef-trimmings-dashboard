import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timedelta
from pathlib import Path
import time
import os
import sys

# Streamlit puts the MAIN script's directory on sys.path, never the page's,
# so this folder is never added automatically -- without the insert the
# trimmings_qc import below dies with ModuleNotFoundError under the portal
# shell. It works standalone, which is how it would reach production unseen.
sys.path.insert(0, str(Path(__file__).parent))

import trimmings_qc as qc

# ── JPSI Brand ───────────────────────────────────────────────────────────────
JPSI_DARK = "#32373c"
JPSI_BLUE = "#0693e3"
MUTED     = "#6b7280"
BORDER    = "#e2e5e9"
POS       = "#1a7f37"
NEG       = "#c62828"

US_COLOR  = JPSI_BLUE      # US Fresh 90s (domestic, daily print)
US_WK_COLOR = "#0b4f7a"    # US Fresh 90s (domestic, weekly average)
SA_COLOR  = "#e8833a"      # South America Frozen 90s (import)
ANZ_COLOR = "#5aa469"      # Australia/NZ Frozen 90s (import)

JSA_LOGO = "https://www.jpsi.com/wp-content/themes/gate39media/img/logo-full.png"

# ── Data sources ─────────────────────────────────────────────────────────────
# US Fresh 90s: USDA AMS LMR, National/Regional Daily Boneless Processing
# Beef/Beef Trimmings - PM (LM_XB401), item "Chemical Lean, Fresh 90%".
LMR_BASE      = "https://mpr.datamart.ams.usda.gov/services/v1.1/reports"
XB401_ID      = 2451
US_ITEM       = "Chemical Lean, Fresh 90%"

# Weekly companion to LM_XB401: National/Regional Weekly Boneless Processing
# Beef and Beef Trimmings (LM_XB460) -- same item, same source, but averaged
# over the whole week's trade instead of one session's. The daily line is a
# weighted average of whatever happened to trade that afternoon, so a single
# off-market cluster can drag it a long way with nothing in the file looking
# wrong; see trimmings_qc.py for the day that cost an afternoon. The weekly
# line is the steadier read, drawn over the daily one on the price chart.
XB460_ID      = 2462

# South America / Australia-NZ Frozen 90s proxy: USDA AMS MARS, Import Beef
# Trade (NW_LS421), commodity "Cow Meat (90%)" broken out by country of origin.
# Published weekly (Fridays). Confirmed against live API 2026-09-02.
MARS_BASE     = "https://marsapi.ams.usda.gov/services/v1.2/reports"
LS421_ID      = 2823
# MARS key comes from Streamlit secrets (Cloud) or the environment (dev); no
# key is committed to the repo. USDA issues MARS keys per requester, so a key
# committed to a public repo is both a leak and a terms problem.
try:
    MARS_KEY = st.secrets.get("MARS_API_KEY", "")
except Exception:
    MARS_KEY = ""
MARS_KEY = MARS_KEY or os.environ.get("MARS_API_KEY", "")
IMPORT_ITEM   = "Cow Meat (90%)"
ORIGIN_SA     = "South America"
ORIGIN_ANZ    = "Australia &/ New Zealand"

# Full-history pull sizes. LM_XB401's "Chemical Lean, Fresh 90%" line goes back
# to at least 2004 (confirmed against live API 2026-09-02) — 6000 reports covers
# the entire archive (~5990 unique report dates back to 2003) as one ~175MB pull,
# which takes 60-90s, hence the long timeout below. NW_LS421 only starts
# 2020-02-26, so a fixed 2019-01-01 anchor safely covers its whole history and
# stays small/fast (a few MB).
US_FULL_HISTORY_REPORTS = 6000
IMPORT_HISTORY_START    = "01/01/2019"
# LM_XB460 reaches back to 2003-01-03 -- 1,236 weekly reports as of Sep 2026,
# so 1300 covers the archive with headroom. Fetched through the section path
# (/<id>/National) rather than allSections=true: the weekly file also carries
# Central, East Coast and West Coast, and dropping those three returns the
# same National rows in ~19MB/8s instead of ~73MB/25s.
US_WEEKLY_HISTORY_REPORTS = 1300

WATERMARK_OPACITY = 0.10

# ── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="JSA Beef Trimmings Dashboard",
    page_icon="🥩",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Source+Sans+Pro:wght@300;400;600;700&display=swap');
  html, body, [class*="css"], .stApp, button, input, select, textarea, table, td, th, .stMarkdown,
  h1, h2, h3, h4, h5, h6, p, span, div {{
    font-family: 'Source Sans Pro', system-ui, -apple-system, sans-serif !important;
  }}

  /* The font rule above ends in `span, div` with !important, which also
     captures Streamlit's Material icon spans -- the expander chevron then
     renders its ligature NAME ("keyboard_arrow_down") as literal text on top
     of the label. Hand those spans their icon font back. */
  [data-testid="stIconMaterial"], span[class*="material-symbols"] {{
    font-family: 'Material Symbols Rounded' !important;
  }}

  header[data-testid="stHeader"] {{ display:none !important; }}
  #MainMenu, footer {{ visibility:hidden !important; }}
  .stDeployButton {{ display:none; }}

  .stApp {{ background-color:#ffffff; }}
  .block-container {{ padding-top:0.75rem !important; max-width:1250px; }}

  [data-testid="stSidebar"] {{ background-color:#f6f8fa; border-right:1px solid {BORDER}; }}

  .dash-header {{
    background:#ffffff; border-bottom:3px solid {JPSI_BLUE};
    padding:16px 8px 14px 8px; margin:-0.75rem 0 22px 0;
    display:flex; align-items:center; gap:20px;
  }}
  .dash-header-logo img {{ height:48px; display:block; }}
  .dash-header-text {{ flex:1; text-align:center; }}
  .dash-header-text h1 {{
    margin:0; color:{JPSI_DARK} !important; font-size:1.65rem; font-weight:700; letter-spacing:-0.01em;
  }}
  .dash-header-text .subtitle {{ color:{MUTED}; font-size:0.83rem; margin:3px 0 0 0; }}
  .dash-header-meta {{ text-align:right; color:{MUTED}; font-size:0.75rem; min-width:150px; }}
  .dash-header-meta b {{ color:{JPSI_DARK}; font-size:1rem; }}

  .sec-header {{
    color:{JPSI_DARK}; font-size:0.78rem; font-weight:700; text-transform:uppercase;
    letter-spacing:0.08em; padding:6px 0 6px 10px; border-left:4px solid {JPSI_BLUE};
    margin:22px 0 12px;
  }}

  .tile {{
    background:#ffffff; border:1px solid {BORDER}; border-top:3px solid {JPSI_BLUE};
    border-radius:10px; padding:14px 16px; text-align:center; height:100%;
    box-shadow:0 1px 4px rgba(50,55,60,0.06);
  }}
  .tile-us    {{ border-top-color:{US_COLOR}; }}
  .tile-sa    {{ border-top-color:{SA_COLOR}; }}
  .tile-anz   {{ border-top-color:{ANZ_COLOR}; }}
  .tile-neu   {{ border-top-color:{MUTED}; }}
  .tile-label {{ color:{MUTED}; font-size:0.66rem; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:6px; }}
  .tile-value {{ color:{JPSI_DARK}; font-size:1.55rem; font-weight:700; line-height:1.1; }}
  .tile-delta-pos {{ color:{POS}; font-size:0.8rem; font-weight:600; margin-top:4px; }}
  .tile-delta-neg {{ color:{NEG}; font-size:0.8rem; font-weight:600; margin-top:4px; }}
  .tile-delta-neu {{ color:{MUTED}; font-size:0.8rem; font-weight:600; margin-top:4px; }}

  .note {{ color:{MUTED}; font-size:0.72rem; line-height:1.5; }}

  .ctx-flag {{
    border-left:3px solid #d99e0b; background:#fffdf7;
    padding:7px 12px; margin:0 0 12px; border-radius:0 6px 6px 0;
    color:{JPSI_DARK}; font-size:0.75rem; line-height:1.5;
  }}
  .ctx-flag-hint {{ color:{MUTED}; }}

  .ctx {{ width:100%; border-collapse:collapse; font-size:0.78rem; }}
  .ctx th {{
    text-align:right; color:{MUTED}; font-weight:700; font-size:0.64rem;
    text-transform:uppercase; letter-spacing:0.07em; padding:6px 10px;
    border-bottom:1px solid {BORDER}; white-space:nowrap;
  }}
  .ctx th:first-child, .ctx td:first-child {{ text-align:left; }}
  .ctx td {{
    text-align:right; padding:8px 10px; color:{JPSI_DARK};
    border-bottom:1px solid #f2f4f6; white-space:nowrap;
  }}
  .ctx td:first-child {{ font-weight:600; }}
  .ctx tr.derived td {{ color:{MUTED}; font-style:italic; }}
  hr {{ border-color:{BORDER}; }}

  .stButton > button {{
    background: {JPSI_BLUE}; color: #fff; border: none;
    border-radius: 6px; font-weight: 600;
  }}
  .stButton > button:hover {{ background: #057ec2; color: #fff; }}
  [data-testid="stSidebar"] .stToggle label div[data-baseweb="toggle"][aria-checked="true"] {{
    background-color: {JPSI_BLUE} !important;
  }}
</style>
""", unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def delta_html(val, suffix=""):
    if val is None:
        return '<div class="tile-delta-neu">—</div>'
    sign  = "▲" if val > 0 else ("▼" if val < 0 else "")
    color = "pos" if val > 0 else ("neg" if val < 0 else "neu")
    return f'<div class="tile-delta-{color}">{sign} {abs(val):.2f}{suffix}</div>'


def tile(label, value, delta="", cls=""):
    return (f'<div class="tile {cls}">'
            f'<div class="tile-label">{label}</div>'
            f'<div class="tile-value">{value}</div>'
            f'{delta}</div>')


def fmt(v):
    return f"${v:.2f}" if v is not None else "—"


def _num(v, spec):
    """Format a possibly-missing number. None/NaN/junk all render as a dash."""
    try:
        if v is None or pd.isna(v):
            return "—"
        return spec.format(v)
    except (TypeError, ValueError):
        return "—"


def ctx_row(label, trades, pounds, low, high, avg, cls=""):
    """One region's line in the breakdown table."""
    rng = "—"
    if _num(low, "{}") != "—" and _num(high, "{}") != "—":
        rng = "${:,.2f}–${:,.2f}".format(low, high)
    return ('<tr class="{}"><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'
            .format(cls, label, _num(trades, "{:,.0f}"), _num(pounds, "{:,.0f}"),
                    rng, _num(avg, "${:,.2f}")))


# changes() and the PREV sentinel live in trimmings_qc so they can be tested --
# app.py renders on import, so nothing defined in here can be reached from a
# test. The offset-vs-previous-observation bug this fixes is documented there.
changes = qc.changes
PREV    = qc.PREV


# ── Data Fetching ────────────────────────────────────────────────────────────

def _session(backoff=3) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=backoff,
                   status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


@st.cache_data(ttl=3600, persist="disk", show_spinner=False)
def fetch_us_fresh90() -> pd.DataFrame:
    """Full-history daily US Chemical Lean, Fresh 90% — National & Central lines from LM_XB401."""
    url  = f"{LMR_BASE}/{XB401_ID}/"
    sess = _session()
    resp = sess.get(url, params={"lastReports": US_FULL_HISTORY_REPORTS, "allSections": "true"}, timeout=180)
    resp.raise_for_status()
    payload = resp.json()

    frames = {}
    for sec in payload:
        name = sec.get("reportSection", "")
        if name not in ("National", "Central"):
            continue
        rows = sec.get("results", [])
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["item_norm"] = df["item_desc"].str.replace(r"\s+", " ", regex=True).str.strip()
        df = df[df["item_norm"] == US_ITEM].copy()
        df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
        df["avg_price"]   = pd.to_numeric(df["price_range_avg"], errors="coerce")
        df["low_price"]   = pd.to_numeric(df["price_range_low"], errors="coerce")
        df["high_price"]  = pd.to_numeric(df["price_range_high"], errors="coerce")
        df["trades"]      = pd.to_numeric(df["number_trades"], errors="coerce")
        # total_pounds arrives thousands-separated ("444,658"); to_numeric on
        # its own coerces that straight to NaN.
        df["pounds"]      = pd.to_numeric(
            df["total_pounds"].astype(str).str.replace(",", "", regex=False), errors="coerce")
        # AMS writes 0.00 for "nothing traded", which is not a price of zero.
        for _c in ("avg_price", "low_price", "high_price"):
            df.loc[df[_c] <= 0, _c] = None
        df = df.dropna(subset=["report_date"]).sort_values("report_date")
        frames[name.lower()] = df[["report_date", "avg_price", "low_price", "high_price",
                                   "trades", "pounds"]].reset_index(drop=True)

    _cols = ["report_date", "avg_price", "low_price", "high_price", "trades", "pounds"]
    national = frames.get("national", pd.DataFrame(columns=_cols))
    central  = frames.get("central",  pd.DataFrame(columns=_cols))

    out = national.rename(columns={
        "avg_price": "national", "low_price": "national_low",
        "high_price": "national_high", "trades": "national_trades",
        "pounds": "national_pounds",
    })
    if not central.empty:
        # Central's range and weight matter as much as its average: they are what
        # the "what's behind this print" breakdown divides to work out where the
        # rest of the country traded.
        out = out.merge(
            central.rename(columns={
                "avg_price": "central", "low_price": "central_low",
                "high_price": "central_high", "trades": "central_trades",
                "pounds": "central_pounds",
            }),
            on="report_date", how="outer",
        )
    else:
        for _c in ("central", "central_low", "central_high",
                   "central_trades", "central_pounds"):
            out[_c] = None
    return out.sort_values("report_date").reset_index(drop=True)


@st.cache_data(ttl=21600, persist="disk", show_spinner=False)
def fetch_us_weekly90() -> pd.DataFrame:
    """Full-history weekly US Chemical Lean, Fresh 90% national average (LM_XB460).

    Same item and same source as the daily line, averaged across the week's
    entire trade. Where one session's average can be captured by a single
    cluster, the weekly runs over several times the poundage -- it is the
    number to reach for when a day looks wrong.
    """
    url  = f"{LMR_BASE}/{XB460_ID}/National"
    sess = _session()
    resp = sess.get(url, params={"lastReports": US_WEEKLY_HISTORY_REPORTS}, timeout=180)
    resp.raise_for_status()
    payload = resp.json()

    # The section path returns ONE object with a flat `results` list, not the
    # list-of-sections that allSections=true returns. Indexing it like the
    # latter yields an empty frame and a silently blank line on the chart.
    rows = payload.get("results", []) if isinstance(payload, dict) else []
    cols = ["report_date", "weekly", "weekly_trades", "weekly_pounds"]
    if not rows:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows)
    df["item_norm"] = df["item_desc"].str.replace(r"\s+", " ", regex=True).str.strip()
    df = df[df["item_norm"] == US_ITEM].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)

    df["report_date"]   = pd.to_datetime(df["report_date"], errors="coerce")
    df["weekly"]        = pd.to_numeric(df["price_range_avg"], errors="coerce")
    df["weekly_trades"] = pd.to_numeric(df["number_trades"], errors="coerce")
    df["weekly_pounds"] = pd.to_numeric(
        df["total_pounds"].astype(str).str.replace(",", "", regex=False), errors="coerce")
    df.loc[df["weekly"] <= 0, "weekly"] = None
    df = df.dropna(subset=["report_date"]).sort_values("report_date")
    return df[cols].reset_index(drop=True)


@st.cache_data(ttl=21600, persist="disk", show_spinner=False)
def fetch_import_cow90() -> pd.DataFrame:
    """Full-history weekly Cow Meat (90%) import prices by origin from NW_LS421 (Import Beef Trade)."""
    hi = (datetime.now() + timedelta(days=2)).strftime("%m/%d/%Y")
    url  = f"{MARS_BASE}/{LS421_ID}"
    sess = _session()
    resp = sess.get(url, params={"q": f"report_begin_date={IMPORT_HISTORY_START}:{hi}", "allSections": "true"},
                     auth=(MARS_KEY, ""), timeout=90)
    resp.raise_for_status()
    payload = resp.json()

    details = next((s["results"] for s in payload if s.get("reportSection") == "Report Details"), [])
    if not details:
        return pd.DataFrame(columns=["report_date", "origin", "avg_price"])

    df = pd.DataFrame(details)
    df = df[df["commodity"] == IMPORT_ITEM].copy()
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    df["low"]  = pd.to_numeric(df["low_price"], errors="coerce")
    df["high"] = pd.to_numeric(df["high_price"], errors="coerce")
    df["mid"]  = df[["low", "high"]].mean(axis=1)
    df = df.dropna(subset=["report_date", "mid"])

    origin_map = {ORIGIN_SA: "South America", ORIGIN_ANZ: "Australia/NZ"}
    df = df[df["country_of_origin"].isin(origin_map)].copy()
    df["origin"] = df["country_of_origin"].map(origin_map)

    weekly = (df.groupby(["report_date", "origin"], as_index=False)
                .agg(avg_price=("mid", "mean"), low=("low", "mean"),
                     high=("high", "mean"), n=("mid", "size")))
    return weekly.sort_values("report_date").reset_index(drop=True)


def pivot_origin(weekly: pd.DataFrame, origin: str) -> pd.DataFrame:
    sub = weekly[weekly["origin"] == origin][["report_date", "avg_price"]].copy()
    return sub.reset_index(drop=True)


def match_nearest(us_df: pd.DataFrame, target_dates: pd.Series) -> pd.Series:
    """For each weekly import report_date, find the most recent US Fresh 90 value on/before it."""
    us = us_df.dropna(subset=["national"]).sort_values("report_date")
    out = []
    for d in target_dates:
        prior = us[us["report_date"] <= d]
        out.append(prior.iloc[-1]["national"] if not prior.empty else None)
    return pd.Series(out, index=target_dates.index)


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image(JSA_LOGO, width="stretch")
    st.markdown("<hr>", unsafe_allow_html=True)

    st.markdown('<div class="sec-header" style="margin-top:0;">History</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="note">Full available history is always loaded — '
        'US Fresh 90s back to 2003, imports back to 2020.</div>',
        unsafe_allow_html=True,
    )

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown('<div class="sec-header">Data refresh</div>', unsafe_allow_html=True)
    auto_refresh = st.toggle("Auto-refresh (30 min)", value=False)
    if st.button("↺  Refresh now", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown(
        '<div class="note">'
        '<b>US Fresh 90s</b> — USDA AMS LMR, National/Regional Daily Boneless '
        'Processing Beef/Beef Trimmings PM (<b>LM_XB401</b>), Chemical Lean Fresh 90% national line. '
        'Published ~2:30pm CT most business days; volume is not guaranteed daily.<br><br>'
        '<b>US Fresh 90s, weekly average</b> — same item from the weekly companion report, '
        'National/Regional Weekly Boneless Processing Beef and Beef Trimmings (<b>LM_XB460</b>), '
        'averaged over the week\'s entire trade. Drawn over the daily line, and the level to trust '
        'when one session prints far away from it.<br><br>'
        '<b>South America &amp; Australia/NZ Frozen 90s</b> — USDA AMS MARS, '
        'Import Beef Trade (<b>NW_LS421</b>), &quot;Cow Meat (90%)&quot; line by country of origin — '
        'the accepted proxy for import Frozen 90s. Published weekly, Fridays. Values average across '
        'East/West Coast and 0–15 / 16–45 day delivery windows reported that week.<br><br>'
        'Cache: US 1 hr, imports 6 hr.</div>',
        unsafe_allow_html=True,
    )


# ── Load Data ────────────────────────────────────────────────────────────────

with st.spinner("Loading full USDA beef trimmings history (US pull can take ~60-90s on a cold cache)…"):
    try:
        us_hist = fetch_us_fresh90()
        imp_hist = fetch_import_cow90()
        load_ok, err_msg = True, ""
    except Exception as e:
        load_ok, err_msg = False, str(e)
        us_hist, imp_hist = pd.DataFrame(), pd.DataFrame()

    # The weekly line is context, not the product. If LM_XB460 is down the page
    # still has to render the daily number it has always rendered, so this gets
    # its own handler instead of joining the hard-stop path above.
    try:
        us_weekly = fetch_us_weekly90()
    except Exception:
        us_weekly = pd.DataFrame(
            columns=["report_date", "weekly", "weekly_trades", "weekly_pounds"])


# ── Header ───────────────────────────────────────────────────────────────────

c1, c2 = st.columns([7, 3])
with c1:
    st.markdown(
        '<div class="dash-header">'
        f'<div class="dash-header-logo"><img src="{JSA_LOGO}"></div>'
        '<div class="dash-header-text">'
        '<h1>Beef Trimmings Dashboard</h1>'
        '<div class="subtitle">US Fresh 90s (Chemical Lean) vs. South America &amp; Australia/NZ Frozen 90s (Cow Meat)</div>'
        '</div></div>',
        unsafe_allow_html=True,
    )

if not load_ok:
    st.warning(
        "⏳ **USDA data temporarily unavailable** — the USDA server is not responding. "
        "This usually resolves in a few minutes. Use **Refresh now** in the sidebar to retry."
    )
    with st.expander("Technical details"):
        st.code(err_msg)
    st.stop()

if us_hist.empty and imp_hist.empty:
    st.warning("No data returned from USDA APIs.")
    st.stop()


# ── Compute Changes ──────────────────────────────────────────────────────────

# Day and week tiles compare against the PREVIOUS OBSERVATION, not a date
# offset: the daily series prints on consecutive sessions and the weekly ones
# are spaced exactly 7 days, so a 2-day / 8-day lookback steps over the very
# period it names. Month and year stay real offsets -- there the elapsed time
# is what the label means. trimmings_qc records the numbers this got wrong.
MONTH, YEAR = timedelta(days=30), timedelta(days=365)

us_cur, us_d1, us_d30, us_d365 = changes(us_hist, "report_date", "national", [PREV, MONTH, YEAR])

# Weekly LM_XB460 level, plus a verdict on the latest daily print. USDA's
# published daily number stays the headline and is never adjusted here -- the
# verdict only decides whether to SAY that a session is not a clean read of the
# market. Thresholds and the measured basis for them live in trimmings_qc.py.
wk_cur, wk_w1 = changes(us_weekly, "report_date", "weekly", [PREV])

_us_priced = us_hist.dropna(subset=["national"]) if not us_hist.empty else us_hist
us_latest  = _us_priced.iloc[-1] if not _us_priced.empty else None
assessment = qc.assess_print(
    us_latest["national"], us_latest["central"],
    us_latest["national_low"], us_latest["national_high"],
) if us_latest is not None else None

sa_df  = pivot_origin(imp_hist, "South America")
anz_df = pivot_origin(imp_hist, "Australia/NZ")

sa_cur,  sa_w1,  sa_m1,  sa_y1  = changes(sa_df,  "report_date", "avg_price", [PREV, MONTH, YEAR])
anz_cur, anz_w1, anz_m1, anz_y1 = changes(anz_df, "report_date", "avg_price", [PREV, MONTH, YEAR])

spread_us_sa  = (us_cur - sa_cur)  if (us_cur is not None and sa_cur  is not None) else None
spread_us_anz = (us_cur - anz_cur) if (us_cur is not None and anz_cur is not None) else None
spread_sa_anz = (sa_cur - anz_cur) if (sa_cur  is not None and anz_cur is not None) else None

last_us_date  = us_hist["report_date"].max()  if not us_hist.empty  else None
last_imp_date = imp_hist["report_date"].max() if not imp_hist.empty else None

with c2:
    meta = []
    if last_us_date is not None:
        meta.append(f"US daily: <b>{last_us_date.strftime('%b %d, %Y')}</b>")
    if last_imp_date is not None:
        meta.append(f"Import weekly: <b>{last_imp_date.strftime('%b %d, %Y')}</b>")
    st.markdown(
        '<div class="dash-header-meta">' + "<br>".join(meta) + '</div>',
        unsafe_allow_html=True,
    )


# ── Tiles — US Fresh 90s ─────────────────────────────────────────────────────

st.markdown('<div class="sec-header">US Fresh 90s — Chemical Lean, National ($/cwt)</div>', unsafe_allow_html=True)

# A thin rule, not a verdict. It reports what is unusual about the session and
# stops there -- whether that makes the print a good read of the market is the
# desk's call, not the page's. The breakdown below carries the detail.
if assessment is not None and assessment.flagged:
    _bits = []
    if assessment.divergence is not None and abs(assessment.divergence) > qc.DIVERGENCE_LIMIT:
        _bits.append("National <b>{}</b> vs Central <b>{}</b>".format(
            _num(us_latest["national"], "${:,.2f}"),
            _num(us_latest["central"], "${:,.2f}")))
    if assessment.dispersion is not None and assessment.dispersion > qc.DISPERSION_LIMIT:
        _bits.append("range {}–{} ({} wide, vs $68 widest since 2023)".format(
            _num(us_latest["national_low"], "${:,.2f}"),
            _num(us_latest["national_high"], "${:,.2f}"),
            _num(assessment.dispersion, "${:,.0f}")))
    if wk_cur is not None:
        _bits.append("week <b>{}</b>".format(_num(wk_cur, "${:,.2f}")))
    st.markdown(
        '<div class="ctx-flag">' + " &nbsp;·&nbsp; ".join(_bits)
        + ' &nbsp;·&nbsp; <span class="ctx-flag-hint">breakdown below</span></div>',
        unsafe_allow_html=True,
    )

cols = st.columns(5)
with cols[0]:
    st.markdown(tile("Current (daily)", fmt(us_cur), cls="tile-us"), unsafe_allow_html=True)
with cols[1]:
    st.markdown(tile("Day change", fmt(us_d1), delta_html(us_d1), "tile-us"), unsafe_allow_html=True)
with cols[2]:
    st.markdown(tile("Weekly avg", fmt(wk_cur), delta_html(wk_w1), "tile-us"), unsafe_allow_html=True)
with cols[3]:
    st.markdown(tile("Month change", fmt(us_d30), delta_html(us_d30), "tile-us"), unsafe_allow_html=True)
with cols[4]:
    st.markdown(tile("Year change", fmt(us_d365), delta_html(us_d365), "tile-us"), unsafe_allow_html=True)
st.markdown(
    '<div class="note" style="margin-top:6px;">'
    '<b>Current (daily)</b> and <b>Day change</b> are the LM_XB401 national weighted average for '
    'the latest session, exactly as USDA publishes it. <b>Weekly avg</b> is LM_XB460 over that '
    'week’s entire trade, shown with its week-over-week change. One session can be carried by a '
    'single off-market cluster; when the two disagree sharply, the weekly is the more reliable '
    'level.</div>',
    unsafe_allow_html=True,
)

# Collapsed by default: the page reads exactly as it did before, and the drivers
# are one click away for anyone asking why the number moved.
with st.expander("What’s behind this print — trades, Central vs National, price range"):
    if us_latest is None:
        st.info("No priced session to break down.")
    else:
        _d = us_latest
        _body = [
            ctx_row("National (all states)", _d["national_trades"], _d["national_pounds"],
                    _d["national_low"], _d["national_high"], _d["national"]),
            ctx_row("Central", _d["central_trades"], _d["central_pounds"],
                    _d["central_low"], _d["central_high"], _d["central"]),
        ]
        _rest = qc.implied_outside_central(
            _d["national"], _d["national_pounds"], _d["central"], _d["central_pounds"])
        if _rest is not None:
            _rest_avg, _rest_lb = _rest
            _rest_trades = None
            if pd.notna(_d["national_trades"]) and pd.notna(_d["central_trades"]):
                _rest_trades = _d["national_trades"] - _d["central_trades"]
            _body.append(ctx_row("Outside Central", _rest_trades, _rest_lb,
                                 None, None, _rest_avg, cls="derived"))
        st.markdown(
            '<div class="note" style="margin-bottom:8px;">Session of <b>{}</b>, '
            'Chemical Lean Fresh 90% (LM_XB401).</div>'.format(
                _d["report_date"].strftime("%b %d, %Y")),
            unsafe_allow_html=True,
        )
        st.markdown(
            '<table class="ctx"><thead><tr><th>Region</th><th>Trades</th><th>Pounds</th>'
            '<th>Price range</th><th>Wtd avg</th></tr></thead><tbody>'
            + "".join(_body) + '</tbody></table>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="note" style="margin-top:10px;">National covers all states and '
            '<i>includes</i> Central, so the last row is what is left once Central’s pounds '
            'are backed out of the national weighted average — the East and West Coast trade '
            'that USDA does not publish as its own line. Derived here, not reported, and '
            'omitted when Central is unpriced or too little weight is left to divide by.</div>',
            unsafe_allow_html=True,
        )

# ── Tiles — South America Frozen 90s ─────────────────────────────────────────

st.markdown('<div class="sec-header">South America Frozen 90s — Cow Meat, avg. E/W Coast ($/cwt)</div>', unsafe_allow_html=True)
cols = st.columns(4)
with cols[0]:
    st.markdown(tile("Current", fmt(sa_cur), cls="tile-sa"), unsafe_allow_html=True)
with cols[1]:
    st.markdown(tile("Week change", fmt(sa_w1), delta_html(sa_w1), "tile-sa"), unsafe_allow_html=True)
with cols[2]:
    st.markdown(tile("Month change", fmt(sa_m1), delta_html(sa_m1), "tile-sa"), unsafe_allow_html=True)
with cols[3]:
    st.markdown(tile("Year change", fmt(sa_y1), delta_html(sa_y1), "tile-sa"), unsafe_allow_html=True)

# ── Tiles — Australia/NZ Frozen 90s ──────────────────────────────────────────

st.markdown('<div class="sec-header">Australia/NZ Frozen 90s — Cow Meat, avg. E/W Coast ($/cwt)</div>', unsafe_allow_html=True)
cols = st.columns(4)
with cols[0]:
    st.markdown(tile("Current", fmt(anz_cur), cls="tile-anz"), unsafe_allow_html=True)
with cols[1]:
    st.markdown(tile("Week change", fmt(anz_w1), delta_html(anz_w1), "tile-anz"), unsafe_allow_html=True)
with cols[2]:
    st.markdown(tile("Month change", fmt(anz_m1), delta_html(anz_m1), "tile-anz"), unsafe_allow_html=True)
with cols[3]:
    st.markdown(tile("Year change", fmt(anz_y1), delta_html(anz_y1), "tile-anz"), unsafe_allow_html=True)

# ── Tiles — Spreads ───────────────────────────────────────────────────────────

st.markdown('<div class="sec-header">Domestic-import spreads ($/cwt)</div>', unsafe_allow_html=True)
cols = st.columns(3)
with cols[0]:
    st.markdown(tile("US Fresh 90 − South America", fmt(spread_us_sa), cls="tile-neu"), unsafe_allow_html=True)
with cols[1]:
    st.markdown(tile("US Fresh 90 − Australia/NZ", fmt(spread_us_anz), cls="tile-neu"), unsafe_allow_html=True)
with cols[2]:
    st.markdown(tile("South America − Australia/NZ", fmt(spread_sa_anz), cls="tile-neu"), unsafe_allow_html=True)
st.markdown(
    '<div class="note" style="margin-top:6px;">US tile uses the most recent daily trade; import tiles use '
    'the most recent weekly report — spreads compare whichever reports are current and are not necessarily '
    'the same calendar day.</div>',
    unsafe_allow_html=True,
)


# ── Comparison Chart ──────────────────────────────────────────────────────────

st.markdown('<div class="sec-header">Price trend</div>', unsafe_allow_html=True)

AXIS = dict(gridcolor=BORDER, linecolor=BORDER, showgrid=True,
            tickfont=dict(color=MUTED, size=11), title_font=dict(color=MUTED, size=11),
            zeroline=False)

fig = go.Figure()

us_plot = us_hist.dropna(subset=["national"])
if not us_plot.empty:
    # Thinner than the weekly it sits under: the daily is the published number,
    # but it is also the noisier one, and on a lopsided session it is the line
    # that misleads.
    fig.add_trace(go.Scatter(
        x=us_plot["report_date"], y=us_plot["national"],
        name="US Fresh 90s (daily)", mode="lines",
        line=dict(color=US_COLOR, width=1.4), connectgaps=True,
        hovertemplate="<b>US Fresh 90s</b>: $%{y:.2f}<extra></extra>",
    ))

wk_plot = us_weekly.dropna(subset=["weekly"]) if not us_weekly.empty else us_weekly
if not wk_plot.empty:
    fig.add_trace(go.Scatter(
        x=wk_plot["report_date"], y=wk_plot["weekly"],
        name="US Fresh 90s (weekly avg)", mode="lines",
        line=dict(color=US_WK_COLOR, width=2.4), connectgaps=True,
        hovertemplate="<b>US Fresh 90s, weekly avg</b>: $%{y:.2f}<extra></extra>",
    ))

if not sa_df.empty:
    fig.add_trace(go.Scatter(
        x=sa_df["report_date"], y=sa_df["avg_price"],
        name="South America Frozen 90s (weekly)", mode="lines+markers",
        line=dict(color=SA_COLOR, width=2, shape="hv"), marker=dict(size=5),
        hovertemplate="<b>South America Frozen 90s</b>: $%{y:.2f}<extra></extra>",
    ))

if not anz_df.empty:
    fig.add_trace(go.Scatter(
        x=anz_df["report_date"], y=anz_df["avg_price"],
        name="Australia/NZ Frozen 90s (weekly)", mode="lines+markers",
        line=dict(color=ANZ_COLOR, width=2, shape="hv"), marker=dict(size=5),
        hovertemplate="<b>Australia/NZ Frozen 90s</b>: $%{y:.2f}<extra></extra>",
    ))

# Default the initial view to the window where both series overlap (imports only
# start 2020) rather than the full ~22-year US archive — "All" still reaches back
# to 2003 via the range selector below.
_default_start = imp_hist["report_date"].min() if not imp_hist.empty else None
_default_end   = max(
    [d for d in [us_hist["report_date"].max() if not us_hist.empty else None,
                 imp_hist["report_date"].max() if not imp_hist.empty else None] if d is not None],
    default=None,
)

fig.add_layout_image(dict(
    source=JSA_LOGO, xref="paper", yref="paper",
    x=0.5, y=0.5, sizex=0.5, sizey=0.5,
    xanchor="center", yanchor="middle", sizing="contain",
    opacity=WATERMARK_OPACITY, layer="below",
))
fig.update_layout(
    paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
    font=dict(color=JPSI_DARK, size=11), hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                font=dict(color=JPSI_DARK, size=11), bgcolor="rgba(0,0,0,0)"),
    margin=dict(l=55, r=20, t=15, b=40),
    xaxis=dict(
        **AXIS, title="",
        range=[_default_start, _default_end] if _default_start is not None else None,
        rangeselector=dict(
            buttons=[
                dict(count=6, label="6M", step="month", stepmode="backward"),
                dict(count=1, label="YTD", step="year", stepmode="todate"),
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(count=5, label="5Y", step="year", stepmode="backward"),
                dict(count=10, label="10Y", step="year", stepmode="backward"),
                dict(step="all", label="All"),
            ],
            bgcolor="#f6f8fa", activecolor=JPSI_BLUE,
            font=dict(color=JPSI_DARK, size=10), bordercolor=BORDER,
        ),
        rangeslider=dict(visible=False), type="date",
    ),
    yaxis=dict(**AXIS, title="$/cwt", tickprefix="$"),
    height=420,
)
st.plotly_chart(fig, width="stretch")


# ── Spread Chart ───────────────────────────────────────────────────────────────

st.markdown('<div class="sec-header">Import spread — US Fresh 90s minus weekly import price</div>', unsafe_allow_html=True)

if not sa_df.empty or not anz_df.empty:
    fig_s = go.Figure()
    if not sa_df.empty:
        sa_matched = sa_df.copy()
        sa_matched["us"] = match_nearest(us_hist, sa_matched["report_date"])
        sa_matched["spread"] = sa_matched["us"] - sa_matched["avg_price"]
        sa_matched = sa_matched.dropna(subset=["spread"])
        fig_s.add_trace(go.Scatter(
            x=sa_matched["report_date"], y=sa_matched["spread"],
            name="US − South America", mode="lines+markers",
            line=dict(color=SA_COLOR, width=2), marker=dict(size=5),
            hovertemplate="<b>US − South America</b>: $%{y:.2f}<extra></extra>",
        ))
    if not anz_df.empty:
        anz_matched = anz_df.copy()
        anz_matched["us"] = match_nearest(us_hist, anz_matched["report_date"])
        anz_matched["spread"] = anz_matched["us"] - anz_matched["avg_price"]
        anz_matched = anz_matched.dropna(subset=["spread"])
        fig_s.add_trace(go.Scatter(
            x=anz_matched["report_date"], y=anz_matched["spread"],
            name="US − Australia/NZ", mode="lines+markers",
            line=dict(color=ANZ_COLOR, width=2), marker=dict(size=5),
            hovertemplate="<b>US − Australia/NZ</b>: $%{y:.2f}<extra></extra>",
        ))
    fig_s.add_hline(y=0, line=dict(color=MUTED, width=1, dash="dot"))
    fig_s.add_layout_image(dict(
        source=JSA_LOGO, xref="paper", yref="paper",
        x=0.5, y=0.5, sizex=0.5, sizey=0.5,
        xanchor="center", yanchor="middle", sizing="contain",
        opacity=WATERMARK_OPACITY, layer="below",
    ))
    fig_s.update_layout(
        paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(color=JPSI_DARK, size=11), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(color=JPSI_DARK, size=11), bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=55, r=20, t=15, b=40),
        xaxis=dict(**AXIS, title="", type="date"),
        yaxis=dict(**AXIS, title="$/cwt", tickprefix="$"),
        height=300,
    )
    st.plotly_chart(fig_s, width="stretch")
else:
    st.info("No import data available to compute spreads.")


# ── Data Tables ────────────────────────────────────────────────────────────────

with st.expander("📋  US Fresh 90s — data table"):
    disp = us_hist.copy()
    disp["report_date"] = disp["report_date"].dt.strftime("%Y-%m-%d")
    disp = disp.rename(columns={
        "report_date": "Date", "national": "National ($/cwt)",
        "national_low": "National low", "national_high": "National high",
        "national_trades": "National trades", "national_pounds": "National lb",
        "central": "Central ($/cwt)", "central_trades": "Central trades",
    }).sort_values("Date", ascending=False).reset_index(drop=True)
    st.dataframe(
        disp.style.format({
            "National ($/cwt)": "${:.2f}", "Central ($/cwt)": "${:.2f}",
            "National low": "${:.2f}", "National high": "${:.2f}",
            "National lb": "{:,.0f}",
            "National trades": "{:.0f}", "Central trades": "{:.0f}",
        }, na_rep="—"),
        width="stretch", height=320,
    )
    st.markdown(
        '<div class="note">Low and high are the day’s national price range. A range far wider '
        'than usual, or a national average well away from the Central line, means the average is '
        'blending trades that are not really the same market.</div>',
        unsafe_allow_html=True,
    )

with st.expander("📋  US Fresh 90s — weekly average (LM_XB460)"):
    if us_weekly.empty:
        st.info("Weekly LM_XB460 data is unavailable.")
    else:
        wdisp = us_weekly.copy()
        wdisp["report_date"] = wdisp["report_date"].dt.strftime("%Y-%m-%d")
        wdisp = wdisp.rename(columns={
            "report_date": "Week ending", "weekly": "National avg ($/cwt)",
            "weekly_trades": "Trades", "weekly_pounds": "Pounds",
        }).sort_values("Week ending", ascending=False).reset_index(drop=True)
        st.dataframe(
            wdisp.style.format({
                "National avg ($/cwt)": "${:.2f}", "Trades": "{:.0f}", "Pounds": "{:,.0f}",
            }, na_rep="—"),
            width="stretch", height=320,
        )

with st.expander("📋  Import Cow Meat (90%) — weekly data table"):
    disp = imp_hist.copy()
    disp["report_date"] = disp["report_date"].dt.strftime("%Y-%m-%d")
    disp = disp.rename(columns={
        "report_date": "Week of", "origin": "Origin", "avg_price": "Avg ($/cwt)",
        "low": "Low ($/cwt)", "high": "High ($/cwt)", "n": "Rows avg'd",
    }).sort_values("Week of", ascending=False).reset_index(drop=True)
    st.dataframe(
        disp.style.format({
            "Avg ($/cwt)": "${:.2f}", "Low ($/cwt)": "${:.2f}", "High ($/cwt)": "${:.2f}",
        }, na_rep="—"),
        width="stretch", height=320,
    )


# ── Legal Disclaimer Footer ───────────────────────────────────────────────────

_disclaimer_year = datetime.now().year
st.markdown("<hr style='border-color:#3a3a3a;margin-top:32px;margin-bottom:16px'>", unsafe_allow_html=True)
st.markdown(
    f'<div style="font-family:inherit;color:#888;font-size:0.68rem;line-height:1.6;text-align:center;padding:0 24px 24px;">'
    f'Trading commodity futures, options on futures, cash commodities, and over-the-counter derivative products involves substantial risk of loss and may not be suitable for all investors. '
    f'This communication is provided for informational purposes only and does not constitute investment advice, a recommendation, or an offer or solicitation to buy or sell any futures, options, cash commodities, or derivative products. '
    f'John Stewart &amp; Associates, Inc. does not accept orders to buy or sell any financial instruments via email. '
    f'The information contained herein has been obtained from sources believed to be reliable; however, its accuracy and completeness are not guaranteed. '
    f'Any opinions expressed are solely those of the author, are subject to change without notice, and should not be relied upon as a basis for investment decisions. '
    f'Past performance is not indicative of future results. '
    f'This message may contain confidential or proprietary information intended solely for the use of the designated recipient. '
    f'&copy; John Stewart &amp; Associates, Inc. {_disclaimer_year}'
    f'</div>',
    unsafe_allow_html=True,
)

# ── Auto-refresh ─────────────────────────────────────────────────────────────

if auto_refresh:
    time.sleep(1800)
    st.cache_data.clear()
    st.rerun()
