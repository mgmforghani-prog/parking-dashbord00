"""استخراج Snapshot واقعی ParkingDB برای ارائه آفلاین. اجرا (داخل شبکه سازمان): python export_demo_snapshot.py"""
import argparse
import json
import logging
import os
import tomllib
from pathlib import Path

os.environ["DEMO_MODE"] = "0"  # استخراج همیشه از دیتابیس زنده انجام می‌شود
logging.getLogger("streamlit").setLevel(logging.ERROR)

import pandas as pd  # noqa: E402

import analytics as an  # noqa: E402
import db  # noqa: E402

FILES = {"view": "view_pbi", "financial": "financial_reports", "pos_tx": "pos_transactions"}
WINDOWED = ["taradods", "view", "anpr", "barrier", "discount_logs", "guests", "pos_tx", "pay_requests", "real_income", "activity", "events", "sessions"]
STATIC = ["inside", "inside_audit", "financial", "discount_codes", "blocked", "cards"]
FIN = ["fin_tx", "fin_view"]
REQUIRED = {"taradods", "view", "inside", "fin_tx"}
LIMIT = 5_000_000
VIEW_EXIT_COLS = ["OutputGateID", "OutputGate", "ExitUser", "TotalPrice", "FinalPrice", "Cash", "Pos", "VATPrice"]


def censor_view(df, cut):
    """خروج‌های امروز هنوز کامل نیستند: سمت خروج پاک می‌شود (هم‌راستا با an.censor_open_day)"""
    if df.empty or "ExitDateTime" not in df:
        return df
    if "EnrtyDateTime" in df:
        df = df[~(pd.to_datetime(df["EnrtyDateTime"], errors="coerce") >= cut).to_numpy()]
    late = (pd.to_datetime(df["ExitDateTime"], errors="coerce") >= cut).to_numpy()
    if late.any():
        df = df.copy()
        for c in ["ExitDateTime", *VIEW_EXIT_COLS]:
            if c in df:
                df[c] = df[c].where(~late)
    return df


def save(df, stem, out, fmt):
    df = df.copy()
    df.attrs = {}
    for ext in ("parquet", "csv"):  # فایل قدیمیِ فرمت دیگر نباید جلوی فایل جدید را بگیرد
        (out / f"{stem}.{ext}").unlink(missing_ok=True)
    if fmt == "csv":
        df.to_csv(out / f"{stem}.csv", index=False, encoding="utf-8")
        return
    try:
        df.to_parquet(out / f"{stem}.parquet", index=False)
    except Exception:  # noqa: BLE001 ستون‌های object با نوع مخلوط
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v))
        df.to_parquet(out / f"{stem}.parquet", index=False)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--secrets", default=".streamlit/secrets.toml")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="demo_data")
    a = ap.parse_args(argv)

    try:
        import pyarrow  # noqa: F401
        fmt = "parquet"
    except ImportError:
        fmt = "csv"
        print("pyarrow نصب نیست؛ خروجی CSV ذخیره می‌شود (برای Parquet: pip install pyarrow)")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sec = tomllib.loads(Path(a.secrets).read_text(encoding="utf-8"))
    db.configure(sec["connections"]["mssql"])

    now = pd.Timestamp(str(db.fetch("server_now").iloc[0, 0]))
    cut = an.current_day_start(now)  # قانون طلایی: روز جاری (ناقص) وارد Snapshot نمی‌شود
    d0, d1 = (cut - pd.Timedelta(days=a.days)).to_pydatetime(), cut.to_pydatetime()
    print(f"server_now={now} | بازه استخراج: {d0:%Y-%m-%d} تا {d1:%Y-%m-%d} (بدون روز جاری)")

    counts, boundary = {}, {}
    for name in WINDOWED + STATIC + FIN:
        try:
            if name in FIN:
                df = db.fetch_fin(name, d0, d1, 0.0)
            else:
                df = db.fetch(name, d0, d1, limit=LIMIT)
            if name == "taradods":
                boundary = an.day_boundary_report(df, now)
                df = an.censor_open_day(df, now)
            elif name == "view":
                df = censor_view(df, cut)
        except Exception as e:  # noqa: BLE001
            if name in REQUIRED:
                raise SystemExit(f"خطای ضروری در «{name}»: {e}")
            print(f"⚠ {name}: رد شد ({str(e)[:120]})")
            df = pd.DataFrame(columns=db.COLUMNS.get(name, []))
        save(df, FILES.get(name, name), out, fmt)
        counts[name] = int(len(df))
        print(f"✓ {name}: {len(df):,} ردیف")

    meta = dict(extracted_at=pd.Timestamp.now().isoformat(timespec="seconds"), server_now=str(now), window_start=str(d0), window_end=str(d1),
                days=a.days, format=fmt, golden_rule="current-day excluded", boundary=boundary, rows=counts)
    (out / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nانجام شد → {out.resolve()}")


if __name__ == "__main__":
    main()
