"""Phase 2 unit tests: bhavcopy (all 3 formats) and MTO parsers."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import sys
sys.path.insert(0, "src")

from nse_cash.data.bhavcopy import (join_delivery, parse_legacy, parse_pr,
                                    parse_udiff)
from nse_cash.data.delivery import parse_mto

D = date(2026, 9, 10)


def _zip_bytes(csv_text: str, name: str = "data.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, csv_text)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Legacy format
# ---------------------------------------------------------------------------

LEGACY_CSV = """SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,ISIN
TCS,EQ,100.5,102.0,99.0,101.0,101.0,100.0,150000,15225000,10-SEP-2026,INE467B01029
RELIANCE,EQ,50.0,51.0,49.0,50.5,50.5,50.0,900000,45225000,10-SEP-2026,INE002A01018
XYZ,BE,10.0,11.0,9.0,10.5,10.5,10.0,5000,52500,10-SEP-2026,INE000A01018
"""


def test_parse_legacy_filters_eq_and_maps_columns():
    df = parse_legacy(_zip_bytes(LEGACY_CSV), D)
    assert set(df["symbol"]) == {"TCS", "RELIANCE"}      # BE series filtered
    assert list(df.columns) == ["symbol", "date", "series", "open", "high", "low",
                                "close", "last", "volume", "turnover",
                                "deliverable_qty", "delivery_pct"]
    row = df[df["symbol"] == "TCS"].iloc[0]
    assert row["open"] == 100.5 and row["close"] == 101.0
    assert row["volume"] == 150000
    assert row["turnover"] == 15225000                   # absolute INR in legacy bhavcopy
    assert row["date"] == D


def test_parse_legacy_rejects_missing_columns():
    with pytest.raises(ValueError, match="missing columns"):
        parse_legacy(_zip_bytes("SYMBOL,SERIES\nTCS,EQ\n"), D)


def test_parse_legacy_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        parse_legacy(_zip_bytes("SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,TOTTRDQTY,TOTTRDVAL,TIMESTAMP\n"), D)


# ---------------------------------------------------------------------------
# UDiFF format
# ---------------------------------------------------------------------------

UDIFF_CSV = """TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StplmntVal,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
2026-09-10,2026-09-10,CM, NSE,XX,12345,INE467B01029,TCS,EQ,,,,100.0,103.0,99.5,102.0,101.5,100.0,,101.0,,,250000,25400000.0,12000,,1,,,
2026-09-10,2026-09-10,CM, NSE,XX,12346,INE002A01018,RELIANCE,EQ,,,,50.0,51.5,49.5,51.0,50.8,50.0,,51.0,,,900000,45800000.0,30000,,1,,,
"""


def test_parse_udiff_maps_columns():
    df = parse_udiff(_zip_bytes(UDIFF_CSV), D)
    assert set(df["symbol"]) == {"TCS", "RELIANCE"}
    row = df[df["symbol"] == "TCS"].iloc[0]
    assert row["close"] == 102.0
    assert row["volume"] == 250000
    assert row["turnover"] == 25400000.0                 # absolute rupees, no scaling


# ---------------------------------------------------------------------------
# PR full-bhavcopy format
# ---------------------------------------------------------------------------

PR_CSV = """SYMBOL,SERIES,DATE1,PREV_CLOSE,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,LAST_PRICE,CLOSE_PRICE,AVG_PRICE,TTL_TRD_QNTY,TURNOVER_LACS,NO_OF_TRADES,DELIV_QTY,DELIV_PER
TCS,EQ,10-SEP-2026,100.00,100.5,102.0,99.0,101.0,101.0,100.8,150000,1522.50,12000,110000,73.33
INFY,EQ,10-SEP-2026,50.00,50.5,51.0,49.5,50.5,50.5,50.2,900000,4522.00,30000,500000,55.56
"""


def test_legacy_url_casing():
    from nse_cash.data.bhavcopy import legacy_url
    url = legacy_url(date(2023, 9, 15))
    # Must preserve lowercase scheme/path for Apache/Akamai servers
    assert url == "https://archives.nseindia.com/content/historical/EQUITIES/2023/SEP/cm15SEP2023bhav.csv.zip"


def test_parse_pr_includes_delivery_columns():
    df = parse_pr(_zip_bytes(PR_CSV, "sec_bhavdata_full_10092026.csv"), D)
    assert set(df["symbol"]) == {"TCS", "INFY"}
    row = df[df["symbol"] == "TCS"].iloc[0]
    assert row["deliverable_qty"] == 110000
    assert row["delivery_pct"] == 73.33
    assert row["turnover"] == 1522.50 * 1e5


def test_parse_pr_handles_multi_file_pr_zip():
    """Real NSE PR zips contain Bc...csv, Pd...csv, etc. Parser must find sec_bhavdata_full."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Put unrelated Bc...csv first alphabetically to test that it is not naively picked
        zf.writestr("Bc100926.csv", "SYMBOL,SERIES,PURPOSE\nTCS,EQ,AGM\n")
        zf.writestr("sec_bhavdata_full_10092026.csv", PR_CSV)
    df = parse_pr(buf.getvalue(), D)
    assert set(df["symbol"]) == {"TCS", "INFY"}


def test_parse_ind_close_real_nse_format():
    from nse_cash.data.indices import _parse_ind_close
    sample = (
        "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
        "Nifty 50,02-09-2024,25333.6,25333.65,25235.5,25278.7,42.8,.17,222815249,28187.71,23.51,4.27,1.21\n"
        "Nifty 500,02-09-2024,23835.2,23835.20,23693.65,23760.7,26.15,.11,2522874000,45000.0,26.5,4.1,1.1\n"
    )
    df = _parse_ind_close(sample, date(2024, 9, 2))
    assert len(df) == 2
    n50 = df[df["index_name"] == "NIFTY 50"].iloc[0]
    assert n50["close"] == pytest.approx(25278.7)
    assert n50["open"] == pytest.approx(25333.6)
    assert not pd.isna(n50["close"])


def test_parse_ind_close_historical_aliases():
    """Verify that historical S&P / CNX index brandings (pre-Nov 2015) normalize properly."""
    from nse_cash.data.indices import _parse_ind_close
    sample_pre_2015 = (
        "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Volume\n"
        "CNX NIFTY,15-01-2013,6010.5,6050.2,5990.1,6035.8,1234567\n"
        "S&P CNX 500,15-01-2013,4850.0,4890.0,4840.0,4880.5,9876543\n"
        "CNX MIDCAP,15-01-2013,7500.0,7550.0,7480.0,7520.0,1111111\n"
    )
    df = _parse_ind_close(sample_pre_2015, date(2013, 1, 15))
    assert len(df) == 2
    names = set(df["index_name"])
    assert names == {"NIFTY 50", "NIFTY 500"}
    n50 = df[df["index_name"] == "NIFTY 50"].iloc[0]
    assert n50["close"] == pytest.approx(6035.8)
    n500 = df[df["index_name"] == "NIFTY 500"].iloc[0]
    assert n500["close"] == pytest.approx(4880.5)


# ---------------------------------------------------------------------------
# MTO delivery
# ---------------------------------------------------------------------------

MTO_TEXT = """ Record Type , Sr No , Symbol , Series , Quantity Traded , Deliverable Quantity , Delivery Percent
 20 , 1 , TCS , EQ , 150000 , 110000 , 73.33
 20 , 2 , INFY , EQ , 900000 , 500000 , 55.56
 20 , 3 , XYZBE , BE , 5000 , 2000 , 40.00
 95 , 4 , TOTAL , , 955000 , 612000 , 64.08
"""


def test_parse_mto_filters_eq_and_record_type_20():
    df = parse_mto(MTO_TEXT)
    assert set(df["symbol"]) == {"TCS", "INFY"}          # BE row and record 95 dropped
    row = df[df["symbol"] == "TCS"].iloc[0]
    assert row["traded_qty"] == 150000
    assert row["deliverable_qty"] == 110000
    assert row["delivery_pct"] == 73.33


def test_parse_mto_rejects_empty():
    with pytest.raises(ValueError, match="no record-type-20"):
        parse_mto("garbage\n")


def test_join_delivery_merges_on_symbol_date():
    bars = pd.DataFrame({
        "symbol": ["TCS"], "date": [D], "series": ["EQ"], "open": [100.0],
        "high": [102.0], "low": [99.0], "close": [101.0], "last": [None],
        "volume": [150000], "turnover": [1.5e7],
        "deliverable_qty": [pd.NA], "delivery_pct": [pd.NA],
    })
    mto = pd.DataFrame({
        "symbol": ["TCS"], "date": [D], "traded_qty": [150000],
        "deliverable_qty": [110000], "delivery_pct": [73.33],
    })
    merged = join_delivery(bars, mto)
    assert merged.iloc[0]["deliverable_qty"] == 110000
    assert merged.iloc[0]["delivery_pct"] == 73.33
