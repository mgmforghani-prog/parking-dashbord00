"""لایه‌ی خواندنی ParkingDB (SQL Server) با pyodbc و ODBC Driver 17 — فقط SELECT، بدون SELECT *، همه‌ی مقادیر با پارامتر (?)"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
# بالای فایل: import pyodbc را با این جایگزین کن
try:
    import pyodbc
except ImportError:  # حالت آفلاین (DEMO_MODE): pyodbc لازم نیست
    import types
    pyodbc = types.SimpleNamespace(Error=type("Error", (Exception,), {}), drivers=lambda: [], connect=None)
import streamlit as st

import offline_db
TOP_DEFAULT = 50000
DRIVER_DEFAULT = "ODBC Driver 17 for SQL Server"
PRESENT_MAX_DAYS = 7   # فقط برای فهرست پلاک‌های مسدودِ جایگزین (blocked#2/#3)؛ تقسیم حاضر/رسوبی به آن وابسته نیست
SEDIMENT_HOURS = 48    # سند: کمتر از ۴۸ ساعت «حاضر قطعی» · از ۴۸ ساعت «رسوبی واقعی / نیازمند بازرسی حراست»
SEDIMENT_MAX_DAYS = 7  # سقف رسوبی واقعی (۱۶۸ ساعت)؛ بالاتر = قدیمی/مشکوک به داده‌ی مرده و خارج از آمار رسوبی بحرانی
STALE_MAX_DAYS = 45
PLATE_LEN = (2, 3, 2)
SECRETS_HELP = ('در .streamlit/secrets.toml (یا Settings > Secrets در Streamlit Cloud) این بخش لازم است:\n'
                '[connections.mssql]\nserver = "..."\nport = 1433\ndatabase = "ParkingDB"\nusername = "..."\npassword = "..."\n'
                'driver = "ODBC Driver 17 for SQL Server"   # اختیاری')
DRIVER_HELP = ('ODBC Driver 17 for SQL Server روی این سیستم نصب نیست. روی ویندوز از سایت مایکروسافت نصب کنید؛ روی لینوکس بسته‌ی msodbcsql17 '
               'و unixodbc را نصب کنید (در Streamlit Cloud فایل packages.txt با محتوای unixodbc و تنظیم مخزن مایکروسافت لازم است).')
AUDIT_REASON = {1: "بدون خروج قطعی (عمر بیش از ۴۵ روز)", 2: "رکورد باطل (تردد جدیدتر برای همان پلاک ثبت شده)", 3: "پلاک نامعتبر یا مخدوش"}


class DBError(RuntimeError):
    """خطای قابل‌نمایش به کاربر"""

# بعد از class DBError
_GATE_SNAPSHOTS = ("gates", "gate_types")


def _demo(name: str, d0=None, d1=None, limit=None) -> pd.DataFrame:
    try:
        df = offline_db.load(name, d0, d1, limit, COLUMNS.get(name))
    except offline_db.SnapshotError as e:
        raise DBError(str(e)) from e
    if name in _GATE_SNAPSHOTS and df.empty:  # offline_db برای فایل اختیاریِ غایب جدول خالی برمی‌گرداند؛ برای گیت‌ها این را خطای قابل‌نمایش می‌کنیم
        raise DBError(f"اسنپ‌شات «{name}» در {offline_db.DIR} خالی/پیدا نشد؛ export_gate_snapshots.py را در شبکه سازمان اجرا کنید.")
    return df


_OVERRIDE: dict | None = None


def configure(cfg: dict) -> None:
    global _OVERRIDE
    _OVERRIDE = dict(cfg)


def _cfg() -> dict:
    try:
        c = dict(_OVERRIDE) if _OVERRIDE else dict(st.secrets["connections"]["mssql"])
    except Exception as e:  # noqa: BLE001
        raise DBError('تنظیمات st.secrets["connections"]["mssql"] پیدا نشد.\n' + SECRETS_HELP) from e
    miss = [k for k in ("server", "port", "database", "username", "password") if not c.get(k)]
    if miss:
        raise DBError("کلیدهای ناقص در secrets: " + "، ".join(miss) + "\n" + SECRETS_HELP)
    return c


def is_configured() -> bool:
    try:
        _cfg()
        return True
    except DBError:
        return False


def _brace(v: str) -> str:
    return "{" + v.replace("}", "}}") + "}"


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _conn_str(c: dict) -> tuple[str, str, int]:
    driver = str(c.get("driver") or DRIVER_DEFAULT)
    login = int(c.get("login_timeout", 10))
    parts = [f"DRIVER={{{driver}}}", f"SERVER={c['server']},{c['port']}", f"DATABASE={_brace(str(c['database']))}", f"UID={_brace(str(c['username']))}",
             f"PWD={_brace(str(c['password']))}", f"Encrypt={'yes' if _truthy(c.get('encrypt', False)) else 'no'}", "TrustServerCertificate=yes", f"Connection Timeout={login}"]
    return ";".join(parts) + ";", driver, login


@contextmanager
def _connect():
    if offline_db.is_demo():
        raise DBError("حالت آفلاین (DEMO_MODE) فعال است؛ اتصال به SQL Server مجاز نیست.")
    cs, driver, login = _conn_str(_cfg())
    if driver not in pyodbc.drivers():
        raise DBError(f"درایور «{driver}» پیدا نشد.\n" + DRIVER_HELP)
    con = pyodbc.connect(cs, timeout=login, autocommit=True)
    try:
        con.timeout = 0
        yield con
    finally:
        con.close()


_SELECT_ONLY = re.compile(r"^\s*SELECT\b", re.I)
_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|GRANT)\b|;|--|/\*|SELECT\s+\*|,\s*\*|\.\*", re.I)
_TRANSIENT_STATES = {"08S01", "08S02", "08001", "HYT00", "HYT01", "40001", "40197", "40501", "40613"}
_CONN_FATAL_STATES = {"08001", "28000", "08004", "IM002", "IM003", "IM004", "IM010"}


def _msg(e: BaseException) -> str:
    m = e.args[-1] if getattr(e, "args", None) else e
    return (m.decode("utf-8", "replace") if isinstance(m, bytes) else str(m)).strip()[:600]


def _state(e: BaseException) -> str:
    return str(e.args[0]) if getattr(e, "args", None) else ""


def _transient(e: BaseException) -> bool:
    return _state(e) in _TRANSIENT_STATES or "deadlock" in _msg(e).lower()


def _numeric(df: pd.DataFrame) -> pd.DataFrame:
    for c in df.columns:
        if df[c].dtype == object:
            s = df[c].dropna()
            if len(s) and isinstance(s.iloc[0], Decimal):
                df[c] = pd.to_numeric(df[c].map(lambda v: float(v) if isinstance(v, Decimal) else v), errors="coerce")
    return df


def run(sql: str, params: tuple = (), retries: int = 2) -> pd.DataFrame:
    """تنها نقطه‌ی اجرای SQL: فقط SELECT، بدون SELECT *، TOP فقط همراه ORDER BY، پارامتر pyodbc (?)"""
    if not _SELECT_ONLY.match(sql) or _FORBIDDEN.search(sql):
        raise DBError("فقط SELECT صریح (بدون *) مجاز است.")
    if re.search(r"\bTOP\b", sql, re.I) and not re.search(r"\bORDER\s+BY\b", sql, re.I):
        raise DBError("TOP بدون ORDER BY ممنوع است.")
    for attempt in range(retries + 1):
        try:
            with _connect() as con:
                cur = con.cursor()
                cur.execute(sql, *params)
                cols = [d[0] for d in cur.description]
                rows = [tuple(r) for r in cur.fetchall()]
            return _numeric(pd.DataFrame(rows, columns=cols))
        except pyodbc.Error as e:
            if attempt < retries and _transient(e):
                time.sleep(0.8 * (attempt + 1))
                continue
            raise
    raise DBError("اجرای کوئری ناموفق بود.")


def _py(d):
    return d.to_pydatetime() if hasattr(d, "to_pydatetime") else d


_RANGE2 = "((EnrtyDateTime >= ? AND EnrtyDateTime < ?) OR (ExitDateTime >= ? AND ExitDateTime < ?))"
_RANGE2_T = "((t.EnrtyDateTime >= ? AND t.EnrtyDateTime < ?) OR (t.ExitDateTime >= ? AND t.ExitDateTime < ?))"
_TARADOD_COLS = ("ID, CarTypeID, CardID, EnrtyDateTime, EntryUserID, InputGateID, ExitDateTime, ExitUserID, OutputGateID, TotalPrice, FinalPrice, PaymentID, "
                 "PosDetailID, IsLost, Cash, Pos, Refund, UserDescription, PlateID, SendToServer, VATPrice, ANPRPlateId, RoundPrice, ExitPlateID, "
                 "ExitANPRPlateID, PosTerminalNo")
SHIFT_SQL = ("CASE WHEN DATEPART(hour, ExitDateTime) >= 6 AND DATEPART(hour, ExitDateTime) < 14 THEN 1 "
             "WHEN DATEPART(hour, ExitDateTime) >= 14 AND DATEPART(hour, ExitDateTime) < 22 THEN 2 ELSE 3 END")
_LATER = ("EXISTS (SELECT 1 FROM dbo.Taradods AS n WHERE n.PlateID = t.PlateID AND "
          "(n.EnrtyDateTime > t.EnrtyDateTime OR (n.EnrtyDateTime = t.EnrtyDateTime AND n.ID > t.ID)))")
_NULL_PLATE = "CAST(NULL AS nvarchar(20)) AS P1, CAST(NULL AS nvarchar(20)) AS P2, CAST(NULL AS nvarchar(20)) AS P3, CAST(NULL AS nvarchar(20)) AS P4, "
_INS_BASE = "t.ID, t.CarTypeID, t.CardID, t.EnrtyDateTime, t.EntryUserID, t.InputGateID, t.PlateID, t.IsLost, t.ANPRPlateId, "
_STAY = "DATEDIFF(minute, t.EnrtyDateTime, GETDATE()) / 1440.0 AS StayDays"


def _plate_ok(a: str) -> str:
    def v(col: str) -> str:
        return f"LTRIM(RTRIM(CAST({a}.{col} AS nvarchar(20))))"
    p1, p2, p3, p4 = v("P1"), v("P2"), v("P3"), v("P4")
    return (f"({p1} NOT LIKE N'%[^0-9]%' AND LEN({p1}) = {PLATE_LEN[0]} AND {p1} <> N'00' AND LEN({p2}) >= 1 AND "
            f"{p3} NOT LIKE N'%[^0-9]%' AND LEN({p3}) = {PLATE_LEN[1]} AND {p3} <> N'000' AND "
            f"{p4} NOT LIKE N'%[^0-9]%' AND LEN({p4}) = {PLATE_LEN[2]} AND {p4} <> N'00')")


def _inside_live(with_plates: bool) -> str:
    if with_plates:
        sel, frm, ok = _INS_BASE + "p.P1, p.P2, p.P3, p.P4, ", "dbo.Taradods AS t INNER JOIN dbo.Plates AS p ON p.ID = t.PlateID", f" AND {_plate_ok('p')}"
    else:
        sel, frm, ok = _INS_BASE + _NULL_PLATE, "dbo.Taradods AS t", ""
    return (f"SELECT TOP (?) {sel}{_STAY} FROM {frm} WHERE t.ExitDateTime IS NULL AND t.PlateID > 0 AND "
            f"t.EnrtyDateTime IS NOT NULL AND t.EnrtyDateTime >= DATEADD(day, -{STALE_MAX_DAYS}, GETDATE()){ok} AND NOT {_LATER} ORDER BY t.EnrtyDateTime DESC")


def _inside_audit(with_plates: bool) -> str:
    if with_plates:
        sel, frm = _INS_BASE + "p.P1, p.P2, p.P3, p.P4, ", "dbo.Taradods AS t LEFT JOIN dbo.Plates AS p ON p.ID = t.PlateID"
        bad = f"WHEN t.PlateID IS NULL OR t.PlateID <= 0 THEN 3 WHEN CASE WHEN {_plate_ok('p')} THEN 0 ELSE 1 END = 1 THEN 3 "
    else:
        sel, frm, bad = _INS_BASE + _NULL_PLATE, "dbo.Taradods AS t", "WHEN t.PlateID IS NULL OR t.PlateID <= 0 THEN 3 "
    inner = (f"SELECT {sel}{_STAY}, CASE {bad}WHEN {_LATER} THEN 2 WHEN t.EnrtyDateTime IS NULL OR t.EnrtyDateTime < DATEADD(day, -{STALE_MAX_DAYS}, GETDATE()) THEN 1 "
             f"ELSE 0 END AS AuditCode FROM {frm} WHERE t.ExitDateTime IS NULL")
    return ("SELECT TOP (?) ID, CarTypeID, CardID, EnrtyDateTime, EntryUserID, InputGateID, PlateID, IsLost, ANPRPlateId, P1, P2, P3, P4, StayDays, AuditCode "
            f"FROM ({inner}) AS x WHERE AuditCode > 0 ORDER BY EnrtyDateTime ASC")


_VIEW_TAIL = ("t.CarTypeID, t.CardID, t.EnrtyDateTime, t.InputGateID, t.ExitDateTime, t.OutputGateID, t.TotalPrice, t.FinalPrice, t.Cash, t.Pos, t.VATPrice, t.PlateID, "
              "CAST(t.OutputGateID AS nvarchar(50)) AS OutputGate, CAST(t.InputGateID AS nvarchar(50)) AS InputGate "
              f"FROM dbo.Taradods AS t WHERE {_RANGE2_T} ORDER BY t.EnrtyDateTime DESC")
_PAY_STATUS = "CASE WHEN ISNULL(t.Refund, 0) > 0 THEN N'بازپرداخت‌شده' WHEN CAST(ISNULL(t.Cash, 0) AS bigint) + ISNULL(t.Pos, 0) >= ISNULL(t.FinalPrice, 0) THEN N'تسویه‌شده' ELSE N'تسویه‌ناقص' END"
_BLOCK_T = ("t.ID AS Id, t.EnrtyDateTime AS CreateDate, t.PlateID AS PlateId, {plate}CAST(1 AS bit) AS IsBlock, t.EnrtyDateTime AS UpdateDate FROM {frm} "
            f"WHERE t.ExitDateTime IS NULL AND t.PlateID > 0 AND t.EnrtyDateTime < DATEADD(day, -{PRESENT_MAX_DAYS}, GETDATE()) AND "
            f"t.EnrtyDateTime >= DATEADD(day, -{STALE_MAX_DAYS}, GETDATE()) AND NOT {_LATER} ORDER BY t.EnrtyDateTime ASC")

FIN_TX_SQL = ("SELECT CAST(ExitDateTime AS date) AS ExitDate, " + SHIFT_SQL + " AS ShiftCode, OutputGateID, ExitUserID, PaymentID, COUNT(*) AS Exits, "
              "SUM(CAST(ISNULL(TotalPrice, 0) AS decimal(19, 2))) AS SumTotal, SUM(CAST(ISNULL(FinalPrice, 0) AS bigint)) AS SumFinal, "
              "SUM(CAST(ISNULL(Cash, 0) AS bigint)) AS SumCash, SUM(CAST(ISNULL(Pos, 0) AS bigint)) AS SumPos, SUM(CAST(ISNULL(Refund, 0) AS bigint)) AS SumRefund, "
              "SUM(CAST(ISNULL(VATPrice, 0) AS bigint)) AS SumVAT, SUM(CAST(ISNULL(RoundPrice, 0) AS bigint)) AS SumRound, "
              "SUM(CASE WHEN ISNULL(Refund, 0) > 0 THEN 1 ELSE 0 END) AS RefundN, "
              "SUM(CASE WHEN ISNULL(Refund, 0) = 0 AND ISNULL(Pos, 0) > 0 AND PosDetailID IS NULL THEN 1 ELSE 0 END) AS PosNoDetailN, "
              "SUM(CASE WHEN ISNULL(Refund, 0) = 0 AND ISNULL(Pos, 0) > 0 AND PosDetailID IS NULL THEN CAST(Pos AS bigint) ELSE 0 END) AS PosNoDetailAmt, "
              "SUM(CASE WHEN ISNULL(Refund, 0) = 0 AND NOT (ISNULL(Pos, 0) > 0 AND PosDetailID IS NULL) AND ISNULL(FinalPrice, 0) - ISNULL(Cash, 0) - ISNULL(Pos, 0) > ? THEN 1 ELSE 0 END) AS UnderN, "
              "SUM(CASE WHEN ISNULL(Refund, 0) = 0 AND NOT (ISNULL(Pos, 0) > 0 AND PosDetailID IS NULL) AND ISNULL(FinalPrice, 0) - ISNULL(Cash, 0) - ISNULL(Pos, 0) > ? "
              "THEN CAST(ISNULL(FinalPrice, 0) AS bigint) - ISNULL(Cash, 0) - ISNULL(Pos, 0) ELSE 0 END) AS UnderAmt, "
              "SUM(CASE WHEN ISNULL(Refund, 0) = 0 AND ISNULL(Cash, 0) + ISNULL(Pos, 0) - ISNULL(FinalPrice, 0) > ? THEN 1 ELSE 0 END) AS OverN, "
              "SUM(CASE WHEN ISNULL(Refund, 0) = 0 AND ISNULL(Cash, 0) + ISNULL(Pos, 0) - ISNULL(FinalPrice, 0) > ? "
              "THEN CAST(ISNULL(Cash, 0) AS bigint) + ISNULL(Pos, 0) - ISNULL(FinalPrice, 0) ELSE 0 END) AS OverAmt, "
              "SUM(CASE WHEN ISNULL(IsLost, 0) = 1 THEN 1 ELSE 0 END) AS LostN, "
              "SUM(CASE WHEN FinalPrice = 0 AND ISNULL(TotalPrice, 0) > 0 THEN 1 ELSE 0 END) AS FreeN, "
              "SUM(CASE WHEN FinalPrice = 0 AND ISNULL(TotalPrice, 0) > 0 THEN CAST(TotalPrice AS decimal(19, 2)) ELSE 0 END) AS FreeAmt "
              "FROM dbo.Taradods WHERE ExitDateTime >= ? AND ExitDateTime < ? "
              "GROUP BY CAST(ExitDateTime AS date), " + SHIFT_SQL + ", OutputGateID, ExitUserID, PaymentID ORDER BY ExitDate ASC")
_REASON_SQL = "CASE WHEN DiscountLogsTaradodID IS NOT NULL THEN 1 WHEN GuestsTaradodID IS NOT NULL THEN 2 WHEN CustomerTaradodID IS NOT NULL THEN 3 ELSE 4 END"
FIN_VIEW_SQL = ("SELECT CAST(ExitDateTime AS date) AS ExitDate, " + SHIFT_SQL + " AS ShiftCode, OutputGateID, OutputGate, ExitUser, " + _REASON_SQL + " AS Reason, "
                "COUNT(*) AS Exits, SUM(CAST(ISNULL(TotalPrice, 0) AS decimal(19, 2))) AS SumTotal, SUM(CAST(ISNULL(FinalPrice, 0) AS bigint)) AS SumFinal, "
                "SUM(CASE WHEN ISNULL(TotalPrice, 0) > 0 AND ISNULL(FinalPrice, 0) < TotalPrice THEN 1 ELSE 0 END) AS WaiverN, "
                "SUM(CASE WHEN ISNULL(TotalPrice, 0) > 0 AND ISNULL(FinalPrice, 0) < TotalPrice THEN CAST(TotalPrice AS decimal(19, 2)) - ISNULL(FinalPrice, 0) ELSE 0 END) AS WaiverAmt, "
                "SUM(CASE WHEN ISNULL(FinalPrice, 0) > ISNULL(TotalPrice, 0) THEN CAST(ISNULL(FinalPrice, 0) AS decimal(19, 2)) - ISNULL(TotalPrice, 0) ELSE 0 END) AS UpliftAmt "
                "FROM dbo.View_Taradods_PBI WHERE ExitDateTime >= ? AND ExitDateTime < ? "
                "GROUP BY CAST(ExitDateTime AS date), " + SHIFT_SQL + ", OutputGateID, OutputGate, ExitUser, " + _REASON_SQL + " ORDER BY ExitDate ASC")

SPEC: dict[str, list[tuple[str, str]]] = {
    "taradods": [(f"SELECT TOP (?) {_TARADOD_COLS} FROM dbo.Taradods WHERE {_RANGE2} ORDER BY EnrtyDateTime DESC", "win2")],
    "view": [
        ("SELECT TOP (?) id, GuestsTaradodID, DiscountLogsTaradodID, CustomerTaradodID, ExitUser, CarTypeID, CardID, EnrtyDateTime, InputGateID, ExitDateTime, "
         f"OutputGateID, TotalPrice, FinalPrice, Cash, Pos, VATPrice, PlateID, OutputGate, InputGate FROM dbo.View_Taradods_PBI WHERE {_RANGE2} ORDER BY EnrtyDateTime DESC", "win2"),
        ("SELECT TOP (?) t.ID AS id, CASE WHEN EXISTS (SELECT 1 FROM dbo.Guests AS g WHERE g.TaradodID = t.ID) THEN t.ID END AS GuestsTaradodID, "
         "CASE WHEN EXISTS (SELECT 1 FROM dbo.DiscountLogs AS d WHERE d.TaradodID = t.ID) THEN t.ID END AS DiscountLogsTaradodID, CAST(NULL AS int) AS CustomerTaradodID, "
         "CAST(t.ExitUserID AS nvarchar(50)) AS ExitUser, " + _VIEW_TAIL, "win2"),
        ("SELECT TOP (?) t.ID AS id, CAST(NULL AS int) AS GuestsTaradodID, CAST(NULL AS int) AS DiscountLogsTaradodID, CAST(NULL AS int) AS CustomerTaradodID, "
         "CAST(t.ExitUserID AS nvarchar(50)) AS ExitUser, " + _VIEW_TAIL, "win2"),
    ],
    "inside": [(_inside_live(True), "lim"), (_inside_live(False), "lim")],
    "inside_audit": [(_inside_audit(True), "lim"), (_inside_audit(False), "lim")],
    "financial": [
        ("SELECT TOP (?) Date_Farsi, TaradodCount, TotalFinalPrice, TotalWebIncome FROM dbo.View_FinancialReports ORDER BY Date_Farsi DESC", "lim"),
        ("SELECT TOP (?) CONVERT(varchar(10), CAST(ExitDateTime AS date), 23) AS Date_Farsi, COUNT(*) AS TaradodCount, SUM(CAST(ISNULL(FinalPrice, 0) AS bigint)) AS TotalFinalPrice, "
         "CAST(NULL AS bigint) AS TotalWebIncome FROM dbo.Taradods WHERE ExitDateTime >= ? AND ExitDateTime < ? GROUP BY CONVERT(varchar(10), CAST(ExitDateTime AS date), 23) "
         "ORDER BY Date_Farsi DESC", "win"),
    ],
    "anpr": [("SELECT TOP (?) Id, CreateDate, Confidence, GateId, P1, P2, P3, P4 FROM dbo.ANPRPlates WHERE CreateDate >= ? AND CreateDate < ? ORDER BY CreateDate DESC", "win")],
    "barrier": [("SELECT TOP (?) Id, GateId, LogDateTime, Message FROM dbo.BarrierLogs WHERE LogDateTime >= ? AND LogDateTime < ? ORDER BY LogDateTime DESC", "win")],
    "discount_logs": [
        ("SELECT TOP (?) ID, DiscountCode, TaradodID, InsertDate, SendToServer FROM dbo.DiscountLogs WHERE InsertDate >= ? AND InsertDate < ? ORDER BY InsertDate DESC", "win"),
        ("SELECT TOP (?) DiscountLogsTaradodID AS ID, CAST(NULL AS nvarchar(50)) AS DiscountCode, DiscountLogsTaradodID AS TaradodID, ISNULL(ExitDateTime, EnrtyDateTime) AS InsertDate, "
         f"CAST(NULL AS bit) AS SendToServer FROM dbo.View_Taradods_PBI WHERE DiscountLogsTaradodID IS NOT NULL AND {_RANGE2} ORDER BY InsertDate DESC", "win2"),
    ],
    "discount_codes": [
        ("SELECT TOP (?) ID, DiscountTypeID, DiscountCategoryID, DiscountType, CategoryType, Amount, Code, Active, StartDate, ExpireDate, UsedCount, DailyCount, MaxCount "
         "FROM dbo.ViewDiscountCodes ORDER BY ID DESC", "lim"),
    ],
    "guests": [
        ("SELECT TOP (?) ID, UserID, TaradodID, CreateDate, SendToServer FROM dbo.Guests WHERE CreateDate >= ? AND CreateDate < ? ORDER BY CreateDate DESC", "win"),
        ("SELECT TOP (?) GuestsTaradodID AS ID, ExitUser AS UserID, GuestsTaradodID AS TaradodID, ISNULL(EnrtyDateTime, ExitDateTime) AS CreateDate, CAST(NULL AS bit) AS SendToServer "
         f"FROM dbo.View_Taradods_PBI WHERE GuestsTaradodID IS NOT NULL AND {_RANGE2} ORDER BY CreateDate DESC", "win2"),
    ],
    "pos_tx": [
        ("SELECT TOP (?) Id, UserId, MerchantName, PosTerminalNo, Amount, TransactionDateTime, Status FROM dbo.PosTransactions "
         "WHERE TransactionDateTime >= ? AND TransactionDateTime < ? ORDER BY TransactionDateTime DESC", "win"),
        ("SELECT TOP (?) t.ID AS Id, t.ExitUserID AS UserId, CAST(NULL AS nvarchar(100)) AS MerchantName, t.PosTerminalNo, CAST(t.Pos AS bigint) AS Amount, "
         "t.ExitDateTime AS TransactionDateTime, CASE WHEN ISNULL(t.Refund, 0) > 0 THEN N'بازپرداخت‌شده' WHEN t.PosDetailID IS NULL THEN N'بدون ثبت پوز' ELSE N'ثبت‌شده' END AS Status "
         "FROM dbo.Taradods AS t WHERE ISNULL(t.Pos, 0) > 0 AND t.ExitDateTime >= ? AND t.ExitDateTime < ? ORDER BY t.ExitDateTime DESC", "win"),
    ],
    "pay_requests": [
        ("SELECT TOP (?) ID, RequsetDateTime, PaymentMethodID, UserID, Price, Status FROM dbo.PaymentRequests "
         "WHERE RequsetDateTime >= ? AND RequsetDateTime < ? ORDER BY RequsetDateTime DESC", "win"),
        ("SELECT TOP (?) t.ID, t.ExitDateTime AS RequsetDateTime, t.PaymentID AS PaymentMethodID, t.ExitUserID AS UserID, CAST(t.FinalPrice AS bigint) AS Price, "
         f"{_PAY_STATUS} AS Status FROM dbo.Taradods AS t WHERE ISNULL(t.FinalPrice, 0) > 0 AND t.ExitDateTime >= ? AND t.ExitDateTime < ? ORDER BY t.ExitDateTime DESC", "win"),
    ],
    "real_income": [
        ("SELECT TOP (?) ID, CreateDateTime, [DateTime], Pecuniary, Pos, CreatorUserID, UpdateUserID, IsEdited, SupervisorID, SendToServer, SetPosTransaction, Closed "
         "FROM dbo.RealIncomes WHERE [DateTime] >= ? AND [DateTime] < ? ORDER BY [DateTime] DESC", "win"),
    ],
    "activity": [
        ("SELECT TOP (?) l.Id, l.UserId, l.CreateDateTime, l.ActivityId, a.ActivityTitle, l.EventId FROM dbo.ActivityLogs AS l "
         "INNER JOIN dbo.Activities AS a ON l.ActivityId = a.Id WHERE l.CreateDateTime >= ? AND l.CreateDateTime < ? ORDER BY l.CreateDateTime DESC", "win"),
    ],
    "events": [
        ("SELECT TOP (?) e.ID, e.UserSessionID, e.ActionFullname, e.SPName, e.ServerTime, s.UserName, s.ClientIP, s.AppType FROM dbo.EventLogs AS e "
         "INNER JOIN dbo.UserSessions AS s ON e.UserSessionID = s.ID WHERE e.ServerTime >= ? AND e.ServerTime < ? ORDER BY e.ServerTime DESC", "win"),
    ],
    "sessions": [
        ("SELECT TOP (?) s.ID, s.UserName, s.ClientIP, s.ServerLoginTime, s.ServerLogoffTime, s.LastActivity, s.AppType, r.Title AS LogoffReason "
         "FROM dbo.UserSessions AS s LEFT JOIN dbo.UserLogoffReasons AS r ON s.UserLogoffReasonID = r.ID "
         "WHERE s.ServerLoginTime >= ? AND s.ServerLoginTime < ? ORDER BY s.ServerLoginTime DESC", "win"),
    ],
    "blocked": [
        ("SELECT TOP (?) b.Id, b.CreateDate, b.PlateId, p.P1, p.P2, p.P3, p.P4, b.IsBlock, b.UpdateDate FROM dbo.BlockPlates AS b "
         "INNER JOIN dbo.Plates AS p ON b.PlateId = p.ID WHERE b.IsBlock = ? ORDER BY b.UpdateDate DESC", "blk"),
        ("SELECT TOP (?) b.Id, b.CreateDate, b.PlateId, p.P1, p.P2, p.P3, p.P4, b.IsBlock, b.UpdateDate FROM dbo.BlockPlates AS b "
         "LEFT JOIN dbo.Plates AS p ON b.PlateId = p.ID WHERE b.IsBlock = ? ORDER BY b.UpdateDate DESC", "blk"),
        ("SELECT TOP (?) " + _BLOCK_T.format(plate="p.P1, p.P2, p.P3, p.P4, ", frm="dbo.Taradods AS t LEFT JOIN dbo.Plates AS p ON p.ID = t.PlateID"), "lim"),
        ("SELECT TOP (?) " + _BLOCK_T.format(plate=_NULL_PLATE, frm="dbo.Taradods AS t"), "lim"),
    ],
    "cards": [
        ("SELECT TOP (?) FullName, Active, TagID, StartDate, EndDate, Title, P1, P2, P3, P4 FROM dbo.[View_Customer_Cards_Detials ] ORDER BY EndDate DESC", "lim"),
        ("SELECT TOP (?) FullName, Active, TagID, StartDate, EndDate, Title, P1, P2, P3, P4 FROM dbo.[View_Customer_Cards_Detials] ORDER BY EndDate DESC", "lim"),
    ],
    "fin_tx": [(FIN_TX_SQL, "fin_tx")],
    "fin_view": [(FIN_VIEW_SQL, "fin_view")],
    # مرجع گیت‌ها (فقط ستون‌های غیرمحرمانه؛ CameraUser/CameraPass/ControllerIP عمداً انتخاب نمی‌شوند)
    "gates": [("SELECT TOP (?) ID, Title, GateTypeID, ANPREnable, ReaderEnable, Kiosk, PCPOSEnable, LoopEnable, RoadblockEnable FROM dbo.Gates ORDER BY ID ASC", "lim")],
    "gate_types": [("SELECT TOP (?) ID, Title FROM dbo.GateTypes ORDER BY ID ASC", "lim")],
    "server_now": [("SELECT GETDATE() AS ServerNow", "none")],
}
QUERIES: dict[str, str] = {k: v[0][0] for k, v in SPEC.items()}
FALLBACK_ON_EMPTY = {"view", "financial", "discount_logs", "guests", "pos_tx", "pay_requests", "blocked", "cards"}
IMAGES_SQL = "SELECT EntryImage, ExitImage FROM dbo.Taradods WHERE ID = ?"

FIN_TX_COLS = ["ExitDate", "ShiftCode", "OutputGateID", "ExitUserID", "PaymentID", "Exits", "SumTotal", "SumFinal", "SumCash", "SumPos", "SumRefund", "SumVAT", "SumRound",
               "RefundN", "PosNoDetailN", "PosNoDetailAmt", "UnderN", "UnderAmt", "OverN", "OverAmt", "LostN", "FreeN", "FreeAmt"]
FIN_VIEW_COLS = ["ExitDate", "ShiftCode", "OutputGateID", "OutputGate", "ExitUser", "Reason", "Exits", "SumTotal", "SumFinal", "WaiverN", "WaiverAmt", "UpliftAmt"]
_INS_COLS = "ID CarTypeID CardID EnrtyDateTime EntryUserID InputGateID PlateID IsLost ANPRPlateId P1 P2 P3 P4 StayDays".split()
COLUMNS = {
    "inside": _INS_COLS + ["StayClass"], "inside_audit": _INS_COLS + ["AuditCode", "AuditReason"], "fin_tx": FIN_TX_COLS, "fin_view": FIN_VIEW_COLS,
    "financial": "Date_Farsi TaradodCount TotalFinalPrice TotalWebIncome".split(), "anpr": "Id CreateDate Confidence GateId P1 P2 P3 P4".split(),
    "barrier": "Id GateId LogDateTime Message".split(), "discount_logs": "ID DiscountCode TaradodID InsertDate SendToServer".split(),
    "discount_codes": "ID DiscountTypeID DiscountCategoryID DiscountType CategoryType Amount Code Active StartDate ExpireDate UsedCount DailyCount MaxCount".split(),
    "guests": "ID UserID TaradodID CreateDate SendToServer".split(), "pos_tx": "Id UserId MerchantName PosTerminalNo Amount TransactionDateTime Status".split(),
    "pay_requests": "ID RequsetDateTime PaymentMethodID UserID Price Status".split(),
    "real_income": ("ID CreateDateTime DateTime Pecuniary Pos CreatorUserID UpdateUserID IsEdited SupervisorID SendToServer SetPosTransaction Closed").split(),
    "activity": "Id UserId CreateDateTime ActivityId ActivityTitle EventId".split(), "events": "ID UserSessionID ActionFullname SPName ServerTime UserName ClientIP AppType".split(),
    "sessions": "ID UserName ClientIP ServerLoginTime ServerLogoffTime LastActivity AppType LogoffReason".split(),
    "blocked": "Id CreateDate PlateId P1 P2 P3 P4 IsBlock UpdateDate".split(), "cards": "FullName Active TagID StartDate EndDate Title P1 P2 P3 P4".split(),
    "gates": "ID Title GateTypeID ANPREnable ReaderEnable Kiosk PCPOSEnable LoopEnable RoadblockEnable".split(), "gate_types": "ID Title".split(),
}


def _params(kind: str, limit: int, d0, d1, tol: float) -> tuple:
    if kind == "win":
        return (limit, d0, d1)
    if kind == "win2":
        return (limit, d0, d1, d0, d1)
    if kind == "lim":
        return (limit,)
    if kind == "blk":
        return (limit, 1)
    if kind == "fin_tx":
        return (tol, tol, tol, tol, d0, d1)
    if kind == "fin_view":
        return (d0, d1)
    return ()


def _jalali_text(v) -> str | None:
    try:
        gy, gm, gd = (int(x) for x in str(v)[:10].split("-"))
    except (TypeError, ValueError):
        return None if v is None else str(v)
    gdm = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + gdm[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    jm, jd = (1 + days // 31, 1 + days % 31) if days < 186 else (7 + (days - 186) // 30, 1 + (days - 186) % 30)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def _post(name: str, df: pd.DataFrame, tier: int) -> pd.DataFrame:
    if name == "financial" and tier > 0 and len(df):
        df["Date_Farsi"] = df["Date_Farsi"].map(_jalali_text)
    elif name in ("inside", "inside_audit"):
        sd = pd.to_numeric(df["StayDays"], errors="coerce").clip(lower=0)
        df["StayDays"] = sd
        if name == "inside":
            df["StayClass"] = np.select([sd.isna(), sd < SEDIMENT_HOURS / 24, sd <= SEDIMENT_MAX_DAYS],
                                        ["نامشخص", "حاضر قطعی", "رسوبی واقعی / نیازمند بازرسی حراست"], "قدیمی‌تر از ۷ روز / مشکوک به داده‌ی مرده")
        else:
            df["AuditCode"] = pd.to_numeric(df["AuditCode"], errors="coerce")
            df["AuditReason"] = df["AuditCode"].map(AUDIT_REASON)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def fetch(name: str, d0: datetime | None = None, d1: datetime | None = None, limit: int = TOP_DEFAULT, tol: float = 0) -> pd.DataFrame:
    if name not in SPEC:
        raise DBError(f"کوئری «{name}» تعریف نشده است.")
    if offline_db.is_demo():
        return _demo(name, d0, d1, limit)
    d0, d1 = _py(d0), _py(d1)

    first_empty, last_err, primary_err = None, None, None
    for i, (sql, kind) in enumerate(SPEC[name]):
        try:
            df = run(sql, _params(kind, limit, d0, d1, tol))
        except DBError:
            raise
        except pyodbc.Error as e:
            if _state(e) in _CONN_FATAL_STATES:
                raise
            last_err = e
            primary_err = primary_err or _msg(e)
            continue
        df = _post(name, df, i)
        df.attrs["source"] = f"{name}#{i}"
        df.attrs["primary_error"] = primary_err
        if len(df) or name not in FALLBACK_ON_EMPTY:
            return df
        first_empty = first_empty if first_empty is not None else df
    if first_empty is not None:
        return first_empty
    raise last_err if last_err is not None else DBError(f"داده‌ای برای «{name}» برنگشت.")


@st.cache_data(ttl=300, show_spinner=False)
def taradod_images(tid: int) -> tuple[bytes | None, bytes | None]:
    if offline_db.is_demo():
        return None, None
    df = run(IMAGES_SQL, (int(tid),))
    if df.empty:
        return None, None
    en, ex = df.iloc[0]["EntryImage"], df.iloc[0]["ExitImage"]
    return (bytes(en) if en is not None else None), (bytes(ex) if ex is not None else None)


def diagnose() -> tuple[str, str]:
    """(سطح، توضیح فارسی). ..."""
    if offline_db.is_demo():
        return "ok", "حالت آفلاین (Snapshot)؛ بدون اتصال به شبکه."
   
    try:
        c = _cfg()
    except DBError as e:
        return "config", str(e)
    driver = str(c.get("driver") or DRIVER_DEFAULT)
    if driver not in pyodbc.drivers():
        return "driver", f"درایور «{driver}» پیدا نشد.\n" + DRIVER_HELP
    host, port = str(c["server"]), int(c["port"])
    try:
        ip = ipaddress.ip_address(host)
        private = ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        private = False
    try:
        socket.create_connection((host, port), timeout=5).close()
    except TimeoutError:
        if private:
            return "network", (f"آدرس {host} یک آدرس خصوصی شبکه‌ی داخلی (LAN) است و از اینترنت در دسترس نیست؛ پاسخی از {host}:{port} نمی‌آید. "
                               "اگر برنامه روی Streamlit Cloud اجرا می‌شود هرگز به این آدرس نمی‌رسد. برنامه را روی سرور یا کامپیوتری داخل همان شبکه اجرا کنید.")
        return "network", f"از {host}:{port} پاسخی نیامد. فایروال/NAT یا باز بودن پورت {port} روی مسیر شبکه را بررسی کنید."
    except ConnectionRefusedError:
        return "network", f"{host} در دسترس است ولی روی پورت {port} سرویسی گوش نمی‌دهد: TCP/IP در SQL Server Configuration Manager یا شماره‌ی پورت را بررسی کنید."
    except socket.gaierror:
        return "network", f"نام سرور «{host}» با DNS پیدا نشد؛ به‌جای نام از آی‌پی استفاده کنید یا DNS را بررسی کنید."
    except OSError as e:
        return "network", f"اتصال TCP به {host}:{port} ممکن نیست: {e}"
    try:
        with _connect():
            return "ok", "اتصال و ورود موفق است."
    except DBError as e:
        return "driver", str(e)
    except pyodbc.Error as e:
        m = _msg(e)
        if "18456" in m or _state(e) == "28000":
            return "auth", "پورت باز است ولی ورود رد شد: نام کاربری یا رمز عبور اشتباه است (یا احراز هویت SQL Server در حالت Mixed Mode فعال نیست)."
        if "4060" in m:
            return "database", f"ورود انجام شد ولی کاربر به پایگاه «{c['database']}» دسترسی ندارد؛ نقش db_datareader بدهید."
        return "network", m


@st.cache_data(ttl=300, show_spinner=False)
def _fin_chunk(name: str, d0: datetime, d1: datetime, tol: float) -> pd.DataFrame:
    return run(SPEC[name][0][0], _params(SPEC[name][0][1], 0, d0, d1, tol))


def _fin_adaptive(name: str, c0: datetime, c1: datetime, tol: float) -> pd.DataFrame:
    try:
        return _fin_chunk(name, c0, c1, tol)
    except pyodbc.Error as e:
        days = (c1 - c0).days
        if days <= 1 or _state(e) in _CONN_FATAL_STATES:
            raise
        mid = c0 + timedelta(days=days // 2)
        return pd.concat([_fin_adaptive(name, c0, mid, tol), _fin_adaptive(name, mid, c1, tol)], ignore_index=True)


def fetch_fin(name: str, d0: datetime, d1: datetime, tol: float = 0, step_days: int = 7) -> pd.DataFrame:
    """کوئری‌های تجمیعی مالی را تکه‌تکه اجرا می‌کند; ..."""
    if offline_db.is_demo():
        return _demo(name, d0, d1)
    d0, d1 = _py(d0), _py(d1)
    parts, c0 = [], d0
    while c0 < d1:
        c1 = min(c0 + timedelta(days=step_days), d1)
        parts.append(_fin_adaptive(name, c0, c1, float(tol)))
        c0 = c1
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=FIN_TX_COLS if name == "fin_tx" else FIN_VIEW_COLS)


def _fatal(name: str, msg: str):
    level, why = diagnose()
    slow = any(k in msg for k in ("HYT00", "HYT01", "08S01", "imeout", "timed out", "Communication link"))
    hint = ("\n\nاتصال حین اجرای کوئری قطع شد (محدودیت زمانی برنامه غیرفعال است؛ احتمالاً timeout سمت سرور/شبکه/فایروال). بازه‌ی زمانی را کوتاه‌تر انتخاب کنید."
            if slow and level == "ok" else "")
    st.error(f"اتصال به دیتابیس ParkingDB برقرار نشد.\n\n{why if level != 'ok' else 'اتصال برقرار است ولی کوئری «' + name + '» خطا داد.'}{hint}", icon="🛑")
    with st.expander("جزئیات فنی خطا"):
        st.code(f"query: {name}\n{msg}")
    st.stop()


def get_fin(name: str, d0, d1, tol: float = 0, required: bool = False) -> tuple[pd.DataFrame, str | None]:
    """(DataFrame، خطا) برای کوئری‌های تجمیعی مالی؛ در خطای اختیاری، جدول خالیِ دارای ستون برمی‌گردد"""
    try:
        with st.spinner("در حال محاسبه‌ی جمع‌های مالی در دیتابیس (بار اول چند ثانیه طول می‌کشد)…"):
            return fetch_fin(name, d0, d1, tol), None
    except (pyodbc.Error, DBError) as e:
        msg = _msg(e)
        if required:
            _fatal(name, msg)
        return pd.DataFrame(columns=FIN_TX_COLS if name == "fin_tx" else FIN_VIEW_COLS), msg


def get(name: str, d0=None, d1=None, limit: int = TOP_DEFAULT, required: bool = False, tol: float = 0) -> tuple[pd.DataFrame, str | None]:
    """(DataFrame، خطا): خطای اختیاری فقط هشدار می‌دهد؛ خطای ضروری با st.error علت را نشان می‌دهد و برنامه را متوقف می‌کند"""
    try:
        return fetch(name, d0, d1, limit, tol), None
    except (pyodbc.Error, DBError) as e:
        msg = _msg(e)
        if required:
            _fatal(name, msg)
        return pd.DataFrame(columns=COLUMNS.get(name, [])), msg


def load_inside(required: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    """(خودروهای زنده‌ی داخل پارکینگ، رکوردهای ممیزی، خطای ممیزی)"""
    live, _ = get("inside", required=required)
    audit, err = get("inside_audit")
    return live, audit, err


def inside_summary(live: pd.DataFrame | None, audit: pd.DataFrame | None = None) -> dict:
    sd = pd.to_numeric(live["StayDays"], errors="coerce") if live is not None and "StayDays" in live else pd.Series(dtype=float)
    ac = pd.to_numeric(audit["AuditCode"], errors="coerce") if audit is not None and "AuditCode" in audit else pd.Series(dtype=float)
    sed_d = SEDIMENT_HOURS / 24
    return dict(live=int(len(sd)), present=int((sd < sed_d).sum()), long_stay=int(((sd >= sed_d) & (sd <= SEDIMENT_MAX_DAYS)).sum()),
                old_open=int(((sd > SEDIMENT_MAX_DAYS) & (sd <= STALE_MAX_DAYS)).sum()), error_stale=int((ac == 1).sum()), superseded=int((ac == 2).sum()), invalid=int((ac == 3).sum()))


def inside_header_text(live: pd.DataFrame | None, audit: pd.DataFrame | None = None) -> str:
    s = inside_summary(live, audit)
    return (f"هم‌اکنون <b>{s['present']:,}</b> خودروی حاضر جاری (توقف کمتر از {SEDIMENT_HOURS} ساعت) و "
            f"<b>{s['long_stay']:,}</b> خودروی رسوبی واقعی ({SEDIMENT_HOURS} ساعت تا {SEDIMENT_MAX_DAYS} روز، نیازمند بازرسی حراست) و "
            f"<b>{s['old_open']:,}</b> رکورد قدیمی‌تر از {SEDIMENT_MAX_DAYS} روز (مشکوک به داده‌ی مرده؛ خارج از آمار رسوبی) داخل پارکینگ است. "
            f"<b>{s['error_stale']:,}</b> تردد بدون خروج با عمر بیش از {STALE_MAX_DAYS} روز به‌عنوان «ترددهای خطای ثبت / بدون خروج قطعی» از موجودی زنده جدا شد "
            f"(همراه با {s['superseded']:,} رکورد باطل‌شده‌ی پلاک تکراری و {s['invalid']:,} رکورد با پلاک نامعتبر در بخش ممیزی).")