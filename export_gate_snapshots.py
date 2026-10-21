"""استخراج Snapshot واقعی dbo.Gates و dbo.GateTypes برای DEMO_MODE=1 (فقط SELECT صریح، فقط ستون‌های غیرمحرمانه).
اجرا (یک‌بار، روی سیستمی که به SQL Server دسترسی دارد):  python export_gate_snapshots.py
تنظیمات اتصال از همان .streamlit/secrets.toml محلی خوانده می‌شود؛ هیچ مقدار محرمانه‌ای چاپ یا ذخیره نمی‌شود.
خروجی: demo_data/gates.csv و demo_data/gate_types.csv (یا مسیر DEMO_DIR) — با قالب CSV سازگار با offline_db.load."""
from __future__ import annotations
import os, sys
from pathlib import Path
import pandas as pd
try:
    import tomllib
except ImportError:  # پایتون < 3.11
    import tomli as tomllib  # type: ignore
import pyodbc

HERE = Path(__file__).parent
OUT = Path(os.environ.get("DEMO_DIR") or HERE / "demo_data")  # همان پوشه‌ی offline_db.DIR
SQL = {
    "gates": "SELECT ID, Title, GateTypeID, ANPREnable, ReaderEnable, Kiosk, PCPOSEnable, LoopEnable, RoadblockEnable FROM dbo.Gates ORDER BY ID ASC",
    "gate_types": "SELECT ID, Title FROM dbo.GateTypes ORDER BY ID ASC",
}


def main() -> int:
    cfg = tomllib.loads((HERE / ".streamlit" / "secrets.toml").read_text(encoding="utf-8"))["connections"]["mssql"]
    br = lambda v: "{" + str(v).replace("}", "}}") + "}"  # noqa: E731
    cs = (f"DRIVER={{{cfg.get('driver', 'ODBC Driver 17 for SQL Server')}}};SERVER={cfg['server']},{cfg['port']};DATABASE={br(cfg['database'])};"
          f"UID={br(cfg['username'])};PWD={br(cfg['password'])};Encrypt=no;TrustServerCertificate=yes;Connection Timeout=10;")
    OUT.mkdir(parents=True, exist_ok=True)
    with pyodbc.connect(cs, timeout=10, autocommit=True) as con:
        for name, sql in SQL.items():
            cur = con.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            df = pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)
            df.to_csv(OUT / f"{name}.csv", index=False, encoding="utf-8")
            print(f"{name}: {len(df)} ردیف ← {OUT / (name + '.csv')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
