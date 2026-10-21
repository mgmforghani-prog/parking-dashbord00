"""واردکردن یک‌باره‌ی داده‌های اکسل نمونه به جدول‌های خام ParkingDB (فقط ستون‌های خام؛ ستون‌های محاسباتی را db.py می‌سازد)
اجرا:  python import_excel_to_sql.py parking_fraud_dataset.xlsx            (اطلاعات اتصال از .streamlit/secrets.toml خوانده می‌شود)
گزینه‌ی --replace جدول‌های موجود را پاک و دوباره می‌سازد.
"""
import sys
import tomllib
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.types import Unicode, UnicodeText

import db  # برای TX_RAW

SECRETS = Path(".streamlit/secrets.toml")


def engine():
    c = tomllib.loads(SECRETS.read_text(encoding="utf-8"))["mssql"]
    try:
        import pymssql  # noqa: F401
        dialect, q = "mssql+pymssql", {"charset": "utf8"}
    except ImportError:
        dialect, q = "mssql+pytds", {}
    return create_engine(URL.create(dialect, username=c["username"], password=c["password"], host=c["server"], port=int(c.get("port", 1433)),
                                    database=c["database"], query=q))


def sheet(x, name):
    d = x.parse(name)
    d.columns = [str(c).split("\n")[-1] for c in d.columns]
    return d


def main(path, replace):
    x, eng = pd.ExcelFile(path), engine()
    tx = sheet(x, "تراکنش_تردد")
    rc = x.parse("مغایرت_شیفت").iloc[:, :11]
    rc.columns = ["Date", "Shift", "Operator_ID", "CashPMS", "CashDeposit", "Diff", "CDR", "Alert", "CashTx", "TotalTx", "CashRatio"]
    rc["Date"] = pd.to_datetime(rc["Date"], errors="coerce")
    cap = x.parse("تراز_ظرفیت").iloc[:, :13]
    cap.columns = ["Start", "End", "Shift", "Entries", "Exits", "Expected", "Reported", "Physical", "LedgerDrift", "Ghosts", "PhysDrift", "Nominal", "Alert"]
    cap["Start"] = pd.to_datetime(cap["Start"], errors="coerce")
    ops = x.parse("اپراتورها").iloc[:, :2]
    ops.columns = ["Operator_ID", "Name"]
    jobs = {
        "transactions": tx[db.TX_RAW + (["Fraud_Scenario_Label"] if "Fraud_Scenario_Label" in tx else [])],
        "operators": ops[ops.Operator_ID.astype(str).str.startswith("OP-")],
        "deposits": rc.dropna(subset=["Date"]).rename(columns={"Date": "Shift_Date", "CashDeposit": "Deposited_Cash"})[["Shift_Date", "Shift", "Operator_ID", "Deposited_Cash"]],
        "capacity": cap.dropna(subset=["Start"]).rename(columns={"Start": "Window_Start", "End": "Window_End"})[["Window_Start", "Window_End", "Shift", "Reported", "Physical"]],
        "barrier": sheet(x, "رویداد_راهبند")[["Barrier_Event_ID", "Gate", "Open_Time", "Close_Time", "Trigger", "Operator_ID", "Linked_Event_ID", "Vehicles_Passed", "Tickets_Issued", "Clip_ID", "Note"]],
        "attendance": sheet(x, "حضور_شیفت")[["Shift_Date", "Shift", "Operator_ID", "Gate", "Clock_In", "Clock_Out", "Note"]],
        "ghosts": sheet(x, "ردیابی_رسوبی")[["Plate", "Entry_Event_ID", "Last_Seen_Time", "Last_Seen_Zone", "Exit_Evidence", "Suspected_Exit_Time", "Suspected_Exit_Gate", "Linked_Event_ID", "Exit_Clip_ID", "Status"]],
        "sightings": sheet(x, "تاریخچه_رسوبی")[["Sighting_ID", "Plate", "Event_Type", "Time", "Camera_ID", "Zone", "Plate_Conf", "Linked_Clip_ID", "Note"]],
        "clips": sheet(x, "لاگ_دوربین")[["Clip_ID", "Source_Type", "Ref_ID", "Camera_ID", "Start_Time", "End_Time", "FPS", "Resolution", "Frame_Drop_Pct", "Plate_Visible", "Storage_Status", "Clip_Path", "Log_Summary"]],
        "whitelist": x.parse("لیست_سفید").iloc[:, :2].set_axis(["Plate", "Owner_Type"], axis=1),
        "terminals": x.parse("پوزها").iloc[:, :1].set_axis(["Terminal_ID"], axis=1),
    }
    for key, df in jobs.items():
        schema, name = (db.TABLES[key].split(".") + [None])[:2] if "." in db.TABLES[key] else (None, db.TABLES[key])
        # متن فارسی باید NVARCHAR باشد (نه VARCHAR)؛ متن‌های بلند NVARCHAR(MAX)
        dtype = {c: (UnicodeText() if c in ("Log_Summary", "Note", "Clip_Path") else Unicode(255)) for c in df.select_dtypes(include=["object", "string"]).columns}
        df.to_sql(name, eng, schema=schema, if_exists="replace" if replace else "append", index=False, dtype=dtype, chunksize=500)
        print(f"{db.TABLES[key]:28s} {len(df):5d} ردیف")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], "--replace" in sys.argv)
