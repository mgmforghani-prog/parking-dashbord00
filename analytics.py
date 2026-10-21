"""تحلیل‌ها و قوانین تشخیص تقلب — فقط بر پایه‌ی ستون‌هایی که در ParkingDB وجود دارند (بدون Streamlit، قابل استفاده در CLI)
معادل‌سازی با سند «کشف تقلب»: هر قانون ستون «معادل در سند» دارد؛ مفاهیمی که ستونشان در دیتابیس نیست (ابطال فیش، لوپ، ساعت NTP، ...) پیاده نشده‌اند.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any
WD = {5: "شنبه", 6: "یکشنبه", 0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه"}
WD_ORDER = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
SHIFTS = {"صبح": (6, 14), "عصر": (14, 22), "شب": (22, 30)}
LOW_CONF = 0.60       # سند: اطمینان زیر ۰.۶۰ نویز سخت‌افزاری است
LONG_STAY_H = 48      # سند: خودروی رسوبی بیش از ۴۸ ساعت
Z_PROTOCOL = 2.5      # سند: پروتکل بازرسی سرزده
Z_OUTLIER = 3.0        # سند: انحراف ۳σ برای اپراتورهای پرت (فرجه/نقد/ابطال)
GRACE_MIN = 10         # سند: فرجه‌ی رایگان ۵ تا ۱۰ دقیقه
GRACE_THR = 5.0        # سند: ترخیص با فرجه > ۵٪ تردد کل
MOE_THR = 3.5          # سند: ویرایش/ناهمخوانی پلاک > ۳.۵٪ خروج‌ها
GHOST_MAX_MIN = 15     # سند: خروج رسوبی با فیش جدید زیر ۱۵ دقیقه
GHOST_MIN_STAY_H = 48
GHOST_THR = 0.5        # آستانه‌ی داخلی (سند عدد صریح ندارد)
OCR_THR = 10.0         # آستانه‌ی داخلی نرخ نویز OCR
CAP_THR = 2            # سند: انحراف ظرفیت ± ۲ خودرو
HEALTH_OK, HEALTH_WARN = 85, 70
LONG_STAY_H = 48         # سند: خودروی رسوبی بیش از ۴۸ ساعت
SEDIMENT_MAX_H = 168     # سقف رسوب (۷ روز) برای جلوگیری از رکوردهای مرده
STALE_H = 45 * 24        # سقف کهنگی

# کلید: (عنوان، وزن در امتیاز ریسک، معادل در سند، منطق دقیق)
RULES = {
    "F_PosNoDetail": ("پرداخت کارتخوان بدون ثبت پوز", 35, "پوز شخصی / بدون RRN", "Pos > 0 و PosDetailID خالی"),
    "F_PayMismatch": ("ناهمخوانی نقد+کارتخوان با مبلغ نهایی", 25, "کم‌ثبتی / اضافه‌دریافتی", "تردد خارج‌شده با Cash + Pos ≠ FinalPrice"),
    "F_FreeExit": ("خروج رایگان بدون سند (معافیت غیرمجاز)", 25, "ثبت خودرو با تعرفه معاف / VIP غیرمجاز", "FinalPrice = 0 و TotalPrice > 0 بدون ثبت در DiscountLogs / Guests / Customer"),
    "F_Refund": ("بازپرداخت ثبت‌شده", 40, "ابطال پس از دریافت پول", "Refund > 0"),
    "F_Lost": ("فیش مفقودی", 30, "اعلام فیش مفقودی", "IsLost = 1"),
}


def shift_of(hour):
    h = pd.Series(hour)
    return pd.Series(np.select([(h >= 6) & (h < 14), (h >= 14) & (h < 22)], ["صبح", "عصر"], "شب"), index=h.index)


def _dt(d, cols):
    for c in cols:
        d[c] = pd.to_datetime(d[c], errors="coerce")


def _timecols(d):
    d["Event_Time"] = d["ExitDateTime"].fillna(d["EnrtyDateTime"])
    d["Date"], d["Hour"] = d.Event_Time.dt.normalize(), d.Event_Time.dt.hour
    d["Weekday"], d["Shift"] = d.Event_Time.dt.weekday.map(WD), shift_of(d.Hour)
    return d


def prepare_taradods(t: pd.DataFrame, tol: float = 0, nocash_ids=(), doc_ids=None) -> pd.DataFrame:
    """ردیف‌های تردد + پرچم‌ها. فرمول‌ها دقیقاً با کوئری تجمیعی fin_tx یکی است (فقط ترددهای خارج‌شده):
    اولویت جزء زیان در هر ردیف: بازپرداخت ← کارتخوان بدون ثبت پوز ← کسری وصول (بدون شمارش مضاعف)
      Gap = FinalPrice − (Cash + Pos)     |   کسری وصول: Gap > tol     |   اضافه‌وصول: −Gap > tol (زیان نیست؛ مغایرت مثبت)
    """
    d = t.copy()
    _dt(d, ["EnrtyDateTime", "ExitDateTime"])
    for c in ("TotalPrice", "FinalPrice", "Cash", "Pos", "Refund", "VATPrice", "RoundPrice"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["Exited"] = d.ExitDateTime.notna()
    d = _timecols(d)
    d["Dur_Min"] = ((d.ExitDateTime - d.EnrtyDateTime).dt.total_seconds() / 60).where(d.Exited)
    cash, pos, fin, tot, ref = d.Cash.fillna(0), d.Pos.fillna(0), d.FinalPrice.fillna(0), d.TotalPrice.fillna(0), d.Refund.fillna(0)
    d["Collected"], d["Gap"] = cash + pos, fin - (cash + pos)
    nocash = d.PaymentID.isin(list(nocash_ids)) if len(nocash_ids) else pd.Series(False, index=d.index)
    refund = d.Exited & (ref > 0)
    posnd = d.Exited & ~refund & (pos > 0) & d.PosDetailID.isna()
    elig = d.Exited & ~refund & ~nocash
    under = elig & ~((pos > 0) & d.PosDetailID.isna()) & (d.Gap > tol)
    over = elig & (-d.Gap > tol)
    d["F_PosNoDetail"], d["F_Refund"] = posnd.astype(int), refund.astype(int)
    d["F_PayMismatch"] = (under | over).astype(int)
    free = d.Exited & d.FinalPrice.notna() & (d.FinalPrice == 0) & (tot > 0)
    if doc_ids is not None:  # سند: فقط معافیت «غیرمجاز» (بدون کد تخفیف/مهمان/اشتراک) پرچم تقلب است؛ معافیت دارای سند عادی است
        free = free & ~d.ID.isin(list(doc_ids))
    d["F_FreeExit"] = free.astype(int)  # در Loss نیست (زیان تخفیف بدون سند از View محاسبه می‌شود) ولی در امتیاز ریسک هست
    d["F_Lost"] = (d.Exited & d.IsLost.fillna(False).astype(bool)).astype(int)
    d["Loss"] = np.select([refund, posnd, under], [ref, pos, d.Gap], 0.0)
    d["LossType"] = np.select([refund, posnd, under], ["بازپرداخت", "کارتخوان بدون ثبت پوز", "کسری وصول"], "")
    d["OverAmt"] = np.where(over, -d.Gap, 0.0)
    d["NFlags"] = d[list(RULES)].sum(axis=1)
    d["Risk_Score"] = sum(d[k] * v[1] for k, v in RULES.items())
    d["Risk_Score"] = d["Risk_Score"].clip(upper=100)
    d["Risk_Level"] = np.select([d.Risk_Score >= 60, d.Risk_Score >= 30, d.Risk_Score > 0], ["بحرانی", "مشکوک", "هشدار"], "عادی")
    return d


def rule_amount(tx: pd.DataFrame, k: str) -> float:
    """مبلغ مرتبط با هر قانون (برای جدول روش‌های تخلف)"""
    f = tx[tx[k] == 1]
    if k == "F_PosNoDetail": return float(f.Pos.fillna(0).sum())
    if k == "F_PayMismatch": return float(f.Gap.abs().sum())
    if k == "F_FreeExit": return float(f.TotalPrice.fillna(0).sum())
    if k == "F_Refund": return float(f.Refund.fillna(0).sum())
    return 0.0


def prepare_view(v: pd.DataFrame) -> pd.DataFrame:
    d = v.copy()
    _dt(d, ["EnrtyDateTime", "ExitDateTime"])
    for c in ("TotalPrice", "FinalPrice", "Cash", "Pos", "VATPrice"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["Exited"] = d.ExitDateTime.notna()
    d = _timecols(d)
    d["Dur_Min"] = ((d.ExitDateTime - d.EnrtyDateTime).dt.total_seconds() / 60).where(d.Exited)
    d["IsDiscount"], d["IsGuest"], d["IsCustomer"] = d.DiscountLogsTaradodID.notna(), d.GuestsTaradodID.notna(), d.CustomerTaradodID.notna()
    d["IsFree"] = d.Exited & d.FinalPrice.notna() & (d.FinalPrice == 0)
    d["IsFreeNoDoc"] = d.IsFree & (d.TotalPrice.fillna(0) > 0) & ~(d.IsDiscount | d.IsGuest | d.IsCustomer)
    return d


PRESENT_LBL = "حاضر قطعی"
SEDIMENT_LBL = "رسوبی واقعی / نیازمند بازرسی حراست"


def _plate_label(d: pd.DataFrame) -> pd.Series:
    """متن خوانای پلاک (P1 P2 P3 - P4)؛ اگر اجزای پلاک موجود نبود، شناسه‌ی PlateID"""
    pid = "PlateID " + pd.to_numeric(d.PlateID, errors="coerce").astype("Int64").astype(str)
    if not all(c in d for c in ("P1", "P2", "P3", "P4")):
        return pid
    p = d[["P1", "P2", "P3", "P4"]].apply(lambda c: c.astype("string").str.strip())
    txt = p.P1 + " " + p.P2 + " " + p.P3 + " - " + p.P4
    return txt.where(p.notna().all(axis=1), pid).astype(str)


INSIDE_STAY_LABELS = {"present": PRESENT_LBL, "sediment": SEDIMENT_LBL, "old_open": "قدیمی‌تر از ۷ روز / مشکوک به داده‌ی مرده",
                      "stale": "خطای ثبت (بیش از ۴۵ روز)", "invalid": "نامعتبر"}
INSIDE_COLS = ["ID", "CardID", "PlateID", "EnrtyDateTime", "InputGateID", "Age_H", "StayHours", "Present", "LongStay", "OldOpen", "Stale", "Live",
               "StayClass", "DupCard", "DupPlate", "IsLost"]


def classify_inside_age(age_h) -> pd.Series:
    """تنها منبع طبقه‌بندی مدت توقف: invalid(NaN) | present(<48) | sediment(48..168) | old_open(168..1080) | stale(>1080)"""
    a = pd.to_numeric(pd.Series(age_h), errors="coerce").clip(lower=0)
    return pd.Series(np.select([a.isna(), a < LONG_STAY_H, a <= SEDIMENT_MAX_H, a <= STALE_H],
                               ["invalid", "present", "sediment", "old_open"], "stale"), index=a.index)


def _dup_flags(values, live) -> pd.Series:
    """تکراری بودن مقدار (>0) فقط میان رکوردهای زنده؛ رکوردهای stale/نامعتبر در تکرار شرکت نمی‌کنند"""
    v = pd.to_numeric(values, errors="coerce").where(live)
    return v.notna() & (v > 0) & v.duplicated(keep=False)


def prepare_inside(i: pd.DataFrame, now=None) -> pd.DataFrame:
    """رکوردهای باز (ExitDateTime خالی)، با ورود معتبر و بدون ID تکراری؛ مدت توقف نسبت به now (ساعت سرور)"""
    if i is None or len(i) == 0:
        return pd.DataFrame(columns=INSIDE_COLS)
    d = i.copy()
    now = pd.Timestamp.now() if now is None else pd.Timestamp(now)
    if "ExitDateTime" in d.columns:
        d = d[pd.to_datetime(d["ExitDateTime"], errors="coerce").isna()]
    if "ID" in d.columns:
        d = d[~(d["ID"].notna() & d["ID"].duplicated(keep="first"))]
    dt_col = next((c for c in ("EnrtyDateTime", "EnterDateTime") if c in d.columns), None)
    if dt_col is None:
        return pd.DataFrame(columns=INSIDE_COLS)
    d = d.copy()
    d["EnrtyDateTime"] = pd.to_datetime(d[dt_col], errors="coerce")
    d = d[d["EnrtyDateTime"].notna()].copy()
    if d.empty:
        return pd.DataFrame(columns=INSIDE_COLS)
    d["Age_H"] = ((now - d["EnrtyDateTime"]).dt.total_seconds() / 3600.0).round(1)
    d["StayHours"] = d["Age_H"]
    cls = classify_inside_age(d["Age_H"])
    d["Present"], d["LongStay"], d["OldOpen"], d["Stale"] = cls == "present", cls == "sediment", cls == "old_open", cls == "stale"
    d["Live"] = d["Present"] | d["LongStay"] | d["OldOpen"]
    d["StayClass"] = cls.map(INSIDE_STAY_LABELS)
    for c, dv in (("ID", "-"), ("CardID", np.nan), ("PlateID", np.nan), ("InputGateID", "-"), ("IsLost", False)):
        if c not in d.columns:
            d[c] = dv
    d["DupCard"] = _dup_flags(d["CardID"], d["Live"])
    d["DupPlate"] = _dup_flags(d["PlateID"], d["Live"])
    return d.reset_index(drop=True)


def inside_counts(ins: pd.DataFrame | None) -> dict:
    """تنها منبع شمارش خودروهای داخل (کارت‌ها، نمودارها، جدول‌ها و قانون A8). ورودی: خروجی prepare_inside.
    live = present + sediment + old_open  ؛  sediment همیشه زیرمجموعه‌ی live است؛ stale (>۴۵ روز) خطای ثبت و خارج از live."""
    out = dict(total_open=0, live=0, present=0, sediment=0, old_open=0, stale=0, dup_card=0, dup_plate=0, max_sediment_h=0.0)
    if ins is None or len(ins) == 0 or "Age_H" not in ins.columns:
        return out
    d = ins
    if "ExitDateTime" in d.columns:
        d = d[pd.to_datetime(d["ExitDateTime"], errors="coerce").isna()]
    age = pd.to_numeric(d["Age_H"], errors="coerce")
    cls = classify_inside_age(age)
    live = cls.isin(["present", "sediment", "old_open"])
    out["present"], out["sediment"] = int((cls == "present").sum()), int((cls == "sediment").sum())
    out["old_open"], out["stale"] = int((cls == "old_open").sum()), int((cls == "stale").sum())
    out["live"] = out["present"] + out["sediment"] + out["old_open"]
    out["total_open"] = out["live"] + out["stale"]
    sa = age[cls == "sediment"]
    out["max_sediment_h"] = float(sa.max()) if len(sa) else 0.0
    if "CardID" in d.columns:
        out["dup_card"] = int(_dup_flags(d["CardID"], live).sum())
    if "PlateID" in d.columns:
        out["dup_plate"] = int(_dup_flags(d["PlateID"], live).sum())
    return out


def validate_inside_counts(c: dict) -> dict:
    """بررسی invariantها؛ در صورت نقض ValueError"""
    errs = []
    if c["sediment"] > c["live"]:
        errs.append("sediment > live")
    if c["present"] + c["sediment"] + c["old_open"] != c["live"]:
        errs.append("present + sediment + old_open != live")
    if c["live"] > c["total_open"]:
        errs.append("live > total_open")
    if c["dup_card"] > c["live"] or c["dup_plate"] > c["live"]:
        errs.append("dup > live")
    if errs:
        raise ValueError("; ".join(errs))
    return c





def _conf(s: pd.Series) -> pd.Series:
    """اطمینان ANPR را به مقیاس ۰ تا ۱ می‌برد (اگر دیتابیس ۰ تا ۱۰۰ ذخیره کرده باشد)؛ آستانه‌های سند (۰.۶۰ و ۰.۸۵) کسری‌اند"""
    c = pd.to_numeric(s, errors="coerce")
    return c / 100.0 if len(c) and c.max() > 1.0 else c


def zscore(s: pd.Series) -> pd.Series:
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and sd == sd else s * 0


def operator_profile(view: pd.DataFrame, min_tx: int = 5, tx: pd.DataFrame | None = None):
    """پروفایل صندوقدار (ExitUser در View_Taradods_PBI) + Z-Score مطابق سند.
    ابعاد: نسبت نقد، ابطال/بازپرداخت (از tx با کلید id=ID)، معافیت بدون سند، رایگان، تخفیف، مهمان، اشتراکی.
    پروتکل بازرسی سرزده (سند): Z نسبت نقد > ۲.۵ و همزمان Z ابطال > ۲.۵؛ اگر ستون Refund در داده خالی باشد
    (Z ابطال همیشه صفر)، «معافیت بدون سند» جایگزین نشانه‌ی ابطال/تخلف می‌شود."""
    e = view[view.Exited & view.ExitUser.notna()].copy()
    if tx is not None and len(tx) and "F_Refund" in tx:
        e["IsRefund"] = e["id"].map(tx.drop_duplicates("ID").set_index("ID")["F_Refund"]).fillna(0).astype(float)
    else:
        e["IsRefund"] = 0.0
    if "IsFreeNoDoc" not in e:
        e["IsFreeNoDoc"] = False
    e["IsFreeNoDoc"] = e["IsFreeNoDoc"].astype(float)
    g = e.groupby("ExitUser")
    p = pd.DataFrame({"تردد": g.size(), "درآمد": g.FinalPrice.sum(), "نسبت نقد": g.Cash.sum() / (g.Cash.sum() + g.Pos.sum()).replace(0, np.nan),
                      "نرخ ابطال/بازپرداخت": g.IsRefund.mean(), "نرخ معافیت بدون سند": g.IsFreeNoDoc.mean(),
                      "نرخ خروج رایگان": g.IsFree.mean(), "نرخ تخفیف": g.IsDiscount.mean(), "نرخ مهمان": g.IsGuest.mean(), "نرخ اشتراکی": g.IsCustomer.mean()})
    p = p[p["تردد"] >= min_tx].fillna(0)
    cols = ["نسبت نقد", "نرخ ابطال/بازپرداخت", "نرخ معافیت بدون سند", "نرخ خروج رایگان", "نرخ تخفیف", "نرخ مهمان", "نرخ اشتراکی"]
    z = p[cols].apply(zscore) if len(p) >= 3 else p[cols] * 0
    p["Z ترکیبی"] = z.mean(axis=1)
    void_hit = (z["نرخ ابطال/بازپرداخت"] > Z_PROTOCOL) | (z["نرخ معافیت بدون سند"] > Z_PROTOCOL)
    hit = list(z[(z["نسبت نقد"] > Z_PROTOCOL) & void_hit].index)
    return p, z, hit


def id_profile(tx: pd.DataFrame) -> pd.DataFrame:
    """پروفایل بر اساس ExitUserID (نام کاربر فقط در View_Taradods_PBI هست و FK به Users ثبت نشده)"""
    e = tx[tx.Exited]
    g = e.groupby("ExitUserID")
    return pd.DataFrame({"تردد": g.size(), "بازپرداخت": g.F_Refund.sum(), "مبلغ بازپرداخت": g.Refund.sum(), "کارتخوان بدون پوز": g.F_PosNoDetail.sum(),
                         "ناهمخوانی پرداخت": g.F_PayMismatch.sum(), "خروج صفر": g.F_FreeExit.sum(), "مفقودی": g.F_Lost.sum(), "ریسک میانگین": g.Risk_Score.mean()})


def anpr_summary(a: pd.DataFrame) -> pd.DataFrame:
    if a.empty:
        return pd.DataFrame(columns=["GateId", "خوانش", "میانگین اطمینان", "نویز (<0.60)", "نسبت نویز"])
    a = a.assign(Confidence=_conf(a.Confidence))
    g = a.groupby("GateId")
    return pd.DataFrame({"خوانش": g.size(), "میانگین اطمینان": g.Confidence.mean(), "نویز (<0.60)": g.Confidence.apply(lambda s: (s < LOW_CONF).sum())}).assign(
        **{"نسبت نویز": lambda x: x["نویز (<0.60)"] / x["خوانش"]}).reset_index()




def discount_reuse(dl: pd.DataFrame) -> pd.DataFrame:
    if dl.empty:
        return pd.DataFrame(columns=["DiscountCode", "تعداد استفاده", "تردد متفاوت", "آخرین استفاده"])
    g = dl.groupby("DiscountCode")
    r = pd.DataFrame({"تعداد استفاده": g.size(), "تردد متفاوت": g.TaradodID.nunique(), "آخرین استفاده": g.InsertDate.max()}).reset_index()
    return r[r["تعداد استفاده"] > 1].sort_values("تعداد استفاده", ascending=False)


def over_limit_codes(codes: pd.DataFrame) -> pd.DataFrame:
    if codes.empty:
        return codes
    c = codes.assign(UsedCount=pd.to_numeric(codes.UsedCount, errors="coerce"), MaxCount=pd.to_numeric(codes.MaxCount, errors="coerce"))
    return c[c.MaxCount.notna() & (c.UsedCount > c.MaxCount)]


# ============================================================ مالی دقیق (بر پایه‌ی کوئری‌های تجمیعی fin_tx / fin_view)
SHIFT_CODE = {1: "صبح", 2: "عصر", 3: "شب"}
REASON = {1: "کد تخفیف", 2: "مهمان", 3: "اشتراکی", 4: "بدون سند"}
_TX_NUM = ["Exits", "SumTotal", "SumFinal", "SumCash", "SumPos", "SumRefund", "SumVAT", "SumRound", "RefundN", "PosNoDetailN", "PosNoDetailAmt",
           "UnderN", "UnderAmt", "OverN", "OverAmt", "LostN", "FreeN", "FreeAmt"]
_VW_NUM = ["Exits", "SumTotal", "SumFinal", "WaiverN", "WaiverAmt", "UpliftAmt"]


def prepare_fin(ft: pd.DataFrame, fv: pd.DataFrame):
    ft, fv = ft.copy(), fv.copy()
    for d, cols in ((ft, _TX_NUM), (fv, _VW_NUM)):
        for c in cols:
            d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0) if c in d else 0
        d["ExitDate"] = pd.to_datetime(d["ExitDate"], errors="coerce")
        d["Shift"] = d.ShiftCode.map(SHIFT_CODE)
    fv["ReasonFa"] = fv.Reason.map(REASON)
    return ft, fv


def fin_filter(ft, fv, shift=None, gate_id=None):
    if shift:
        ft, fv = ft[ft.Shift == shift], fv[fv.Shift == shift]
    if gate_id is not None:
        ft, fv = ft[ft.OutputGateID == gate_id], fv[fv.OutputGateID == gate_id]
    return ft, fv


# ============================================================ روز جاری (ناقص) در تحلیل‌های تاریخی
# «روز جاری» = روز تقویم میلادیِ ساعت سرور دیتابیس (GETDATE همان کوئری server_now)؛ هرگز ساعت/تاریخ سیستم کاربر.
# اطلاعات این روز هنوز کامل نیست (ثبت تردد و وصول ادامه دارد) و نباید کسری قطعی / نقطه‌ی پرت / نقض حد کنترل تفسیر شود.
# همه‌ی فیلترها فقط از همین چند تابع می‌گذرند و روی تاریخ میلادیِ واقعی رکورد (پیش از هر تبدیل به شمسی) اعمال می‌شوند.
CURRENT_DAY_NOTICE = "روز جاری از تحلیل‌های تاریخی حذف شده است؛ اطلاعات امروز هنوز کامل نیست."
NO_HISTORY_NOTICE = "برای این بازه هنوز دادهٔ تاریخیِ کامل وجود ندارد"
# ستون‌های سمت «خروج» که تا لحظه‌ی خروج مقدار قطعی ندارند (برای برش لحظه‌ای ترددهای خروجِ امروز)
_EXIT_SIDE_COLS = ("ExitUserID", "OutputGateID", "TotalPrice", "FinalPrice", "PaymentID", "PosDetailID", "Cash", "Pos", "Refund", "VATPrice", "RoundPrice",
                   "ExitPlateID", "ExitANPRPlateID", "PosTerminalNo")


def current_day_start(now) -> pd.Timestamp:
    """آغاز (۰۰:۰۰) روز جاری بر پایه‌ی ساعت سرور دیتابیس؛ رکوردهای تاریخی باید «پیش از» این لحظه باشند"""
    return pd.Timestamp(now).normalize()


def historical_only(df: pd.DataFrame, date_col: str, now) -> pd.DataFrame:
    """تنها ردیف‌های روزهای کاملِ گذشته: date_col (تاریخ میلادی واقعی) < آغاز روز جاری سرور.
    ردیفی که تاریخش قابل‌تعیین نیست (NaT) نمی‌توان ثابت کرد امروز نیست، پس از تحلیل تاریخی کنار می‌ماند (نه حدس)."""
    if df is None:
        return df
    if date_col not in df.columns:
        if len(df) == 0:
            return df
        raise KeyError(f"ستون تاریخ «{date_col}» برای فیلتر روز جاری وجود ندارد")
    return df[pd.to_datetime(df[date_col], errors="coerce") < current_day_start(now)]


def censor_open_day(raw: pd.DataFrame, now, entry_col: str = "EnrtyDateTime", exit_col: str = "ExitDateTime") -> pd.DataFrame:
    """نمای تاریخیِ ترددهای خام «در لحظه‌ی آغاز روز جاری» (پیش از prepare_taradods):
      • ورودهای امروز حذف می‌شوند؛
      • تردد با ورود دیروز (یا قبل‌تر) و خروج امروز، در تحلیل تاریخی «هنوز خارج‌نشده» است: ورودش در روز خودش می‌ماند
        (ترازِ ورود/خروج روزهای گذشته تغییر نمی‌کند) ولی خروج و همه‌ی مبالغ سمت خروجش (تعرفه، وصول، بازپرداخت، ...) خالی می‌شود
        تا پرداختِ ناقصِ امروز هرگز کسری یا زیان به‌حساب نیاید."""
    if raw is None or len(raw) == 0:
        return raw
    cut = current_day_start(now)
    ent, ext = pd.to_datetime(raw[entry_col], errors="coerce"), pd.to_datetime(raw[exit_col], errors="coerce")
    keep = (ent < cut) | (ent.isna() & (ext < cut))
    d = raw[keep.to_numpy()].copy()
    late = (ext[keep] >= cut).to_numpy()
    if late.any():
        for c in (exit_col, *_EXIT_SIDE_COLS):
            if c in d.columns:
                d[c] = d[c].where(~late)
    return d


def day_boundary_report(raw: pd.DataFrame, now, entry_col: str = "EnrtyDateTime", exit_col: str = "ExitDateTime") -> dict:
    """شمارش رکوردهای مرزی برای گزارش شفاف: ورودهای امروز، خروج‌های امروزِ برش‌خورده، و رکوردهای بدون تاریخ قابل‌تعیین (مبهم)"""
    if raw is None or len(raw) == 0:
        return dict(today_entries=0, today_exits_censored=0, undated=0)
    cut = current_day_start(now)
    ent, ext = pd.to_datetime(raw[entry_col], errors="coerce"), pd.to_datetime(raw[exit_col], errors="coerce")
    return dict(today_entries=int((ent >= cut).sum()), today_exits_censored=int(((ent < cut) & (ext >= cut)).sum()), undated=int((ent.isna() & ext.isna()).sum()))


def fin_bundle(ft: pd.DataFrame, fv: pd.DataFrame, nocash_ids=()) -> dict:
    """همه‌ی مجموع‌های مالی با فرمول‌های ثابت:
      تعرفه (Σ TotalPrice) − تخفیف/معافیت (Σ max(Total−Final,0)) + افزوده (Σ max(Final−Total,0)) = مبلغ نهایی (Σ FinalPrice)
      مبلغ نهایی − کسری وصول + اضافه‌وصول = وصول‌شده (Σ Cash + Σ Pos)         [فقط ردیف‌های بدون بازپرداخت و بدون PaymentID مستثنا]
      زیان مشکوک = کسری وصول + کارتخوان بدون پوز + بازپرداخت + تخفیف/معافیت بدون سند (اجزای مجزا؛ شمارش مضاعف ندارد)"""
    ft = ft.copy()
    if len(nocash_ids):
        m = ft.PaymentID.isin(list(nocash_ids))
        ft.loc[m, ["UnderN", "UnderAmt", "OverN", "OverAmt"]] = 0
    B: dict[str, Any] = dict(exits=int(ft.Exits.sum()), tariff=float(fv.SumTotal.sum()), billed_tx=float(ft.SumFinal.sum()), billed_vw=float(fv.SumFinal.sum()),

             cash=float(ft.SumCash.sum()), pos=float(ft.SumPos.sum()), refund=float(ft.SumRefund.sum()), vat=float(ft.SumVAT.sum()), rnd=float(ft.SumRound.sum()),
             under=float(ft.UnderAmt.sum()), under_n=int(ft.UnderN.sum()), over=float(ft.OverAmt.sum()), over_n=int(ft.OverN.sum()),
             posnd=float(ft.PosNoDetailAmt.sum()), posnd_n=int(ft.PosNoDetailN.sum()), refund_n=int(ft.RefundN.sum()), lost_n=int(ft.LostN.sum()),
             free_n=int(fv[fv.Reason.notna()].WaiverN.sum()), uplift=float(fv.UpliftAmt.sum()), waiver_all=float(fv.WaiverAmt.sum()))
    B["collected"] = B["cash"] + B["pos"]
    wr = fv.groupby("Reason").WaiverAmt.sum()
    B["waiver_by_reason"] = {REASON[k]: float(wr.get(k, 0.0)) for k in REASON}
    B["unexplained"], B["explained"] = B["waiver_by_reason"]["بدون سند"], B["waiver_all"] - B["waiver_by_reason"]["بدون سند"]
    B["unexplained_n"] = int(fv[fv.Reason == 4].WaiverN.sum())
    B["components"] = {"کسری وصول": B["under"], "کارتخوان بدون ثبت پوز": B["posnd"], "بازپرداخت": float(ft.loc[ft.RefundN > 0, "SumRefund"].sum()), "تخفیف/معافیت بدون سند": B["unexplained"]}
    B["loss"] = float(sum(B["components"].values()))
    B["integrity_diff"] = B["billed_tx"] - B["billed_vw"]  # دو منبع (Taradods و View) باید مبلغ نهایی یکسان بدهند
    # روند روزانه
    dt = ft.groupby("ExitDate").agg(billed=("SumFinal", "sum"), cash=("SumCash", "sum"), pos=("SumPos", "sum"), refund=("SumRefund", "sum"), posnd=("PosNoDetailAmt", "sum"), under=("UnderAmt", "sum"), over=("OverAmt", "sum"))
    dv = fv.groupby("ExitDate").agg(tariff=("SumTotal", "sum"), waiver_all=("WaiverAmt", "sum"), uplift=("UpliftAmt", "sum"))
    dv["unexplained"] = fv[fv.Reason == 4].groupby("ExitDate").WaiverAmt.sum()
    day = dt.join(dv, how="outer").fillna(0)
    day["waiver_doc"] = day.waiver_all - day.unexplained
    day["collected"] = day.cash + day.pos
    day["loss"] = day.refund + day.posnd + day.under + day.unexplained
    B["daily"] = day.reset_index().rename(columns={"ExitDate": "Date"})
    # به تفکیک صندوقدار
    uv = fv.groupby("ExitUser").agg(خروج=("Exits", "sum"), تعرفه=("SumTotal", "sum"), نهایی=("SumFinal", "sum"), تخفیف_کل=("WaiverAmt", "sum"))
    uv["تخفیف_بدون_سند"] = fv[fv.Reason == 4].groupby("ExitUser").WaiverAmt.sum()
    uv = uv.fillna(0)
    uv["سهم_بدون_سند"] = np.where(uv.تعرفه > 0, uv.تخفیف_بدون_سند / uv.تعرفه, 0.0)
    B["by_user_view"] = uv.reset_index().sort_values("تخفیف_بدون_سند", ascending=False)
    ut = ft.groupby("ExitUserID").agg(خروج=("Exits", "sum"), نهایی=("SumFinal", "sum"), نقد=("SumCash", "sum"), کارتخوان=("SumPos", "sum"), بازپرداخت=("SumRefund", "sum"),
                                     پوز_بدون_ثبت=("PosNoDetailAmt", "sum"), کسری_وصول=("UnderAmt", "sum"), اضافه_وصول=("OverAmt", "sum"))
    ut["زیان"] = ut.بازپرداخت + ut.پوز_بدون_ثبت + ut.کسری_وصول
    B["by_user_tx"] = ut.reset_index().sort_values("زیان", ascending=False)
    pm = ft.groupby("PaymentID", dropna=False).agg(خروج=("Exits", "sum"), نهایی=("SumFinal", "sum"), نقد=("SumCash", "sum"), کارتخوان=("SumPos", "sum"), کسری=("UnderAmt", "sum"), اضافه=("OverAmt", "sum"))
    pm["وصول_شده"] = pm.نقد + pm.کارتخوان
    B["by_payment"] = pm.reset_index()
    B["by_gate"] = ft.groupby("OutputGateID").agg(خروج=("Exits", "sum"), نهایی=("SumFinal", "sum"), وصول=("SumCash", "sum"), کارتخوان=("SumPos", "sum")).reset_index()
    return B


def identity_table(ex: pd.DataFrame) -> pd.DataFrame:
    """کدام رابطه‌ی ریاضی بین ستون‌های مالی واقعاً در داده برقرار است؟ (برای تشخیص فرمول درست و دلیل صفر بودن اجزا)"""
    e = ex[ex.Exited & (ex.TotalPrice.fillna(0) > 0) & (ex.FinalPrice.fillna(0) > 0)]
    if e.empty:
        return pd.DataFrame(columns=["رابطه", "سهم ردیف‌های برقرار", "تعداد ردیف"])
    tot, fin, vat, rnd = e.TotalPrice, e.FinalPrice, e.VATPrice.fillna(0), e.RoundPrice.fillna(0)
    ok = lambda x, y: float(((x - y).abs() < 0.5).mean())  # noqa: E731
    rows = [("FinalPrice = TotalPrice", ok(fin, tot)), ("FinalPrice = TotalPrice + VATPrice", ok(fin, tot + vat)), ("FinalPrice = TotalPrice + RoundPrice", ok(fin, tot + rnd)),
            ("FinalPrice = TotalPrice + VATPrice + RoundPrice", ok(fin, tot + vat + rnd)), ("Cash + Pos = FinalPrice", ok(e.Cash.fillna(0) + e.Pos.fillna(0), fin))]
    return pd.DataFrame(rows, columns=["رابطه", "سهم ردیف‌های برقرار"]).assign(**{"تعداد ردیف": len(e)})
def sediment_table(ins_df: pd.DataFrame) -> pd.DataFrame:
    """جدول خودروهای رسوبی مشکوک (بیش از ۴۸ ساعت توقف و بدون خروج)"""
    if ins_df is None or ins_df.empty:
        return pd.DataFrame(columns=["شناسه تردد", "شماره کارت", "پلاک", "زمان ورود", "گیت ورود", "ساعت توقف"])

    df = ins_df.copy()

    if "LongStay" in df.columns:
        df = df[df["LongStay"].astype(bool)].copy()
    elif "Age_H" in df.columns:
        df = df[classify_inside_age(df["Age_H"]) == "sediment"].copy()
    else:
        df = df.iloc[0:0].copy()
    # تطبیق و ساخت ستون‌های نمایشی فارسی
    res = pd.DataFrame()
    res["شناسه تردد"] = df["ID"] if "ID" in df.columns else df.index
    res["شماره کارت"] = df["CardID"] if "CardID" in df.columns else "-"
    
    # ستون پلاک
    if "PlateID" in df.columns:
        res["پلاک"] = df["PlateID"]
    elif "Plate" in df.columns:
        res["پلاک"] = df["Plate"]
    else:
        res["پلاک"] = "-"

    # زمان ورود
    entry_col = "EnrtyDateTime" if "EnrtyDateTime" in df.columns else ("EnterDateTime" if "EnterDateTime" in df.columns else None)
    res["زمان ورود"] = df[entry_col] if entry_col else "-"

    res["گیت ورود"] = df["InputGateID"] if "InputGateID" in df.columns else "-"

    # ساعت توقف
    if "Age_H" in df.columns:
        res["ساعت توقف"] = df["Age_H"].round(1)
    elif "StayHours" in df.columns:
        res["ساعت توقف"] = df["StayHours"].round(1)
    else:
        res["ساعت توقف"] = 0.0

    return res.sort_values("ساعت توقف", ascending=False).reset_index(drop=True)
# ============================================================ تاریخ شمسی
try:
    import jdatetime as _jd  # اختیاری: pip install jdatetime
except Exception:  # noqa: BLE001
    _jd = None
_FA_D = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _g2j(gy, gm, gd):
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
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def jalali_str(v, with_time=False, fa_digits=True) -> str:
    try:
        ts = pd.Timestamp(v)
    except Exception:  # noqa: BLE001
        return ""
    if pd.isna(ts):
        return ""
    if _jd is not None:
        j = _jd.date.fromgregorian(date=ts.date())
        y, m, d = j.year, j.month, j.day
    else:
        y, m, d = _g2j(ts.year, ts.month, ts.day)
    s = f"{y:04d}/{m:02d}/{d:02d}" + (f" {ts.hour:02d}:{ts.minute:02d}" if with_time else "")
    return s.translate(_FA_D) if fa_digits else s


# ============================================================ مدیریتی: روند و گلوگاه
def revenue_gaps(B: dict) -> pd.DataFrame:
    """شکاف درآمدی روزانه = تعرفه (پتانسیل) − وصول‌شده (تحقق‌یافته)، تجزیه‌ی دقیق (مجموع اجزا = شکاف):
      شکاف = تخفیف مجاز خالص + تخفیف بدون سند + کسری وصول − اضافه‌وصول + سایر
      سایر = (اختلاف دو منبع Taradods/View) + باقی‌مانده‌ی بازپرداخت/پوز بدون ثبت"""
    d = B["daily"].copy().sort_values("Date")
    d["authorized"] = d.waiver_doc - d.uplift
    d["gap"] = d.tariff - d.collected
    d["other"] = d.gap - (d.authorized + d.unexplained + d.under - d.over)
    net_potential = d.tariff - d.authorized
    d["realization_pct"] = np.where(d.tariff > 0, d.collected / d.tariff * 100, 0.0)
    d["gap_pct"] = np.where(d.tariff > 0, d.gap / d.tariff * 100, 0.0)
    d["leak_pct"] = np.where(net_potential > 0, d.loss / net_potential * 100, 0.0)   # فرار درآمدی نسبت به پتانسیل پس از تخفیف مجاز
    d["loss_pct"] = np.where(d.tariff > 0, d.loss / d.tariff * 100, 0.0)
    return d


def entry_exit_balance(tx: pd.DataFrame) -> pd.DataFrame:
    """عدم تطابق ورود/خروج روزانه"""
    ent = tx.groupby(tx.EnrtyDateTime.dt.normalize()).size()
    ext = tx[tx.Exited].groupby("Date").size()
    d = pd.DataFrame({"ورود": ent, "خروج": ext}).fillna(0).sort_index()
    d["مانده خالص"] = d["ورود"] - d["خروج"]
    d["مانده تجمعی"] = d["مانده خالص"].cumsum()
    d.index.name = "Date"
    return d.reset_index()

def daily_flow(tx: pd.DataFrame) -> pd.DataFrame:
    ex = tx[tx.Exited]
    d = pd.DataFrame({"ورود": tx.groupby(tx.EnrtyDateTime.dt.normalize()).size(), "خروج": ex.groupby("Date").size()}).fillna(0).sort_index()
    d["میانگین متحرک خروج (۷ روزه)"] = d["خروج"].rolling(7, min_periods=1).mean()
    d.index.name = "Date"
    return d.reset_index()


def hourly_profile(tx: pd.DataFrame) -> pd.DataFrame:
    e = tx[tx.Exited].dropna(subset=["Hour"])
    g = e.groupby("Hour")
    d = pd.DataFrame({"خروج": g.size(), "میانه ماندگاری (دقیقه)": g.Dur_Min.median()}).reindex(range(24))
    d["خروج"] = d["خروج"].fillna(0)
    d.index.name = "Hour"
    return d.reset_index()


def gate_hour_matrix(vw: pd.DataFrame) -> pd.DataFrame:
    e = vw[vw.Exited & vw.OutputGate.notna()].dropna(subset=["Hour"])
    if e.empty:
        return pd.DataFrame()
    m = e.pivot_table(index="OutputGate", columns="Hour", values="id", aggfunc="count").reindex(columns=range(24)).fillna(0)
    return m / max(e.Date.nunique(), 1)  # میانگین خروج در هر ساعت از شبانه‌روز


def gate_bottlenecks(vw: pd.DataFrame) -> pd.DataFrame:
    e = vw[vw.Exited & vw.OutputGate.notna()].dropna(subset=["Hour"])
    if e.empty:
        return pd.DataFrame(columns=["OutputGate", "میانگین", "P95", "اوج", "ضریب اوج"])
    h = e.groupby(["OutputGate", "Date", "Hour"]).size().rename("n").reset_index()
    g = h.groupby("OutputGate").agg(میانگین=("n", "mean"), P95=("n", lambda s: s.quantile(.95)), اوج=("n", "max")).reset_index()
    g["ضریب اوج"] = np.where(g["میانگین"] > 0, g["اوج"] / g["میانگین"], 0.0)
    return g.sort_values("P95", ascending=False)


# ============================================================ حراست: نرخ پرچم
def flag_rate_by_hour(ex: pd.DataFrame) -> pd.DataFrame:
    e = ex.dropna(subset=["Hour"])
    d = e.assign(_f=(e.NFlags > 0).astype(int)).groupby("Hour").agg(خروج=("ID", "size"), پرچم=("_f", "sum"), زیان=("Loss", "sum")).reindex(range(24)).fillna(0)
    d["نرخ پرچم (٪)"] = np.where(d["خروج"] > 0, d["پرچم"] / d["خروج"] * 100, 0.0)
    d.index.name = "Hour"
    return d.reset_index()


def flag_rate_by_gate(ex: pd.DataFrame, gates: pd.DataFrame | None = None) -> pd.DataFrame:
    if ex.empty:
        return pd.DataFrame(columns=["گیت", "خروج", "پرچم", "نرخ پرچم (٪)", "ضریب نسبت به میانگین"])
    d = ex.assign(_f=(ex.NFlags > 0).astype(int)).groupby("OutputGateID").agg(خروج=("ID", "size"), پرچم=("_f", "sum"), زیان=("Loss", "sum")).reset_index()
    d["گیت"] = gate_labels(d.OutputGateID, gates) if gates is not None else d.OutputGateID.astype(str)
    d["نرخ پرچم (٪)"] = d["پرچم"] / d["خروج"] * 100
    overall = (ex.NFlags > 0).mean() * 100
    d["ضریب نسبت به میانگین"] = d["نرخ پرچم (٪)"] / overall if overall else 0.0
    return d


def daily_flag_control(ex: pd.DataFrame) -> pd.DataFrame:
    """نمودار کنترل p برای نرخ روزانه‌ی پرچم؛ روزهای بالاتر از UCL غیرعادی‌اند"""
    if ex.empty:
        return pd.DataFrame(columns=["Date", "n", "k", "rate", "CL", "UCL", "LCL", "out"])
    d = ex.assign(_f=(ex.NFlags > 0).astype(int)).groupby("Date").agg(n=("ID", "size"), k=("_f", "sum")).reset_index()
    p = d.k.sum() / max(d.n.sum(), 1)
    se = np.sqrt(p * (1 - p) / d.n)
    d["rate"], d["CL"] = d.k / d.n * 100, p * 100
    d["UCL"], d["LCL"] = (p + 3 * se) * 100, np.clip((p - 3 * se) * 100, 0, None)
    d["out"] = d.rate > d.UCL
    return d


# ============================================================ اثر مالی واقعی اقدامات
def operator_exposure(vw: pd.DataFrame, names) -> float:
    names = [n for n in (names or []) if n is not None]
    if not names or vw is None or vw.empty:
        return 0.0
    x = vw[vw.Exited & vw.ExitUser.isin(names) & vw.IsFree & (vw.TotalPrice.fillna(0) > 0)]
    return float(x.TotalPrice.sum())


def impact_estimates(tx, vw, ins, dl=None, codes=None) -> dict:
    """هر مقدار: (مبلغ تومان، توضیح مبنای محاسبه)؛ همه از داده‌ی واقعی بازه"""
    ex = tx[tx.Exited] if tx is not None and len(tx) else pd.DataFrame(columns=["FinalPrice"])
    fee = ex.FinalPrice[ex.FinalPrice > 0]
    avg_fee = float(fee.mean()) if len(fee) else 0.0
    out = {}
    ls = int(ins.LongStay.sum()) if ins is not None and len(ins) and "LongStay" in ins else 0
    out["long_stay"] = (ls * avg_fee, f"{ls} خودرو × میانگین تعرفه‌ی خروج‌های واقعی ({avg_fee:,.0f} تومان)")
    dup = int(ins.DupCard.sum() + ins.DupPlate.sum()) if ins is not None and len(ins) and "DupCard" in ins else 0
    out["dup_open"] = (dup * avg_fee, f"{dup} ردیف تکراری × میانگین تعرفه ({avg_fee:,.0f} تومان)")
    # تخفیف واقعی هر تردد
    disc = pd.Series(dtype=float)
    avg_disc = 0.0
    if vw is not None and len(vw):
        w = vw.drop_duplicates("id").assign(_d=lambda x: (x.TotalPrice.fillna(0) - x.FinalPrice.fillna(0)).clip(lower=0))
        disc = w.set_index("id")["_d"]
        dsc = w[w.IsDiscount & w.Exited]["_d"]
        avg_disc = float(dsc.mean()) if len(dsc) else 0.0
    ra, rn = 0.0, 0
    if dl is not None and len(dl) and "DiscountCode" in dl:
        x = dl.dropna(subset=["DiscountCode"]).sort_values("InsertDate").copy()
        x["_rk"] = x.groupby("DiscountCode").cumcount()
        extra = x[x._rk >= 1]
        rn = len(extra)
        ra = float(pd.to_numeric(extra.TaradodID, errors="coerce").map(disc).fillna(0).sum())
    out["disc_reuse"] = (ra, f"{rn} استفاده‌ی دوم به بعد از کدهای تکراری، مجموع تخفیف واقعی همان ترددها")
    ex_n = 0
    if codes is not None and len(codes):
        c = over_limit_codes(codes)
        ex_n = int((c.UsedCount - c.MaxCount).clip(lower=0).sum()) if len(c) else 0
    out["over_limit"] = (ex_n * avg_disc, f"{ex_n} استفاده‌ی مازاد بر سقف × میانگین تخفیف واقعی هر تردد ({avg_disc:,.0f} تومان)")
    return out


def income_recon(ex: pd.DataFrame, ri: pd.DataFrame):
    """(جدول روزانه، مجموع |اختلاف| ، مجموع |اختلاف| در روزهای دارای ویرایش)"""
    if ri is None or ri.empty:
        return pd.DataFrame(), 0.0, 0.0
    s = ex.groupby("Date").agg(سامانه_نقد=("Cash", "sum"), سامانه_پوز=("Pos", "sum"))
    r0 = ri.assign(d=pd.to_datetime(ri.DateTime, errors="coerce").dt.normalize(), Pecuniary=pd.to_numeric(ri.Pecuniary, errors="coerce"), Pos=pd.to_numeric(ri.Pos, errors="coerce"))
    r = r0.groupby("d").agg(واقعی_نقد=("Pecuniary", "sum"), واقعی_پوز=("Pos", "sum"))
    m = s.join(r, how="outer").fillna(0)
    m["اختلاف"] = (m.واقعی_نقد + m.واقعی_پوز) - (m.سامانه_نقد + m.سامانه_پوز)
    edays = set(r0[r0.IsEdited.fillna(False).astype(bool)].d.dropna())
    return m.rename_axis("Date"), float(m["اختلاف"].abs().sum()), float(m.loc[m.index.isin(edays), "اختلاف"].abs().sum())


def cash_deficit_rate(m: pd.DataFrame):
    """سند: Cash_PMS − Deposited_Cash > 0 ؛ CDR = Σ کسری نقد ÷ Σ نقد سامانه × ۱۰۰ (آستانه‌ی هشدار ۱.۵٪).
    فقط روزهایی که درآمد واقعی (RealIncomes) ثبت شده؛ اضافه‌واریز کسری را جبران نمی‌کند. خروجی: (مبلغ کسری، CDR٪)"""
    if m is None or m.empty or "واقعی_نقد" not in m:
        return 0.0, 0.0
    x = m[(m.واقعی_نقد + m.واقعی_پوز) > 0]
    short = float((x.سامانه_نقد - x.واقعی_نقد).clip(lower=0).sum())
    base = float(x.سامانه_نقد.sum())
    return short, (short / base * 100 if base > 0 else 0.0)


SCEN = {"بدبینانه (۳۰٪)": .3, "محتمل (۶۰٪)": .6, "خوش‌بینانه (۹۰٪)": .9}


def scenario_table(pairs, n_days: int) -> pd.DataFrame:
    """pairs: [(عنوان اقدام، اثر در بازه)] ← بازیابی در بازه و برآورد سالانه در سه سناریو"""
    rows = [{"اقدام": t, "سناریو": s, "بازیابی در بازه": v * r, "برآورد سالانه": v * r * 365 / max(n_days, 1)} for t, v in pairs for s, r in SCEN.items()]
    return pd.DataFrame(rows, columns=["اقدام", "سناریو", "بازیابی در بازه", "برآورد سالانه"])


# ============================================================ عملیات: خودروهای داخل
STALE_H = 45 * 24  # ۱۰۸۰ ساعت؛ هم‌راستا با db.STALE_MAX_DAYS


def stay_buckets(ins: pd.DataFrame | None) -> pd.DataFrame:
    """رکوردهای باز (بدون خروج). رکوردهای بالای ۴۵ روز جدا و به‌عنوان خطای ثبت برچسب می‌خورند"""
    labels = ["کمتر از ۶ ساعت", "۶ تا ۲۴", "۲۴ تا ۴۸", "۴۸ تا ۷۲", "۷۲ تا ۱۶۸", "۷ تا ۴۵ روز", "بیش از ۴۵ روز (خطای ثبت)"]
    if ins is None or ins.empty:
        return pd.DataFrame({"رده": labels, "تعداد": 0})
    c = pd.cut(ins.Age_H.clip(lower=0), bins=[0, 6, 24, 48, 72, 168, STALE_H, np.inf], labels=labels, right=False)
    c = c.value_counts().reindex(labels).fillna(0).astype(int)
    return pd.DataFrame({"رده": labels, "تعداد": c.values})


def exit_stay_buckets(tx: pd.DataFrame) -> pd.DataFrame:
    """توزیع مدت توقف واقعی خودروهای خارج‌شده (Dur_Min = خروج − ورود)"""
    labels = ["تا ۳۰ دقیقه", "۳۰ دقیقه تا ۱ ساعت", "۱ تا ۳ ساعت", "۳ تا ۶ ساعت", "۶ تا ۲۴ ساعت", "۲۴ تا ۴۸ ساعت", "بیش از ۴۸ ساعت"]
    if tx is None or tx.empty or "Dur_Min" not in tx:
        return pd.DataFrame({"رده": labels, "تعداد": 0, "درصد": 0.0})
    h = pd.to_numeric(tx.loc[tx.Exited, "Dur_Min"], errors="coerce").dropna() / 60.0
    h = h[h >= 0]
    c = pd.cut(h, bins=[0, .5, 1, 3, 6, 24, 48, np.inf], labels=labels, right=False, include_lowest=True)
    c = c.value_counts().reindex(labels).fillna(0).astype(int)
    counts = c.to_numpy(dtype=float)
    tot = float(counts.sum())
    pct = np.round(counts / tot * 100, 1) if tot > 0 else np.zeros(len(labels))
    return pd.DataFrame({"رده": labels, "تعداد": counts.astype(int), "درصد": pct})

# ============================================================ فنی: ANPR / راهبند / فعالیت
def anpr_gaps(a: pd.DataFrame) -> pd.DataFrame:
    cols = ["GateId", "بیشترین فاصله (دقیقه)", "از", "تا"]
    if a.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    x = a.assign(CreateDate=pd.to_datetime(a.CreateDate, errors="coerce")).dropna(subset=["CreateDate"]).sort_values("CreateDate")
    for gid, d in x.groupby("GateId"):
        if len(d) < 2:
            continue
        t = d.CreateDate.reset_index(drop=True)
        gap = t.diff().dt.total_seconds() / 60
        i = int(gap.fillna(-1).to_numpy().argmax())
        rows.append((gid, round(float(gap.iloc[i]), 1), jalali_str(t.iloc[i - 1], True), jalali_str(t.iloc[i], True)))
    return pd.DataFrame(rows, columns=cols).sort_values(cols[1], ascending=False)


def anpr_outages(a: pd.DataFrame, min_gap_s: float = 120, top: int = 12, peak_q: float = 0.75) -> pd.DataFrame:
    """سند: Drop_Duration > 120s دقیقاً در پیک تردد. پیک = ساعت‌هایی که تعداد خوانش‌شان ≥ صدک peak_q توزیع ساعتی است؛
    وقفه‌ی بیش از min_gap_s ثانیه‌ای که شروعش در ساعت پیک باشد، مشکوک به قطعی/خرابکاری دوربین است."""
    cols = ["GateId", "از", "تا", "مدت (دقیقه)", "برچسب"]
    if a.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    x = a.assign(CreateDate=pd.to_datetime(a.CreateDate, errors="coerce")).dropna(subset=["CreateDate"]).sort_values("CreateDate")
    if x.empty:
        return pd.DataFrame(columns=cols)
    hc = x.CreateDate.dt.hour.value_counts().reindex(range(24)).fillna(0)
    cut = float(hc.quantile(peak_q))
   # تبدیل صریح کلیدها به عدد صحیح برای جلوگیری از خطای Type Checking
    peak = {int(str(h)) for h, v in hc.items() if v is not None and v > 0 and float(v) >= cut}

    for gid, d in x.groupby("GateId"):
        t = d.CreateDate.reset_index(drop=True)
        start, gap_s = t.shift(1), t.diff().dt.total_seconds()
        m = (gap_s > min_gap_s) & start.dt.hour.isin(peak)
        for i in gap_s[m].index:
            rows.append((gid, jalali_str(start[i], True), jalali_str(t[i], True), round(float(gap_s[i]) / 60, 1), f"گیت {gid} · {jalali_str(start[i], True)}"))
    return pd.DataFrame(rows, columns=cols).sort_values("مدت (دقیقه)", ascending=False).head(top)


def anpr_noise_matrix(a: pd.DataFrame) -> pd.DataFrame:
    x = a.assign(Confidence=_conf(a.Confidence), CreateDate=pd.to_datetime(a.CreateDate, errors="coerce")).dropna(subset=["Confidence", "CreateDate"])
    if x.empty:
        return pd.DataFrame()
    x["Hour"], x["_n"], x["_g"] = x.CreateDate.dt.hour, (x.Confidence < LOW_CONF) * 100.0, x.GateId.astype(str)
    return x.pivot_table(index="_g", columns="Hour", values="_n", aggfunc="mean").reindex(columns=range(24))


def anpr_daily(a: pd.DataFrame) -> pd.DataFrame:
    x = a.assign(Confidence=_conf(a.Confidence), CreateDate=pd.to_datetime(a.CreateDate, errors="coerce")).dropna(subset=["Confidence", "CreateDate"])
    if x.empty:
        return pd.DataFrame(columns=["Date", "میانگین اطمینان", "نرخ نویز (٪)", "خوانش"])
    x["Date"] = x.CreateDate.dt.normalize()
    g = x.groupby("Date")
    return pd.DataFrame({"میانگین اطمینان": g.Confidence.mean(), "نرخ نویز (٪)": g.Confidence.apply(lambda s: (s < LOW_CONF).mean() * 100), "خوانش": g.size()}).reset_index()


def barrier_daily_mix(bl: pd.DataFrame, topn: int = 6) -> pd.DataFrame:
    x = bl.assign(LogDateTime=pd.to_datetime(bl.LogDateTime, errors="coerce")).dropna(subset=["LogDateTime"])
    if x.empty:
        return pd.DataFrame(columns=["Date", "پیام", "تعداد"])
    msg = x.Message.astype(str).str.slice(0, 28)
    top = msg.value_counts().head(topn).index
    x["پیام"], x["Date"] = np.where(msg.isin(top), msg, "سایر"), x.LogDateTime.dt.normalize()
    return x.groupby(["Date", "پیام"]).size().reset_index(name="تعداد").sort_values("Date")


def barrier_gate_hour(bl: pd.DataFrame) -> pd.DataFrame:
    x = bl.assign(LogDateTime=pd.to_datetime(bl.LogDateTime, errors="coerce")).dropna(subset=["LogDateTime"])
    if x.empty:
        return pd.DataFrame()
    x["Hour"], x["_g"] = x.LogDateTime.dt.hour, x.GateId.astype(str)
    return x.pivot_table(index="_g", columns="Hour", values="Id", aggfunc="count").reindex(columns=range(24)).fillna(0)


def barrier_bursts(bl: pd.DataFrame, z: float = 3.0) -> pd.DataFrame:
    cols = ["GateId", "زمان", "تعداد", "Z"]
    x = bl.assign(LogDateTime=pd.to_datetime(bl.LogDateTime, errors="coerce")).dropna(subset=["LogDateTime"])
    if x.empty:
        return pd.DataFrame(columns=cols)
    x["slot"] = x.LogDateTime.dt.floor("h")
    h = x.groupby(["GateId", "slot"]).size().rename("تعداد").reset_index()
    g = h.groupby("GateId")["تعداد"]
    h["Z"] = ((h["تعداد"] - g.transform("mean")) / g.transform("std").replace(0, np.nan)).round(2)
    b = h[h.Z > z].sort_values("Z", ascending=False).head(50)
    b["زمان"] = b.slot.map(lambda v: jalali_str(v, True))
    return b[cols]


def barrier_message_table(bl: pd.DataFrame) -> pd.DataFrame:
    x = bl.assign(LogDateTime=pd.to_datetime(bl.LogDateTime, errors="coerce"))
    g = x.groupby("Message").agg(تعداد=("Id", "size"), اولین=("LogDateTime", "min"), آخرین=("LogDateTime", "max")).reset_index().sort_values("تعداد", ascending=False).head(15)
    g["سهم (٪)"] = (g["تعداد"] / max(len(x), 1) * 100).round(1)
    g["اولین"], g["آخرین"] = g["اولین"].map(lambda v: jalali_str(v, True)), g["آخرین"].map(lambda v: jalali_str(v, True))
    return g


def user_hour_matrix(df: pd.DataFrame, user_col: str, time_col: str, topn: int = 12) -> pd.DataFrame:
    """کاربر × ساعت (تعداد رویداد)"""
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.assign(_t=pd.to_datetime(df[time_col], errors="coerce")).dropna(subset=["_t", user_col])
    if x.empty:
        return pd.DataFrame()
    x["_u"] = x[user_col].astype(str)
    x = x[x._u.isin(x._u.value_counts().head(topn).index)]
    x["_h"] = x._t.dt.hour
    return x.pivot_table(index="_u", columns="_h", values="_t", aggfunc="count").reindex(columns=range(24)).fillna(0)


# ============================================================ مالی: نشت و انحراف
def leak_flows(B: dict):
    """گره‌ها و جریان‌های Sankey: تعرفه → (تخفیف مجاز / بدون سند / مبلغ نهایی) → (وصول‌شده / کسری وصول)"""
    wr = B["waiver_by_reason"]
    doc, undoc = wr["کد تخفیف"] + wr["مهمان"] + wr["اشتراکی"], wr["بدون سند"]
    base = max(B["tariff"] - doc - undoc, 0.0)
    labels = ["تعرفه", "افزوده", "تخفیف دارای سند", "تخفیف بدون سند", "مبلغ نهایی", "وصول‌شده", "کسری وصول"]
    fl = [(0, 2, doc, False), (0, 3, undoc, True), (0, 4, base, False), (1, 4, B["uplift"], False), (4, 5, max(B["billed_tx"] - B["under"], 0.0), False), (4, 6, B["under"], True)]
    return labels, [f for f in fl if f[2] > 0]


def daily_deviation(B: dict) -> pd.DataFrame:
    d = B["daily"].copy().sort_values("Date")
    d["انحراف"] = d.collected - d.billed
    mu, sd = d["انحراف"].mean(), d["انحراف"].std(ddof=0)
    d["UCL"], d["LCL"] = mu + 3 * sd, mu - 3 * sd
    d["out"] = (d["انحراف"] > d.UCL) | (d["انحراف"] < d.LCL)
    return d


def cashier_deviation(B: dict, min_exits: int = 20) -> pd.DataFrame:
    u = B["by_user_tx"].copy()
    u = u[u.خروج >= min_exits]
    if u.empty:
        return u
    u["نرخ زیان (٪ از مبلغ نهایی)"] = np.where(u.نهایی > 0, u.زیان / u.نهایی * 100, 0.0)
    u["Z"] = zscore(u["نرخ زیان (٪ از مبلغ نهایی)"])
    u["شناسه"] = u.ExitUserID.astype(str)
    return u

# ============================================================ تطبیق با سند: فرجه، پلاک، خروج شبح، ظرفیت، Health Score
def grace_summary(ex: pd.DataFrame, grace: float = GRACE_MIN, min_exits: int = 20, z_thr: float = Z_OUTLIER):
    """Grace% = خروج‌های بدون پرداخت با توقف ≤ فرجه / کل خروج؛ پرچم اپراتور: Z > 3σ"""
    e = ex[ex.Exited].copy()
    e["Grace"] = e.Dur_Min.between(0, grace) & ((e.Cash.fillna(0) + e.Pos.fillna(0)) == 0)
    rate = float(e.Grace.mean() * 100) if len(e) else 0.0
    cols = ["شناسه", "خروج", "فرجه", "نرخ فرجه (٪)", "Z", "پرچم"]
    if e.empty:
        return rate, pd.DataFrame(columns=cols)
    g = e.groupby("ExitUserID").agg(خروج=("ID", "size"), فرجه=("Grace", "sum")).reset_index()
    g = g[g.خروج >= min_exits].copy()
    g["نرخ فرجه (٪)"] = g.فرجه / g.خروج * 100
    g["Z"] = zscore(g["نرخ فرجه (٪)"]) if len(g) >= 3 else 0.0
    g["پرچم"] = g.Z > z_thr
    g["شناسه"] = g.ExitUserID.astype(str)
    return rate, g[cols].sort_values("Z", ascending=False)


def plate_mismatch(ex: pd.DataFrame, anpr: pd.DataFrame | None = None):
    """ناهمخوانی پلاک ورود و خروج (ExitPlateID ≠ PlateID)؛ معادل «انحراف ثبت پلاک ناهمخوان / MOE».
    راهنمای کالیبراسیون سند: خوانش‌های با اطمینان < ۰.۶۰ نویز سخت‌افزاری‌اند و از شاخص تقلب عمدی حذف می‌شوند
    (اطمینان از ANPRPlates با کلید ANPRPlateId / ExitANPRPlateID خوانده می‌شود؛ اگر موجود نبود، حذفی انجام نمی‌شود)."""
    e = ex[ex.Exited]
    a, b = pd.to_numeric(e.PlateID, errors="coerce"), pd.to_numeric(e.ExitPlateID, errors="coerce")
    m = a.notna() & b.notna() & (a > 0) & (b > 0) & (a != b)
    if anpr is not None and len(anpr) and {"Id", "Confidence"} <= set(anpr.columns) and {"ANPRPlateId", "ExitANPRPlateID"} <= set(e.columns):
        cm = anpr.assign(Id=pd.to_numeric(anpr.Id, errors="coerce"), Confidence=_conf(anpr.Confidence)).dropna(subset=["Id"]).drop_duplicates("Id").set_index("Id")["Confidence"]
        cin, cout = pd.to_numeric(e.ANPRPlateId, errors="coerce").map(cm), pd.to_numeric(e.ExitANPRPlateID, errors="coerce").map(cm)
        m = m & ~((cin < LOW_CONF) | (cout < LOW_CONF))
    return m, float(m.mean() * 100) if len(e) else 0.0


def ghost_exits(ex: pd.DataFrame, audit: pd.DataFrame, max_min: float = GHOST_MAX_MIN, min_stay_h: float = GHOST_MIN_STAY_H) -> pd.DataFrame:
    """Stay < 15m & Historical_In_Park: خروج کوتاه‌مدت پلاکی که رکورد رسوبی قدیمی (≥۴۸ ساعت، باطل‌شده با تردد جدید) دارد"""
    cols = ["ID", "PlateID", "EnrtyDateTime", "ExitDateTime", "Dur_Min", "ExitUserID", "OldEntry", "OldStayH"]
    if ex is None or ex.empty or audit is None or audit.empty or "AuditCode" not in audit:
        return pd.DataFrame(columns=cols)
    e = ex[ex.Exited & (ex.Dur_Min >= 0) & (ex.Dur_Min < max_min)].assign(PlateID=lambda x: pd.to_numeric(x.PlateID, errors="coerce")).dropna(subset=["PlateID"])
    a = audit.assign(AuditCode=pd.to_numeric(audit.AuditCode, errors="coerce"), PlateID=pd.to_numeric(audit.PlateID, errors="coerce"),
                     OldEntry=pd.to_datetime(audit.EnrtyDateTime, errors="coerce"))
    a = a[(a.AuditCode == 2) & a.PlateID.notna()][["PlateID", "OldEntry"]]
    m = e.merge(a, on="PlateID")
    if m.empty:
        return pd.DataFrame(columns=cols)
    m["OldStayH"] = ((m.EnrtyDateTime - m.OldEntry).dt.total_seconds() / 3600).round(1)
    m = m[m.OldStayH >= min_stay_h].sort_values("OldStayH", ascending=False).drop_duplicates("ID")
    return m[cols].reset_index(drop=True)


def capacity_drift(system_count: int, physical=None, capacity=None, thr: int = CAP_THR) -> dict:
    """Abs(Physical − System) > ±2؛ همچنین منفی بودن یا عبور از ظرفیت اسمی"""
    drift = abs(physical - system_count) if physical is not None else None
    return dict(system=int(system_count), physical=physical, drift=drift, flag=bool(drift is not None and drift > thr),
                over=bool(capacity is not None and system_count > capacity), negative=bool(system_count < 0))


def health_score(ex: pd.DataFrame, B: dict) -> dict:
    """Score = 100 − (Weighted_Anomaly_Rate × 100)
    Weighted_Anomaly_Rate = ۰.۵ × میانگین(Risk_Score/100) + ۰.۵ × (زیان مشکوک / تعرفه)"""
    n = len(ex)
    w = float((ex.Risk_Score.fillna(0) / 100).mean()) if n else 0.0
    ls = min(B["loss"] / B["tariff"], 1.0) if B["tariff"] > 0 else 0.0
    war = 0.5 * w + 0.5 * ls
    score = float(np.clip(100 - war * 100, 0, 100))
    state = "ok" if score >= HEALTH_OK else ("warn" if score >= HEALTH_WARN else "bad")
    return dict(score=score, war=war, w_rate=w, loss_share=ls, anomaly_pct=float((ex.NFlags > 0).mean() * 100) if n else 0.0, state=state)


def health_daily(ex: pd.DataFrame, B: dict) -> pd.DataFrame:
    e = ex.dropna(subset=["Date"])
    a = e.groupby("Date").agg(n=("ID", "size"), w=("Risk_Score", lambda s: (s.fillna(0) / 100).mean()), flagged=("NFlags", lambda s: (s > 0).mean() * 100))
    m = a.join(B["daily"][["Date", "tariff", "loss"]].set_index("Date"), how="left").fillna(0)
    ls = np.where(m.tariff > 0, np.minimum(m.loss / m.tariff, 1), 0.0)
    m["score"] = np.clip(100 - (0.5 * m.w + 0.5 * ls) * 100, 0, 100)
    return m.reset_index()
def leak_breakdown(B: dict, tx: pd.DataFrame, grace: float = GRACE_MIN) -> dict:
    """تجزیه‌ی نشت درآمد (بدون شمارش مضاعف): ناخالص پتانسیل → کسورات → درآمد خالص محقق‌شده"""
    e = tx[tx.Exited] if tx is not None and len(tx) else pd.DataFrame()
    plate_amt = grace_under = 0.0
    if len(e):
        under_rows = e.LossType == "کسری وصول"
        pm, _ = plate_mismatch(tx)
        pm = pm.reindex(e.index, fill_value=False)
        gr = e.Dur_Min.between(0, grace) & ((e.Cash.fillna(0) + e.Pos.fillna(0)) == 0)
        plate_amt = float(e.loc[under_rows & pm, "Loss"].sum())
        grace_under = float(e.loc[under_rows & ~pm & gr, "Loss"].sum())
    plate_amt = min(plate_amt, B["under"])
    grace_under = min(grace_under, B["under"] - plate_amt)
    cash_short = B["under"] - plate_amt - grace_under
    grace_loss = B["unexplained"] + grace_under
    reg_err = B["posnd"] + B["components"]["بازپرداخت"]
    authorized = B["explained"] - B["uplift"]
    gross, net = B["tariff"], B["collected"]
    other = net - (gross - authorized - grace_loss - cash_short - plate_amt - reg_err + B["over"])
    return dict(gross=gross, authorized=authorized, grace_loss=grace_loss, cash_short=cash_short, plate=plate_amt,
                reg_err=reg_err, over=B["over"], other=other, net=net)

# ============================================================ گیت‌ها: مرجع dbo.Gates / dbo.GateTypes
# توجه: در فایل روابط (Relationships) هیچ FK ای برای Gates ثبت نشده است؛ نگاشت InputGateID/OutputGateID ← Gates.ID بر پایه‌ی دستور پروژه است
# و درستی آن با گزارش «شناسه‌های بدون نگاشت» (gate_quality) در هر بار اجرا سنجیده می‌شود. معنای GateTypeID حدس زده نمی‌شود؛ فقط عنوان GateTypes نمایش داده می‌شود.
GATE_COLS = ["ID", "Title", "GateTypeID", "GateTypeTitle", "Label"]
UNKNOWN_GATE = "نامشخص (ID: {})"


def _gate_id(v):
    n = pd.to_numeric(v, errors="coerce")
    return None if pd.isna(n) else int(n)


def prepare_gates(gates: pd.DataFrame | None, gate_types: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """(جدول گیت‌ها با ستون Label یکتا، گزارش کیفیت). ردیف‌های تکراریِ ID حذف نمی‌شوند مگر اینکه عیناً تکرار باشند؛ اولین ردیف نگاشت را می‌سازد و تکرار گزارش می‌شود."""
    q = {"dup_gate_ids": [], "dup_type_ids": [], "type_orphans": [], "untitled": [], "dup_titles": [], "gates_unavailable": False}
    if gates is None or len(gates) == 0:
        q["gates_unavailable"] = True
        return pd.DataFrame(columns=GATE_COLS), q
    g = gates.copy()
    g["ID"] = pd.to_numeric(g["ID"], errors="coerce")
    g = g.dropna(subset=["ID"]).astype({"ID": int})
    q["dup_gate_ids"] = sorted(g.loc[g.duplicated("ID", keep=False), "ID"].unique().tolist())
    g = g.drop_duplicates("ID", keep="first")
    tmap = {}
    if gate_types is not None and len(gate_types):
        t = gate_types.copy()
        t["ID"] = pd.to_numeric(t["ID"], errors="coerce")
        t = t.dropna(subset=["ID"]).astype({"ID": int})
        q["dup_type_ids"] = sorted(t.loc[t.duplicated("ID", keep=False), "ID"].unique().tolist())
        tmap = dict(zip(t.drop_duplicates("ID").ID, t.drop_duplicates("ID").Title.astype("string").str.strip()))
    g["GateTypeID"] = pd.to_numeric(g["GateTypeID"], errors="coerce")
    g["GateTypeTitle"] = g.GateTypeID.map(lambda v: tmap.get(int(v)) if pd.notna(v) else None)
    q["type_orphans"] = sorted(g.loc[g.GateTypeTitle.isna() & g.GateTypeID.notna(), "GateTypeID"].astype(int).unique().tolist())
    title = g["Title"].astype("string").str.strip()
    blank = title.isna() | (title == "")
    q["untitled"] = g.loc[blank, "ID"].tolist()
    q["dup_titles"] = sorted(title[~blank][title[~blank].duplicated(keep=False)].unique().tolist())
    g["Label"] = [(f"{t} (ID: {i})" if (not b and t in q["dup_titles"]) else (f"بدون عنوان (ID: {i})" if b else str(t))) for t, i, b in zip(title, g.ID, blank)]
    g = g.sort_values("ID").reset_index(drop=True)
    return g, q


def gate_labels(ids: pd.Series, gates: pd.DataFrame | None) -> pd.Series:
    """نام نمایشی گیت از Gates.Title؛ ID بدون نگاشت ← «نامشخص (ID: n)»؛ مقدار تهی ← تهی. اگر Gates در دسترس نباشد، همان ID متنی می‌ماند."""
    s = pd.Series(ids)
    if gates is None or len(gates) == 0 or "Label" not in gates:
        return s.astype(str)
    nm = dict(zip(gates.ID.astype(int), gates.Label))

    def one(v):
        if isinstance(v, str) and not v.strip().lstrip("-").isdigit():
            return v  # مقدار متنی غیرعددی (مثلاً «-») دست‌نخورده می‌ماند
        n = _gate_id(v)
        return None if n is None else nm.get(n, UNKNOWN_GATE.format(n))
    return s.map(one)


def apply_gate_names(df: pd.DataFrame, gates: pd.DataFrame | None, in_name: str = "InputGate", out_name: str = "OutputGate") -> tuple[pd.DataFrame, dict]:
    """افزودن/جایگزینی نام ورود/خروج از Gates.Title بدون تغییر تعداد ردیف. نقش ورودی/خروجی از ستون InputGateID/OutputGateID حفظ می‌شود؛
    نام خروج فقط برای ترددِ خارج‌شده ساخته می‌شود (خروجِ نشده ← تهی). نام قبلی (مثلاً از View) در ستون *_View نگه داشته می‌شود."""
    d = df.copy()
    rep = {"input": {}, "output": {}, "applied": False}
    if gates is None or len(gates) == 0 or "Label" not in gates:
        return d, rep
    nm = dict(zip(gates.ID.astype(int), gates.Label))
    for idc, name, key in (("InputGateID", in_name, "input"), ("OutputGateID", out_name, "output")):
        if idc not in d.columns:
            continue
        if name in d.columns:
            d[name + "_View"] = d[name]
        ids = d[idc].map(_gate_id)
        mask = d["Exited"].astype(bool) if (key == "output" and "Exited" in d.columns) else (d["ExitDateTime"].notna() if (key == "output" and "ExitDateTime" in d.columns) else pd.Series(True, index=d.index))
        lab = ids.map(lambda n: None if n is None else nm.get(n, UNKNOWN_GATE.format(n)))
        d[name] = lab.where(mask)
        miss = ids[mask & ids.notna() & ~ids.map(lambda n: n in nm if n is not None else True)]
        rep[key] = miss.value_counts().to_dict()
    rep["applied"] = True
    assert len(d) == len(df), "apply_gate_names نباید تعداد ردیف را تغییر دهد"
    return d, rep


def label_gate_column(df: pd.DataFrame, gates: pd.DataFrame | None, col: str = "GateId") -> pd.DataFrame:
    """برای ANPR/راهبند: ستون GateId (عدد) را با نام گیت جایگزین می‌کند؛ مقدار تهی دست‌نخورده می‌ماند (رفتار فعلی groupby)."""
    if df is None or df.empty or col not in df.columns or gates is None or len(gates) == 0:
        return df
    d = df.copy()
    d[col] = gate_labels(d[col], gates).where(d[col].notna())
    return d


def gate_filter_options(gates: pd.DataFrame | None, vw: pd.DataFrame) -> pd.DataFrame:
    """فهرست گزینه‌های «گیت خروج»: همه‌ی گیت‌های dbo.Gates (حتی بدون تردد) + شناسه‌های خروجِ مشاهده‌شده که در Gates نیستند.
    ستون‌ها: OutputGateID, OutputGate (نمایشی), Exits (خروج در داده‌ی بازه)."""
    ex = vw[vw.Exited] if "Exited" in vw.columns else vw
    cnt = ex["OutputGateID"].map(_gate_id).dropna().astype(int).value_counts() if len(ex) else pd.Series(dtype=int)
    rows = []
    known = set()
    if gates is not None and len(gates):
        for i, lab in zip(gates.ID.astype(int), gates.Label):
            known.add(i)
            rows.append((i, lab, int(cnt.get(i, 0))))
    # داخل gate_filter_options (به‌جای حلقهٔ for i, n in cnt.items())
    for i, n in {int(str(k)): int(v) for k, v in cnt.items()}.items():
        if i not in known:
            rows.append((i, UNKNOWN_GATE.format(i), n))
    o = pd.DataFrame(rows, columns=["OutputGateID", "OutputGate", "Exits"])
    o["OutputGate"] = [f"{l} · بدون خروج در بازه" if n == 0 else l for l, n in zip(o.OutputGate, o.Exits)]
    return o.sort_values("OutputGate").reset_index(drop=True)


def gate_coverage(gates: pd.DataFrame | None, vw: pd.DataFrame, d0=None, d1=None) -> tuple[pd.DataFrame, dict]:
    """جدول وضعیت هر گیت + شمارش‌ها. تعداد «تعریف‌شده» از Gates است و «مشاهده‌شده» از ترددهای بازه؛ این دو جدا گزارش می‌شوند.
    ورود: ردیف‌هایی که EnrtyDateTime‌شان در [d0, d1) است (روی InputGateID)؛ خروج: ExitDateTime در [d0, d1) (روی OutputGateID)."""
    v = vw
    en = v["EnrtyDateTime"] if "EnrtyDateTime" in v else pd.Series(pd.NaT, index=v.index)
    xt = v["ExitDateTime"] if "ExitDateTime" in v else pd.Series(pd.NaT, index=v.index)
    me = en.notna() & ((en >= d0) if d0 is not None else True) & ((en < d1) if d1 is not None else True)
    mx = xt.notna() & ((xt >= d0) if d0 is not None else True) & ((xt < d1) if d1 is not None else True)
    ent = v.loc[me, "InputGateID"].map(_gate_id).dropna().astype(int).value_counts()
    ext = v.loc[mx, "OutputGateID"].map(_gate_id).dropna().astype(int).value_counts()
    # داخل gate_coverage (به‌جای سه خط defined / lab / gt)
    has_gates = gates is not None and len(gates) > 0
    defined = set(gates.ID.astype(int)) if gates is not None and has_gates else set()
    seen_in, seen_out = set(ent.index), set(ext.index)
    ids = sorted(defined | seen_in | seen_out)
    lab = dict(zip(gates.ID.astype(int), gates.Label)) if gates is not None and has_gates else {}
    gt = dict(zip(gates.ID.astype(int), gates.GateTypeTitle)) if gates is not None and has_gates else {}
    rows = []
    for i in ids:
        e, x = int(ent.get(i, 0)), int(ext.get(i, 0))
        rows.append({"ID": i, "گیت": lab.get(i, UNKNOWN_GATE.format(i)), "نوع گیت (GateTypes)": gt.get(i), "در Gates": i in defined, "ورود در بازه": e, "خروج در بازه": x,
                     "وضعیت": ("فقط در ترددها (بدون نگاشت)" if i not in defined else ("دارای تردد" if e + x else "بدون تردد در بازه"))})
    tab = pd.DataFrame(rows, columns=["ID", "گیت", "نوع گیت (GateTypes)", "در Gates", "ورود در بازه", "خروج در بازه", "وضعیت"])
    summ = {"defined": len(defined), "observed_in": len(seen_in), "observed_out": len(seen_out), "observed_any": len(seen_in | seen_out),
            "defined_no_traffic": len(defined - seen_in - seen_out), "unmapped_ids": sorted((seen_in | seen_out) - defined)}
    return tab, summ


def gate_quality(q: dict, summ: dict, rep: dict | None = None) -> pd.DataFrame:
    """گزارش کیفیت داده‌ی گیت‌ها (بی‌صدا حذف نمی‌کند؛ همه‌ی موارد را فهرست می‌کند)."""
    r = []
    if q.get("gates_unavailable"):
        r.append(("dbo.Gates در دسترس/خالی است", "-", "نام‌ها از View (در صورت وجود) یا «نامشخص» نمایش داده می‌شود"))
    for i in q.get("dup_gate_ids", []):
        r.append(("ID تکراری در Gates", i, "اولین ردیف برای نگاشت استفاده شد؛ نیازمند بررسی"))
    for i in q.get("dup_type_ids", []):
        r.append(("ID تکراری در GateTypes", i, "اولین ردیف استفاده شد"))
    for i in q.get("type_orphans", []):
        r.append(("GateTypeID بدون ردیف در GateTypes", i, "نوع گیت نامشخص ماند"))
    for i in q.get("untitled", []):
        r.append(("گیت بدون عنوان", i, "با «بدون عنوان (ID)» نمایش داده شد"))
    for t in q.get("dup_titles", []):
        r.append(("عنوان تکراری برای چند گیت", t, "برای تفکیک، ID به نام افزوده شد"))
    for i in summ.get("unmapped_ids", []):
        r.append(("ID در ترددها ولی نه در Gates", i, "با «نامشخص (ID: n)» نمایش داده شد؛ ردیف حذف نشد"))
    for k, lbl in (("input", "ورود"), ("output", "خروج")):
        for i, n in ((rep or {}).get(k) or {}).items():
            r.append((f"ردیف‌های {lbl} با ID بدون نگاشت", i, f"{n:,} ردیف"))
    return pd.DataFrame(r, columns=["نوع مورد", "ID/مقدار", "توضیح"])
