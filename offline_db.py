"""آداپتور آفلاین: خواندن Snapshot از demo_data/ — بدون streamlit، بدون pyodbc و بدون هیچ اتصال شبکه"""
import json
import os
import re
from pathlib import Path

import pandas as pd

def _find_dir() -> Path:
    env = os.getenv("DEMO_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    cands = [here / "demo_data", Path.cwd() / "demo_data", here, Path.cwd()]
    for base in (here, Path.cwd()):
        try:
            cands += [p for p in base.iterdir() if p.is_dir() and p.name.lower() == "demo_data"]
        except OSError:
            pass
    for p in cands:
        if (p / "metadata.json").exists():
            return p
    return here / "demo_data"


DIR = _find_dir()
FILES = {"view": "view_pbi", "financial": "financial_reports", "pos_tx": "pos_transactions"}
REQUIRED = {"taradods", "view", "inside", "fin_tx"}
# بازه‌ی زمانی هر کوئری (هم‌ارز WHERE در SPEC): یک ستون = بازه‌ی ساده؛ دو ستون = ورود «یا» خروج در بازه
WIN = {"taradods": ("EnrtyDateTime", "ExitDateTime"), "view": ("EnrtyDateTime", "ExitDateTime"), "anpr": ("CreateDate",), "barrier": ("LogDateTime",),
       "discount_logs": ("InsertDate",), "guests": ("CreateDate",), "pos_tx": ("TransactionDateTime",), "pay_requests": ("RequsetDateTime",),
       "real_income": ("DateTime",), "activity": ("CreateDateTime",), "events": ("ServerTime",), "sessions": ("ServerLoginTime",),
       "fin_tx": ("ExitDate",), "fin_view": ("ExitDate",)}
# ORDER BY هر کوئری در SPEC (برای اعمال TOP/limit با همان ردیف‌هایی که SQL برمی‌گرداند)
ORDER = {"taradods": ("EnrtyDateTime", False), "view": ("EnrtyDateTime", False), "inside": ("EnrtyDateTime", False), "inside_audit": ("EnrtyDateTime", True),
         "financial": ("Date_Farsi", False), "anpr": ("CreateDate", False), "barrier": ("LogDateTime", False), "discount_logs": ("InsertDate", False),
         "discount_codes": ("ID", False), "guests": ("CreateDate", False), "pos_tx": ("TransactionDateTime", False), "pay_requests": ("RequsetDateTime", False),
         "real_income": ("DateTime", False), "activity": ("CreateDateTime", False), "events": ("ServerTime", False), "sessions": ("ServerLoginTime", False),
         "blocked": ("UpdateDate", False), "cards": ("EndDate", False)}
BOOL_COLS = {"IsLost", "IsBlock", "Active", "IsEdited", "Closed", "SetPosTransaction", "SendToServer"}
STR_COLS = {"P1", "P2", "P3", "P4"}
_META = {}


class SnapshotError(RuntimeError):
    """Snapshot ناقص یا پیدا نشد"""


def _live_configured() -> bool:
    """آیا تنظیمات اتصال زنده (secrets) وجود دارد؟ import تنبل تا ماژول مستقل از streamlit بماند"""
    try:
        import streamlit as st
        return "mssql" in dict(st.secrets.get("connections", {}))
    except Exception:  # noqa: BLE001
        return False


def is_demo() -> bool:
    v = os.getenv("DEMO_MODE", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    if (DIR / "metadata.json").exists():
        return True
    return not _live_configured()  # نه Snapshot دارد نه secrets ⇒ خطای واضحِ Snapshot به‌جای خطای اتصال


def meta() -> dict:
    if not _META:
        try:
            _META.update(json.loads((DIR / "metadata.json").read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            raise SnapshotError(f"فایل metadata.json در «{DIR}» پیدا نشد. پوشه‌ی demo_data (با metadata.json) باید داخل مخزن گیت‌هاب، کنار app.py، کامیت شده باشد.") from e
    return _META

def _is_dt_col(c: str) -> bool:
    return c != "Date_Farsi" and (bool(re.search(r"Date|Time", c)) or c == "LastActivity")


def _read_csv(p: Path) -> pd.DataFrame:
    df = pd.read_csv(p, low_memory=False, dtype={c: str for c in STR_COLS})
    for c in df.columns:
        if c in BOOL_COLS:
            df[c] = df[c].map({"True": True, "False": False, True: True, False: False})
        elif _is_dt_col(c):
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def _window(name: str, df: pd.DataFrame, d0, d1) -> pd.DataFrame:
    cols = WIN.get(name)
    if d0 is None or d1 is None or not cols or df.empty:
        return df
    d0, d1 = pd.Timestamp(d0), pd.Timestamp(d1)
    m = pd.Series(False, index=df.index)
    for c in cols:
        if c in df:
            t = pd.to_datetime(df[c], errors="coerce")
            m |= (t >= d0) & (t < d1)
    return df[m].reset_index(drop=True)


def _limit(name: str, df: pd.DataFrame, limit) -> pd.DataFrame:
    if not limit or limit <= 0 or name not in ORDER or len(df) <= limit:
        return df
    col, asc = ORDER[name]
    if col not in df:
        return df
    return df.sort_values(col, ascending=asc, kind="stable").head(int(limit)).reset_index(drop=True)


def load(name: str, d0=None, d1=None, limit=None, columns=None) -> pd.DataFrame:
    if name == "server_now":
        return pd.DataFrame({"ServerNow": [pd.Timestamp(meta()["server_now"])]})
    stem = FILES.get(name, name)
    pq, cs = DIR / f"{stem}.parquet", DIR / f"{stem}.csv"
    if pq.exists():
        df = pd.read_parquet(pq)
    elif cs.exists():
        df = _read_csv(cs)
    elif name in REQUIRED:
        raise SnapshotError(f"فایل Snapshot «{stem}» در «{DIR}» پیدا نشد.")
    else:
        df = pd.DataFrame(columns=list(columns or []))
    df = _limit(name, _window(name, df, d0, d1), limit)
    df.attrs["source"] = f"demo:{name}"
    df.attrs["primary_error"] = None
    return df
