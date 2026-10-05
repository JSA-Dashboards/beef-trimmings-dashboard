"""
Weekly fetch of USDA AMS MARS NW_LS421 (Import Beef Trade), Cow Meat (90%) by
country of origin -- writes to Snowflake JSA.BEEF_TRIMMINGS.IMPORT_COW90.

Runs on this droplet because marsapi.ams.usda.gov rejects requests from
Streamlit Community Cloud's IPs (confirmed 2026-10-04: identical request
returns 200 from this droplet and from a residential-ish IP, 401 from
Streamlit Cloud). The Cloud-hosted beef-trimmings-dashboard reads this table
instead of calling MARS directly.

Idempotent: safe to re-run, MERGEs on (report_date, origin).
"""
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import requests
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MARS_BASE = "https://marsapi.ams.usda.gov/services/v1.2/reports"
LS421_ID = 2823
MARS_KEY = os.environ["MARS_API_KEY"]
IMPORT_ITEM = "Cow Meat (90%)"
ORIGIN_SA = "South America"
ORIGIN_ANZ = "Australia &/ New Zealand"
IMPORT_HISTORY_START = "01/01/2019"


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=3,
                   status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def fetch_import_cow90() -> pd.DataFrame:
    hi = (datetime.now() + timedelta(days=2)).strftime("%m/%d/%Y")
    url = f"{MARS_BASE}/{LS421_ID}"
    sess = _session()
    resp = sess.get(url, params={"q": f"report_begin_date={IMPORT_HISTORY_START}:{hi}", "allSections": "true"},
                     auth=(MARS_KEY, ""), timeout=90)
    resp.raise_for_status()
    payload = resp.json()

    details = next((s["results"] for s in payload if s.get("reportSection") == "Report Details"), [])
    if not details:
        return pd.DataFrame(columns=["report_date", "origin", "avg_price", "low", "high", "n"])

    df = pd.DataFrame(details)
    df = df[df["commodity"] == IMPORT_ITEM].copy()
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    df["low"] = pd.to_numeric(df["low_price"], errors="coerce")
    df["high"] = pd.to_numeric(df["high_price"], errors="coerce")
    df["mid"] = df[["low", "high"]].mean(axis=1)
    df = df.dropna(subset=["report_date", "mid"])

    origin_map = {ORIGIN_SA: "South America", ORIGIN_ANZ: "Australia/NZ"}
    df = df[df["country_of_origin"].isin(origin_map)].copy()
    df["origin"] = df["country_of_origin"].map(origin_map)

    weekly = (df.groupby(["report_date", "origin"], as_index=False)
                .agg(avg_price=("mid", "mean"), low=("low", "mean"),
                     high=("high", "mean"), n=("mid", "size")))
    return weekly.sort_values("report_date").reset_index(drop=True)


def sf_connect():
    with open(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"], "rb") as f:
        pwd = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PWD") or None
        pkey = serialization.load_pem_private_key(f.read(), password=pwd.encode() if pwd else None)
    pkb = pkey.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        role=os.environ["SNOWFLAKE_ROLE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        private_key=pkb,
    )


def upsert(conn, df: pd.DataFrame):
    if df.empty:
        print("No rows fetched, nothing to upsert.")
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE OR REPLACE TEMP TABLE JSA.BEEF_TRIMMINGS._STAGE_IMPORT_COW90 LIKE JSA.BEEF_TRIMMINGS.IMPORT_COW90
    """)
    rows = [
        (r.report_date.date(), r.origin, float(r.avg_price), float(r.low), float(r.high), int(r.n))
        for r in df.itertuples()
    ]
    cur.executemany(
        """
        INSERT INTO JSA.BEEF_TRIMMINGS._STAGE_IMPORT_COW90
            (REPORT_DATE, ORIGIN, AVG_PRICE, LOW_PRICE, HIGH_PRICE, N_ROWS, FETCHED_AT)
        VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP())
        """,
        rows,
    )
    cur.execute("""
        MERGE INTO JSA.BEEF_TRIMMINGS.IMPORT_COW90 t
        USING JSA.BEEF_TRIMMINGS._STAGE_IMPORT_COW90 s
        ON t.REPORT_DATE = s.REPORT_DATE AND t.ORIGIN = s.ORIGIN
        WHEN MATCHED THEN UPDATE SET
            AVG_PRICE = s.AVG_PRICE, LOW_PRICE = s.LOW_PRICE, HIGH_PRICE = s.HIGH_PRICE,
            N_ROWS = s.N_ROWS, FETCHED_AT = s.FETCHED_AT
        WHEN NOT MATCHED THEN INSERT
            (REPORT_DATE, ORIGIN, AVG_PRICE, LOW_PRICE, HIGH_PRICE, N_ROWS, FETCHED_AT)
            VALUES (s.REPORT_DATE, s.ORIGIN, s.AVG_PRICE, s.LOW_PRICE, s.HIGH_PRICE, s.N_ROWS, s.FETCHED_AT)
    """)
    print(f"Merged {len(rows)} rows into JSA.BEEF_TRIMMINGS.IMPORT_COW90")


def main():
    df = fetch_import_cow90()
    print(f"Fetched {len(df)} weekly origin rows from MARS NW_LS421")
    if not df.empty:
        print(df.tail(6).to_string(index=False))
    conn = sf_connect()
    try:
        upsert(conn, df)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), MAX(REPORT_DATE) FROM JSA.BEEF_TRIMMINGS.IMPORT_COW90")
        count, max_date = cur.fetchone()
        print(f"Table now has {count} rows, newest report_date {max_date}")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
