"""هشدار لحظه‌ای شیفت (Shift Alerts) با پیامک — بر پایه‌ی داده‌ی واقعی ParkingDB
قوانین فقط از ستون‌های موجود در دیتابیس ساخته شده‌اند (ابطال فیش، لوپ و راهبند خارج از شیفت در اسکیما نیست و پیاده نشده است).

مبنای زمانی: داده‌ی «روز جاری» کامل و زنده ارزیابی می‌شود (بدون حذف امروز). بازه‌ها: صبح ۰۶–۱۴ ، عصر ۱۴–۲۲ ، شب ۲۲–۰۶ روز بعد؛
انتهای بازه انحصاری است (t0 <= زمان < t1). شیفتِ در جریان با برچسب «موقت تا این لحظه» علامت می‌خورد.

اجرا (خارج از Streamlit؛ اطلاعات اتصال از .streamlit/secrets.toml خوانده می‌شود):
  python shift_alerts.py --day 2026-09-25 --shift عصر            # فقط نمایش
  python shift_alerts.py --day 1405/07/03 --all-shifts           # تاریخ شمسی + هر سه شیفت
  python shift_alerts.py --day 2026-09-25 --shift عصر --send     # ارسال واقعی
  python shift_alerts.py --watch --interval 300 --send            # شیفت جاری، هر ۵ دقیقه
  DEMO_MODE=1 python shift_alerts.py --watch --send               # حالت دمو: هیچ پیامکی ارسال نمی‌شود

secrets.toml (بخش‌های اختیاری):
  [sms]            provider = "kavenegar" | "smsir" | "console",  api_key = "...",  sender = "..."
  [sms.recipients] shift_manager = ["0912..."],  security = ["0935..."],  site_manager = ["0912..."]
  [sms.operators]  "12" = "0912..."      # کلید = rbac.Users.ID یا UserName ؛ (rbac.Users ستون موبایل ندارد)
  [alerts]         tol = 0,  nocash_payment_ids = []
"""
import argparse
import json
import os
import re
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

import analytics as an

try:
    from offline_db import is_demo as _offline_is_demo
except Exception:  # noqa: BLE001
    _offline_is_demo = None


def is_demo() -> bool:
    """حالت دمو/آفلاین: هیچ اتصال شبکه‌ای و پیامکی انجام نمی‌شود"""
    if os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        return bool(_offline_is_demo()) if _offline_is_demo else False
    except Exception:  # noqa: BLE001
        return False


SEV_ORDER = {"فوری": 0, "مهم": 1, "پایش": 2}
ROLE_FA = {"shift_manager": "مدیر شیفت", "security": "حراست", "site_manager": "مدیر پارکینگ", "ceo": "مدیرعامل", "operator": "اپراتور متصدی"}
ROUTING = {"فوری": ["shift_manager", "security", "site_manager"], "مهم": ["shift_manager"], "پایش": []}
A8_SEVERITY = "فوری"  # خودروی رسوبی (۴۸ ساعت تا ۷ روز) = وضعیت بحرانی؛ مسیر ارسال: مدیر شیفت + حراست + مدیر پارکینگ (همه‌ی بخش‌های برنامه از همین ثابت می‌خوانند)
TH = dict(anpr_noise_burst=10)  # حداقل تعداد خوانش نویزی یک گیت در شیفت برای هشدار (قابل تنظیم)
PROVISIONAL = "موقت تا این لحظه"
ALL_SHIFTS = "همه شیفت‌ها"
SHIFT_NAMES = list(an.SHIFTS)
ALERT_COLS = ["rule", "severity", "title", "entity", "count", "time", "shift", "day", "message", "key", "roles", "event_ids", "phones",
              "provisional", "operator", "gate", "amount", "phone_missing"]

# (کد، پرچم، شدت، عنوان، قالب پیام)
FLAG_RULES = [
    ("A1", "F_PosNoDetail", "فوری", "پرداخت کارتخوان بدون ثبت پوز", "کارتخوان بدون ثبت پوز: {n} مورد · کاربر خروج {u} · مبلغ {amt} تومان · {t}"),
    ("A2", "F_Refund", "فوری", "بازپرداخت ثبت‌شده", "بازپرداخت: {n} مورد · کاربر خروج {u} · مجموع {amt} تومان · {t}"),
    ("A3", "F_PayMismatch", "مهم", "ناهمخوانی پرداخت با مبلغ نهایی", "ناهمخوانی نقد+کارتخوان با مبلغ نهایی: {n} مورد · کاربر خروج {u} · {t}"),
    ("A4", "F_FreeExit", "مهم", "خروج رایگان بدون سند (معافیت غیرمجاز)", "خروج رایگان بدون سند: {n} مورد · کاربر خروج {u} · تعرفه {amt} تومان · {t}"),
    ("A5", "F_Lost", "مهم", "فیش مفقودی", "فیش مفقودی: {n} مورد · کاربر خروج {u} · {t}"),
]


# ----------------------------------------------------------------------------- زمان و شیفت
def shift_window(day, shift):
    """[شروع، پایان) شیفت؛ شیفت شب از ۲۲ همان روز تا ۰۶ روز بعد (عبور از نیمه‌شب)"""
    d0 = pd.Timestamp(day).normalize()
    a, b = an.SHIFTS[shift]
    return d0 + pd.Timedelta(hours=a), d0 + pd.Timedelta(hours=b)


def current_shift(now):
    now = pd.Timestamp(now)
    h = now.hour
    if 6 <= h < 14:
        return now.normalize(), "صبح"
    if 14 <= h < 22:
        return now.normalize(), "عصر"
    return (now.normalize() if h >= 22 else now.normalize() - pd.Timedelta(days=1)), "شب"


def shift_state(now, day, shift):
    """'جاری' | 'پایان‌یافته' | 'آینده' ؛ بدون now → 'پایان‌یافته'"""
    try:
        if now is None or pd.isna(now):
            return "پایان‌یافته"
        t0, t1 = shift_window(day, shift)
        n = pd.Timestamp(now)
        return "آینده" if n < t0 else ("جاری" if n < t1 else "پایان‌یافته")
    except Exception:  # noqa: BLE001
        return "پایان‌یافته"


def shifts_of(sel):
    """sel = نام شیفت یا ALL_SHIFTS/None → فهرست شیفت‌ها"""
    return SHIFT_NAMES if sel in (None, ALL_SHIFTS, "all") else [sel]


# ----------------------------------------------------------------------------- تاریخ شمسی (ورودی/خروجی ایمن)
JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def _j2g_raw(jy, jm, jd):
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd + ((jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186)
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    sal = [0, 31, 29 if (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 12 and gd > sal[gm]:
        gd -= sal[gm]
        gm += 1
    return gy, gm, gd


def gregorian_to_jalali(v):
    """تاریخ میلادی → (سال، ماه، روز) شمسی؛ None/NaT/نامعتبر → None"""
    try:
        if v is None or pd.isna(v):
            return None
        ts = pd.Timestamp(v)
        if pd.isna(ts):
            return None
        return an._g2j(ts.year, ts.month, ts.day)
    except Exception:  # noqa: BLE001
        return None


def jalali_to_gregorian(jy, jm, jd):
    """تاریخ شمسی → pd.Timestamp میلادی (ساعت ۰۰:۰۰)؛ تاریخ نامعتبر (مثلاً ۳۱ مهر) → None"""
    try:
        jy, jm, jd = int(jy), int(jm), int(jd)
        if not (1 <= jm <= 12 and 1 <= jd <= 31):
            return None
        g = _j2g_raw(jy, jm, jd)
        if gregorian_to_jalali(pd.Timestamp(*g)) != (jy, jm, jd):  # رفت‌وبرگشت؛ روزهای ناموجود ماه را رد می‌کند
            return None
        return pd.Timestamp(*g)
    except Exception:  # noqa: BLE001
        return None


def jalali_month_days(jy, jm):
    """تعداد روزهای ماه شمسی (با رفت‌وبرگشت)"""
    for d in (31, 30, 29):
        if jalali_to_gregorian(jy, jm, d) is not None:
            return d
    return 29


def jalali_text(v, with_time=False, fa_digits=True):
    """رشته‌ی شمسی؛ ورودی تهی/NaT → «-»"""
    try:
        s = an.jalali_str(v, with_time=with_time, fa_digits=fa_digits)
        return s or "-"
    except Exception:  # noqa: BLE001
        return "-"


def parse_day(text):
    """'2026-09-25' یا '1405/07/03' → Timestamp ۰۰:۰۰؛ نامعتبر → None"""
    try:
        s = str(text).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).replace("-", "/")
        p = [int(x) for x in s.split("/")[:3]]
        if p[0] < 1700:
            return jalali_to_gregorian(*p)
        return pd.Timestamp(year=p[0], month=p[1], day=p[2])
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------------- گیت‌ها و اپراتورها
def _uid(v):
    try:
        if v is None or pd.isna(v):
            return "-"
        f = float(v)
        return str(int(f)) if f == int(f) else str(v)
    except Exception:  # noqa: BLE001
        return str(v)


def gate_label(gid, gates):
    """نام گیت از dbo.Gates (+GateTypes)؛ هرگز شناسه‌ی خام نمایش داده نمی‌شود: بدون نگاشت ← «نامشخص (ID: n)»"""
    n = pd.to_numeric(pd.Series([gid]), errors="coerce").iloc[0]
    if pd.isna(n):
        return "نامشخص"
    n = int(n)
    try:
        if gates is not None and len(gates) and "Label" in gates:
            m = dict(zip(gates.ID.astype(int), gates.Label))
            if n in m:
                return str(m[n])
    except Exception:  # noqa: BLE001
        pass
    return an.UNKNOWN_GATE.format(n)


def mask_phone(p):
    p = str(p or "")
    return p[:4] + "***" + p[-3:] if len(p) >= 8 else "-"


def operator_directory(users, mapping=None):
    """جدول اپراتورها: ID, UserName, Name, PosTerminalNo, Phone
    منبع نام: rbac.Users (FirstName/LastName/PosTerminalNo) از طریق ExitUserID/EntryUserID در Taradods.
    rbac.Users ستون موبایل ندارد؛ شماره از mapping (بخش [sms.operators] در secrets؛ کلید = ID یا UserName) خوانده می‌شود."""
    cols = ["ID", "UserName", "Name", "PosTerminalNo", "Phone"]
    mp = {}
    for k, v in dict(mapping or {}).items():
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        ph = norm_phone(v)
        if ph:
            mp[str(k).strip().lower()] = ph
    rows = []
    known = set()
    if users is not None and len(users):
        u = users.copy()
        for c in ("ID", "UserName", "FirstName", "LastName", "PosTerminalNo"):
            if c not in u:
                u[c] = None
        for r in u.itertuples():
            i, un = _uid(r.ID), ("" if pd.isna(r.UserName) else str(r.UserName).strip())
            fn = " ".join(str(x).strip() for x in (r.FirstName, r.LastName) if x is not None and not pd.isna(x) and str(x).strip())
            known.add(i)
            rows.append(dict(ID=i, UserName=un, Name=fn, PosTerminalNo=None if pd.isna(r.PosTerminalNo) else r.PosTerminalNo,
                             Phone=mp.get(i.lower()) or mp.get(un.lower())))
    for k, ph in mp.items():  # شماره‌هایی که کاربرشان در جدول Users خوانده نشد (جدول اختیاری)
        if k.isdigit() and k not in known:
            rows.append(dict(ID=k, UserName="", Name="", PosTerminalNo=None, Phone=ph))
    return pd.DataFrame(rows, columns=cols)


def _op_lookup(ops):
    if ops is None or len(ops) == 0:
        return {}
    return {str(r.ID): r for r in ops.itertuples()}


# ----------------------------------------------------------------------------- ارزیابی
def _empty_alerts():
    return pd.DataFrame(columns=ALERT_COLS)


def evaluate(tx, view, inside, anpr, disc_logs, day, shift, *, gates=None, ops=None, now=None, tol=0.0, nocash=()):
    """ورودی‌ها: DataFrameهای آماده‌شده با analytics.prepare_*؛ خروجی: جدول هشدارها.
    now = ساعت سرور دیتابیس؛ اگر شیفت در جریان باشد هشدارها «موقت» علامت می‌خورند و قوانین لحظه‌ای (A8/A9) فعال‌اند."""
    day = pd.Timestamp(day).normalize()
    t0, t1 = shift_window(day, shift)
    now_ts = None if now is None or pd.isna(now) else pd.Timestamp(now)
    provisional = bool(now_ts is not None and t0 <= now_ts < t1)
    live = now_ts is None or provisional
    lk = _op_lookup(ops)
    out = []

    def opname(u):
        r = lk.get(_uid(u))
        nm = (getattr(r, "Name", "") or getattr(r, "UserName", "")) if r is not None else ""
        return nm or (f"کاربر {_uid(u)}" if _uid(u) != "-" else "نامشخص")

    def add(rule, sev, title, entity, n, t, text, roles=None, ids=(), phones=(), operator="", gate="", amount=0.0, phone_missing=False):
        msg = f"[{sev}] {text} | شیفت {shift}" + (" · موقت" if provisional else "")
        out.append(dict(rule=rule, severity=sev, title=title, entity=str(entity), count=int(n), time=t, shift=shift, day=f"{day:%Y%m%d}", message=msg,
                        key=f"{rule}|{entity}|{day:%Y%m%d}|{shift}", roles=list(ROUTING[sev] if roles is None else roles),
                        event_ids=[str(i) for i in ids], phones=list(phones), provisional=provisional, operator=operator, gate=gate,
                        amount=float(amount or 0), phone_missing=bool(phone_missing)))

    if tx is not None and len(tx) and "Event_Time" in tx:
        has_tx = True
        w = tx[(tx.Event_Time >= t0) & (tx.Event_Time < t1)]
    else:
        has_tx = False
        w = pd.DataFrame()
    if len(w):
        for rule, col, sev, title, tmpl in FLAG_RULES:
            if col not in w:
                continue
            for u, g in w[w[col] == 1].groupby("ExitUserID", dropna=False):
                amt = an.rule_amount(g, col) or g.Loss.sum()
                add(rule, sev, title, _uid(u), len(g), g.Event_Time.max(), tmpl.format(n=len(g), u=opname(u), amt=f"{amt:,.0f}", t=g.Event_Time.max().strftime("%H:%M")),
                    ids=g.ID, operator=opname(u), amount=amt)
        # A11: کسری وصول هدفمند در سطح «شیفت × گیت × اپراتور»: کسری = FinalPrice − (Cash + Pos − Refund) یا خطای پرداخت (کارتخوان بدون ثبت پوز)
        try:
            ex = w[w.Exited.astype(bool)].copy()
            ex["Deficit"] = ex.FinalPrice.fillna(0) - (ex.Cash.fillna(0) + ex.Pos.fillna(0) - ex.Refund.fillna(0))
            nc = ex.PaymentID.isin(list(nocash)) if len(nocash) else pd.Series(False, index=ex.index)
            ex["_def"] = (ex.Deficit > float(tol)) & ~nc
            ex["_err"] = ex.F_PosNoDetail == 1
            ev = ex[ex["_def"] | ex["_err"]]
            for (u, gid), g in ev.groupby(["ExitUserID", "OutputGateID"], dropna=False):
                gname, r = gate_label(gid, gates), lk.get(_uid(u))
                phone = getattr(r, "Phone", None) if r is not None else None
                phone = phone if isinstance(phone, str) and phone else None
                amt = float(np.where(g["_def"], g.Deficit.clip(lower=0), g.Pos.fillna(0)).sum())
                n_err = int((g["_err"]).sum())
                add("A11", "مهم", "کسری وصول (اپراتور)", f"{_uid(u)}|{_uid(gid)}", len(g), g.Event_Time.max(),
                    f"کسری وصول: گیت {gname} · {opname(u)} · {len(g)} تردد" + (f" ({n_err} خطای پرداخت)" if n_err else "") + f" · مجموع {amt:,.0f} تومان · {g.Event_Time.max():%H:%M}",
                    roles=["operator"], ids=g.ID, phones=[phone] if phone else [], operator=opname(u), gate=gname, amount=amt, phone_missing=phone is None)
        except Exception:  # noqa: BLE001  # نبود ستون‌های اختیاری نباید کل ارزیابی را بشکند
            pass
    # کدهای تخفیف تکراری در بازه‌ی شیفت (DiscountLogs)
    if disc_logs is not None and len(disc_logs) and "InsertDate" in disc_logs:
        di = pd.to_datetime(disc_logs.InsertDate, errors="coerce")
        dl = disc_logs[(di >= t0) & (di < t1)]
        for code, g in dl.groupby("DiscountCode"):
            if len(g) > 1:
                add("A6", "فوری", "کد تخفیف تکراری", code, len(g), pd.to_datetime(g.InsertDate).max(), f"کد تخفیف {code} در این شیفت {len(g)} بار استفاده شد", ids=g.ID)
    # نویز ANPR: انبوه خوانش با اطمینان کم در یک گیت (نشانه‌ی خرابی دوربین یا نور)
    if anpr is not None and len(anpr) and {"CreateDate", "Confidence", "GateId"} <= set(anpr.columns):
        a = anpr.assign(CreateDate=pd.to_datetime(anpr.CreateDate, errors="coerce"), Confidence=an._conf(anpr.Confidence))
        a = a[(a.CreateDate >= t0) & (a.CreateDate < t1) & (a.Confidence < an.LOW_CONF)]
        for gid, g in a.groupby("GateId"):
            if len(g) >= TH["anpr_noise_burst"]:
                add("A7", "مهم", "کیفیت پلاک‌خوان", gid, len(g), g.CreateDate.max(), f"گیت {gate_label(gid, gates)}: {len(g)} خوانش با اطمینان زیر {an.LOW_CONF} (بررسی دوربین/نور)",
                    gate=gate_label(gid, gates))
    # خودروهای داخل: وضعیت لحظه‌ای؛ فقط در شیفتِ در جریان معنا دارد
    if live and inside is not None and len(inside) and "LongStay" in inside:
        t_live = now_ts if now_ts is not None else t1
        cnt = an.inside_counts(inside)
        n_long, n_card, n_plate = cnt["sediment"], int(inside.DupCard.sum()), int(inside.DupPlate.sum())
        if n_long:  # فقط ExitDateTime IS NULL و ۴۸ ≤ Age_H ≤ ۱۶۸ ساعت؛ رکوردهای قدیمی‌تر از ۷ روز (داده‌ی مرده) در این قانون نمی‌آیند
            add("A8", A8_SEVERITY, "خودروی بیش از ۴۸ ساعت", "inside", n_long, t_live,
                f"{n_long} خودروی رسوبی ({an.LONG_STAY_H} ساعت تا {an.SEDIMENT_MAX_H // 24} روز) داخل پارکینگ است؛ بیشترین توقف {cnt['max_sediment_h']:.0f} ساعت · نیازمند بازرسی حراست")
        if n_card or n_plate:
            add("A9", "مهم", "ورود باز تکراری", "inside", n_card + n_plate, t_live, f"ورود باز تکراری: {n_card} ردیف با کارت مشترک و {n_plate} ردیف با پلاک مشترک")
    # پروتکل Z-Score (سند): نسبت نقد و ابطال/معافیت بدون سند همزمان بالای ۲.۵
    if view is not None and len(view) and "Event_Time" in view and tx is not None and has_tx:
        try:
            _, _, hit = an.operator_profile(view[(view.Event_Time < t1)], tx=tx[tx.Event_Time < t1])
            for u in hit:
                add("A10", "فوری", "بازرسی سرزده (Z-Score)", u, 1, t1 if not provisional else now_ts,
                    f"بازرسی سرزده: Z نسبت نقد و Z ابطال/معافیت بدون سند صندوقدار {u} از {an.Z_PROTOCOL} گذشت")
        except Exception:  # noqa: BLE001
            pass
    df = pd.DataFrame(out, columns=ALERT_COLS)
    if df.empty:
        return df
    df["_o"] = df.severity.map(SEV_ORDER)
    return df.sort_values(["_o", "time"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)


def evaluate_shifts(tx, view, inside, anpr, disc_logs, day, sel=ALL_SHIFTS, **kw):
    """یک شیفت یا هر سه شیفتِ روز (ستون shift در خروجی). برای بازه‌ی شیفت‌ها فیلتر جداگانه اعمال می‌شود."""
    frames = []
    for i, s in enumerate(shifts_of(sel)):
        ins = inside if (kw.get("now") is not None or i == 0) else pd.DataFrame()
        f = evaluate(tx, view, ins, anpr, disc_logs, day, s, **kw)
        if len(f):
            frames.append(f)
    if not frames:
        return _empty_alerts()
    d = pd.concat(frames, ignore_index=True)
    d["_o"] = d.severity.map(SEV_ORDER)
    return d.sort_values(["_o", "time"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)


# ----------------------------------------------------------------------------- داده‌ی نمودارهای تحلیلی
RULE_TITLES = {r[0]: r[3] for r in FLAG_RULES}
RULE_TITLES.update({"A6": "کد تخفیف تکراری", "A7": "کیفیت پلاک‌خوان", "A8": "خودروی بیش از ۴۸ ساعت", "A9": "ورود باز تکراری",
                    "A10": "بازرسی سرزده (Z-Score)", "A11": "کسری وصول (اپراتور)"})
ROUTING_SEV = {r[0]: r[2] for r in FLAG_RULES}
ROUTING_SEV.update({"A6": "فوری", "A7": "مهم", "A8": A8_SEVERITY, "A9": "مهم", "A10": "فوری", "A11": "مهم"})
LIVE_ONLY = {"A8", "A9"}  # وضعیت لحظه‌ای هستند و برای روزهای گذشته قابل بازسازی نیستند

RULES_INFO = [
    ("A1", "کارتخوان بدون ثبت پوز", "فوری", "Pos > 0 و PosDetailID خالی"),
    ("A2", "بازپرداخت ثبت‌شده", "فوری", "Refund > 0"),
    ("A3", "ناهمخوانی پرداخت با مبلغ نهایی", "مهم", "Cash + Pos ≠ FinalPrice (پس از حد تحمل)"),
    ("A4", "خروج رایگان بدون سند", "مهم", "FinalPrice = 0 و TotalPrice > 0 بدون سند معافیت"),
    ("A5", "فیش مفقودی", "مهم", "IsLost = 1"),
    ("A6", "کد تخفیف تکراری در شیفت", "فوری", "یک DiscountCode بیش از یک بار در DiscountLogs"),
    ("A7", "انبوه خوانش ANPR نویزی در یک گیت", "مهم", f"≥ {TH['anpr_noise_burst']} خوانش با اطمینان < {an.LOW_CONF}"),
    ("A8", "خودروی رسوبی (۴۸ ساعت تا ۷ روز)", A8_SEVERITY, f"ExitDateTime IS NULL و {an.LONG_STAY_H} ≤ Age_H ≤ {an.SEDIMENT_MAX_H} ساعت (فقط شیفت جاری)"),
    ("A9", "ورود باز تکراری (کارت/پلاک)", "مهم", "CardID/PlateID تکراری در ترددهای بدون خروج (فقط شیفت جاری)"),
    ("A10", "پروتکل Z-Score (نقد و خروج رایگان > ۲.۵)", "فوری", f"Z نسبت نقد و Z ابطال/معافیت بدون سند > {an.Z_PROTOCOL}"),
    ("A11", "کسری وصول هدفمند اپراتور/گیت/شیفت", "مهم", "FinalPrice − (Cash + Pos − Refund) > حد تحمل، یا کارتخوان بدون ثبت پوز"),
]


def rules_table():
    rows = []
    for code, title, sev, cond in RULES_INFO:
        who = "اپراتور متصدی همان گیت/شیفت (در صورت وجود شماره)" if code == "A11" else ("، ".join(ROLE_FA[x] for x in ROUTING[sev]) or "فقط داشبورد")
        rows.append({"کد": code, "قانون": title, "شدت": sev, "شرط": cond, "گیرنده": who})
    return pd.DataFrame(rows)


def _rule_num(code):
    return int(code[1:])


def hour_matrix(al, day, shift):
    """ماتریس «قانون × ساعتِ شیفت» (تعداد هشدار در هر ساعت) برای هیت‌مپ. shift=None/«همه شیفت‌ها» → ۲۴ ساعت از ۰۶:۰۰ تا ۰۶:۰۰ بعد."""
    if shift in (None, ALL_SHIFTS):
        t0 = pd.Timestamp(day).normalize() + pd.Timedelta(hours=6)
        n = 24
    else:
        t0, t1 = shift_window(day, shift)
        n = int((t1 - t0) / pd.Timedelta(hours=1))
    slots = [f"{(t0 + pd.Timedelta(hours=i)).hour:02d}:00" for i in range(n)]
    if al is None or al.empty:
        return pd.DataFrame(columns=slots, dtype=int)
    a = al.copy()
    a["slot"] = ((pd.to_datetime(a.time, errors="coerce") - t0) // pd.Timedelta(hours=1)).clip(0, n - 1).fillna(0).astype(int)
    m = a.pivot_table(index="rule", columns="slot", values="key", aggfunc="count", fill_value=0).reindex(columns=range(n), fill_value=0)
    m = m.loc[sorted(m.index, key=lambda c: (SEV_ORDER[ROUTING_SEV[c]], _rule_num(c)))]
    m.index = [f"{c} · {RULE_TITLES[c]}" for c in m.index]
    m.columns = slots
    return m.astype(int)


def _jshort(d):
    j = gregorian_to_jalali(d)
    return f"{j[1]:02d}/{j[2]:02d}" if j else "-"


def history_matrix(tx, view, anpr, disc_logs, day, shift, days=7, **kw):
    """ماتریس «قانون × روز» برای همین شیفت (یا همه‌ی شیفت‌ها) در days روز منتهی به روز انتخاب‌شده. قوانین لحظه‌ای (A8/A9) نمی‌آیند."""
    day = pd.Timestamp(day).normalize()
    kw = {k: v for k, v in kw.items() if k != "now"}  # تاریخچه همیشه برای روزهای کامل‌شده/ثابت است
    dates, rows = [], []
    for k in range(days - 1, -1, -1):
        d = day - pd.Timedelta(days=k)
        al = evaluate_shifts(tx, view, pd.DataFrame(), anpr, disc_logs, d, shift or ALL_SHIFTS, **kw)
        rows.append(al.groupby("rule").size() if not al.empty else pd.Series(dtype=int))
        dates.append(d)
    codes = [c for c in RULE_TITLES if c not in LIVE_ONLY]
    m = pd.DataFrame(rows, index=dates).T.reindex(codes).fillna(0).astype(int)
    m.index = [f"{c} · {RULE_TITLES[c]}" for c in m.index]
    m.columns = [_jshort(d) for d in dates]  # برچسب ستون‌ها شمسی (ماه/روز)
    return m


# ----------------------------------------------------------------------------- پیامک
def norm_phone(p):
    if p is None:
        return None
    try:
        if pd.isna(p):
            return None
    except (TypeError, ValueError):
        pass
    p = re.sub(r"\D", "", str(p).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    p = re.sub(r"^(98|0098)", "0", p)
    if re.fullmatch(r"9\d{9}", p):
        p = "0" + p
    return p if re.fullmatch(r"09\d{9}", p) else None


def sms_segments(text):
    """پیامک یونیکد: ۷۰ نویسه برای یک بخش، ۶۷ نویسه برای هر بخش در پیامک چندبخشی"""
    n = len(text)
    return 1 if n <= 70 else -(-n // 67)


class SMSGateway:
    """provider: kavenegar | smsir | console | mock. در DEMO_MODE/بدون کلید/dry_run هیچ درخواست شبکه‌ای ارسال نمی‌شود (Mock بدون خطا)."""

    def __init__(self, provider=None, api_key=None, sender=None, dry_run=True):
        self.provider = (provider or os.getenv("SMS_PROVIDER", "console")).lower()
        self.key, self.sender = api_key or os.getenv("SMS_API_KEY"), sender or os.getenv("SMS_SENDER")
        self.dry = bool(dry_run) or self.provider in ("console", "mock") or not self.key or is_demo()

    def send(self, phone, text):
        ph = norm_phone(phone)
        if not ph:
            return dict(phone=str(phone), ok=False, info="شماره نامعتبر")
        if self.dry:
            return dict(phone=ph, ok=True, info="DEMO (آفلاین؛ ارسال نشد)" if is_demo() else "DRY-RUN (ارسال نشد)", segments=sms_segments(text))
        try:
            if self.provider == "kavenegar":
                req = urllib.request.Request(f"https://api.kavenegar.com/v1/{self.key}/sms/send.json",
                                             urllib.parse.urlencode({"receptor": ph, "sender": self.sender, "message": text}).encode())
            elif self.provider == "smsir":
                payload = json.dumps({"lineNumber": int(self.sender or 0), "messageText": text, "mobiles": [ph]}).encode("utf-8")
                headers = {"X-API-KEY": str(self.key), "Content-Type": "application/json"}
                req = urllib.request.Request("https://api.sms.ir/v1/send/bulk", data=payload, headers=headers)
            else:
                return dict(phone=ph, ok=False, info=f"سرویس‌دهنده ناشناخته: {self.provider}")
            with urllib.request.urlopen(req, timeout=15) as r:
                return dict(phone=ph, ok=200 <= r.status < 300, info=f"HTTP {r.status}", segments=sms_segments(text))
        except Exception as e:  # noqa: BLE001
            return dict(phone=ph, ok=False, info=str(e)[:120])


class AlertStore:
    """جلوگیری از ارسال تکراری (Dedupe) در سطح «قانون + شناسه رویداد + شیفت + گیرنده»"""

    def __init__(self, path=".alert_state.json"):
        self.p = Path(path)
        try:
            self.d = json.loads(self.p.read_text(encoding="utf-8")) if self.p.exists() else {}
        except Exception:  # noqa: BLE001
            self.d = {}

    def seen(self, key):
        return key in self.d

    def mark(self, key):
        self.mark_many([key])

    def mark_many(self, keys):
        now = pd.Timestamp.now().isoformat()
        for k in keys:
            self.d[k] = now
        try:
            self.p.write_text(json.dumps(self.d, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def event_keys(a, phone):
    """کلیدهای Dedupe یک هشدار برای یک گیرنده: برای هر شناسه‌ی رویداد (ID تردد/کد/گیت) یک کلید"""
    ids = list(a["event_ids"]) if isinstance(a["event_ids"], (list, tuple, np.ndarray)) and len(a["event_ids"]) else [a["entity"]]
    return [f"{a['rule']}|{i}|{a['day']}|{a['shift']}|{phone}" for i in ids]


def dispatch(alerts, gateway, recipients, store=None, min_severity="مهم"):
    """ارسال پیامک: نقش‌های مدیریتی از recipients و اپراتور از ستون phones هر هشدار.
    اپراتور بدون شماره → ارسال رد می‌شود (info = «شماره اپراتور نامشخص»)."""
    res = []
    if alerts is None or len(alerts) == 0:
        return res
    for _, a in alerts.iterrows():
        if SEV_ORDER[a.severity] > SEV_ORDER[min_severity]:
            continue
        phones = set()
        for r_ in a.roles:
            if r_ == "operator":
                continue
            for p in recipients.get(r_, []) or []:
                np_ = norm_phone(p)
                if np_:
                    phones.add(np_)
        if "operator" in a.roles:
            direct = {norm_phone(p) for p in (a.phones or [])} - {None}
            if not direct:
                res.append(dict(key=a.key, rule=a.rule, phone="-", ok=False, info="شماره اپراتور نامشخص (ارسال رد شد)"))
            phones |= direct
        if not phones:
            if "operator" not in a.roles:
                res.append(dict(key=a.key, rule=a.rule, phone="-", ok=False, info="گیرنده‌ای تعریف نشده"))
            continue
        for p in sorted(phones):
            keys = event_keys(a, p)
            if store and all(store.seen(k) for k in keys):
                res.append(dict(key=a.key, rule=a.rule, phone=p, ok=True, info="قبلاً ارسال شده"))
                continue
            r = gateway.send(p, a.message)
            r["key"], r["rule"] = a.key, a.rule
            res.append(r)
            if store and r["ok"] and not gateway.dry:
                store.mark_many(keys)
    return res


# ----------------------------------------------------------------------------- بارگذاری داده‌ی زنده (مشترک بین CLI و داشبورد)
def load_live(fetch, day, now, lookback_days=8, tol=0.0, nocash=(), op_mapping=None) -> dict[str, Any]:
    """داده‌ی روز جاری را بدون حذف امروز واکشی و آماده می‌کند. fetch(name, *args) → DataFrame (خالی در خطا).
    خطای هر جدول اختیاری فقط در errors می‌آید و کرش نمی‌کند."""
    day = pd.Timestamp(day).normalize()
    now = pd.Timestamp(now)
    d0 = (day - pd.Timedelta(days=lookback_days)).to_pydatetime()
    d1 = (max(day, now.normalize()) + pd.Timedelta(days=2)).to_pydatetime()
    errors = []

    def safe(name, *a):
        try:
            df = fetch(name, *a)
            return df if df is not None else pd.DataFrame()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {str(e)[:120]}")
            return pd.DataFrame()

    def prep(fn, df, *a):
        try:
            return fn(df, *a) if df is not None and len(df) else pd.DataFrame()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{getattr(fn, '__name__', 'prepare')}: {str(e)[:120]}")
            return pd.DataFrame()

    vw = prep(an.prepare_view, safe("view", d0, d1))
    doc_ids = set()
    if len(vw) and {"IsDiscount", "IsGuest", "IsCustomer", "id"} <= set(vw.columns):
        doc_ids = set(pd.to_numeric(vw.loc[vw.IsDiscount | vw.IsGuest | vw.IsCustomer, "id"], errors="coerce").dropna().astype(int))
    tx = prep(lambda d: an.prepare_taradods(d, float(tol), tuple(nocash), doc_ids), safe("taradods", d0, d1))
    ins = prep(an.prepare_inside, safe("inside"), now)
    gates, _ = an.prepare_gates(safe("gates"), safe("gate_types"))
    if len(ins) and len(gates):
        ins, _ = an.apply_gate_names(ins, gates)
    ops = operator_directory(safe("users"), op_mapping)
    return dict(tx=tx, view=vw, inside=ins, anpr=safe("anpr", d0, d1), dl=safe("discount_logs", d0, d1), gates=gates, ops=ops, now=now, errors=errors)


# ----------------------------------------------------------------------------- وضعیت آخرین پایش (برای چک‌لیست سلامت)
LAST_RUN_FILE = ".shift_alerts_last_run.json"


def write_last_run(info, path=LAST_RUN_FILE):
    try:
        Path(path).write_text(json.dumps(info, ensure_ascii=False, default=str), encoding="utf-8")
    except OSError:
        pass


def read_last_run(path=LAST_RUN_FILE):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


# ----------------------------------------------------------------------------- CLI
def _cli_fetch(db):
    def f(name, *a):
        return db.fetch(name, *a)
    return f


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--secrets", default=".streamlit/secrets.toml")
    ap.add_argument("--day", help="میلادی 2026-09-25 یا شمسی 1405/07/03")
    ap.add_argument("--shift", choices=SHIFT_NAMES)
    ap.add_argument("--all-shifts", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--min-severity", default="مهم", choices=list(SEV_ORDER))
    a = ap.parse_args()

    sec = tomllib.loads(Path(a.secrets).read_text(encoding="utf-8")) if Path(a.secrets).exists() else {}
    import db
    if sec.get("connections", {}).get("mssql"):
        db.configure(sec["connections"]["mssql"])
    sms = sec.get("sms", {})
    rec = sms.get("recipients", {})
    cfg = sec.get("alerts", {})
    tol, nocash = float(cfg.get("tol", 0)), tuple(int(x) for x in cfg.get("nocash_payment_ids", []))
    fetch = _cli_fetch(db)
    store = AlertStore()
    day_arg = parse_day(a.day) if a.day else None
    if a.day and day_arg is None:
        raise SystemExit("فرمت تاریخ نامعتبر است (مثال: 2026-09-25 یا 1405/07/03)")

    while True:
        now = pd.Timestamp(str(fetch("server_now").iloc[0, 0]))
        cur_day, cur_shift = current_shift(now)
        day = day_arg if day_arg is not None else cur_day
        sel = ALL_SHIFTS if a.all_shifts else (a.shift or (cur_shift if day_arg is None else ALL_SHIFTS))
        D = load_live(fetch, day, now, tol=tol, nocash=nocash, op_mapping=sms.get("operators", {}))
        al = evaluate_shifts(D["tx"], D["view"], D["inside"], D["anpr"], D["dl"], day, sel, gates=D["gates"], ops=D["ops"], now=now, tol=tol, nocash=nocash)
        print(f"{'همه شیفت‌ها' if sel == ALL_SHIFTS else 'شیفت ' + sel} {jalali_text(day, fa_digits=False)} ({day:%Y-%m-%d}): {len(al)} هشدار" + (f" · {PROVISIONAL}" if len(al) and al.provisional.any() else ""))
        for e in D["errors"]:
            print("  هشدار داده:", e)
        for _, x in al.iterrows():
            print(f"- {x.message} (بخش پیامک: {sms_segments(x.message)})")
        res = []
        if a.send:
            gw = SMSGateway(sms.get("provider"), sms.get("api_key"), sms.get("sender"), dry_run=False)
            res = dispatch(al, gw, rec, store, a.min_severity)
            for r in res:
                print(" SMS", r)
        write_last_run(dict(at=str(pd.Timestamp.now()), server_now=str(now), day=f"{day:%Y-%m-%d}", shift=sel, alerts=int(len(al)), sms=len(res), demo=is_demo(), errors=D["errors"]))
        if not a.watch:
            break
        time.sleep(a.interval)
