"""سنجمان · داشبورد ممیزی و کشف تقلب پارکینگ — اتصال مستقیم و فقط‌خواندنی به SQL Server (ParkingDB)
اجرا: streamlit run app.py     (اتصال: .streamlit/secrets.toml → [connections.mssql])

ساختار جدید رابط کاربری:
  • صفحه‌ی اصلی: فقط دو کارت اصلی (درآمد و زیان مشکوک) + آیکون‌های داشبوردها
  • با کلیک روی هر آیکون، داشبورد مخصوص همان بخش باز می‌شود (بدون کارت‌های بالای صفحه)
  • همه‌ی داشبوردها طوری چیده شده‌اند که در یک صفحه جا شوند (بدون اسکرول)؛ اندازه‌ی نمودارها از «اندازه‌ی صفحه نمایش» در سایدبار تنظیم می‌شود
محاسبات مالی دقیق: جمع‌ها در خود SQL Server (کوئری‌های تجمیعی fin_tx / fin_view) محاسبه می‌شوند؛ فرمول‌ها در analytics.py مستند شده‌اند.
"""
import re
from pathlib import Path
from urllib.parse import quote
from typing import Any, cast
import numpy as np  # noqa: F401
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import textwrap
import datetime as _dtm
WHITE = "#FFFFFF"

def wrap(text, width=25):
    if not text:
        return ""
    return "<br>".join(textwrap.wrap(str(text), width=width))

st.set_page_config(page_title="سنجمان | پایش پارکینگ", page_icon="▦", layout="wide")

# ------------------------------------------------------------ پالت (استخراج‌شده از لوگو)
SLATE, DEEP, SURF = "#455E62", "#2C4144", "#365054"
YEL, MINT, TEAL, GRN, CORAL = "#FFE08A", "#88CFC0", "#4FC0A8", "#1BB08E", "#FF8A7A"
TXT, SUB = "#FFFFFF", "#FFFFFF"  # در تم تیره همه‌ی متن‌ها و لیبل‌ها کاملاً سفید
STATE_C = {"ok": GRN, "warn": YEL, "bad": CORAL}
STATE_C["neutral"] = "#9AA9AB"
FONT_PL = "BNazanin, 'B Nazanin', Vazirmatn, sans-serif"
PCOL = {"فوری": CORAL, "مهم": YEL, "پایش": MINT}
HOVER = dict(bgcolor="#FFFFFF", bordercolor="#C9D1D3", font=dict(family=FONT_PL, size=13, color="#000000"))
PALETTE = [YEL, MINT, TEAL, GRN, CORAL]

# ------------------------------------------------------------ اعداد فارسی
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(s):
    """ارقام لاتین را به فارسی تبدیل می‌کند (فقط برای متن؛ نه برای رشته‌های حاوی رنگ/استایل)"""
    return re.sub(r"\d+(?:[,.]\d+)*", lambda m: m.group().translate(_FA).replace(",", "٬").replace(".", "٫"), str(s))


def num(v, d=0):
    return fa(f"{v:,.{d}f}")
try:
    import jdatetime as _jdt  # اختیاری؛ در نبود آن از تبدیل داخلی analytics استفاده می‌شود
except Exception:  # noqa: BLE001
    _jdt = None


def _sf(v, default=0.0):
    """تبدیل ایمن به float؛ NaN/inf/None → مقدار پیش‌فرض"""
    try:
        v = float(v)
        return default if (v != v or v in (float("inf"), float("-inf"))) else v
    except Exception:  # noqa: BLE001
        return default


def _pct(df, col):
    """درصد میانگین یک ستون پرچم؛ در خالی بودن داده ۰"""
    try:
        if df is None or len(df) == 0 or col not in df:
            return 0.0
        return _sf(pd.to_numeric(df[col], errors="coerce").mean() * 100)
    except Exception:  # noqa: BLE001
        return 0.0


def _to_jalali(dt, fa_digits=True):
    """تاریخ میلادی → رشته‌ی شمسی YYYY/MM/DD"""
    try:
        if dt is None or pd.isna(dt):
            return ""
        ts = pd.Timestamp(dt)
        if _jdt is not None:
            j = _jdt.date.fromgregorian(date=ts.date())
            y, m, d = j.year, j.month, j.day
        else:
            import analytics as _an
            y, m, d = _an._g2j(ts.year, ts.month, ts.day)
        s = f"{y:04d}/{m:02d}/{d:02d}"
        return s.translate(_FA) if fa_digits else s
    except Exception:  # noqa: BLE001
        return str(dt)


def _xlab(c):
    """برچسب محور برای ستون‌های تاریخ‌دار (میلادی → شمسی)، در غیر این صورت ارقام فارسی"""
    if isinstance(c, (_dtm.date, pd.Timestamp)) or (isinstance(c, str) and re.match(r"^\d{4}-\d{2}-\d{2}", c)):
        return _to_jalali(c)
    return fa(c)


def _is_dt_array(a):
    if a.size == 0:
        return False
    if np.issubdtype(a.dtype, np.datetime64):
        return True
    if a.dtype == object:
        v = next((x for x in a if x is not None and not (isinstance(x, float) and x != x)), None)
        return isinstance(v, (_dtm.datetime, _dtm.date, pd.Timestamp))
    return False


def _jalali_axis(fig):
    """محور X همه‌ی ترِیس‌های تاریخ‌دار را شمسی می‌کند (ترتیب زمانی حفظ می‌شود)"""
    try:
        seen = {}
        for tr in fig.data:
            if getattr(tr, "type", "") in ("sankey", "indicator", "heatmap", "scatterpolar", "pie"):
                continue
            x = getattr(tr, "x", None)
            if x is None:
                continue
            a = np.asarray(x)
            if not _is_dt_array(a):
                continue
            ts = pd.to_datetime(pd.Series(a), errors="coerce")
            labels = [_to_jalali(v) for v in ts]
            for t_, l_ in zip(ts, labels):
                if pd.notna(t_):
                    seen[l_] = t_
            tr.x = labels
        if seen:
            order = [k for k, _ in sorted(seen.items(), key=lambda kv: kv[1])]
            fig.update_xaxes(type="category", categoryorder="array", categoryarray=order, tickangle=-45)
    except Exception:  # noqa: BLE001
        pass
    return fig
def _tbl(df, columns=None, height=300, empty_msg="داده‌ای برای نمایش وجود ندارد."):
    if df is None or (hasattr(df, "empty") and df.empty):
        st.info(empty_msg)
        return

    if columns:
        cols = [c for c in columns if c in df.columns]
        display_df = df[cols] if cols else df
    else:
        display_df = df

    st.dataframe(display_df, use_container_width=True, height=height)


ROW_PX = 35  # ارتفاع هر ردیف st.dataframe (px)


def fit_df(df, max_rows=15, csv_name=None, **kw):
    """جدول بدون اسکرول داخلی: ارتفاع دقیقاً به اندازه‌ی ردیف‌ها؛ فقط max_rows ردیف اول نمایش داده می‌شود و فهرست کامل با CSV (csv_name) در دسترس است"""
    n = min(len(df), max_rows)
    st.dataframe(df.head(max_rows), hide_index=True, use_container_width=True, height=ROW_PX * (n + 1) + 3, **kw)
    if len(df) > max_rows:
        st.caption(f"{num(max_rows)} مورد اول از {num(len(df))} مورد نمایش داده شد" + ("؛ فهرست کامل در فایل CSV." if csv_name else "."))
    if csv_name and len(df):
        st.download_button("دانلود CSV", df.to_csv(index=False).encode("utf-8-sig"), f"{csv_name}.csv", "text/csv", key=f"dl_{csv_name}", use_container_width=True)


CSS = """

@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
@font-face{font-family:'BNazanin';src:local('B Nazanin'),local('BNazanin'),url('app/static/BNazanin.ttf') format('truetype');font-display:swap}
@font-face{font-family:'BNazanin';font-weight:700;src:local('B Nazanin Bold'),local('BNazanin-Bold'),url('app/static/BNazanin-Bold.ttf') format('truetype');font-display:swap}
html{font-size:100%}
html,body{overflow-x:hidden}
/* --- فونت B Nazanin و Bold برای کل داشبورد (متن، عنوان، جدول، ورودی‌ها، پنجره‌های بازشو، نمودارها)؛ آیکون‌های Material مستثنا هستند --- */
html,body,.stApp,button,input,textarea,select,[data-testid="stSidebar"]{font-family:'BNazanin','B Nazanin','Vazirmatn',Tahoma,sans-serif!important;font-weight:700!important}
.stApp *:not([data-testid="stIconMaterial"]):not([class*="material"]):not([class*="Material"]),
[data-baseweb="popover"] *:not([data-testid="stIconMaterial"]),[data-testid="stDialog"] *:not([data-testid="stIconMaterial"]),[role="dialog"] *:not([data-testid="stIconMaterial"]){
 font-family:'BNazanin','B Nazanin','Vazirmatn',Tahoma,sans-serif!important;font-weight:700!important}
[data-testid="stIconMaterial"]{font-family:'Material Symbols Rounded','Material Icons'!important;font-weight:400!important}
.js-plotly-plot .plotly text,.stApp svg text{font-family:'BNazanin','B Nazanin','Vazirmatn',Tahoma,sans-serif!important;font-weight:700!important;fill:#FFFFFF!important}
/* --- متن کاملاً سفید در تم تیره --- */
.stApp,.stApp p,.stApp label,.stApp li,.stApp td,.stApp th,.stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp h5,.stApp h6,.stApp small,
[data-testid="stMarkdownContainer"] *,[data-testid="stWidgetLabel"] *,[data-testid="stCaptionContainer"] *,[data-testid="stRadio"] *,[data-testid="stCheckbox"] *,
[data-testid="stSidebar"] *,[data-testid="stExpander"] *,[data-testid="stMetric"] *,[data-testid="stAlert"] *,[data-baseweb="popover"] *,[data-baseweb="select"] *,
[data-testid="stDialog"] *,input,textarea,.stApp .kpi *,.stApp .fc *,.stApp .tile *,.stApp .note *,.stApp .acard *,.stApp .ban *,.stApp .sumrow *,.stApp .ct *{color:#FFFFFF!important}
::placeholder{color:#FFFFFF!important;opacity:.7}
.stApp,.block-container,[data-testid="stSidebar"]{direction:rtl;text-align:right}
.stApp{background:@DEEP@;color:@TXT@}
header[data-testid="stHeader"],footer,#MainMenu{display:none}
.block-container{padding:.5rem 1rem 0!important;max-width:100%!important}
[data-testid="stVerticalBlock"]{gap:.45rem}
[data-testid="stSidebar"]{background:@SLATE@;border-left:1px solid rgba(255,255,255,.08)}
[data-testid="stVerticalBlockBorderWrapper"]{background:@SURF@;border:1px solid rgba(255,255,255,.08)!important;border-radius:18px!important}
/* --- سرتیتر داشبورد (فشرده) --- */
.ban{--ac:@YEL@;display:flex;align-items:center;gap:18px;background:linear-gradient(120deg,@SLATE@,@SURF@);border:1px solid rgba(255,255,255,.1);border-radius:18px;padding:12px 22px;position:relative;overflow:hidden;min-height:68px;height:auto;flex-shrink:0;box-sizing:border-box;margin:0 0 16px}
.ban::before{content:'';position:absolute;inset:0 auto 0 0;width:7px;background:var(--ac)}
.ban h1{font-size:1.25rem;margin:0;font-weight:800;white-space:nowrap;color:var(--ac)}
.ban .vd p{margin:0;font-size:.95rem;line-height:1.85;color:@TXT@;display:block;overflow:visible}
.ban .vd p b{color:var(--ac)}
/* --- کارت‌های اصلی صفحه‌ی اصلی: اعداد با فونت فارسی بی‌نازنین --- */
.kpi{--ac:@TEAL@;background:@SURF@;border:1px solid rgba(255,255,255,.08);border-radius:22px;padding:14px 18px;height:100%;min-width:0;overflow:hidden}
.kpi .kl{color:@SUB@;font-size:.95rem;display:block}
.kpi b{font-family:'BNazanin','B Nazanin',serif!important;font-size:clamp(1.3rem,2.2vw,1.9rem);font-weight:700;display:block;margin:4px 0;white-space:nowrap;direction:rtl;overflow:hidden;text-overflow:ellipsis}
.kpi b small{font-size:.45em;font-weight:600;color:@SUB@}
.kpi .kh{font-size:.85rem;color:@SUB@;display:inline-flex;align-items:center;gap:6px}
.kpi .kh::before{content:'';width:8px;height:8px;border-radius:50%;background:var(--sc,@GRN@)}
.kpi.hero{background:linear-gradient(135deg,@SLATE@,@SURF@);border:2px solid var(--ac);box-shadow:0 0 0 4px rgba(255,255,255,.03);padding:18px 26px}
.kpi.hero .kl{color:var(--ac);font-weight:700;font-size:1.1rem}
.kpi.hero b{font-size:clamp(1.9rem,3.2vw,3.2rem);font-weight:800;color:@TXT@;line-height:1.25}
.kpi.hero .kh{font-size:.95rem}
/* --- اقدام/هشدار (فشرده) --- */
.acard{--pc:@YEL@;display:flex;flex-direction:column;gap:8px;background:@SURF@;border:1px solid rgba(255,255,255,.1);border-right:7px solid var(--pc);border-radius:16px;padding:15px;margin-bottom:15px;height:auto;overflow:visible;box-sizing:border-box}
.acard .ah{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;margin:0}.acard .ah b{font-size:1.05rem;line-height:1.7;flex:1 1 150px;min-width:0}
.pill{display:inline-block;flex:none;white-space:nowrap;line-height:1.6;background:var(--pc);color:@DEEP@;border-radius:999px;padding:2px 12px;font-weight:800;font-size:.8rem}
.acard .ae{color:@SUB@;font-size:.92rem;line-height:1.8;margin:0;display:block;overflow:visible}
.acard .as{font-size:.9rem;line-height:1.8;margin:0}
.acard .am{display:flex;flex-wrap:wrap;gap:8px;margin:0}
.acard .am span{display:inline-block;background:rgba(255,255,255,.08);border-radius:999px;padding:3px 12px;margin:0;font-size:.85rem;line-height:1.6;white-space:nowrap}
/* --- کارت اقدام پیشنهادی: هر کارت یک st.container با کلید مجزا؛ رنگ نوار کناری بر اساس اولویت --- */
[class*="st-key-acard_"]{--pc:@YEL@;display:flex;flex-direction:column;gap:0;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-right:7px solid var(--pc);border-radius:16px;
 padding:15px!important;margin-bottom:15px;height:auto!important;overflow:visible!important;box-sizing:border-box}
[class*="st-key-acard_hi_"]{--pc:@CORAL@}[class*="st-key-acard_mid_"]{--pc:@YEL@}[class*="st-key-acard_lo_"]{--pc:@MINT@}
.ap{display:flex;flex-direction:column;gap:10px;width:100%}
.ap-h{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px}
.ap-h b{flex:1 1 150px;min-width:0;font-size:1.05rem;line-height:1.7}
.ap-e{font-size:.92rem;line-height:1.8}
.ap-s{margin:0;padding:0 20px 0 0;list-style:disc}.ap-s li{font-size:.9rem;line-height:1.8;margin:0 0 2px}
.ap-m{display:flex;flex-wrap:wrap;gap:8px}
.ap-m span{display:inline-block;background:rgba(255,255,255,.08);border-radius:999px;padding:3px 12px;font-size:.85rem;line-height:1.6;white-space:nowrap}
.sumrow span{display:inline-block;background:rgba(255,255,255,.08);border-radius:999px;padding:3px 12px;margin:0 0 6px 8px;font-size:.82rem}.sumrow b{color:@YEL@}
.ct{font-weight:700;font-size:.98rem;line-height:1.9;margin:2px 0 10px;padding:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ct .cs{color:@SUB@;font-size:.76rem;font-weight:400}
.hometitle{font-size:1.5rem;font-weight:800;margin:2px 0 0}.homesub{color:@SUB@;font-size:.9rem;margin:0 0 4px}
/* --- تب‌ها --- */
div[data-baseweb="tab-list"]{display:flex;flex-wrap:wrap;gap:10px 12px;border:0;margin:4px 0 10px;padding:2px 0;overflow:visible!important;height:auto!important}
button[data-baseweb="tab"]{flex:0 0 auto;background:@SURF@;border:1px solid rgba(255,255,255,.14);border-radius:999px;padding:6px 22px;margin:0!important;height:auto;min-height:38px;line-height:1.5;white-space:nowrap;overflow:visible}
button[data-baseweb="tab"] p{color:@TXT@!important;font-size:.95rem;font-weight:700;margin:0;line-height:1.5;white-space:nowrap}
button[data-baseweb="tab"][aria-selected="true"]{background:@YEL@;border-color:@YEL@}
div[data-baseweb="tab-highlight"],div[data-baseweb="tab-border"]{display:none}
div[data-baseweb="tab-panel"]{padding-top:.25rem!important}
[data-testid="stSidebar"] *{color:@TXT@}
[data-testid="stSidebar"] [data-baseweb="select"]>div,[data-testid="stSidebar"] input,[data-baseweb="popover"] ul{background:@SURF@!important;color:@TXT@!important;border-color:rgba(255,255,255,.18)!important}
span[data-baseweb="tag"]{background:@TEAL@!important}span[data-baseweb="tag"] *{color:@DEEP@!important}
[data-testid="stAlert"] *{color:@TXT@!important}
[data-testid="stBaseButton-secondary"]{background:@SURF@!important;border:1px solid rgba(255,255,255,.2)!important}[data-testid="stBaseButton-secondary"] *{color:@TXT@!important}
[data-testid="stBaseButton-primary"]{background:@YEL@!important;border:0!important}[data-testid="stBaseButton-primary"] *{color:@DEEP@!important}
[data-testid="stPopoverBody"]{background:@SURF@!important}[data-testid="stPopoverBody"] *{color:@TXT@}
pre,code{background:#1F3134!important;color:@TXT@!important}
/* --- فاصله‌ها و جلوگیری از روی‌هم‌افتادن --- */
[data-testid="stHorizontalBlock"]{gap:.65rem}
.ban{flex-wrap:wrap}.ban .vd{flex:1;min-width:240px}
.ban h1{white-space:normal}
/* --- کاشی‌های صفحه‌ی اصلی: کارت HTML + دکمه‌ی نامرئی روی آن (آیکون/عنوان/توضیح هرگز روی هم نمی‌افتند) --- */
[class*="st-key-tile_"]{position:relative}
[class*="st-key-tile_"] [data-testid="stElementContainer"]:has(button){position:absolute;inset:0;z-index:5;margin:0}
[class*="st-key-tile_"] [data-testid="stButton"],[class*="st-key-tile_"] .stButton{height:100%}
[class*="st-key-tile_"] button{width:100%;height:100%;opacity:0;cursor:pointer;border:0;padding:0}
[class*="st-key-tile_"]{margin-top:14px;margin-bottom:6px}
.tile{display:flex;flex-direction:column;align-items:center;justify-content:flex-start;gap:14px;min-height:clamp(190px,27vh,260px);height:auto;padding:22px 16px 18px;box-sizing:border-box;
 background:linear-gradient(135deg,@SLATE@,@SURF@);border:2px solid var(--ac);border-radius:26px;transition:.15s;overflow:visible;text-align:center}
.tile svg{height:56px;width:56px;min-height:56px;flex:none;display:block;margin:0 auto}
.tile .tt{font-size:1.35rem;font-weight:800;line-height:1.7;color:@TXT@;margin:0;display:block;width:100%}
.tile .ts{font-size:.95rem;color:@SUB@;line-height:1.7;margin:0;display:block;width:100%}
.vsp{display:block;width:100%;flex:none}
/* --- ظرف تب‌ها: ارتفاع خودکار تا ردیف دوم تب‌ها روی محتوا نیفتد --- */
[data-testid="stTabs"],[data-baseweb="tabs"]{height:auto!important;overflow:visible!important}
[data-baseweb="tabs"]>div:first-child,[data-testid="stTabs"]>div:first-child{height:auto!important;min-height:0!important;overflow:visible!important}
[data-baseweb="tab-list"]{position:relative!important;z-index:1}
[data-testid="stElementContainer"]{flex-shrink:0}
[class*="st-key-tile_"]:hover .tile{transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,0,0,.25)}
/* --- چیپ‌های مالی --- */
.fx{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:8px;margin:2px 0 4px}
.fc{background:@SURF@;border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:6px 12px;min-width:0}
.fc span{display:block;color:@SUB@;font-size:.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fc b{display:block;font-size:1.08rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fc.bad{border-color:@CORAL@}.fc.ok{border-color:@MINT@}
.note{background:rgba(255,224,138,.1);border:1px dashed @YEL@;border-radius:12px;padding:6px 12px;font-size:.82rem;color:@TXT@;margin:2px 0}
/* --- چیدمان بدون اسکرول غیرضروری --- */
.block-container{padding-bottom:0!important}
[data-testid="stVerticalBlockBorderWrapper"]{overflow:hidden}
/* --- ویجت‌های سفیدِ ناحیه‌ی اصلی (فیلترها): پس‌زمینه سفید، متن مشکی؛ سایدبار و بقیه‌ی بخش‌های تیره سفید می‌مانند --- */
.stApp :is([data-testid="stMain"],section.main) [data-baseweb="select"]>div,
.stApp :is([data-testid="stMain"],section.main) [data-baseweb="input"],
.stApp :is([data-testid="stMain"],section.main) [data-baseweb="base-input"],
.stApp :is([data-testid="stMain"],section.main) [data-baseweb="textarea"],
.stApp :is([data-testid="stMain"],section.main) input,
.stApp :is([data-testid="stMain"],section.main) textarea{background:#FFFFFF!important;border-color:#C9D1D3!important}
.stApp :is([data-testid="stMain"],section.main) [data-baseweb="select"] *,
.stApp :is([data-testid="stMain"],section.main) [data-baseweb="input"] *,
.stApp :is([data-testid="stMain"],section.main) [data-baseweb="base-input"] *,
.stApp :is([data-testid="stMain"],section.main) [data-baseweb="textarea"] *,
.stApp :is([data-testid="stMain"],section.main) input,
.stApp :is([data-testid="stMain"],section.main) textarea{color:#000000!important;-webkit-text-fill-color:#000000!important}
.stApp :is([data-testid="stMain"],section.main) ::placeholder{color:#000000!important;opacity:.55}
.stApp :is([data-testid="stMain"],section.main) [data-baseweb="select"] svg{fill:#000000!important}
/* تقویم انتخاب تاریخ (پس‌زمینه سفید) */
[data-baseweb="calendar"],[data-baseweb="calendar"] *{background-color:#FFFFFF!important;color:#000000!important}
/* --- استثنا: متن تیره روی پس‌زمینه‌های روشن (باید آخر بیاید) --- */
.stApp.stApp .pill,.stApp.stApp .pill *{color:@DEEP@!important}
.stApp.stApp button[data-baseweb="tab"][aria-selected="true"],.stApp.stApp button[data-baseweb="tab"][aria-selected="true"] *{color:@DEEP@!important}
.stApp.stApp [data-testid="stBaseButton-primary"],.stApp.stApp [data-testid="stBaseButton-primary"] *{color:@DEEP@!important}
.stApp.stApp span[data-baseweb="tag"],.stApp.stApp span[data-baseweb="tag"] *{color:@DEEP@!important}
"""
CSS += """
.ct{white-space:normal!important;overflow:visible!important;text-overflow:clip!important;line-height:1.8!important}
.fc span,.fc b{white-space:normal!important;overflow:visible!important;text-overflow:clip!important}
.ban,.ban h1{overflow:visible!important;white-space:normal!important}
.pill,.ap-m span,.acard .am span,button[data-baseweb="tab"] p{white-space:normal!important}
[data-testid="stVerticalBlockBorderWrapper"]{overflow:visible!important}


/* --- Tooltip: پس‌زمینه سفید، متن مشکی --- */
html body .stApp .js-plotly-plot .hoverlayer .hovertext path{fill:#FFFFFF!important;fill-opacity:1!important;stroke:#C9D1D3!important}
html body .stApp .js-plotly-plot .hoverlayer .hovertext text,
html body .stApp .js-plotly-plot .hoverlayer .hovertext tspan,
html body .stApp .js-plotly-plot .hoverlayer text{fill:#000000!important;color:#000000!important}
html body [data-baseweb="tooltip"],html body [role="tooltip"],html body [data-testid="stTooltipContent"]{background:#FFFFFF!important}
html body [data-baseweb="tooltip"] *,html body [role="tooltip"] *,html body [data-testid="stTooltipContent"] *{color:#000000!important;-webkit-text-fill-color:#000000!important;background:transparent!important}
                                                                                                               
/* --- هدر فشرده + تب‌های چسبان بالای صفحه (بدون اسکرول اضافه) --- */
html,body,[data-testid="stMain"],.stApp{overflow-x:hidden!important}
.ban{min-height:48px!important;padding:6px 18px!important;margin:0 0 4px!important}
.ban h1{font-size:1.1rem!important}
div[data-baseweb="tab-list"]{position:sticky!important;top:0;z-index:30;background:@DEEP@;margin:0 0 6px!important;padding:4px 0!important;gap:6px 8px!important}
button[data-baseweb="tab"]{padding:2px 14px!important;min-height:30px!important}
button[data-baseweb="tab"] p{font-size:.88rem!important}
div[data-baseweb="tab-panel"]{padding-top:0!important}
[data-testid="stVerticalBlock"]{gap:.3rem}
"""
CSS += """
/* --- کارت‌های بزرگ نمای کلی مدیریتی --- */
.kc{--ac:@CORAL@;position:relative;background:@SURF@;border:1px solid var(--ac);border-radius:18px;padding:14px 20px;
 min-width:0;box-sizing:border-box;direction:rtl;text-align:right;overflow:visible;margin-bottom:6px}
.kc-l{display:flex;align-items:center;gap:8px;font-family:'BNazanin','B Nazanin',serif!important;font-weight:700!important;
 font-size:1.35rem;line-height:1.7}
.kc-v{display:block;font-family:'BNazanin','B Nazanin',serif!important;font-weight:800!important;
 font-size:clamp(1.9rem,3vw,2.9rem);line-height:1.35;margin-top:4px;white-space:normal;word-break:break-word}
.kc-v small{font-size:.5em;font-weight:700!important}
.kc-i{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;
 border:1.5px solid #FFFFFF;font-size:.85rem;line-height:1;cursor:help;flex:none;font-style:normal}
.kc-tip{visibility:hidden;opacity:0;transition:opacity .15s;position:absolute;top:calc(100% + 8px);right:0;
 width:min(340px,80vw);background:#FFFFFF;border:1px solid #C9D1D3;border-radius:12px;padding:10px 14px;
 box-shadow:0 8px 24px rgba(0,0,0,.35);font-size:.95rem;font-weight:700;line-height:1.9;text-align:right;z-index:999;pointer-events:none}
.kc-i:hover .kc-tip,.kc-i:focus .kc-tip,.kc-i:focus-within .kc-tip{visibility:visible;opacity:1}
.stApp.stApp .kc .kc-l,.stApp.stApp .kc .kc-v,.stApp.stApp .kc .kc-v small,.stApp.stApp .kc .kc-i{color:#FFFFFF!important}
.stApp.stApp .kc .kc-tip{color:#000000!important}
.kpi.has-tip{position:relative;overflow:visible}
.kpi .kl .kc-i{width:18px;height:18px;font-size:.7rem;margin-inline-start:6px;vertical-align:middle}
.stApp.stApp .kpi .kl .kc-i{color:#FFFFFF!important}
.stApp.stApp .kpi .kc-tip{color:#000000!important;font-weight:700;font-size:.9rem}
"""
CSS += """
/* --- تثبیت چیدمان: بدون اسکرول تودرتو و بدون هم‌پوشانی (ستون‌ها، کارت‌ها، تب‌ها، نمودارها) --- */
[data-testid="stColumn"]{min-width:0}
[data-testid="stMainBlockContainer"],.block-container{overflow-x:clip}
.kpi{overflow:visible!important;height:auto;min-height:112px}
.kpi b{white-space:normal!important;overflow:visible!important;text-overflow:clip!important;word-break:break-word;line-height:1.5}
.kpi .kh{flex-wrap:wrap;line-height:1.7}
[data-testid="stPlotlyChart"],[data-testid="stPlotlyChart"]>div,[data-testid="stTabs"] [data-baseweb="tab-panel"]{overflow:visible!important}
div[data-baseweb="tab-panel"] div[data-baseweb="tab-list"]{position:static!important;top:auto!important}
[data-testid="stDataFrame"]{max-width:100%}
[data-testid="stExpanderDetails"]{overflow:visible!important}
"""
for _k, _v in dict(SLATE=SLATE, DEEP=DEEP, SURF=SURF, YEL=YEL, MINT=MINT, TEAL=TEAL, GRN=GRN, CORAL=CORAL, TXT=TXT, SUB=SUB).items():
    CSS = CSS.replace(f"@{_k}@", _v)
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


# ------------------------------------------------------------ ابزارهای نمایش
def money(v):
    return f"{num(v)} تومان"


def mh(v):
    return f"{num(v)}<small> تومان</small>"


def kpi(col, label, value, hint, state="ok", hero=False, ac=TEAL, desc=None):
    """desc: متن راهنما فقط هنگام قرار گرفتن ماوس روی آیکون «i» کنار عنوان نمایش داده می‌شود"""
    icon = ""
    if desc:
        tip = fa(desc).strip().strip("()").strip()
        icon = (f'<span class="kc-i" tabindex="0" role="button" aria-label="{tip}">i'
                f'<span class="kc-tip" role="tooltip">{tip}</span></span>')
    col.markdown(f'<div class="kpi {"hero" if hero else ""} {"has-tip" if desc else ""}" style="--ac:{ac};--sc:{STATE_C[state]}">'
                 f'<span class="kl">{fa(label)}{icon}</span>'
                 f'<b>{fa(value)}</b><span class="kh">{fa(hint)}</span></div>', unsafe_allow_html=True)


def head(t, s=""):
    st.markdown(f'<div class="ct">{t}' + (f'<span class="cs"> · {s}</span>' if s else "") + "</div>", unsafe_allow_html=True)


FH = 320  # ارتفاع استاندارد نمودارها در بخش مالی
M_TIGHT = dict(l=20, r=20, t=35, b=25)


def show(fig, h=300, margin=None):
    _jalali_axis(fig)
    fig.update_layout(height=h, margin=margin or dict(l=8, r=8, t=40, b=8), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family=FONT_PL, size=13, color=TXT), hoverlabel=HOVER,
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, title=None, font=dict(size=11)),
                      colorway=[TEAL, YEL, MINT, GRN, CORAL])
    fig.update_xaxes(gridcolor="rgba(255,255,255,.08)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(255,255,255,.08)", zeroline=False)
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False, "displayModeBar": False})


def _bold_font(size=13):
    f = dict(family="BNazanin, 'B Nazanin', Vazirmatn, sans-serif", size=size, color=WHITE)
    try:
        go.layout.Font(weight="bold")
        f["weight"] = "bold"
    except Exception:  # noqa: BLE001
        pass
    return f
def gauge_row(specs, h=168):
    """specs: (عنوان، مقدار، حداکثر، آستانه، واحد، بالاتر=بهتر)"""
    cols = st.columns(len(specs), gap="large")
    for col, (title, v, mx, thr, unit, high) in zip(cols, specs):
        v = _sf(v)
        mx = _sf(mx, 1.0) or 1.0
        thr = _sf(thr, 1.0) or 1.0
        lo = thr * .82
        steps = ([dict(range=[0, lo], color=CORAL), dict(range=[lo, thr], color=YEL), dict(range=[thr, mx], color=GRN)] if high else
                 [dict(range=[0, thr], color=GRN), dict(range=[thr, thr * 1.5], color=YEL), dict(range=[thr * 1.5, max(mx, thr * 1.5)], color=CORAL)])
        f = go.Figure(go.Indicator(mode="gauge+number", value=v, number=dict(suffix=unit, valueformat=".1f" if mx <= 20 else ".0f", font=dict(size=24, color=WHITE)),
                                   title=dict(text=title, font=dict(size=13, color=WHITE)),
                                   gauge=dict(axis=dict(range=[0, mx], tickcolor=WHITE, tickfont=dict(color=WHITE, size=10)), bar=dict(color=DEEP, thickness=.3), bgcolor="rgba(0,0,0,0)",
                                              borderwidth=0, steps=steps, threshold=dict(line=dict(color=WHITE, width=3), thickness=.85, value=thr))))
        f.update_layout(height=h, margin=dict(l=24, r=24, t=48, b=4), paper_bgcolor="rgba(0,0,0,0)", font=dict(family=FONT_PL, color=WHITE), hoverlabel=HOVER)
        with col:
            st.plotly_chart(f, use_container_width=True, config={"displaylogo": False, "displayModeBar": False})

def P(p, title, ev, steps, owner, due, impact):
    return dict(p=p, title=title, ev=ev, steps=steps, owner=owner, due=due, impact=float(impact))


import analytics as an  # noqa: E402
import db  # noqa: E402
import shift_alerts as SA  # noqa: E402

HERE = Path(__file__).parent
NIGHT = (22, 6)  # ساعات شب برای تحلیل فعالیت (۲۲ تا ۰۶)


def empty(msg="داده‌ای برای نمایش نیست."):
    st.info(msg)


def hist_notice():
    """پیام بالای نمودارهای تاریخی: وقتی روز جاری (ساعت سرور) در بازه‌ی انتخابی است"""
    if COVERS_TODAY:
        st.info(an.CURRENT_DAY_NOTICE, icon="⏳")


def hist_empty_guard() -> bool:
    """True = پس از حذف روز جاری دادهٔ تاریخی نمانده؛ پیام نمایش داده شد و نمودار/KPI تاریخی نباید رسم شود"""
    if HIST_EMPTY:
        st.info(an.NO_HISTORY_NOTICE)
    return HIST_EMPTY


def opt(name, *a, **k):
    """واکشی اختیاری: خطا فقط یک هشدار کوچک می‌دهد و ویجت‌های وابسته خالی می‌مانند"""
    df, err = db.get(name, *a, **k)
    if err:
        st.warning(f"خواندن «{name}» ممکن نشد: {err}", icon="⚠️")
    return df


def rule_loss(tx, k):
    return an.rule_amount(tx, k)


# ============================================================ ناوبری
def go_to(k):
    st.session_state["dash"] = k
    if k:
        st.query_params["d"] = k
    else:
        st.query_params.pop("d", None)


@st.dialog("تصویر تردد", width="large")
def img_dialog(ids, with_exit=True):
    tid = st.selectbox("شناسه تردد", ids)
    if st.button("نمایش تصویر", type="primary"):
        try:
            en, exi = db.taradod_images(int(tid))
            pairs = [(en, "ورود")] + ([(exi, "خروج")] if with_exit else [])
            for col, (im, tt) in zip(st.columns(len(pairs), gap="large"), pairs):
                col.markdown(f"**{tt}**")
                if im:
                    col.image(im)
                else:
                    col.caption("تصویری ثبت نشده")
        except Exception as e:  # noqa: BLE001
            st.warning(f"نمایش تصویر ممکن نشد: {str(e)[:200]}")


# ============================================================ بارگذاری
NOW = pd.Timestamp(str(db.get("server_now", required=True)[0].iloc[0, 0]))
if "dash" not in st.session_state:
    st.session_state["dash"] = st.query_params.get("d")

VH_OPT = {"لپ‌تاپ کوچک (ارتفاع ≈ ۶۵۰)": 650, "لپ‌تاپ / فول‌اچ‌دی (≈ ۸۵۰)": 850, "مانیتور بزرگ (≈ ۱۰۵۰)": 1050, "۲K / ۴K (≈ ۱۳۰۰)": 1300}
with st.sidebar:
    if (HERE / "logo.jpg").exists():
        st.image(str(HERE / "logo.jpg"), use_container_width=True)
    if db.offline_db.is_demo():
        st.markdown('<div style="background:#FFE08A;color:#2C4144!important;border-radius:999px;padding:4px 12px;font-size:.78rem;'
                    'text-align:center;margin:4px 0 2px;font-weight:800">نسخه ارائه مدیریتی — داده‌های واقعی آفلاین (Snapshot)</div>', unsafe_allow_html=True)
        st.caption("زمان استخراج: " + fa(str(db.offline_db.meta().get("extracted_at", "-")).replace("T", " ")))
    if st.session_state.get("dash"):
        st.button("🏠 بازگشت به صفحه اصلی", key="side_home", on_click=go_to, args=(None,), use_container_width=True)
    st.markdown("#### بازه زمانی")
    preset = st.radio("بازه", ["۷ روز اخیر", "۳۰ روز اخیر", "۹۰ روز اخیر", "انتخاب دلخواه"], index=0, label_visibility="collapsed")
    if preset == "انتخاب دلخواه":
        v = st.date_input("از تاریخ تا تاریخ", (NOW.date() - pd.Timedelta(days=30), NOW.date()))
        dates = v if isinstance(v, tuple) else (v, v)
        a_ = dates[0] if len(dates) > 0 and dates[0] is not None else NOW.date() - pd.Timedelta(days=30)
        b_ = dates[1] if len(dates) > 1 and dates[1] is not None else a_
        
        D0, D1 = pd.Timestamp(a_), pd.Timestamp(b_) + pd.Timedelta(days=1)

    else:
        n_days = {"۷ روز اخیر": 7, "۳۰ روز اخیر": 30, "۹۰ روز اخیر": 90}[preset]
        D1 = NOW.normalize() + pd.Timedelta(days=1)
        D0 = D1 - pd.Timedelta(days=n_days)
    D0, D1 = D0.to_pydatetime(), D1.to_pydatetime()
    if D0 <= an.current_day_start(NOW).to_pydatetime() < D1:  # روز جاری (طبق ساعت سرور دیتابیس) در بازه است
        st.caption("⏳ " + an.CURRENT_DAY_NOTICE)
    if st.button("🔄 رفرش دستی داده", use_container_width=True, help="کش دیتابیس را پاک می‌کند"):
        st.cache_data.clear()
        st.rerun()
    with st.expander("تنظیمات محاسبات مالی"):
        TOL = st.number_input("حد تحمل اختلاف وصول (تومان)", min_value=0, value=0, step=500, help="اختلاف |FinalPrice − (Cash+Pos)| تا این مقدار مغایرت حساب نمی‌شود (مثلاً گرد کردن)")
        GRACE = st.number_input("فرجه‌ی رایگان (دقیقه)", min_value=1, value=an.GRACE_MIN, step=1, help="سند: ۵ تا ۱۰ دقیقه")
        CAP_NOM = st.number_input("ظرفیت اسمی پارکینگ (۰ = نامشخص)", min_value=0, value=0, step=10)
        CAP_PHYS = st.number_input("شمارش فیزیکی خودروها (−۱ = نامشخص)", min_value=-1, value=-1, step=1, help="برای محاسبه‌ی انحراف ظرفیت (Capacity Drift)")
        _nc_slot = st.container()
    vh_label = st.selectbox("اندازه‌ی صفحه نمایش", list(VH_OPT), index=1, key="vh", help="ارتفاع نمودارها را طوری تنظیم می‌کند که صفحه اسکرول نداشته باشد. اگر صفحه‌ی شما اسکرول داشت، یک گزینه‌ی کوچک‌تر انتخاب کنید.")
    VH = VH_OPT[vh_label]
    st.caption(f"زمان سرور: {fa(f'{NOW:%Y-%m-%d %H:%M:%S}')} · کش ۶۰ ثانیه")
    st.divider()


def R(n, extra=0):
    """ارتفاع نمودار برای n ردیف کارت در یک صفحه‌ی بدون اسکرول (extra = فضای اشغال‌شده‌ی دیگر)"""
    return max(150, int((VH - 150 - extra) / n - 84))


RAW_TX = db.get("taradods", D0, D1, required=True)[0]
_ft = db.get_fin("fin_tx", D0, D1, tol=float(TOL), required=True)[0]
_fv, _fv_err = db.get_fin("fin_view", D0, D1)  # سنگین‌ترین کوئری؛ اگر timeout شد برنامه ادامه می‌دهد و فقط بخش «تخفیف بدون سند» صفر می‌ماند
if _fv_err:
    st.sidebar.warning("محاسبه‌ی تخفیف‌ها از View_Taradods_PBI ممکن نشد (احتمالاً timeout). «تخفیف بدون سند» صفر نمایش داده می‌شود؛ بازه را کوتاه‌تر کنید یا query_timeout را در secrets بیشتر کنید.")
FT0, FV0 = an.prepare_fin(_ft, _fv)
with _nc_slot:
    NOCASH = tuple(st.multiselect("PaymentID هایی که وصولشان در Cash/Pos ثبت نمی‌شود (از کسری وصول مستثنا)", sorted(FT0.PaymentID.dropna().astype(int).unique()), key="nocash",
                                  help="جدول «وصول به تفکیک PaymentID» در تب «صحت و فرمول‌ها» کمک می‌کند تشخیص دهید کدام شناسه‌ها پرداخت دیگری دارند"))
VW0 = an.prepare_view(db.get("view", D0, D1, required=True)[0])
# مرجع گیت‌ها: dbo.Gates (+ عنوان نوع از dbo.GateTypes). در صورت خطا/نبود اسنپ‌شات، داشبورد با نام‌های View ادامه می‌دهد و هشدار می‌دهد.
_g_raw, _g_err = db.get("gates")
_gt_raw, _gt_err = db.get("gate_types")
GATES, GATE_Q = an.prepare_gates(_g_raw, _gt_raw)
if _g_err or _gt_err:
    st.sidebar.warning("خواندن dbo.Gates/GateTypes ممکن نشد؛ نام گیت‌ها از View (در صورت وجود) یا «نامشخص» نمایش داده می‌شود.")
VW0, _GN_VW = an.apply_gate_names(VW0, GATES)
DOC_IDS = set(pd.to_numeric(VW0.loc[VW0.IsDiscount | VW0.IsGuest | VW0.IsCustomer, "id"], errors="coerce").dropna().astype(int))  # معافیت‌های دارای سند (کد تخفیف/مهمان/اشتراکی)
TX0 = an.prepare_taradods(RAW_TX, float(TOL), NOCASH, DOC_IDS)
INS = an.prepare_inside(db.get("inside", required=True)[0], NOW)
TX0, _GN_TX = an.apply_gate_names(TX0, GATES)
INS, _GN_INS = an.apply_gate_names(INS, GATES)
TX0, VW0 = TX0[(TX0.Event_Time >= D0) & (TX0.Event_Time < D1)], VW0[(VW0.Event_Time >= D0) & (VW0.Event_Time < D1)]
if len(RAW_TX) >= db.TOP_DEFAULT:
    st.sidebar.warning(f"ردیف‌های خام به سقف {num(db.TOP_DEFAULT)} رسید؛ جمع‌های مالی از کوئری تجمیعی (دقیق) هستند ولی جدول‌های ردیفی ناقص‌اند. بازه را کوتاه‌تر کنید.")
_GCOV, _GSUM = an.gate_coverage(GATES, VW0, D0, D1)
with st.sidebar:
    with st.expander("گیت‌ها و کیفیت داده", expanded=False):
        st.caption(f"تعریف‌شده در dbo.Gates: **{num(_GSUM['defined'])}** · مشاهده‌شده در ترددهای بازه: **{num(_GSUM['observed_any'])}** "
                   f"(ورود {num(_GSUM['observed_in'])} / خروج {num(_GSUM['observed_out'])}) · تعریف‌شده بدون تردد: **{num(_GSUM['defined_no_traffic'])}**")
        st.dataframe(_GCOV, hide_index=True, use_container_width=True, height=240)
        _GQ = an.gate_quality(GATE_Q, _GSUM, {"input": {**_GN_VW["input"]}, "output": {**_GN_VW["output"]}})
        if _GQ.empty:
            st.caption("مورد کیفیتی برای گیت‌ها ثبت نشد.")
        else:
            st.dataframe(_GQ, hide_index=True, use_container_width=True, height=200)
TX, VW, FT, FV = TX0, VW0, FT0, FV0

# ------------------------------------------------------------ مجموعه‌داده‌ی تاریخیِ مشترک (بدون روز جاری)
# تنها منبع تحلیل‌ها/نمودارهای تاریخی و مقایسه‌ای بخش مالی: FTH/FVH (جمع‌های روزانه)، TXH/VWH (ردیف‌ها). روز جاری = روز تقویم میلادیِ NOW (ساعت سرور DB).
# TX/VW/FT/FV (شامل امروز) فقط برای نمایش عملیاتی/زنده باقی می‌مانند. فیلتر روی تاریخ میلادیِ رکوردها است، پیش از هر تبدیل به شمسی/برچسب نمودار.
TODAY = an.current_day_start(NOW)
COVERS_TODAY = D0 <= TODAY.to_pydatetime() < D1
HIST_DAYS = max((min(D1, TODAY.to_pydatetime()) - D0).days, 1)  # تعداد روزهای تقویمیِ بازه‌ی تاریخی (برای برآوردهای سالانه)
FT0H, FV0H = an.historical_only(FT0, "ExitDate", NOW), an.historical_only(FV0, "ExitDate", NOW)
_txh = an.prepare_taradods(an.censor_open_day(RAW_TX, NOW), float(TOL), NOCASH, DOC_IDS)
_txh, _ = an.apply_gate_names(_txh, GATES)
TX0H = _txh[(_txh.Event_Time >= D0) & (_txh.Event_Time < D1)]
VW0H = an.historical_only(VW0, "Event_Time", NOW)
HIST_EMPTY = COVERS_TODAY and FT0H.empty and not (len(TX0H) and bool(TX0H.Exited.any()))  # پس از حذف روز جاری هیچ دادهٔ تاریخی نمی‌ماند
TXH, VWH, FTH, FVH = TX0H, VW0H, FT0H, FV0H
_bd = an.day_boundary_report(RAW_TX, NOW)
if _bd["undated"] or (len(FT0) and int(FT0.ExitDate.isna().sum())):
    st.sidebar.warning(f"تاریخ برخی رکوردها قابل‌تعیین نیست ({num(_bd['undated'] + (int(FT0.ExitDate.isna().sum()) if len(FT0) else 0))} ردیف)؛ چون نمی‌توان ثابت کرد امروز نیستند، از تحلیل تاریخی کنار گذاشته شدند.")


# ============================================================ قطعات مشترک داشبوردها
def spacer(h=14):
    """فاصله‌ی عمودی مشخص (px) بین بخش‌ها تا کانتینرها به هم نچسبند"""
    st.markdown(f'<div class="vsp" style="height:{int(h)}px"></div>', unsafe_allow_html=True)


def top(verdict=""):
    """نوار فشرده‌ی بالای هر داشبورد: دکمه‌ی بازگشت + عنوان (+ اگر verdict خالی نباشد خلاصه‌ی متنی)"""
    c1, c2 = st.columns([1.1, 9], gap="large")
    c1.button("🏠 اصلی", key="home_btn", on_click=go_to, args=(None,), use_container_width=True)
    vd = f'<div class="vd"><p>{fa(verdict)}</p></div>' if verdict else ""
    
    cur_dict = CUR if isinstance(CUR, dict) else {"ac": "#38bdf8", "t": ""}
    ac_val = cur_dict.get("ac", "#38bdf8")
    t_val = cur_dict.get("t", "")
    
    c2.markdown(f'<div class="ban" style="--ac:{ac_val}"><h1>{t_val}</h1>{vd}</div>', unsafe_allow_html=True)
    spacer(4)


def header_cards(cards):
    """فهرست (برچسب، مقدار، توضیح، وضعیت)"""
    cur_dict = CUR if isinstance(CUR, dict) else {"ac": "#38bdf8"}
    ac_val = str(cur_dict.get("ac", "#38bdf8"))

    for col, (label, value, hint, state) in zip(st.columns(len(cards), gap="large"), cards):
        kpi(col, label, value, hint, state, ac=ac_val)
    spacer(12)


def plan(items, ac, scen=False, days=None):
    items = sorted(items, key=lambda i: ({"فوری": 0, "مهم": 1, "پایش": 2}[i["p"]], -i["impact"]))
    cn = pd.Series([i["p"] for i in items]).value_counts()
    n_days = max(int(days), 1) if days else max((D1 - D0).days, 1)
    nh = 2 if scen else 1
    left, right = st.columns([2, 3], gap="large")
    with left.container(border=True):
        head("پارتوی اثر مالی اقدامات", "میله = اثر هر اقدام · خط = سهم تجمعی")
        bd = pd.DataFrame({"اقدام": [wrap(i["title"]) for i in items], "اثر": [i["impact"] for i in items], "p": [i["p"] for i in items]}).sort_values("اثر", ascending=False)
        if bd["اثر"].sum() <= 0:
            empty("برای این اقدامات اثر مالی عددی در بازه‌ی انتخابی محاسبه نشد.")
        else:
            cum = bd["اثر"].cumsum() / bd["اثر"].sum()
            f = go.Figure([go.Bar(x=bd.اقدام, y=bd.اثر, name="اثر مالی (تومان)", marker_color=[PCOL[p] for p in bd.p], text=[num(v) for v in bd.اثر], textposition="outside", cliponaxis=False),
                           go.Scatter(x=bd.اقدام, y=cum, yaxis="y2", name="سهم تجمعی", mode="lines+markers", line=dict(color=WHITE))])
            f.update_layout(yaxis=dict(tickformat=",d"), yaxis2=dict(overlaying="y", side="right", range=[0, 1.05], tickformat=".0%", showgrid=False))
            show(f, R(nh, 30))
    if scen:
        with left.container(border=True):
            pairs = [(wrap(i["title"]), i["impact"]) for i in items if i["impact"] > 0]
            tot = sum(v for _, v in pairs)
            head("سناریوهای اثر مالی", f"نرخ اجرای موفق ۳۰/۶۰/۹۰٪ · برآورد سالانه (محتمل): {money(tot * .6 * 365 / n_days)}")
            if pairs:
                sc = an.scenario_table(pairs, n_days)
                show(px.bar(sc, x="اقدام", y="بازیابی در بازه", color="سناریو", barmode="group", color_discrete_sequence=[CORAL, YEL, GRN],
                            hover_data={"برآورد سالانه": ":,.0f"}).update_layout(yaxis=dict(tickformat=",d")), R(nh, 30))
            else:
                empty("اثر مالی عددی برای سناریوسازی وجود ندارد.")
    with right:
        st.markdown(f'<div class="sumrow"><span>اقدام فوری: <b>{fa(cn.get("فوری", 0))}</b></span><span>مهم: <b>{fa(cn.get("مهم", 0))}</b></span>'
                    f'<span>پایشی: <b>{fa(cn.get("پایش", 0))}</b></span><span>اثر مالی قابل بازیابی: <b>{money(sum(i["impact"] for i in items))}</b></span></div>', unsafe_allow_html=True)
        spacer(8)
        k1, k2 = st.columns(2, gap="large")
        pkey = {"فوری": "hi", "مهم": "mid", "پایش": "lo"}
        for n_, i in enumerate(items):
            steps = "".join(f"<li>{fa(x)}</li>" for x in i["steps"])
            card = (f'<div class="ap"><div class="ap-h"><span class="pill">{i["p"]}</span><b>{i["title"]}</b></div>'
                    f'<div class="ap-e">{fa(i["ev"])}</div><ul class="ap-s">{steps}</ul>'
                    f'<div class="ap-m"><span>مسئول: <b>{i["owner"]}</b></span><span>مهلت: <b>{fa(i["due"])}</b></span><span>اثر: <b>{money(i["impact"])}</b></span></div></div>')
            try:
                box = (k1 if n_ % 2 == 0 else k2).container(key=f"acard_{pkey[i['p']]}_{n_}")
            except TypeError:
                box = (k1 if n_ % 2 == 0 else k2).container()
            with box:
                st.markdown(card, unsafe_allow_html=True)


# ============================================================ مالی دقیق: قطعات مشترک
def fin_chips(B):
    def c(label, v, cls=""):
        return f'<div class="fc {cls}"><span>{label}</span><b>{money(v)}</b></div>'
    bad = lambda v: "bad" if v else ""  # noqa: E731
    st.markdown('<div class="fx">' + "".join([
        c("تعرفه (ΣTotalPrice)", B["tariff"]), c("مبلغ نهایی (ΣFinalPrice)", B["billed_tx"], "ok"), c("وصول‌شده (Cash+Pos)", B["collected"], "ok"),
        c("زیان مشکوک", B["loss"], bad(B["loss"])), c("کسری وصول", B["under"], bad(B["under"])), c("کارتخوان بدون ثبت پوز", B["posnd"], bad(B["posnd"])),
        c("بازپرداخت", B["components"]["بازپرداخت"], bad(B["components"]["بازپرداخت"])), c("تخفیف/معافیت بدون سند", B["unexplained"], bad(B["unexplained"])),
        c("اضافه‌وصول (مغایرت مثبت)", B["over"])]) + "</div>", unsafe_allow_html=True)


def _wf(x, y, m, h):
    f = go.Figure(go.Waterfall(x=x, y=y, measure=m, text=[num(v) for v in y], textposition="outside", cliponaxis=False, textfont=dict(size=10),
                               increasing=dict(marker=dict(color=TEAL)), decreasing=dict(marker=dict(color=CORAL)), totals=dict(marker=dict(color=YEL)),
                               connector=dict(line=dict(color="rgba(255,255,255,.3)"))))
    f.update_layout(showlegend=False, yaxis=dict(tickformat=",d"))
    show(f, h)


def fin_waterfalls(B, h):
    w1, w2 = st.columns(2, gap="large")
    wr = B["waiver_by_reason"]
    with w1.container(border=True):
        head("از تعرفه تا مبلغ نهایی", "View_Taradods_PBI · Σتعرفه − تخفیف‌ها + افزوده = Σمبلغ نهایی")
        _wf(["تعرفه", "کد تخفیف", "مهمان", "اشتراکی", "بدون سند", "افزوده", "مبلغ نهایی"],
            [B["tariff"], -wr["کد تخفیف"], -wr["مهمان"], -wr["اشتراکی"], -wr["بدون سند"], B["uplift"], B["billed_vw"]], ["absolute"] + ["relative"] * 5 + ["total"], h)
    resid = B["collected"] - (B["billed_tx"] - B["under"] + B["over"])
    with w2.container(border=True):
        head("از مبلغ نهایی تا وصول", "Taradods · Σنهایی − کسری + اضافه‌وصول ± سایر = Σ(Cash+Pos)")
        _wf(["مبلغ نهایی", "کسری وصول", "اضافه‌وصول", "سایر اختلاف‌ها", "وصول‌شده"], [B["billed_tx"], -B["under"], B["over"], resid, B["collected"]],
            ["absolute", "relative", "relative", "relative", "total"], h)


def fin_daily(B, h):
    d = B["daily"].sort_values("Date")
    if d.empty:
        return empty()
    f = go.Figure([go.Bar(x=d.Date, y=d.loss, name="زیان مشکوک", marker_color=CORAL, yaxis="y2", opacity=.85),
                   go.Scatter(x=d.Date, y=d.tariff, name="تعرفه", line=dict(color=MINT)), go.Scatter(x=d.Date, y=d.billed, name="مبلغ نهایی", line=dict(color=YEL)),
                   go.Scatter(x=d.Date, y=d.collected, name="وصول‌شده", line=dict(color=TEAL, dash="dot"))])
    f.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, title="زیان"), yaxis=dict(tickformat=",d"))
    show(f, h)


def fin_leak(B, h):
    """نشت درآمد (Sankey): تعرفه → تخفیف مجاز/بدون سند/مبلغ نهایی → وصول‌شده/کسری؛ جریان‌های قرمز = نشت"""
    labels, fl = an.leak_flows(B)
    if not fl:
        return empty()
    ncol = [MINT, TEAL, MINT, CORAL, YEL, GRN, CORAL]
    f = go.Figure(go.Sankey(arrangement="snap", textfont=dict(color=WHITE, size=13),
                            node=dict(label=labels, color=ncol, pad=18, thickness=16, line=dict(width=0)),
                            link=dict(source=[a for a, _, _, _ in fl], target=[b for _, b, _, _ in fl], value=[v for _, _, v, _ in fl],
                                      color=["rgba(255,138,122,.55)" if lk else "rgba(255,255,255,.20)" for _, _, _, lk in fl])))
    show(f, h)


def fin_deviation(B, h):
    d = an.daily_deviation(B)
    if d.empty:
        return empty()
    f = go.Figure([go.Bar(x=d.Date, y=d["انحراف"], name="وصول − مبلغ نهایی", marker_color=[CORAL if o else TEAL for o in d.out]),
                   go.Scatter(x=d.Date, y=d.UCL, name="حد بالا (۳σ)", line=dict(color=YEL, dash="dash")),
                   go.Scatter(x=d.Date, y=d.LCL, name="حد پایین (۳σ)", line=dict(color=YEL, dash="dash"))])
    f.update_layout(yaxis=dict(tickformat=",d"))
    show(f, h)


def fin_leak_wf(B, h=FH):
    """نمودار آبشاری نشت درآمد: ناخالص پتانسیل → کسورات (قرمز) → درآمد خالص محقق‌شده"""
    L = an.leak_breakdown(B, TXH, GRACE)  # هم‌مجموعه با B (بدون روز جاری)
    steps = [("درآمد ناخالص پتانسیل", L["gross"], "absolute"), ("تخفیف مجاز (دارای سند)", -L["authorized"], "relative"),
             ("تخفیف/فرجه غیرمجاز", -L["grace_loss"], "relative"), ("کسری صندوق ثبت‌شده", -L["cash_short"], "relative"),
             ("پلاک ناهمخوان/مفقود", -L["plate"], "relative"), ("خطای ثبتی/کارتخوان/بازپرداخت", -L["reg_err"], "relative"),
             ("اضافه‌وصول", L["over"], "relative"), ("سایر/اختلاف منابع", L["other"], "relative"),
             ("درآمد خالص محقق‌شده", L["net"], "total")]
    if L["gross"] <= 0:
        return empty()
    f = go.Figure(go.Waterfall(x=[wrap(s[0], 13) for s in steps], y=[s[1] for s in steps], measure=[s[2] for s in steps],
                               text=[num(s[1]) for s in steps], textposition="outside", cliponaxis=False, textfont=dict(size=10, color=WHITE),
                               increasing=dict(marker=dict(color=GRN)), decreasing=dict(marker=dict(color=CORAL)),
                               totals=dict(marker=dict(color="#6C8EBF")), connector=dict(line=dict(color="rgba(255,255,255,.3)"))))
    f.update_layout(showlegend=False, yaxis=dict(tickformat=",d"))
    show(f, h, dict(l=20, r=20, t=35, b=55))


def fin_cashier(B, h=FH):
    u = an.cashier_deviation(B)
    if u.empty:
        return empty("داده‌ی کافی (حداقل ۲۰ خروج برای هر شناسه) نیست.")
    u = u.sort_values("Z", ascending=False).head(20)
    col = [CORAL if z >= 3.0 else (YEL if z >= 2.5 else TEAL) for z in u.Z]
    bf = _bold_font(13)
    f = go.Figure(go.Bar(x=u["شناسه"], y=u["Z"], marker_color=col, text=[fa(f"{z:.2f}") for z in u.Z], textposition="outside", cliponaxis=False,
                         textfont=bf, hovertemplate="شناسه %{x}<br>Z = %{y:.2f}<extra></extra>"))
    f.add_hline(y=2.5, line_dash="dash", line_color=YEL, line_width=2, annotation_text="<b>Z = +2.5 (بازرسی سرزده)</b>",
                annotation_position="top left", annotation_font=dict(color=YEL, size=12))
    f.add_hline(y=3.0, line_dash="dash", line_color=CORAL, line_width=2, annotation_text="<b>Z = +3.0 (بحرانی)</b>",
                annotation_position="top right", annotation_font=dict(color=CORAL, size=12))
    f.update_layout(font=bf, showlegend=False, title=dict(text="<b>انحراف Z-Score نرخ زیان صندوقداران (شناسه کاربر خروج)</b>", font=bf, x=0.5),
                    xaxis=dict(type="category", title=dict(text="<b>شناسه صندوقدار</b>", font=bf), tickfont=bf, tickangle=-30, automargin=True),
                    yaxis=dict(title=dict(text="<b>Z-Score</b>", font=bf), tickfont=bf, range=[min(-1, float(u.Z.min()) - 0.5), max(3.6, float(u.Z.max()) + 0.8)]))
    show(f, h, dict(b=60, l=40, r=40, t=50))


def fin_main(B, h=FH):
    ST = st.tabs(["نشت درآمد", "روند و انحراف روزانه", "انحراف صندوقداران"])
    with ST[0]:
        fin_chips(B)
        a, b = st.columns([3, 2], gap="large")
        with a.container(border=True):
            head("نشت درآمد (Waterfall)", "ناخالص پتانسیل → کسورات نشت → درآمد خالص محقق‌شده")
            fin_leak_wf(B, h)
        with b.container(border=True):
            head("اجزای زیان مشکوک", "هر جزء مجزا؛ جمع = زیان مشکوک")
            comp = pd.DataFrame({"جزء": list(B["components"]), "مبلغ": list(B["components"].values())}).sort_values("مبلغ")
            show(px.bar(comp, x="مبلغ", y="جزء", orientation="h", color_discrete_sequence=[CORAL]).update_layout(xaxis=dict(tickformat=",d")), h, M_TIGHT)
    with ST[1]:
        a, b = st.columns([3, 2], gap="large")
        with a.container(border=True):
            head("روند روزانه: تعرفه، مبلغ نهایی، وصول و زیان")
            d = B["daily"].sort_values("Date")
            if d.empty:
                empty()
            else:
                f = go.Figure([go.Bar(x=d.Date, y=d.loss, name="زیان مشکوک", marker_color=CORAL, yaxis="y2", opacity=.85),
                               go.Scatter(x=d.Date, y=d.tariff, name="تعرفه", line=dict(color=MINT)), go.Scatter(x=d.Date, y=d.billed, name="مبلغ نهایی", line=dict(color=YEL)),
                               go.Scatter(x=d.Date, y=d.collected, name="وصول‌شده", line=dict(color=TEAL, dash="dot"))])
                f.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False), yaxis=dict(tickformat=",d"))
                show(f, h, M_TIGHT)
        with b.container(border=True):
            head("انحراف مالی روزانه", "میله‌ی قرمز = خارج از ±۳σ")
            dd = an.daily_deviation(B)
            if dd.empty:
                empty()
            else:
                f = go.Figure([go.Bar(x=dd.Date, y=dd["انحراف"], name="وصول − مبلغ نهایی", marker_color=[CORAL if o else TEAL for o in dd.out]),
                               go.Scatter(x=dd.Date, y=dd.UCL, name="حد بالا (۳σ)", line=dict(color=YEL, dash="dash")),
                               go.Scatter(x=dd.Date, y=dd.LCL, name="حد پایین (۳σ)", line=dict(color=YEL, dash="dash"))])
                f.update_layout(yaxis=dict(tickformat=",d"))
                show(f, h, M_TIGHT)
    with ST[2]:
        with st.container(border=True):
            head("انحراف صندوقداران (Z-Score)", "ستون زرد: Z ≥ 2.5 · ستون قرمز: Z ≥ 3.0")
            fin_cashier(B, h)

def fin_audit(B, tx, h):
    """صحت فرمول‌ها + پاسخ به «چرا صفر؟»"""
    ex = tx[tx.Exited]
    why = []
    tol_txt = f"{num(TOL)} تومان"
    if B["exits"] == 0:
        why.append(("همه‌ی شاخص‌ها", "در این بازه/فیلتر هیچ خروجی ثبت نشده؛ بازه یا گیت را عوض کنید."))
    if B["billed_tx"] > 0 and B["collected"] == 0:
        why.append(("کسری وصول", "Cash و Pos هیچ مبلغی ثبت نکرده‌اند در حالی که FinalPrice مثبت است؛ احتمالاً پرداخت‌ها با روش دیگری (PaymentID) ثبت می‌شود. جدول «وصول به تفکیک PaymentID» را ببینید."))
    elif B["under"] == 0:
        why.append(("کسری وصول", f"برای همه‌ی ردیف‌های بدون بازپرداخت، Cash+Pos کمتر از FinalPrice نبوده (حد تحمل {tol_txt}) — صفر واقعی است." + (" (برخی PaymentID ها مستثنا شده‌اند)" if NOCASH else "")))
    if B["posnd"] == 0:
        why.append(("کارتخوان بدون ثبت پوز", "هیچ مبلغ Pos بدون PosDetailID نیست." if B["pos"] > 0 else "ستون Pos هیچ مبلغی ندارد؛ پرداخت کارتخوانی در Taradods ثبت نشده است."))
    if B["refund_n"] == 0:
        why.append(("بازپرداخت", "ستون Refund در هیچ ردیفی بزرگ‌تر از صفر نیست."))
    if B["unexplained"] == 0:
        why.append(("تخفیف/معافیت بدون سند", "هیچ ردیفی FinalPrice<TotalPrice ندارد." if B["waiver_all"] == 0 else "همه‌ی تخفیف‌ها یکی از سه سند (کد تخفیف، مهمان، اشتراکی) دارند — صفر واقعی است."))
    if B["billed_vw"] == 0 and B["billed_tx"] > 0:
        why.append(("تخفیف بدون سند", "جمع‌های View_Taradods_PBI محاسبه نشد (timeout یا خطا)؛ این جزء صفر نمایش داده شده و قابل اتکا نیست."))
    elif abs(B["integrity_diff"]) > 0.5:
        why.append(("یکپارچگی دو منبع", f"Σ FinalPrice در Taradods و View_Taradods_PBI {money(abs(B['integrity_diff']))} اختلاف دارد؛ یکی از دو منبع ناقص یا فیلتر است."))
    a1, a2 = st.columns([3, 2], gap="large")
    with a1.container(border=True):
        head("فرمول و مقدار هر جزء زیان", "اولویت در هر تردد: بازپرداخت ← کارتخوان بدون پوز ← کسری وصول (بدون شمارش مضاعف)")
        rows = [("کسری وصول", "Σ max(FinalPrice − Cash − Pos, 0) · ردیف بدون بازپرداخت و بدون «کارتخوان بدون پوز» و PaymentID مستثنا", B["under"], B["under_n"]),
                ("کارتخوان بدون ثبت پوز", "Σ Pos · وقتی Pos>0 و PosDetailID خالی و Refund=0", B["posnd"], B["posnd_n"]),
                ("بازپرداخت", "Σ Refund · وقتی Refund>0", B["components"]["بازپرداخت"], B["refund_n"]),
                ("تخفیف/معافیت بدون سند", "Σ max(TotalPrice − FinalPrice, 0) · بدون ثبت در DiscountLogs/Guests/Customer", B["unexplained"], B["unexplained_n"]),
                ("اضافه‌وصول (زیان نیست)", "Σ max(Cash + Pos − FinalPrice, 0) · نشانه‌ی دریافت اضافه یا ثبت ناقص", B["over"], B["over_n"])]
        st.dataframe(pd.DataFrame(rows, columns=["جزء", "فرمول", "مبلغ (تومان)", "تعداد"]), hide_index=True, use_container_width=True, height=h,
                     column_config={"مبلغ (تومان)": st.column_config.NumberColumn(format="%d")})
    with a2.container(border=True):
        head("چرا بعضی اجزا صفر است؟", "تشخیص خودکار بر اساس داده")
        if why:
            for t, m in why:
                st.markdown(f'<div class="note"><b>{t}:</b> {fa(m)}</div>', unsafe_allow_html=True)
        else:
            st.success("همه‌ی اجزا مقدار غیرصفر دارند.")
    b1, b2 = st.columns(2, gap="large")
    with b1.container(border=True):
        head("کدام رابطه‌ی ریاضی در داده برقرار است؟", "برای ترددهای خارج‌شده با تعرفه و مبلغ نهایی مثبت")
        idt = an.identity_table(tx)
        st.dataframe(idt, hide_index=True, use_container_width=True, height=h - 40,
                     column_config={"سهم ردیف‌های برقرار": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.3f")}) if len(idt) else empty()
    with b2.container(border=True):
        head("وصول به تفکیک PaymentID", "برای تعیین شناسه‌هایی که پرداختشان در Cash/Pos نیست")
        st.dataframe(B["by_payment"], hide_index=True, use_container_width=True, height=h - 40)


def fin_people(B, h):
    p1, p2 = st.columns(2, gap="large")
    with p1.container(border=True):
        head("تخفیف/معافیت بدون سند به تفکیک صندوقدار", "View_Taradods_PBI · نام کاربر خروج")
        st.dataframe(B["by_user_view"], hide_index=True, use_container_width=True, height=h,
                     column_config={"سهم_بدون_سند": st.column_config.ProgressColumn("سهم از تعرفه", min_value=0, max_value=0.3, format="%.3f")})
    with p2.container(border=True):
        head("زیان وصول به تفکیک شناسه کاربر خروج", "Taradods.ExitUserID · نام کاربر در این جدول موجود نیست")
        st.dataframe(B["by_user_tx"], hide_index=True, use_container_width=True, height=h)


def fin_items(B):
    c = B["components"]
    return [P("فوری" if B["under"] else "پایش", "کسری وصول", f"{B['under_n']} تردد با Cash+Pos کمتر از FinalPrice · {money(B['under'])}",
              ["بازبینی ردیف‌ها با گزارش بانک و دخل", "اضافه‌کردن PaymentIDهای غیرنقد به لیست مستثنا", "الزام ثبت مبلغ هنگام خروج"], "مدیر مالی", "۷ روز", B["under"]),
            P("فوری" if B["unexplained"] else "پایش", "تخفیف/معافیت بدون سند", f"{B['unexplained_n']} تردد با FinalPrice<TotalPrice بدون کد تخفیف/مهمان/اشتراک · {money(B['unexplained'])}",
              ["بازبینی صندوقداران پرتخفیف در تب «صحت و فرمول‌ها»", "الزام ثبت سند برای هر معافیت"], "حراست", "۷ روز", B["unexplained"]),
            P("فوری" if B["posnd"] else "پایش", "کارتخوان بدون ثبت پوز", f"{B['posnd_n']} تردد با Pos>0 و بدون PosDetailID · {money(B['posnd'])}",
              ["تطبیق با گزارش PSP", "الزام ثبت PosDetailID هنگام خروج"], "حسابداری", "۷ روز", B["posnd"]),
            P("فوری" if c["بازپرداخت"] else "پایش", "کنترل بازپرداخت‌ها", f"{B['refund_n']} تردد با Refund>0 · {money(c['بازپرداخت'])}",
              ["تایید کتبی سرپرست برای هر بازپرداخت", "بررسی کاربران تکرارکننده"], "مدیر مالی", "۷ روز", c["بازپرداخت"])]


# ============================================================ ۱ · مدیریتی
def loss_panel(B, G, h):
    lost = _sf(B["loss"])
    pct = lost / B["tariff"] * 100 if B["tariff"] else 0
    tip = "نسبت درآمد از دست‌رفته (زیان مشکوک و کسری‌ها) به کل پتانسیل درآمد تعرفه. این شاخص درصد نشت مالی سیستم را نشان می‌دهد."
    k1, k2 = st.columns(2, gap="large")
    k1.markdown(f'<div class="kc"><span class="kc-l">درآمد از دست‌رفته (زیان مشکوک)</span>'
                f'<b class="kc-v">{mh(lost)}</b></div>', unsafe_allow_html=True)
    k2.markdown(f'<div class="kc"><span class="kc-l">سهم از تعرفه'
                f'<span class="kc-i" tabindex="0" role="button" aria-label="{tip}">i<span class="kc-tip" role="tooltip">{tip}</span></span></span>'
                f'<b class="kc-v">{fa(f"{pct:.2f}")}٪</b></div>', unsafe_allow_html=True)
    spacer(18)  # فاصله‌ی استاندارد بین کارت‌ها و نمودار
    if G.empty or lost <= 0:
        return empty("زیان مشکوکی در این بازه ثبت نشده است.")
    f = go.Figure()
    for col, name, c in (("refund", "بازپرداخت", CORAL), ("posnd", "کارتخوان بدون ثبت پوز", YEL), ("under", "کسری وصول", MINT), ("unexplained", "تخفیف بدون سند", TEAL)):
        f.add_bar(x=G.Date, y=G[col], name=name, marker_color=c)
    f.add_scatter(x=G.Date, y=G.loss.cumsum(), name="زیان انباشته", yaxis="y2", mode="lines", line=dict(color=WHITE, width=2))
    f.update_layout(barmode="stack", yaxis=dict(tickformat=",d"), yaxis2=dict(overlaying="y", side="right", showgrid=False, tickformat=",d"))
    # حاشیه‌ی بالای بیشتر: لجند افقی دوخطی هم به کارت‌ها یا لبه‌ی کانتینر نمی‌چسبد
    show(f, max(200, h - 40), margin=dict(l=8, r=16, t=72, b=8))

def loss_components(B, h):
    c = pd.DataFrame({"جزء": list(B["components"]), "مبلغ": list(B["components"].values())}).sort_values("مبلغ")
    tot = c["مبلغ"].sum()
    if tot <= 0:
        return empty("زیان مشکوکی ثبت نشده است.")
    f = px.bar(c, x="مبلغ", y="جزء", orientation="h", text=[fa(f"{v / tot:.0%}") for v in c["مبلغ"]], color_discrete_sequence=[CORAL])
    f.update_traces(textposition="outside", cliponaxis=False)
    f.update_layout(xaxis=dict(tickformat=",d"))
    show(f, h)


def revenue_gap_chart(G, h):
    """شکاف درآمدی = تعرفه (پتانسیل) − وصول‌شده؛ میله‌ها اجزای دقیق شکاف (مجموع = شکاف)، خط = درصد فرار درآمدی"""
    if G.empty:
        return empty()
    f = go.Figure()
    for col, name, c in (("authorized", "تخفیف مجاز (دارای سند، خالص از افزوده)", MINT), ("unexplained", "تخفیف بدون سند", CORAL), ("under", "کسری وصول", YEL),
                         ("other", "سایر (بازپرداخت/پوز بدون ثبت/اختلاف دو منبع)", "#9AA9AB")):
        f.add_bar(x=G.Date, y=G[col], name=name, marker_color=c)
    f.add_bar(x=G.Date, y=-G.over, name="اضافه‌وصول (منفی)", marker_color=GRN)
    f.add_scatter(x=G.Date, y=G.leak_pct, name="درصد فرار درآمدی", yaxis="y2", mode="lines+markers", line=dict(color=WHITE, width=2))
    f.update_layout(barmode="relative", yaxis=dict(tickformat=",d"), yaxis2=dict(overlaying="y", side="right", showgrid=False, ticksuffix="٪", rangemode="tozero"))
    show(f, h)


def gap_chips(G):
    pot, col = float(G.tariff.sum()), float(G.collected.sum())
    auth, leak = float(G.authorized.sum()), float(G.loss.sum())
    real = col / pot * 100 if pot else 0
    lk = leak / (pot - auth) * 100 if pot - auth > 0 else 0
    c = lambda l, v, cls="": f'<div class="fc {cls}"><span>{l}</span><b>{v}</b></div>'  # noqa: E731
    st.markdown('<div class="fx">' + "".join([c("درآمد پتانسیل (تعرفه)", money(pot)), c("تخفیف مجاز خالص", money(auth)), c("درآمد تحقق‌یافته", money(col), "ok"),
                                              c("نرخ تحقق", f"{fa(f'{real:.2f}')}٪", "ok"), c("فرار درآمدی (زیان مشکوک)", money(leak), "bad" if leak else ""),
                                              c("نرخ فرار از پتانسیل خالص", f"{fa(f'{lk:.2f}')}٪", "bad" if lk > 1.5 else "")]) + "</div>", unsafe_allow_html=True)


def gap_breakdown(G, h):
    if G.empty:
        return empty()
    s = pd.DataFrame({"جزء": ["تخفیف مجاز", "تخفیف بدون سند", "کسری وصول", "سایر", "اضافه‌وصول (−)"],
                      "مبلغ": [G.authorized.sum(), G.unexplained.sum(), G.under.sum(), G.other.sum(), -G.over.sum()]}).sort_values("مبلغ")
    show(px.bar(s, x="مبلغ", y="جزء", orientation="h", color="جزء", color_discrete_sequence=[GRN, MINT, "#9AA9AB", YEL, CORAL]).update_layout(showlegend=False, xaxis=dict(tickformat=",d")), h)
def heat(data, height=420, title=None):
    if data is None or getattr(data, "empty", False):
        return empty()
    fig = px.imshow(data, aspect="auto", color_continuous_scale="YlOrRd", labels={"x": "ساعت", "y": "روز", "color": "تعداد"})
    if title:
        fig.update_layout(title=title)
    show(fig, height)

def page1():
    ex = TX[TX.Exited]  # عملیاتی (شامل امروز): فقط تب‌های گلوگاه و ممیزی تقلب
    exH = TXH[TXH.Exited]  # تاریخی (بدون روز جاری): همه‌ی KPI/نمودارهای مالی و مقایسه‌ای
    B = an.fin_bundle(FTH, FVH, NOCASH)
    G = an.revenue_gaps(B)
    top()
    hist_notice()
    T = st.tabs(["نمای کلی", "درآمد", "روند", "گلوگاه‌ها", "ممیزی تقلب", "سلامت (Health Score)"])
    h2 = R(2, 40)
    with T[0]:
        if not hist_empty_guard():
            q1, q2 = st.columns(2, gap="large")
            with q1.container(border=True): 
                head("درآمد از دست‌رفته و زیان مالی", "اجزای زیان به‌تفکیک روز + زیان انباشته")
                loss_panel(B, G, h2 - 70)
            with q2.container(border=True):
                head("اجزای زیان مشکوک", "سهم هر جزء از کل زیان")
                loss_components(B, h2 - 10)
            q3, q4 = st.columns([3, 2], gap="large")
            with q3.container(border=True):
                head("نشت درآمد", "تعرفه تا وصول؛ جریان قرمز = نشت")
                fin_leak(B, h2 - 10)
            with q4.container(border=True):
                head("انحراف مالی روزانه", "میله‌ی قرمز = خارج از ±۳σ")
                fin_deviation(B, h2 - 10)
    with T[1]:
        if not hist_empty_guard():
            gauge_row([("خروج رایگان بدون سند (معافیت غیرمجاز)", _pct(exH, "F_FreeExit"), 20, 5, "٪", False), ("بازپرداخت", _pct(exH, "F_Refund"), 10, 2, "٪", False),
                       ("کارتخوان بدون ثبت پوز", _pct(exH, "F_PosNoDetail"), 5, 1, "٪", False), ("ناهمخوانی پرداخت", _pct(exH, "F_PayMismatch"), 10, 1.5, "٪", False)], h=110)
            gap_chips(G)
            hg = R(1, 270)
            a1, a2 = st.columns([3, 2], gap="large")
            with a1.container(border=True):
                head("شکاف‌های درآمدی (Revenue Gaps)", "تعرفه − وصول‌شده، تجزیه به تخفیف مجاز / بدون سند / کسری / اضافه‌وصول / سایر")
                revenue_gap_chart(G, hg)
            with a2.container(border=True):
                head("مجموع اجزای شکاف در بازه")
                gap_breakdown(G, hg)
    with T[2]:
        if not hist_empty_guard():
            FL, EB = an.daily_flow(TXH), an.entry_exit_balance(TXH)
            r1a, r1b = st.columns(2, gap="large")
            with r1a.container(border=True):
                head("روند روزانه ورود و خروج", "میانگین متحرک ۷روزه")
                if FL.empty:
                    empty()
                else:
                    show(go.Figure([go.Scatter(x=FL.Date, y=FL["ورود"], name="ورود", line=dict(color=MINT)), go.Scatter(x=FL.Date, y=FL["خروج"], name="خروج", line=dict(color=YEL)),
                                    go.Scatter(x=FL.Date, y=FL["میانگین متحرک خروج (۷ روزه)"], name="میانگین متحرک خروج", line=dict(color=WHITE, dash="dot", width=3))]), h2 - 10)
            with r1b.container(border=True):
                head("روند روزانه: تعرفه، مبلغ نهایی، وصول و زیان")
                fin_daily(B, h2 - 10)
            r2a, r2b = st.columns(2, gap="large")
            with r2a.container(border=True):
                head("عدم تطابق ورود و خروج", "میله = ورود − خروج روز · خط = مانده‌ی تجمعی در بازه")
                if EB.empty:
                    empty()
                else:
                    f = go.Figure([go.Bar(x=EB.Date, y=EB["مانده خالص"], name="ورود − خروج", marker_color=[CORAL if v > 0 else TEAL for v in EB["مانده خالص"]]),
                                   go.Scatter(x=EB.Date, y=EB["مانده تجمعی"], name="مانده تجمعی", yaxis="y2", line=dict(color=WHITE))])
                    f.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False))
                    show(f, h2 - 10)
            with r2b.container(border=True):
                head("روند روزانه نرخ پرچم", "نقطه‌ی قرمز = بالاتر از حد کنترل ۳σ")
                DC = an.daily_flag_control(exH)
                if DC.empty:
                    empty()
                else:
                    o = DC[DC.out]
                    show(go.Figure([go.Scatter(x=DC.Date, y=DC.rate, name="نرخ پرچم (٪)", mode="lines+markers", line=dict(color=MINT)), go.Scatter(x=DC.Date, y=DC.UCL, name="حد بالا", line=dict(color=YEL, dash="dash")),
                                    go.Scatter(x=o.Date, y=o.rate, name="روز غیرعادی", mode="markers", marker=dict(color=CORAL, size=12))]).update_layout(yaxis=dict(ticksuffix="٪")), h2 - 10)
    with T[3]:
        HP, GB, GH = an.hourly_profile(TX), an.gate_bottlenecks(VW), an.gate_hour_matrix(VW)
        r1a, r1b = st.columns(2, gap="large")
        with r1a.container(border=True):
            head("گلوگاه ساعتی", "حجم خروج در برابر میانه‌ی ماندگاری؛ افزایش همزمان = تراکم")
            f = go.Figure([go.Bar(x=HP.Hour, y=HP["خروج"], name="تعداد خروج", marker_color=TEAL, opacity=.75),
                           go.Scatter(x=HP.Hour, y=HP["میانه ماندگاری (دقیقه)"], name="میانه ماندگاری (دقیقه)", yaxis="y2", mode="lines+markers", line=dict(color=CORAL), connectgaps=True)])
            f.update_layout(xaxis=dict(dtick=1, title="ساعت"), yaxis2=dict(overlaying="y", side="right", showgrid=False))
            show(f, h2 - 10)
        with r1b.container(border=True):
            head("گلوگاه هر گیت", "میانگین در برابر صدک ۹۵ و اوج (×ضریب اوج)")
            if GB.empty:
                empty()
            else:
                g = GB.OutputGate.astype(str)
                f = go.Figure([go.Bar(x=g, y=GB["میانگین"], name="میانگین خروج/ساعت", marker_color=MINT), go.Bar(x=g, y=GB["P95"], name="صدک ۹۵ (حد گلوگاه)", marker_color=YEL),
                               go.Scatter(x=g, y=GB["اوج"], mode="markers+text", name="اوج", text=[fa(f"×{v:.1f}") for v in GB["ضریب اوج"]], textposition="top center", marker=dict(color=CORAL, size=11))])
                f.update_layout(barmode="group")
                show(f, h2 - 10)
        r2a, r2b = st.columns(2, gap="large")
        with r2a.container(border=True):
            head("بار گیت‌ها در طول شبانه‌روز", "میانگین خروج در هر ساعت · گیت × ساعت")
            heat(GH, h2 - 10, "خروج/ساعت")
        with r2b.container(border=True):
            head("نقشه حرارتی خروج", "روز هفته × ساعت")
            hm = ex.pivot_table(index="Weekday", columns="Hour", values="ID", aggfunc="count").reindex(index=an.WD_ORDER, columns=range(24)).fillna(0)
            heat(hm, h2 - 10, "خروج")
    with T[4]:
        tab_fraud(ex, B, h2)
    with T[5]:
        if not hist_empty_guard():
            tab_health(exH, B, h2)


def tab_fraud(ex, B, h):
    """ممیزی تقلب بر پایه‌ی سند: فرجه، ناهمخوانی پلاک (MOE)، خروج شبح، نویز OCR، انحراف ظرفیت"""
    anpr, aud = opt("anpr", D0, D1), opt("inside_audit")
    grace_rate, gtab = an.grace_summary(ex, GRACE)
    pm_mask, pm_rate = an.plate_mismatch(ex, anpr)  # خوانش‌های نویزی ANPR (< 0.60) حذف می‌شوند
    gh = an.ghost_exits(ex, aud)
    asum = an.anpr_summary(anpr)
    tot_reads = float(asum["خوانش"].sum()) if len(asum) else 0.0
    ocr = float(asum["نویز (<0.60)"].sum()) / tot_reads * 100 if tot_reads else 0.0
    ghost_rate = len(gh) / len(ex) * 100 if len(ex) else 0.0
    gauge_row([("ترخیص در فرجه (Grace)", grace_rate, 20, an.GRACE_THR, "٪", False), ("ناهمخوانی پلاک ورود/خروج (MOE)", pm_rate, 10, an.MOE_THR, "٪", False),
               ("خروج شبح (Ghost Exit)", ghost_rate, 5, an.GHOST_THR, "٪", False), (f"نویز OCR (اطمینان < {an.LOW_CONF})", ocr, 30, an.OCR_THR, "٪", False)], h=110)
    sysn = an.inside_counts(INS)["live"]  # همان شمارنده‌ی واحد؛ خطای ثبت (> ۴۵ روز) خارج از موجودی زنده
    cd = an.capacity_drift(sysn, CAP_PHYS if CAP_PHYS >= 0 else None, CAP_NOM if CAP_NOM > 0 else None)
    drift_txt = fa(f"{cd['drift']:.0f}") if cd["drift"] is not None else "نیازمند شمارش فیزیکی"
    c = lambda l, v, cls="": f'<div class="fc {cls}"><span>{l}</span><b>{v}</b></div>'  # noqa: E731
    st.markdown('<div class="fx">' + "".join([c("خودروی داخل (سیستم)", fa(cd["system"])), c(f"انحراف ظرفیت (آستانه ±{an.CAP_THR})", drift_txt, "bad" if cd["flag"] else "ok"),
                                              c("عبور از ظرفیت اسمی", "بله" if cd["over"] else "خیر", "bad" if cd["over"] else "ok"),
                                              c("ناهمخوانی پلاک (تعداد)", fa(int(pm_mask.sum())), "bad" if pm_mask.sum() else ""), c("خروج شبح (تعداد)", fa(len(gh)), "bad" if len(gh) else "")]) + "</div>", unsafe_allow_html=True)
    hh = R(2, 230)
    a1, a2 = st.columns(2, gap="large")
    with a1.container(border=True):
        head("انحراف فرجه‌ی صندوقدارها", f"نرخ خروج بدون پرداخت با توقف ≤ {GRACE} دقیقه · Z > ۳ = پرت")
        if gtab.empty:
            empty("داده‌ی کافی (حداقل ۲۰ خروج برای هر شناسه) نیست.")
        else:
            show(px.bar(gtab.sort_values("Z"), x="Z", y="شناسه", orientation="h", color="Z", color_continuous_scale=[TEAL, YEL, CORAL]).add_vline(x=an.Z_OUTLIER, line_dash="dot", line_color=WHITE)
                 .update_layout(yaxis_type="category"), hh)
    with a2.container(border=True):
        head("خروج‌های شبح", "توقف < ۱۵ دقیقه برای پلاکی با رکورد رسوبی ≥ ۴۸ ساعت")
        _tbl(gh, ["ID", "PlateID", "EnrtyDateTime", "ExitDateTime", "Dur_Min", "ExitUserID", "OldEntry", "OldStayH"], hh, "خروج شبحی یافت نشد.")
    with st.container(border=True):
        head("ترددهای دارای ناهمخوانی پلاک ورود/خروج")
        x = ex[ex.Exited].loc[pm_mask[pm_mask].index] if pm_mask.any() else pd.DataFrame()
        _tbl(x, ["ID", "PlateID", "ExitPlateID", "EnrtyDateTime", "ExitDateTime", "ExitUserID", "OutputGate", "OutputGateID", "FinalPrice"], hh - 40, "ناهمخوانی پلاکی نیست.")


def _health_safe(ex, B):
    base = dict(score=None, war=0.0, w_rate=0.0, loss_share=0.0, anomaly_pct=0.0, state="neutral")
    try:
        if ex is None or len(ex) == 0:
            return base, pd.DataFrame()
        H = an.health_score(ex, B)
        H = {k: (v if k == "state" else _sf(v)) for k, v in H.items()}
    except Exception:  # noqa: BLE001
        return base, pd.DataFrame()
    try:
        HD = an.health_daily(ex, B)
        for c in ("flagged", "score"):
            HD[c] = pd.to_numeric(HD[c], errors="coerce").fillna(0)
    except Exception:  # noqa: BLE001
        HD = pd.DataFrame()
    return H, HD


def tab_health(ex, B, h):
    """Health Score = 100 − (Weighted_Anomaly_Rate × 100)"""
    H, HD = _health_safe(ex, B)
    h = max(int(h), 260)
    valid = H["score"] is not None
    score = _sf(H["score"]) if valid else 0.0
    state = H["state"] if (valid and H["state"] in STATE_C) else "neutral"
    label = {"ok": "سالم", "warn": "نیازمند پایش", "bad": "بحرانی", "neutral": "نامشخص / نرمال"}[state]

    k = st.columns(4, gap="large")
    kpi(k[0], "شاخص سلامت", f"{score:.1f}" if valid else "نامشخص", label, state, ac=TEAL,
        desc="(نشان می‌دهد سیستم از ۱۰۰٪ چقدر بدون خطا، کسری مالی یا تخلف کار می‌کند)")
    kpi(k[1], "نرخ تردد ناهنجار", f"{_sf(H['anomaly_pct']):.2f}٪", "درصد ترددهای پرچم‌دار", "neutral" if not valid else "ok", ac=MINT)
    kpi(k[2], "نرخ ناهنجاری وزنی", f"{_sf(H['war']) * 100:.2f}٪", "ترکیب ریسک و زیان", state if valid else "neutral", ac=YEL,
        desc="(میانگینی از خطاهای رفتاری ترددها به همراه نسبت پول ازدست‌رفته به کل درآمد)")
    kpi(k[3], "سهم زیان از تعرفه", f"{_sf(H['loss_share']) * 100:.2f}٪", "زیان مشکوک ÷ تعرفه", "neutral" if not valid else "ok", ac=CORAL)
    spacer(8)

    c1, c2 = st.columns([2, 3], gap="large")
    with c1.container(border=True):
        head("شاخص سلامت (Health Score)", "≥۸۵ سالم · ۷۰ تا ۸۵ نیازمند پایش · <۷۰ بحرانی")
        steps = ([dict(range=[0, an.HEALTH_WARN], color=CORAL), dict(range=[an.HEALTH_WARN, an.HEALTH_OK], color=YEL), dict(range=[an.HEALTH_OK, 100], color=GRN)]
                 if valid else [dict(range=[0, 100], color="#9AA9AB")])
        GAUGE_NUM_Y = 0.30   # تنظیم دقیق عمودی عدد داخل کمان (۰ = پایین، ۱ = بالا)
        f = go.Figure(go.Indicator(
            mode="gauge", value=score,                       # عدد پیش‌فرض Plotly خاموش است
            domain=dict(x=[0, 1], y=[0, 1]),
            gauge=dict(axis=dict(range=[0, 100], tickcolor=WHITE, tickfont=dict(color=WHITE)), bar=dict(color=DEEP, thickness=.3), bgcolor="rgba(0,0,0,0)",
                       borderwidth=0, steps=steps, threshold=dict(line=dict(color=WHITE, width=4), thickness=.85, value=score))))
        f.add_annotation(xref="paper", yref="paper", x=0.5, y=GAUGE_NUM_Y, xanchor="center", yanchor="middle", align="center", showarrow=False,
                         text=fa(f"{score:.1f}") if valid else "نامشخص",
                         font=dict(family=FONT_PL, size=40 if valid else 26, color=WHITE))
        show(f, h - 40, margin=dict(l=24, r=24, t=32, b=12))
   
        # ... بلوک روند روزانه بدون تغییر
    with c2.container(border=True):
        head("روند روزانه‌ی Health Score", "میله = درصد ترددهای پرچم‌دار · خط = امتیاز سلامت")
        if HD is None or HD.empty or "Date" not in HD:
            empty("داده‌ی کافی برای روند روزانه نیست.")
        else:
            f = go.Figure([go.Bar(x=HD.Date, y=HD.flagged, name="تردد پرچم‌دار (٪)", marker_color=CORAL, opacity=.55),
                           go.Scatter(x=HD.Date, y=HD.score, name="Health Score", yaxis="y2", mode="lines+markers", line=dict(color=TEAL, width=3))])
            f.add_hline(y=an.HEALTH_OK, line_dash="dot", line_color=GRN, yref="y2")
            f.add_hline(y=an.HEALTH_WARN, line_dash="dot", line_color=YEL, yref="y2")
            f.update_layout(yaxis=dict(ticksuffix="٪"), yaxis2=dict(overlaying="y", side="right", range=[0, 100], showgrid=False))
            show(f, h - 40)

# ============================================================ ۲ · حراست و ممیزی
def page2():
    ex = TX[TX.Exited]
    dl = opt("discount_logs", D0, D1)
    dcodes = opt("discount_codes", limit=5000)
    aud = opt("inside_audit")
    n_pb = 0
    if len(aud) and {"AuditCode", "EnrtyDateTime"} <= set(aud.columns):
        _ed = pd.to_datetime(aud.EnrtyDateTime, errors="coerce")
        n_pb = int(((pd.to_numeric(aud.AuditCode, errors="coerce") == 2) & (_ed >= D0) & (_ed < D1)).sum())  # ورود جدید بدون خروجِ قبلی برای همان پلاک
    rows = [dict(روش=v[0], دسته="مالی و پرداخت" if k != "F_Lost" else "فیش", تعداد=int(ex[k].sum()), زیان=rule_loss(ex, k), وزن=v[1], معادل_در_سند=v[2], منطق=v[3]) for k, v in an.RULES.items()]
    dr, ol = an.discount_reuse(dl), an.over_limit_codes(dcodes)
    rows += [dict(روش="ورود باز با کارت مشترک", دسته="تکرار", تعداد=int(INS.DupCard.sum()), زیان=0.0, وزن=30, معادل_در_سند="نقض توالی تردد", منطق="چند ردیف بدون خروج با CardID یکسان"),
             dict(روش="ورود باز با پلاک مشترک", دسته="تکرار", تعداد=int(INS.DupPlate.sum()), زیان=0.0, وزن=30, معادل_در_سند="نقض توالی تردد", منطق="چند ردیف بدون خروج با PlateID یکسان (PlateID>0)"),
             dict(روش="ورود مجدد بدون خروج قبلی (Passback)", دسته="تکرار", تعداد=n_pb, زیان=0.0, وزن=30, معادل_در_سند="ترددهای غیرممکن / نقض توالی تردد", منطق="Event(Entry) جدید برای پلاکی که خروج رکورد قبلی‌اش ثبت نشده (AuditCode=2)"),
             dict(روش="خودروی رسوبی (۴۸ ساعت تا ۷ روز) داخل", دسته="رسوبی", تعداد=int(INS.LongStay.sum()), زیان=0.0, وزن=15, معادل_در_سند="خودروی رسوبی", منطق="ExitDateTime IS NULL و ۴۸ ≤ Age_H ≤ ۱۶۸ ساعت"),
             dict(روش="کد تخفیف تکراری", دسته="تخفیف", تعداد=len(dr), زیان=0.0, وزن=25, معادل_در_سند="تکرار بن تخفیف", منطق="DiscountCode با بیش از یک ثبت در DiscountLogs"),
             dict(روش="کد تخفیف بیش از سقف", دسته="تخفیف", تعداد=len(ol), زیان=0.0, وزن=25, معادل_در_سند="تکرار بن تخفیف", منطق="UsedCount بیشتر از MaxCount")]
    mt = pd.DataFrame(rows)
    nflag = int((ex.NFlags > 0).sum())
    top(f"<b>{nflag:,}</b> تردد پرچم‌دار از <b>{len(ex):,}</b> خروج؛ زیان مشکوک <b>{money(an.fin_bundle(FT, FV, NOCASH)['loss'])}</b>، بیشترین زیان مربوط به <b>{mt.sort_values('زیان').iloc[-1]['روش']}</b>. "
        f"<b>{int(mt.loc[mt.دسته.isin(['تکرار', 'تخفیف', 'رسوبی']), 'تعداد'].sum())}</b> مورد تکرار/تخفیف/رسوبی هم ثبت شده.")
    T = st.tabs(["روش‌های تخلف", "نقشه زمانی و گیت", "ترددهای پرریسک", "تکرار و محدودیت‌ها", "پلاک‌های رسوبی مشکوک", "اقدامات پیشنهادی"])
    with T[0]:
        a1, a2 = st.columns([3, 2], gap="large")
        with a1.container(border=True):
            head("نقشه روش‌های تخلف", "افقی: تعداد · عمودی: زیان · اندازه: وزن ریسک")
            show(px.scatter(mt[mt.تعداد > 0], x="تعداد", y="زیان", size="وزن", color="دسته", text="روش", size_max=34, color_discrete_sequence=PALETTE).update_traces(textposition="top center"), R(2)) if (mt.تعداد > 0).any() else empty()
        with a2.container(border=True):
            head("پارتو: تعداد هر روش")
            p = mt[mt.تعداد > 0].sort_values("تعداد", ascending=False)
            if len(p):
                cum = p.تعداد.cumsum() / p.تعداد.sum()
                f = go.Figure([go.Bar(x=p.روش, y=p.تعداد, marker_color=MINT, name="تعداد"), go.Scatter(x=p.روش, y=cum, yaxis="y2", line=dict(color=YEL), name="تجمعی")])
                f.update_layout(yaxis2=dict(overlaying="y", side="right", range=[0, 1.02], tickformat=".0%", showgrid=False))
                show(f, R(2))
            else:
                empty()
        with st.container(border=True):
            head("منطق کشف هر روش", "برگرفته از سند کشف تقلب؛ ستونی که در دیتابیس نبود پیاده نشده")
            st.dataframe(mt, hide_index=True, use_container_width=True, height=R(2) - 10, column_config={"زیان": st.column_config.NumberColumn("زیان (تومان)", format="%d")})
    with T[1]:
        HR, GR, DC = an.flag_rate_by_hour(ex), an.flag_rate_by_gate(ex, GATES), an.daily_flag_control(ex)
        overall = (ex.NFlags > 0).mean() * 100 if len(ex) else 0
        h = R(2, 40)
        b1, b2 = st.columns(2, gap="large")
        with b1.container(border=True):
            head("نرخ پرچم به‌تفکیک ساعت", "میله = حجم خروج · خط = درصد ترددهای پرچم‌دار")
            f = go.Figure([go.Bar(x=HR.Hour, y=HR["خروج"], name="تعداد خروج", marker_color=MINT, opacity=.6),
                           go.Scatter(x=HR.Hour, y=HR["نرخ پرچم (٪)"], name="نرخ پرچم (٪)", yaxis="y2", mode="lines+markers", line=dict(color=CORAL))])
            f.update_layout(xaxis=dict(dtick=1, title="ساعت"), yaxis2=dict(overlaying="y", side="right", showgrid=False, ticksuffix="٪"))
            show(f, h)
        with b2.container(border=True):
            head("نرخ پرچم هر گیت خروج", "×عدد = نسبت به میانگین کل؛ بالاتر از ۱ یعنی پرریسک‌تر از معمول")
            if GR.empty:
                empty()
            else:
                f = px.bar(GR, x="گیت", y="نرخ پرچم (٪)", color="نرخ پرچم (٪)", color_continuous_scale=[TEAL, YEL, CORAL], text=[fa(f"×{v:.1f}") for v in GR["ضریب نسبت به میانگین"]])
                f.add_hline(y=overall, line_dash="dot", line_color=WHITE, annotation_text="میانگین کل")
                f.update_traces(textposition="outside", cliponaxis=False)
                f.update_layout(xaxis_title="شناسه گیت خروج", xaxis_type="category")
                show(f, h)
        with st.container(border=True):
            head("نمودار کنترل روزانه‌ی نرخ پرچم", "نقطه‌ی قرمز = روز بالاتر از حد کنترل بالا (CL±3σ)")
            if DC.empty:
                empty()
            else:
                o = DC[DC.out]
                f = go.Figure([go.Scatter(x=DC.Date, y=DC.rate, name="نرخ پرچم (٪)", mode="lines+markers", line=dict(color=MINT)),
                               go.Scatter(x=DC.Date, y=DC.CL, name="میانگین", line=dict(color=WHITE, dash="dot")),
                               go.Scatter(x=DC.Date, y=DC.UCL, name="حد بالا", line=dict(color=YEL, dash="dash")),
                               go.Scatter(x=DC.Date, y=DC.LCL, name="حد پایین", line=dict(color=YEL, dash="dash")),
                               go.Scatter(x=o.Date, y=o.rate, name="روز غیرعادی", mode="markers", marker=dict(color=CORAL, size=12))])
                f.update_layout(yaxis=dict(ticksuffix="٪"))
                show(f, h)
    with T[2]:
        hi = ex[ex.Risk_Score > 0].sort_values(["Risk_Score", "Loss"], ascending=False)
        cols = ["ID", "ExitDateTime", "ExitUserID", "OutputGate", "OutputGateID", "TotalPrice", "FinalPrice", "Cash", "Pos", "Refund", "Loss", "Risk_Score", "Risk_Level"]
        with st.container(border=True):
            c1, c2, c3 = st.columns([5, 1.2, 1.4], gap="large")
            with c1:
                head("ترددهای پرریسک", "امتیاز ریسک = مجموع وزن قوانین (سقف ۱۰۰)")
            c2.download_button("دانلود CSV", hi[cols].to_csv(index=False).encode("utf-8-sig"), "high_risk.csv", "text/csv", use_container_width=True)
            if c3.button("🖼 تصویر تردد", key="img_risk", use_container_width=True, disabled=hi.empty):
                img_dialog(hi.ID.head(500).astype(int).tolist(), True)
            st.dataframe(hi[cols].head(500), hide_index=True, use_container_width=True, height=R(1, 20),
                         column_config={"Risk_Score": st.column_config.ProgressColumn("امتیاز", min_value=0, max_value=100, format="%d"), "Loss": st.column_config.NumberColumn("زیان (تومان)", format="%d")})
    with T[3]:
        h = R(2, 40)
        dups = INS[INS.DupCard | INS.DupPlate] if len(INS) else INS
        d1_, d2_ = st.columns(2, gap="large")
        with d1_.container(border=True):
            head("ورود باز با کارت یا پلاک مشترک", "ردیف‌های بدون خروج")
            _tbl(dups, ["ID", "CardID", "PlateID", "EnrtyDateTime", "InputGate", "InputGateID", "Age_H", "DupCard", "DupPlate"], h)
        with d2_.container(border=True):
            head("کدهای تخفیف تکراری")
            _tbl(dr, ["DiscountCode", "تعداد استفاده", "تردد متفاوت", "آخرین استفاده"], h)
        with st.container(border=True):
            head("کدهای تخفیف بیش از سقف استفاده", "UsedCount > MaxCount")
            _tbl(ol, ["ID", "Code", "DiscountType", "Amount", "UsedCount", "MaxCount", "DailyCount", "Active", "ExpireDate"], h)
    with T[4]:
        sed = an.sediment_table(INS)
        with st.container(border=True):
            c1, c2, c3 = st.columns([5, 1.2, 1.4], gap="large")
            with c1:
                head("پلاک‌های رسوبی مشکوک", "بدون خروج و بدون تردد جدیدتر · ۴۸ ساعت تا ۷ روز · برای بازرسی فیزیکی حراست")
            c2.download_button("دانلود CSV", sed.to_csv(index=False).encode("utf-8-sig"), "suspect_sediment.csv", "text/csv", use_container_width=True, disabled=sed.empty)
            if c3.button("🖼 تصویر ورود", key="img_sed", use_container_width=True, disabled=sed.empty):
                img_dialog(sed["شناسه تردد"].head(500).astype(int).tolist(), False)
            if sed.empty:
                empty("پلاک رسوبی مشکوکی وجود ندارد.")
            else:
                fit_df(sed, 15, column_config={"ساعت توقف": st.column_config.ProgressColumn("ساعت توقف", min_value=0, max_value=float(an.SEDIMENT_MAX_H), format="%.0f")})
    with T[5]:
        IM = an.impact_estimates(TX, VW, INS, dl, dcodes)
        top2 = mt[mt.تعداد > 0].sort_values("تعداد", ascending=False).head(2)
        items = [P("فوری", f"بازنویسی قانون «{r['روش']}»", f"{r['تعداد']} رخداد · زیان واقعی {money(r['زیان'])}",
                   ["تبدیل شرط به قانون لحظه‌ای", "ارزیابی ماهانه مثبت کاذب", "فعال‌سازی پیامک شیفت"], "تیم علم داده", "۷ روز", r["زیان"]) for _, r in top2.iterrows()]
        ra, rb = IM["disc_reuse"][0], IM["over_limit"][0]
        items += [P("مهم" if len(dr) else "پایش", "قفل استفاده‌ی مکرر از کد تخفیف", f"{len(dr)} کد با بیش از یک استفاده · {len(ol)} کد بیش از سقف | {IM['disc_reuse'][1]}؛ {IM['over_limit'][1]}",
                    ["ابطال خودکار کد پس از اولین استفاده", "بازبینی صادرکنندگان کد"], "مدیر عملیات", "۱۰ روز", ra + rb),
                  P("مهم" if (INS.DupCard.sum() or INS.DupPlate.sum()) else "پایش", "ورودهای باز تکراری",
                    f"{int(INS.DupCard.sum())} ردیف کارت مشترک · {int(INS.DupPlate.sum())} ردیف پلاک مشترک | {IM['dup_open'][1]}",
                    ["اعتبارسنجی خروج قبل از ورود مجدد", "بازبینی دستی ردیف‌ها"], "مدیر پارکینگ", "۷ روز", IM["dup_open"][0]),
                  P(SA.ROUTING_SEV["A8"] if INS.LongStay.sum() else "پایش", "پایش خودروهای رسوبی (۴۸ ساعت تا ۷ روز)", f"{IM['long_stay'][1]}", ["بازرسی فیزیکی حراست", "تطبیق با تصویر ورود"], "حراست", "۴۸ ساعت", IM["long_stay"][0])]
        plan(items, MINT, scen=True)


# ============================================================ ۳ · عملیات و اپراتور
def page3():
    prof, z, hit = an.operator_profile(VW, tx=TX)
    watch = hit if hit else ([prof["Z ترکیبی"].idxmax()] if len(prof) else [])
    EXP = an.operator_exposure(VW, watch)
    IM = an.impact_estimates(TX, VW, INS)
    aud = opt("inside_audit")
    _c = an.inside_counts(INS)  # تنها منبع شمارش: live = حاضر + رسوبی + قدیمی‌تر از ۷ روز
    _live, _sed, _pres, _old = _c["live"], _c["sediment"], _c["present"], _c["old_open"]
    _stale = int((pd.to_numeric(aud.AuditCode, errors="coerce") == 1).sum()) if len(aud) and "AuditCode" in aud else 0
    _z = (f"<b>بازرسی سرزده برای {'، '.join(map(str, hit))}</b> لازم است (Z نسبت نقد و Z ابطال/معافیت بدون سند همزمان بیش از ۲.۵). " if hit
          else "اپراتوری از آستانه‌ی بازرسی سرزده (Z=2.5) عبور نکرده. ")
    top(_z + f"از <b>{_live:,}</b> خودروی باز (عمر کمتر از ۴۵ روز)، <b>{_pres:,}</b> حاضر جاری (کمتر از {an.LONG_STAY_H} ساعت)، "
             f"<b>{_sed:,}</b> رسوبی واقعی ({an.LONG_STAY_H} ساعت تا {an.SEDIMENT_MAX_H // 24} روز) و <b>{_old:,}</b> رکورد قدیمی‌تر از {an.SEDIMENT_MAX_H // 24} روز (مشکوک به داده‌ی مرده؛ در آمار رسوبی نیامده) است؛ "
             f"و <b>{_stale:,}</b> رکورد بدون خروج با عمر بیش از ۴۵ روز به‌عنوان خطای ثبت از موجودی زنده جدا شده است.")
    T = st.tabs(["اپراتورها", "خودروهای داخل", "اقدامات پیشنهادی"])
    with T[0]:
        h = R(2, 60)
        if prof.empty:
            empty("داده‌ی کافی برای پروفایل نیست.")
        else:
            a1, a2 = st.columns([2, 3], gap="large")
            with a1.container(border=True):
                head("Z-Score ترکیبی", "بالاتر از میانگین همکاران = مشکوک‌تر")
                zz = prof["Z ترکیبی"].sort_values().reset_index()
                zz.columns = ["اپراتور", "Z"]
                show(px.bar(zz, x="Z", y="اپراتور", orientation="h", color="Z", color_continuous_scale=[TEAL, YEL, CORAL]), h)
            with a2.container(border=True):
                head("رادار رفتار صندوقدار", "هر محور نسبت به بیشترین مقدار همکاران")
                sel = st.selectbox("صندوقدار", list(prof.index), label_visibility="collapsed")
                cols = ["نسبت نقد", "نرخ ابطال/بازپرداخت", "نرخ معافیت بدون سند", "نرخ خروج رایگان", "نرخ تخفیف", "نرخ مهمان", "نرخ اشتراکی"]
                nm = prof[cols] / prof[cols].max().replace(0, 1)
                f = go.Figure([go.Scatterpolar(r=list(nm.loc[sel]) + [nm.loc[sel].iloc[0]], theta=cols + cols[:1], fill="toself", name=str(sel), line_color=CORAL),
                               go.Scatterpolar(r=list(nm.mean()) + [nm.mean().iloc[0]], theta=cols + cols[:1], fill="toself", name="میانگین همکاران", line_color=MINT)])
                f.update_layout(polar=dict(bgcolor="rgba(0,0,0,0)", radialaxis=dict(range=[0, 1], gridcolor="rgba(255,255,255,.15)", tickfont=dict(color=WHITE)),
                                           angularaxis=dict(tickfont=dict(color=WHITE, size=12))))
                show(f, h - 40)
        r1, r2 = st.columns(2, gap="large")
        with r1.container(border=True):
            head("پروفایل اپراتورها", "بر اساس نام ExitUser")
            st.dataframe(prof.round(3), use_container_width=True, height=h, column_config={"درآمد": st.column_config.NumberColumn("درآمد (تومان)", format="%d")}) if len(prof) else empty()
        with r2.container(border=True):
            head("پروفایل بر اساس شناسه کاربر خروج", "Taradods.ExitUserID")
            st.dataframe(an.id_profile(TX).round(2), use_container_width=True, height=h, column_config={"مبلغ بازپرداخت": st.column_config.NumberColumn("مبلغ بازپرداخت (تومان)", format="%d")})
    with T[1]:
        x = INS.sort_values("Age_H", ascending=False) if len(INS) else INS
        SB = an.stay_buckets(INS)
        l_, r_ = st.columns([3, 2], gap="large")
        with l_.container(border=True):
            c1, c2 = st.columns([4, 1.4], gap="large")
            with c1:
                head("کارتابل خودروهای داخل پارکینگ", "ExitDateTime خالی · مرتب بر اساس مدت حضور")
            if c2.button("🖼 تصویر ورود", key="img_ins", use_container_width=True, disabled=x.empty):
                img_dialog(x.ID.head(200).astype(int).tolist(), False)
            if x.empty:
                empty()
            else:
                _cols = [c for c in ["ID", "CardID", "PlateID", "EnrtyDateTime", "InputGate", "InputGateID", "Age_H", "StayClass", "DupCard", "DupPlate", "IsLost"] if c in x]
                TT = st.tabs([f"رسوبی ({num(_sed)})", f"حاضر جاری ({num(_pres)})", f"قدیمی‌تر از ۷ روز ({num(_old)})"])
                for _tab, _mask, _nm in zip(TT, (x.LongStay, x.Present, x.OldOpen), ("sediment", "present", "old")):
                    with _tab:
                        _sub = x[_mask.astype(bool)]
                        if _sub.empty:
                            empty("موردی در این رده نیست.")
                        else:
                            fit_df(_sub[_cols], 12, csv_name=f"inside_{_nm}",
                                   column_config={"Age_H": st.column_config.ProgressColumn("ساعت حضور", min_value=0, max_value=float(max(_sub.Age_H.max(), 48)), format="%.0f")})
        with r_:
            with st.container(border=True):
                head("توزیع مدت حضور", "مدت توقف واقعی خودروهای خارج‌شده · یا عمر رکوردهای باز")
                mode = st.radio("نمای نمودار", ["خودروهای خارج‌شده (مدت توقف واقعی)", "رکوردهای باز داخل پارکینگ"], horizontal=True, label_visibility="collapsed", key="stay_mode")
                if mode.startswith("خودروهای خارج"):
                    EB = an.exit_stay_buckets(TX)
                    show(px.bar(EB, x="رده", y="تعداد", color="رده", text="درصد",
                                color_discrete_sequence=[GRN, GRN, GRN, GRN, YEL, CORAL, CORAL]).update_traces(texttemplate="%{text}٪").update_layout(showlegend=False), R(2, 20))
                else:
                    show(px.bar(SB, x="رده", y="تعداد", color="رده", text="تعداد",
                                color_discrete_sequence=[GRN, GRN, YEL, CORAL, CORAL, CORAL, "#888888"]).update_layout(showlegend=False), R(2, 20))
            with st.container(border=True):
                head("وضعیت خودروها به‌تفکیک گیت ورود")
                if INS.empty:
                    empty()
                else:
                    _lbl = {"حاضر": "حاضر (< ۴۸ ساعت)", "رسوبی": "رسوبی (۴۸ ساعت تا ۷ روز)", "قدیمی": "قدیمی‌تر از ۷ روز"}
                    g = INS.assign(وضعیت=np.select([INS.LongStay, INS.OldOpen], [_lbl["رسوبی"], _lbl["قدیمی"]], _lbl["حاضر"]), گیت=an.gate_labels(INS.InputGateID, GATES)).groupby(["گیت", "وضعیت"]).size().reset_index(name="تعداد")
                    show(px.bar(g, x="گیت", y="تعداد", color="وضعیت", barmode="stack", color_discrete_map={_lbl["حاضر"]: TEAL, _lbl["رسوبی"]: CORAL, _lbl["قدیمی"]: "#9AA9AB"}), R(2, 20))
    with T[2]:
        items = []
        if hit:
            items.append(P("فوری", "بازرسی سرزده", f"{'، '.join(map(str, hit))}: Z نسبت نقد و Z ابطال/معافیت بدون سند از ۲.۵ گذشت | مجموع تعرفه‌ی خروج‌های رایگان این اپراتورها در بازه",
                           ["حضور بازرس در باجه", "تطبیق دخل و ترددهای رایگان"], "مدیر شیفت", "۲۴ ساعت", EXP))
        elif len(prof):
            t = prof["Z ترکیبی"].idxmax()
            items.append(P("پایش", f"پایش {t}", f"بالاترین Z ترکیبی ({float(prof['Z ترکیبی'].max()):.2f}) | تعرفه‌ی خروج‌های رایگان او در بازه", ["مقایسه ماهانه با همکاران"], "سرپرست شیفت", "۳۰ روز", EXP))
        items.append(P(SA.ROUTING_SEV["A8"] if _sed else "پایش", "خودروهای رسوبی (۴۸ ساعت تا ۷ روز)", IM["long_stay"][1], ["بازبینی تصویر ورود", "تطبیق با دوربین طبقات", "گزارش به حراست"], "مدیر پارکینگ", "۲۴ ساعت", IM["long_stay"][0]))
        items.append(P("مهم" if (INS.DupCard.sum() or INS.DupPlate.sum()) else "پایش", "ورودهای باز تکراری", IM["dup_open"][1], ["اعتبارسنجی خروج قبل از ورود مجدد"], "مدیر پارکینگ", "۷ روز", IM["dup_open"][0]))
        plan(items, TEAL, scen=True)

# ============================================================ ۴ · فنی و تجهیزات
def _activity_source(ev, ses):
    """منبع داده‌دار برای ریتم فعالیت: EventLogs ← UserSessions ← خروج‌های Taradods"""
    if len(ev):
        return an.user_hour_matrix(ev, "UserName", "ServerTime"), "EventLogs"
    if len(ses):
        return an.user_hour_matrix(ses, "UserName", "ServerLoginTime"), "UserSessions"
    ex = TX[TX.Exited]
    return an.user_hour_matrix(ex.assign(ExitUserID=ex.ExitUserID.astype("string")), "ExitUserID", "ExitDateTime"), "Taradods (خروج‌ها)"


def page_tech():
    anpr = an.label_gate_column(opt("anpr", D0, D1), GATES, "GateId")  # GateId ← Gates.Title (نبود نگاشت: «نامشخص (ID: n)»)
    asum, agap = an.anpr_summary(anpr), an.anpr_gaps(anpr)
    noise = int(asum["نویز (<0.60)"].sum()) if len(asum) else 0
    top(f"<b>{noise:,}</b> خوانش ANPR با اطمینان زیر {an.LOW_CONF} (نویز) ثبت شده"
        + (f"؛ بیشترین وقفه‌ی خوانش مربوط به گیت <b>{agap.iloc[0]['GateId']}</b> و برابر <b>{agap.iloc[0]['بیشترین فاصله (دقیقه)']}</b> دقیقه است." if len(agap) else "."))
    T = st.tabs(["دوربین پلاک‌خوان (ANPR)", "راهبند", "ممیزی سیستم", "اقدامات پیشنهادی"])
    h = R(2, 40)
    with T[0]:
        if anpr.empty:
            empty("خوانش ANPR در این بازه نیست.")
        else:
            AD, NM, OUT = an.anpr_daily(anpr), an.anpr_noise_matrix(anpr), an.anpr_outages(anpr)
            g1, g2 = st.columns(2, gap="large")
            with g1.container(border=True):
                head("روند روزانه کیفیت ANPR", f"خط = میانگین اطمینان (مرز نویز {an.LOW_CONF}) · میله = درصد خوانش نویزی")
                f = go.Figure([go.Bar(x=AD.Date, y=AD["نرخ نویز (٪)"], name="نرخ نویز (٪)", yaxis="y2", marker_color=CORAL, opacity=.6),
                               go.Scatter(x=AD.Date, y=AD["میانگین اطمینان"], name="میانگین اطمینان", mode="lines+markers", line=dict(color=TEAL))])
                f.add_hline(y=an.LOW_CONF, line_dash="dot", line_color=YEL)
                f.update_layout(yaxis=dict(range=[0, 1]), yaxis2=dict(overlaying="y", side="right", showgrid=False, ticksuffix="٪"))
                show(f, h)
            with g2.container(border=True):
                head("نویز به‌تفکیک گیت و ساعت", "درصد خوانش زیر مرز؛ لکه‌ی روشن = افت دوربین (نور/زاویه)")
                heat(NM, h, "نویز ٪")
            g3, g4 = st.columns(2, gap="large")
            with g3.container(border=True):
                head("وقفه‌های مشکوک به قطعی دوربین", "وقفه‌های بیش از ۱۲۰ ثانیه که در ساعات پیک تردد شروع شده‌اند (سند: Drop_Duration > 120s)")
                if OUT.empty:
                    empty("وقفه‌ی مشکوکی نیست.")
                else:
                    show(px.bar(OUT.sort_values("مدت (دقیقه)"), x="مدت (دقیقه)", y="برچسب", orientation="h", color_discrete_sequence=[CORAL], text="مدت (دقیقه)"), h)
            with g4.container(border=True):
                head("خلاصه کیفیت هر گیت", "خوانش، اطمینان، نویز و بیشترین وقفه")
                sm = asum.merge(agap[["GateId", "بیشترین فاصله (دقیقه)"]], on="GateId", how="left") if len(agap) else asum
                st.dataframe(sm.round(3), hide_index=True, use_container_width=True, height=h)
    with T[1]:
        bl = an.label_gate_column(opt("barrier", D0, D1), GATES, "GateId")
        if bl.empty:
            empty("لاگ راهبند در این بازه نیست.")
        else:
            MIX, BH, BB, BM = an.barrier_daily_mix(bl), an.barrier_gate_hour(bl), an.barrier_bursts(bl), an.barrier_message_table(bl)
            k1, k2 = st.columns(2, gap="large")
            with k1.container(border=True):
                head("ترکیب پیام‌ها در طول زمان", "شش پیام پرتکرار؛ تغییر ناگهانی ترکیب = تغییر رفتار یا خرابی")
                show(px.bar(MIX, x="Date", y="تعداد", color="پیام", barmode="stack", color_discrete_sequence=PALETTE + [MINT]), h)
            with k2.container(border=True):
                head("بار رخداد هر راهبند در شبانه‌روز", "تعداد کل پیام‌ها · گیت × ساعت")
                heat(BH, h, "رخداد")
            k3, k4 = st.columns(2, gap="large")
            with k3.container(border=True):
                head("انفجار رخداد (Burst)", "ساعت‌هایی که تعداد پیام گیت از میانگین خودش بیش از ۳ انحراف معیار بالاتر است")
                _tbl(BB, ["GateId", "زمان", "تعداد", "Z"], h, "ساعت غیرعادی‌ای نیست.")
            with k4.container(border=True):
                head("پرتکرارترین پیام‌ها", "سهم، اولین و آخرین مشاهده")
                _tbl(BM, ["Message", "تعداد", "سهم (٪)", "اولین", "آخرین"], h)
    with T[2]:
        ev, ses = opt("events", D0, D1), opt("sessions", D0, D1)
        mat, src = _activity_source(ev, ses)
        m1, m2 = st.columns(2, gap="large")
        with m1.container(border=True):
            head("ریتم کاری کاربران", f"کاربر × ساعت · منبع: {src}")
            heat(mat, h, "رویداد")
        with m2.container(border=True):
            head("سهم فعالیت شبانه‌ی هر کاربر (۲۲ تا ۰۶)", "کاربران بالای نمودار خارج از ساعات کاری فعال‌ترند")
            if mat.empty:
                empty()
            else:
                nc = [c for c in mat.columns if str(c).isdigit() and (int(c) >= NIGHT[0] or int(c) < NIGHT[1])]

                ns = (mat[nc].sum(axis=1) / mat.sum(axis=1).replace(0, np.nan) * 100).fillna(0).sort_values().reset_index()
                ns.columns = ["کاربر", "سهم شبانه (٪)"]
                show(px.bar(ns, x="سهم شبانه (٪)", y="کاربر", orientation="h", color="سهم شبانه (٪)", color_continuous_scale=[TEAL, YEL, CORAL]), h)
        with st.container(border=True):
            head("نشست‌های کاربران", "نشست‌های بدون زمان خروج و ورودهای شبانه")
            if ses.empty:
                empty()
            else:
                s = ses.assign(ServerLoginTime=pd.to_datetime(ses.ServerLoginTime), ServerLogoffTime=pd.to_datetime(ses.ServerLogoffTime))
                s["ورود شبانه"] = (s.ServerLoginTime.dt.hour >= NIGHT[0]) | (s.ServerLoginTime.dt.hour < NIGHT[1])
                s["بدون خروج"] = s.ServerLogoffTime.isna()
                _tbl(s[s["ورود شبانه"] | s["بدون خروج"]].head(300), ["ID", "UserName", "ClientIP", "ServerLoginTime", "ServerLogoffTime", "LogoffReason", "ورود شبانه", "بدون خروج"], h, "نشست مشکوکی نیست.")
    with T[3]:
        items = []
        if len(agap):
            items.append(P("مهم", f"بررسی دوربین گیت {agap.iloc[0]['GateId']}", f"بیشترین وقفه خوانش {agap.iloc[0]['بیشترین فاصله (دقیقه)']} دقیقه", ["تست دوربین و نور", "بررسی قطعی شبکه"], "فنی سخت‌افزار", "۷ روز", 0))
        items.append(P("پایش", "کالیبراسیون ANPR", f"{noise:,} خوانش زیر {an.LOW_CONF} (نویز)", ["تنظیم زاویه و نور", "حذف نویز از شاخص تقلب"], "فنی سخت‌افزار", "۳۰ روز", 0))
        items.append(P("پایش", "بازبینی نشست‌های شبانه و بدون خروج", "در تب «ممیزی سیستم» فهرست شده است", ["تطبیق با برنامه‌ی شیفت", "قفل خودکار نشست‌های باز"], "مدیر فناوری", "۱۴ روز", 0))
        plan(items, TEAL)

# ============================================================ ۵ · مالی و حسابداری
def page4():
    exH = TXH[TXH.Exited]  # تاریخی (بدون روز جاری)؛ هم‌مجموعه با B
    B = an.fin_bundle(FTH, FVH, NOCASH)
    # منابع روزانه‌ی مقایسه‌ای (تراکنش پوز، درآمد واقعی، لاگ تخفیف) نیز فقط تا پایان دیروز؛ درآمد واقعیِ امروز هنوز ثبت/بسته نشده و کسری جعلی می‌سازد
    pos = an.historical_only(opt("pos_tx", D0, D1), "TransactionDateTime", NOW)
    ri = an.historical_only(opt("real_income", D0, D1), "DateTime", NOW)
    dl, codes, guests = opt("discount_logs", D0, D1), opt("discount_codes", limit=5000), opt("guests", D0, D1)
    dlH = an.historical_only(dl, "InsertDate", NOW)
    cards, blocked = opt("cards", limit=5000), opt("blocked", limit=2000)
    dr, ol = an.discount_reuse(dl), an.over_limit_codes(codes)
    pos_sum = float(pd.to_numeric(pos.Amount, errors="coerce").sum()) if len(pos) else 0.0
    edited = int(ri.IsEdited.fillna(False).astype(bool).sum()) if len(ri) else 0
    _verdict = (f"مبلغ نهایی <b>{money(B['billed_tx'])}</b>، وصول‌شده <b>{money(B['collected'])}</b> و زیان مشکوک <b>{money(B['loss'])}</b>؛ تراکنش‌های کارتخوان ثبت‌شده <b>{money(pos_sum)}</b>. "
        f"<b>{len(dr)}</b> کد تخفیف تکراری، <b>{len(ol)}</b> کد بیش از سقف و <b>{edited}</b> ردیف درآمد واقعی ویرایش‌شده دیده شد؛ پلاک مسدود: <b>{len(blocked)}</b>.")
    top("" if HIST_EMPTY else _verdict)  # بدون دادهٔ تاریخی، خلاصه‌ی صفرِ گمراه‌کننده نشان داده نمی‌شود (پیام در تب‌ها)
    hist_notice()
    T = st.tabs(["تحلیل مالی دقیق", "درآمد ثبت‌شده و واقعی", "تخفیف", "اشتراک، مهمان و مسدودی", "اقدامات پیشنهادی"])
    with T[0]:
        if not hist_empty_guard():
            fin_main(B, FH)
    with T[1]:
        if not hist_empty_guard():
            _rm, _, _ = an.income_recon(exH, ri)
            _short, _cdr = an.cash_deficit_rate(_rm)
            g1, g2 = st.columns([1, 2], gap="large")
            with g1:
                gauge_row([("نرخ کسری نقد صندوق (CDR)", _cdr, 5, 1.5, "٪", False)], h=130)
            with g2:
                st.markdown(f'<div class="fx"><div class="fc {"bad" if _cdr > 1.5 else "ok"}"><span>کسری نقد (سامانه − واقعی)</span><b>{money(_short)}</b></div>'
                            f'<div class="fc ok"><span>تراکنش‌های کارتخوان ثبت‌شده (POS)</span><b>{money(pos_sum)}</b></div>'
                            f'<div class="fc"><span>ردیف‌های ویرایش‌شده‌ی درآمد واقعی</span><b>{fa(edited)}</b></div></div>', unsafe_allow_html=True)
            RT = st.tabs(["روند روزانه", "ردیف‌های درآمد واقعی"])
            with RT[0]:
                with st.container(border=True):
                    head("درآمد روزانه: ثبت‌شده در سامانه در برابر واقعی", "CDR = Σ max(نقد سامانه − نقد واقعی، 0) ÷ Σ نقد سامانه")
                    sysd = exH.groupby("Date").agg(نقد_سامانه=("Cash", "sum"), کارتخوان_سامانه=("Pos", "sum"))
                    if len(ri):
                        r = ri.assign(d=pd.to_datetime(ri.DateTime).dt.normalize(), Pecuniary=pd.to_numeric(ri.Pecuniary, errors="coerce"), Pos=pd.to_numeric(ri.Pos, errors="coerce")).groupby("d").agg(نقد_واقعی=("Pecuniary", "sum"), کارتخوان_واقعی=("Pos", "sum"))
                        m = sysd.join(r, how="outer").fillna(0)
                        m.index.name = "تاریخ"
                        show(px.line(m.reset_index().melt("تاریخ"), x="تاریخ", y="value", color="variable", markers=True, color_discrete_sequence=PALETTE), FH, M_TIGHT)
                    else:
                        empty("درآمد واقعی در این بازه ثبت نشده.")
            with RT[1]:
                with st.container(border=True):
                    head("ردیف‌های درآمد واقعی", "IsEdited و Closed")
                    if len(ri):
                        st.dataframe(ri[["ID", "DateTime", "Pecuniary", "Pos", "CreatorUserID", "SupervisorID", "IsEdited", "Closed", "SetPosTransaction"]].head(300), hide_index=True, use_container_width=True, height=FH)
                    else:
                        empty()
    with T[2]:
        DT = st.tabs(["کدهای تکراری و مصرف", "تخفیف به تفکیک صندوقدار"])
        with DT[0]:
            t1, t2 = st.columns(2, gap="large")
            with t1.container(border=True):
                head("کدهای تخفیف تکراری")
                st.dataframe(dr, hide_index=True, use_container_width=True, height=FH) if len(dr) else empty("موردی نیست.")
            with t2.container(border=True):
                head("مصرف کدها نسبت به سقف", "ViewDiscountCodes")
                if len(codes):
                    q = codes.assign(UsedCount=pd.to_numeric(codes.UsedCount, errors="coerce"), MaxCount=pd.to_numeric(codes.MaxCount, errors="coerce")).dropna(subset=["MaxCount"])
                    q = q[q.MaxCount > 0].assign(نسبت=lambda x: x.UsedCount / x.MaxCount).sort_values("نسبت", ascending=False).head(20)
                    show(px.bar(q, x="Code", y="نسبت", color_discrete_sequence=[YEL]), FH, M_TIGHT) if len(q) else empty()
                else:
                    empty()
        with DT[1]:
            with st.container(border=True):
                head("سهم ترددهای تخفیف‌خورده به تفکیک صندوقدار")
                ve = VW[VW.Exited & VW.ExitUser.notna()]
                if len(ve):
                    g = ve.groupby("ExitUser").IsDiscount.mean().sort_values().tail(15).reset_index()
                    show(px.bar(g, x="IsDiscount", y="ExitUser", orientation="h", color_discrete_sequence=[CORAL]).update_xaxes(tickformat=".0%"), FH, dict(l=20, r=20, t=35, b=25))
                else:
                    empty()
    with T[3]:
        UT = st.tabs(["ثبت‌های مهمان", "کارت‌های اشتراکی منقضی‌شده", "پلاک‌های مسدود"])
        with UT[0]:
            with st.container(border=True):
                head("ثبت‌های مهمان", "Guests · جدیدترین بالا")
                if len(guests):
                    g = guests.assign(CreateDate=pd.to_datetime(guests.CreateDate, errors="coerce"))
                    g["تعداد ثبت این کاربر"] = g.groupby("UserID").UserID.transform("size")
                    g = g.sort_values("CreateDate", ascending=False)[["ID", "UserID", "TaradodID", "CreateDate", "تعداد ثبت این کاربر"]]
                    st.dataframe(g.head(500), hide_index=True, use_container_width=True, height=FH)
                else:
                    empty()
        with UT[1]:
            with st.container(border=True):
                head("کارت‌های اشتراکی منقضی‌شده ولی فعال", "Active=1 و EndDate گذشته")
                x = pd.DataFrame()
                if len(cards):
                    x = cards.assign(EndDate=pd.to_datetime(cards.EndDate, errors="coerce"))
                    x = x[x.Active.fillna(False).astype(bool) & (x.EndDate < NOW)]
                if len(x):
                    st.dataframe(x[["FullName", "TagID", "Title", "EndDate"]].head(200), hide_index=True, use_container_width=True, height=FH)
                else:
                    empty("موردی نیست.")
        with UT[2]:
            with st.container(border=True):
                head("پلاک‌های مسدود", "BlockPlates ← Plates")
                if len(blocked):
                    st.dataframe(blocked[["Id", "P1", "P2", "P3", "P4", "CreateDate", "UpdateDate"]], hide_index=True, use_container_width=True, height=FH)
                else:
                    empty()
    with T[4]:
        if not hist_empty_guard():
            IM = an.impact_estimates(TXH, VWH, INS, dlH, codes)
            _rec, rec_abs, rec_edit = an.income_recon(exH, ri)
            plan(fin_items(B) + [
                P("فوری" if len(dr) else "پایش", "کنترل کد تخفیف", f"{len(dr)} کد تکراری · {len(ol)} کد بیش از سقف | {IM['disc_reuse'][1]}؛ {IM['over_limit'][1]}",
                  ["ابطال خودکار پس از استفاده", "بررسی صادرکنندگان کد"], "مدیر عملیات", "۱۰ روز", IM["disc_reuse"][0] + IM["over_limit"][0]),
                P("مهم" if edited else "پایش", "ممیزی ویرایش درآمد واقعی", f"{edited} ردیف RealIncomes ویرایش شده | مجموع |اختلاف| درآمد سامانه و واقعی در روزهای ویرایش‌شده: {money(rec_edit)}",
                  ["مقایسه نسخه‌ی قبل و بعد با سرپرست", "الزام تایید دوم برای ویرایش"], "حسابرسی داخلی", "۷ روز", rec_edit),
                P("مهم", "تطبیق درآمد سامانه با درآمد واقعی", f"مجموع قدرمطلق اختلاف روزانه (نقد+کارتخوان): {money(rec_abs)}",
                  ["تعیین رابطه‌ی رسمی بین RealIncomes و Taradods", "گزارش تفاوت روزانه به مدیر مالی"], "مدیر مالی", "۳۰ روز", rec_abs)], GRN, scen=True, days=HIST_DAYS)
# ============================================================ ۶ · هشدار لحظه‌ای شیفت
# ============================================================ ۶ · هشدار لحظه‌ای شیفت
# (کل این بخش را جایگزین تابع page5 قبلی کنید؛ یعنی از خط «# ===== ۶ · هشدار لحظه‌ای شیفت» تا قبل از «# ===== ثبت داشبوردها و صفحه‌ی اصلی»)
import html as _html  # noqa: E402


def _safe(fn):
    """خطای یک تب، بقیه‌ی صفحه را نمی‌شکند"""
    def w(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            st.warning(f"نمایش این بخش با خطا مواجه شد ({type(e).__name__}): {str(e)[:160]}", icon="⚠️")
    return w


def _plain(d):
    """st.secrets (AttrDict) → dict ساده"""
    try:
        return {str(k): (list(v) if isinstance(v, (list, tuple)) else (dict(v) if hasattr(v, "items") else v)) for k, v in dict(d).items()}
    except Exception:  # noqa: BLE001
        return {}


def _sms_cfg():
    try:
        return _plain(st.secrets.get("sms", {}))
    except Exception:  # noqa: BLE001
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def _live_bundle(day_s, now_s, tol, nocash, op_items):
    """داده‌ی «روز جاری» کامل و زنده (بدون حذف امروز)؛ مستقل از بازه‌ی سایدبار عمومی"""
    def fetch(name, *a):
        df, err = db.get(name, *a)
        if err:
            raise RuntimeError(err)
        return df
    return SA.load_live(fetch, pd.Timestamp(day_s), pd.Timestamp(now_s), tol=tol, nocash=nocash, op_mapping=dict(op_items))


@st.cache_data(ttl=60, show_spinner=False)
def _diag():
    try:
        return db.diagnose()
    except Exception as e:  # noqa: BLE001
        return "error", str(e)[:200]


def _al_today():
    j = SA.gregorian_to_jalali(SA.current_shift(NOW)[0]) or SA.gregorian_to_jalali(pd.Timestamp.now()) or (1405, 1, 1)
    st.session_state.update(al_jy=j[0], al_jm=j[1], al_jd=j[2], al_shift=SA.current_shift(NOW)[1])


def _alert_sidebar():
    cur_day, cur_shift = SA.current_shift(NOW)
    jn = SA.gregorian_to_jalali(cur_day) or SA.gregorian_to_jalali(pd.Timestamp.now()) or (1405, 1, 1)
    ss = st.session_state
    ss.setdefault("al_jy", jn[0])
    ss.setdefault("al_jm", jn[1])
    ss.setdefault("al_jd", jn[2])
    ss.setdefault("al_shift", cur_shift)
    with st.sidebar:
        st.markdown("#### فیلترهای هشدار")
        st.caption("تاریخ شیفت (شمسی)")
        years = sorted(set(range(jn[0] - 3, jn[0] + 1)) | {int(ss["al_jy"])})
        c1, c2, c3 = st.columns([1.1, 1.6, 1])
        jy = c1.selectbox("سال", years, key="al_jy", format_func=fa, label_visibility="collapsed")
        jm = c2.selectbox("ماه", list(range(1, 13)), key="al_jm", format_func=lambda m: SA.JALALI_MONTHS[int(m) - 1], label_visibility="collapsed")
        jd = c3.selectbox("روز", list(range(1, 32)), key="al_jd", format_func=fa, label_visibility="collapsed")
        st.button("📅 شیفت جاری", key="al_today", on_click=_al_today, use_container_width=True)
        day = SA.jalali_to_gregorian(jy, jm, jd)
        if day is None:  # مثلاً ۳۱ مهر یا ۳۰ اسفند در سال غیرکبیسه
            md = SA.jalali_month_days(jy, jm)
            day = SA.jalali_to_gregorian(jy, jm, min(int(jd), md))
            st.caption(f"روز {fa(jd)} در این ماه وجود ندارد؛ روز {fa(min(int(jd), md))} در نظر گرفته شد.")
        if day is None or pd.isna(day):
            day = cur_day
        day = pd.Timestamp(day).normalize()
        all_on = bool(ss.get("al_all", False))
        shift = st.radio("شیفت", SA.SHIFT_NAMES, horizontal=True, key="al_shift", disabled=all_on)
        sevf = st.multiselect("شدت", ["فوری", "مهم", "پایش"], default=["فوری", "مهم", "پایش"], key="al_sev")
        tog = getattr(st, "toggle", st.checkbox)
        all_on = bool(tog("🕒 همه شیفت‌ها", key="al_all", help="هشدارهای هر ۳ شیفتِ تاریخ انتخابی (صبح ۰۶–۱۴ ، عصر ۱۴–۲۲ ، شب ۲۲–۰۶ روز بعد) با برچسب شیفت"))
        if all_on:
            st.caption("بازه: ۰۶:۰۰ تا ۰۶:۰۰ روز بعد (انتهای هر بازه انحصاری)")
        else:
            t0, t1 = SA.shift_window(day, shift)
            st.caption(f"بازه: {fa(f'{t0:%H:%M}')} تا {fa(f'{t1:%H:%M}')}" + (" روز بعد" if t1.normalize() > t0.normalize() else "") + " (انتهای بازه انحصاری)")
        st.divider()
    return day, (SA.ALL_SHIFTS if all_on else shift), sevf, all_on


def _alert_heat(m, hh, mark_last=False):
    z = m.values
    f = go.Figure(go.Heatmap(
        z=z, x=[_xlab(c) for c in m.columns], y=[fa(i) for i in m.index],
        text=[[fa(int(v)) if v else "" for v in row] for row in z], texttemplate="%{text}",
        textfont=dict(color="#FFFFFF", size=15), zmin=0, zmax=max(3, int(z.max())), xgap=3, ygap=3,
        showscale=False, hoverongaps=False,
        colorscale=[[0, "rgba(255,255,255,.07)"], [.0001, "#1F8A74"], [.5, "#B7791F"], [1, "#C9473A"]],
        hovertemplate="%{y}<br>%{x} ← %{z} هشدار<extra></extra>"))
    f.update_yaxes(autorange="reversed", automargin=True, showgrid=False)
    f.update_xaxes(showgrid=False)
    if mark_last:  # دور ستون روز انتخاب‌شده کادر زرد می‌کشد
        f.add_shape(type="rect", xref="x", yref="paper", x0=len(m.columns) - 1.5, x1=len(m.columns) - .5, y0=0, y1=1, line=dict(color=YEL, width=2))
    show(f, max(hh, 38 * len(m) + 80))


def _who(r):
    roles = list(r["roles"]) if isinstance(r["roles"], (list, tuple)) else []
    parts: list[str] = [str(SA.ROLE_FA.get(x, x)) for x in roles if x != "operator"]
    if "operator" in roles:
        ph = [str(p) for p in (r["phones"] or []) if p]
        parts.append(f"اپراتور {SA.mask_phone(ph[0])}" if ph else "اپراتور (شماره نامشخص)")
    return " + ".join(parts) or "فقط داشبورد"

def _alert_table(a):
    if a is None or a.empty:
        return pd.DataFrame()
    return pd.DataFrame({
        "شدت": a["severity"].values,
        "شیفت": [s + (" (موقت)" if p else "") for s, p in zip(a["shift"], a["provisional"])],
        "قانون": [f"{c} · {t}" for c, t in zip(a["rule"], a["title"])],
        "اپراتور / گیت": [" · ".join(x for x in (o, g) if x) or "-" for o, g in zip(a["operator"], a["gate"])],
        "تعداد": a["count"].values,
        "زمان (شمسی)": [fa(SA.jalali_text(t, True)) for t in a["time"]],
        "پیام": a["message"].values,
        "گیرنده": [_who(r) for _, r in a.iterrows()],
    })


def _cards(a, ncols=3, limit=9):
    if a is None or a.empty:
        return
    rows = [r for _, r in a.head(limit).iterrows()]
    for i in range(0, len(rows), ncols):
        for col, r in zip(st.columns(ncols, gap="large"), rows[i:i + ncols]):
            pills = f'<span class="pill" style="--pc:#9AA9AB">شیفت {_html.escape(str(r["shift"]))}</span>'
            if r["provisional"]:
                pills += f'<span class="pill" style="--pc:{MINT}">{SA.PROVISIONAL}</span>'
            if r["rule"] == "A11" and r["phone_missing"]:
                pills += f'<span class="pill" style="--pc:{CORAL}">شماره اپراتور نامشخص</span>'
            meta = f'<span>گیرنده: <b>{_html.escape(_who(r))}</b></span>'
            if r["operator"] or r["gate"]:
                meta += f'<br><span>{_html.escape(" · ".join(x for x in (r["operator"], r["gate"]) if x))}</span>'
            col.markdown(f'<div class="acard" style="--pc:{PCOL[r["severity"]]}"><div class="ah"><span class="pill">{r["severity"]}</span><b>{_html.escape(str(r["title"]))}</b></div>'
                         f'<div class="am">{pills}</div><div class="ae">{fa(_html.escape(str(r["message"])))}</div><div class="am">{meta}</div></div>', unsafe_allow_html=True)


def _rows_by_sev(m, sev):
    if m is None or m.empty:
        return pd.DataFrame()
    keep = [i for i in m.index if SA.ROUTING_SEV.get(str(i).split(" · ")[0]) == sev]
    m = m.loc[keep]
    return m[m.sum(axis=1) > 0]


@_safe
def _sev_tab(sev, al, hist, day, sel, all_on, live_any, h):
    a = al[al.severity == sev].reset_index(drop=True) if len(al) else al
    n_rules = int(a["rule"].nunique()) if len(a) else 0
    amt = float(a["amount"].sum()) if len(a) else 0.0
    c1, c2, c3 = st.columns(3, gap="large")
    kpi(c1, f"هشدار {sev}", num(len(a)), "در بازه‌ی انتخابی" + (" · موقت تا این لحظه" if live_any else ""), "bad" if (len(a) and sev == "فوری") else ("warn" if len(a) else "ok"), ac=PCOL[sev])
    kpi(c2, "قانون فعال", num(n_rules), "از " + num(sum(1 for r_ in SA.RULES_INFO if r_[2] == sev)) + " قانون این شدت", "neutral", ac=PCOL[sev])
    kpi(c3, "مبلغ مرتبط", money(amt), "جمع مبلغ هشدارها (در صورت وجود)", "warn" if amt else "ok", ac=PCOL[sev])
    st.divider()
    if a.empty:
        st.success(f"هشدار {sev}ی در این بازه ثبت نشده است.")
    else:
        _cards(a)
        st.divider()
        head("جدول همه‌ی هشدارها", f"{num(len(a))} مورد")
        fit_df(_alert_table(a), 12, csv_name=f"alerts_{SA.SEV_ORDER[sev]}")
    st.divider()
    x1, x2 = st.columns(2, gap="large")
    with x1.container(border=True):
        head("مقایسه با ۷ روز اخیر", "قانون × روز (شمسی) · " + ("همه‌ی شیفت‌ها" if all_on else f"شیفت {sel}") + " · کادر زرد = روز انتخاب‌شده")
        hs = _rows_by_sev(hist, sev)
        _alert_heat(hs, h, mark_last=True) if len(hs) else empty("در ۷ روز اخیر هشداری با این شدت ثبت نشده است.")
    with x2.container(border=True):
        head("نقشه‌ی ساعتی هشدارها", "قانون × ساعت · هرچه سلول پررنگ‌تر، هشدار بیشتر")
        hm = SA.hour_matrix(a, day, None if all_on else sel)
        _alert_heat(hm, h) if len(hm) else empty("هشداری برای نمایش ساعتی نیست.")


@_safe
def _monitor_tab(al, D, day, h, live_any):
    ins = D["inside"]
    has = isinstance(ins, pd.DataFrame) and len(ins) > 0 and "LongStay" in ins
    c = an.inside_counts(ins if has else None)  # همان شمارنده‌ی صفحه‌ی ۳ و قانون A8
    dup = c["dup_card"] + c["dup_plate"]
    a8 = SA.ROUTING_SEV["A8"]
    st.caption("شاخص‌های این تب وضعیت «لحظه‌ی اکنون» هستند و به تاریخ/شیفت انتخابی وابسته نیستند؛ نمودارهای روند تا روز انتخابی‌اند.")
    k = st.columns(4, gap="large")
    kpi(k[0], "خودروی داخل پارکینگ", num(c["live"]), f"حاضر جاری (کمتر از {num(an.LONG_STAY_H)} ساعت): {num(c['present'])}", "neutral", ac=MINT)
    kpi(k[1], f"رسوبی ({num(an.LONG_STAY_H)} ساعت تا {num(an.SEDIMENT_MAX_H // 24)} روز)", num(c["sediment"]),
        f"شدت {a8} · گزارش به حراست · بیشترین توقف {num(c['max_sediment_h'])} ساعت" if c["sediment"] else "موردی ثبت نشده",
        "bad" if c["sediment"] else "ok", ac=PCOL[a8])
    kpi(k[2], f"قدیمی‌تر از {num(an.SEDIMENT_MAX_H // 24)} روز", num(c["old_open"]), f"مشکوک به داده‌ی مرده · خارج از آمار رسوبی · حد خطای ثبت {num(an.STALE_H / 24)} روز",
        "warn" if c["old_open"] else "ok", ac=MINT)
    kpi(k[3], "ورود باز تکراری", num(dup), "ردیف با کارت/پلاک مشترک", "warn" if dup else "ok", ac=MINT)
    spacer(10)
    live_al = al[(al.severity == "پایش") | al["rule"].isin(SA.LIVE_ONLY)].reset_index(drop=True) if len(al) else al
    if live_al is None or live_al.empty:
        st.success("هشدار لحظه‌ای (A8/A9) فعالی وجود ندارد." if live_any else "هشدارهای لحظه‌ای (A8/A9) فقط برای شیفتِ در جریان محاسبه می‌شوند.")
    else:
        _cards(live_al)
    spacer(6)
    TT = st.tabs(["نمودارها", "طولانی‌ترین توقف‌ها", "کیفیت پلاک‌خوان"])
    with TT[0]:
        x1, x2 = st.columns(2, gap="large")
        with x1.container(border=True):
            head("توزیع مدت توقف خودروهای داخل", "رده‌های توقف · بدون خروج")
            try:
                b_ = an.stay_buckets(ins if has else None)
                f = px.bar(b_, x="رده", y="تعداد", text="تعداد")
                f.update_traces(marker_color=MINT, textposition="outside")
                show(f, h)
            except Exception as e:  # noqa: BLE001
                empty(f"رسم نمودار ممکن نشد: {str(e)[:100]}")
        with x2.container(border=True):
            head(f"روند خروج با توقف بیش از {num(an.LONG_STAY_H)} ساعت", "۱۴ روز منتهی به روز انتخابی")
            tx = D["tx"]
            try:
                end = pd.Timestamp(day).normalize()
                idx = pd.date_range(end - pd.Timedelta(days=13), end, freq="D")
                if len(tx) and "Dur_Min" in tx:
                    e = tx[tx.Exited.astype(bool) & (pd.to_numeric(tx.Dur_Min, errors="coerce") >= an.LONG_STAY_H * 60)]
                    s_ = e.groupby(pd.to_datetime(e["Date"]).dt.normalize()).size().reindex(idx, fill_value=0)
                else:
                    s_ = pd.Series(0, index=idx)
                f = px.bar(pd.DataFrame({"تاریخ": s_.index, "تعداد": s_.values}), x="تاریخ", y="تعداد")
                f.update_traces(marker_color=YEL)
                show(f, h)
            except Exception as e:  # noqa: BLE001
                empty(f"رسم نمودار ممکن نشد: {str(e)[:100]}")
    with TT[1]:
        with st.container(border=True):
            head("طولانی‌ترین توقف‌های رسوبی", f"{num(an.LONG_STAY_H)} ساعت تا {num(an.SEDIMENT_MAX_H // 24)} روز · نام گیت از dbo.Gates")
            if has and c["sediment"]:
                lt = ins[ins.LongStay].sort_values("Age_H", ascending=False)
                fit_df(pd.DataFrame({"شناسه تردد": lt["ID"].values, "کارت": lt["CardID"].values,
                                     "ورود (شمسی)": [fa(SA.jalali_text(t, True)) for t in lt["EnrtyDateTime"]],
                                     "ساعت توقف": lt["Age_H"].values, "گیت ورود": [SA.gate_label(g, D["gates"]) for g in lt["InputGateID"]]}),
                       15, csv_name="monitor_sediment",
                       column_config={"ساعت توقف": st.column_config.ProgressColumn("ساعت توقف", min_value=0, max_value=float(an.SEDIMENT_MAX_H), format="%.0f")})
            else:
                empty("خودروی رسوبی (۴۸ ساعت تا ۷ روز) وجود ندارد.")
    with TT[2]:
        with st.container(border=True):
            head("روند کیفیت پلاک‌خوان", f"نرخ نویز (اطمینان < {an.LOW_CONF}) · ۱۴ روز")
            try:
                ad = an.anpr_daily(D["anpr"]) if len(D["anpr"]) else pd.DataFrame()
                ad = ad[(ad["Date"] <= pd.Timestamp(day)) & (ad["Date"] >= pd.Timestamp(day) - pd.Timedelta(days=13))] if len(ad) else ad
                if len(ad):
                    f = px.line(ad, x="Date", y="نرخ نویز (٪)", markers=True)
                    f.update_traces(line_color=CORAL)
                    show(f, h)
                else:
                    empty("داده‌ی ANPR برای این بازه موجود نیست.")
            except Exception as e:  # noqa: BLE001
                empty(f"رسم نمودار ممکن نشد: {str(e)[:100]}")


@_safe
def _rules_tab():
    with st.container(border=True):
        head("قوانین هشدار", "شدت و مسیر ارسال هر قانون")
        fit_df(SA.rules_table(), 20)
        st.caption(f"آستانه‌ی نویز ANPR: {num(SA.TH['anpr_noise_burst'])} خوانش در شیفت · کسری وصول = FinalPrice − (Cash + Pos − Refund) بیش از «حد تحمل اختلاف وصول» سایدبار · PaymentIDهای مستثنا در سایدبار اعمال می‌شود.")


@_safe
def _sms_tab(al, D, sec, rec, h):
    l_, r_ = st.columns([2, 3], gap="large")
    with l_.container(border=True):
        head("گیرندگان مدیریتی", "شماره‌ها را در secrets بخش [sms.recipients] بگذارید")
        st.dataframe(pd.DataFrame([{"نقش": SA.ROLE_FA.get(k, k), "شماره‌ها": "، ".join(SA.mask_phone(SA.norm_phone(x) or x) for x in v_)} for k, v_ in rec.items()]),
                     hide_index=True, use_container_width=True, height=150)
        st.divider()
        head("اپراتورهای هدف کسری وصول", "شماره از [sms.operators] (rbac.Users ستون موبایل ندارد)")
        a11 = al[al["rule"] == "A11"] if len(al) else al
        if len(a11):
            st.dataframe(pd.DataFrame({"اپراتور": a11["operator"].values, "گیت": a11["gate"].values, "شیفت": a11["shift"].values,
                                       "موبایل": [SA.mask_phone(p[0]) if p else "شماره اپراتور نامشخص" for p in a11["phones"]]}),
                         hide_index=True, use_container_width=True, height=170)
        else:
            empty("کسری وصولی برای اپراتورها در این بازه ثبت نشده است.")
        st.divider()
        real = st.checkbox("ارسال واقعی (نیازمند کلید API در secrets)", value=False)
        minsev = st.selectbox("حداقل شدت برای ارسال", ["فوری", "مهم", "پایش"], index=1)
        if st.button("ارسال پیامک‌ها", type="primary", disabled=al.empty, use_container_width=True):
            gw = SA.SMSGateway(sec.get("provider"), sec.get("api_key"), sec.get("sender"), dry_run=not real)
            res = SA.dispatch(al, gw, rec, SA.AlertStore() if real else None, minsev)
            if SA.is_demo():
                st.toast("حالت آفلاین: پیامک واقعی ارسال نشد (شبیه‌سازی)", icon="📵")
            if res:
                st.dataframe(pd.DataFrame(res), hide_index=True, use_container_width=True, height=160)
                st.success(f"{sum(bool(r2.get('ok')) for r2 in res)} از {len(res)} ارسال موفق" + (" (حالت آزمایشی؛ پیامکی ارسال نشد)" if gw.dry else ""))
            else:
                st.info("هشداری با این حداقل شدت برای ارسال وجود ندارد.")
        st.caption("جلوگیری از تکرار: قانون + شناسه رویداد + شیفت + گیرنده (فقط در ارسال واقعی ثبت می‌شود).")
    with r_.container(border=True):
        head("پیش‌نمایش پیامک‌ها")
        if len(al):
            st.dataframe(pd.DataFrame({"شدت": al["severity"].values, "شیفت": al["shift"].values, "متن پیامک": al["message"].values,
                                       "بخش": [SA.sms_segments(m_) for m_ in al["message"]], "گیرنده": [_who(r) for _, r in al.iterrows()]}),
                         hide_index=True, use_container_width=True, height=R(1, 20))
        else:
            empty("هشداری برای ارسال نیست.")


def _health_rows(D, sec, rec):
    lvl, why = _diag()
    rows = []
    err = " | ".join(D["errors"])

    def add(ok, item, detail):
        rows.append({"وضعیت": {"ok": "✅ سالم", "warn": "⚠️ نیازمند توجه", "bad": "⛔ خطا", "info": "ℹ️ اطلاع"}[ok], "مورد": item, "جزئیات": detail})

    add("info" if SA.is_demo() else ("ok" if lvl == "ok" else "bad"), "اتصال دیتابیس", ("حالت دمو/آفلاین (Snapshot)" if SA.is_demo() else str(why)[:160]))
    add("info", "حالت دمو (DEMO_MODE)", "فعال؛ هیچ پیامکی ارسال نمی‌شود" if SA.is_demo() else "غیرفعال (حالت زنده)")
    ops = D["ops"]
    for label, df, key, need in (("dbo.Taradods", D["tx"], "taradods", True), ("dbo.View_Taradods_PBI", D["view"], "view", True), ("ترددهای بدون خروج (inside)", D["inside"], "inside", True),
                                 ("dbo.Gates", D["gates"], "gates", True), ("dbo.ANPRPlates", D["anpr"], "anpr", False), ("dbo.DiscountLogs", D["dl"], "discount_logs", False),
                                 ("rbac.Users", ops[ops["Name"].astype(str) != ""] if len(ops) else ops, "users", False)):
        n = len(df) if isinstance(df, pd.DataFrame) else 0
        e = next((x for x in D["errors"] if x.startswith(key)), "")
        add("ok" if n else ("bad" if need else "warn"), f"جدول {label}" + ("" if need else " (اختیاری)"), (f"{num(n)} ردیف" if n else (e[:120] or "خالی یا در دسترس نیست")))
    n_ph = int(ops["Phone"].notna().sum()) if len(ops) else 0
    add("ok" if n_ph else "warn", "شماره‌ی اپراتورها", f"{num(n_ph)} از {num(len(ops))} اپراتور دارای شماره‌اند" if len(ops) else "هیچ اپراتوری شناسایی نشد؛ [sms.operators] را تنظیم کنید")
    add("ok" if any(rec.values()) else "warn", "شماره‌های مدیریتی", f"{num(sum(len(v) for v in rec.values()))} شماره برای {num(len(rec))} نقش" if rec else "تعریف نشده")
    add("ok" if (sec.get("api_key") or SA.is_demo()) else "warn", "درگاه پیامک", f"{sec.get('provider', 'console')} · " + ("کلید API تنظیم شده" if sec.get("api_key") else "بدون کلید → فقط Dry-run"))
    lr = SA.read_last_run()
    add("ok" if lr else "warn", "آخرین پایش سرویس پس‌زمینه", (f"{fa(SA.jalali_text(pd.Timestamp(lr['at']), True))} · {num(lr.get('alerts', 0))} هشدار" if lr.get("at") else "") if lr else "هنوز سرویس shift_alerts.py اجرا نشده است")
    add("info", "آخرین ارزیابی داشبورد", fa(SA.jalali_text(NOW, True)) + " (ساعت سرور)")
    return rows


@_safe
def _setup_tab(D, sec, rec):
    lvl, _ = _diag()
    ops = D["ops"]
    n_ph = int(ops["Phone"].notna().sum()) if len(ops) else 0
    demo = SA.is_demo()
    steps = [
        ("۱", "اتصال دیتابیس", "ok" if (demo or lvl == "ok") else "bad", "secrets.toml",
         '[connections.mssql]\nserver = "10.0.0.5"\nport = 1433\ndatabase = "ParkingDB"\nusername = "readonly_user"\npassword = "***"\ndriver = "ODBC Driver 17 for SQL Server"', "toml",
         "فقط‌خواندنی (db_datareader). فایل: .streamlit/secrets.toml"),
        ("۲", "درگاه پیامک", "ok" if (demo or sec.get("api_key")) else "warn", "Kavenegar / SMS.ir",
         '[sms]\nprovider = "kavenegar"   # یا smsir | console\napi_key = "..."\nsender = "..."', "toml",
         "DEMO_MODE=1 یا provider=console ⇒ Mock؛ هیچ پیامکی ارسال نمی‌شود و خطایی رخ نمی‌دهد."),
        ("۳", "گیرندگان مدیریتی و اپراتوری", "ok" if (any(rec.values()) and n_ph) else "warn", "مدیران + اپراتورها",
         '[sms.recipients]\nshift_manager = ["0912xxxxxxx"]\nsecurity = ["0935xxxxxxx"]\nsite_manager = ["0912xxxxxxx"]\n\n[sms.operators]   # کلید = rbac.Users.ID یا UserName\n"12" = "0912xxxxxxx"\nali = "0935xxxxxxx"', "toml",
         "rbac.Users ستون موبایل ندارد؛ نگاشت اپراتور→موبایل از همین بخش خوانده می‌شود."),
        ("۴", "اجرای سرویس پس‌زمینه", "ok" if SA.read_last_run() else "warn", "--watch --interval",
         "python shift_alerts.py --watch --interval 300 --send\n\n# یا با cron (هر ۵ دقیقه، یک‌بار اجرا):\n*/5 * * * * cd /path/to/app && python shift_alerts.py --send", "bash",
         "پیش‌آزمایش: python shift_alerts.py --all-shifts (بدون --send فقط نمایش)"),
    ]
    chip = {"ok": ("آماده", GRN), "warn": ("نیازمند تنظیم", YEL), "bad": ("خطا", CORAL)}
    for col, (no, title, state, sub, code, lang, note) in zip(st.columns(4, gap="large"), steps):
        with col.container(border=True):
            txt, clr = chip[state]
            st.markdown(f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><span class="pill" style="--pc:{clr}">گام {no}</span>'
                        f'<b style="font-size:1.05rem">{title}</b></div><div style="margin:4px 0"><span class="pill" style="--pc:{clr}">{txt}</span> <small>{sub}</small></div>', unsafe_allow_html=True)
            st.code(code, language=lang)
            st.caption(note)
    st.divider()
    head("چک‌لیست سلامت سیستم", "دیتابیس · جداول کلیدی · حالت دمو · آخرین پایش")
    rows = _health_rows(D, sec, rec)
    cnt = pd.Series([r["وضعیت"] for r in rows]).value_counts()
    k1, k2, k3 = st.columns(3, gap="large")
    kpi(k1, "موارد سالم", num(int(cnt.get("✅ سالم", 0))), "از کل بررسی‌ها", "ok", ac=GRN)
    kpi(k2, "نیازمند توجه", num(int(cnt.get("⚠️ نیازمند توجه", 0))), "موارد اختیاری/تنظیمات ناقص", "warn", ac=YEL)
    kpi(k3, "خطا", num(int(cnt.get("⛔ خطا", 0))), "موارد ضروری ناموفق", "bad" if cnt.get("⛔ خطا", 0) else "ok", ac=CORAL)
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=min(480, 60 + 36 * len(rows)))
    if D["errors"]:
        with st.expander("جزئیات خطای خواندن جدول‌ها"):
            st.code("\n".join(D["errors"]))


def page5():
    day, sel, sevf, all_on = _alert_sidebar()
    sec = _sms_cfg()
    rec = {k: [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in _plain(sec.get("recipients", {})).items()} or \
          {"shift_manager": ["09120000001"], "security": ["09350000002"], "site_manager": ["09120000003"]}  # نمونه‌ی نمایشی در نبود secrets
    op_map = {str(k): (v[0] if isinstance(v, (list, tuple)) and v else v) for k, v in _plain(sec.get("operators", {})).items()}
    nocash = tuple(int(x) for x in NOCASH)
    try:
        D = _live_bundle(f"{day:%Y-%m-%d}", f"{NOW:%Y-%m-%d %H:%M:%S}", float(TOL), nocash, tuple(sorted((str(k), str(v)) for k, v in op_map.items())))
        D: dict[str, Any] = D  # noqa
    except Exception as e:  # noqa: BLE001
        top("بارگذاری داده‌ی هشدار ممکن نشد.")
        st.error(f"خطا در واکشی داده‌ی زنده: {str(e)[:200]}", icon="🛑")
        return
    shifts = SA.shifts_of(sel)
    states = {s: SA.shift_state(NOW, day, s) for s in shifts}
    live_any = "جاری" in states.values()
    kw = dict(gates=D["gates"], ops=D["ops"], tol=float(TOL), nocash=nocash)
    try:
        al_full = SA.evaluate_shifts(D["tx"], D["view"], D["inside"], D["anpr"], D["dl"], day, sel, now=D["now"], **kw)
    except Exception as e:  # noqa: BLE001
        st.warning(f"ارزیابی هشدارها با خطا مواجه شد: {str(e)[:160]}", icon="⚠️")
        al_full = pd.DataFrame(columns=SA.ALERT_COLS)
    al = al_full[al_full.severity.isin(sevf)].reset_index(drop=True) if len(al_full) else al_full
    cn = al.severity.value_counts() if len(al) else pd.Series(dtype=int)
    n_a11 = int((al["rule"] == "A11").sum()) if len(al) else 0
    n_nophone = int(((al["rule"] == "A11") & al["phone_missing"]).sum()) if len(al) else 0
    wl = "همه‌ی شیفت‌ها" if all_on else f"شیفت <b>{sel}</b>"
    top(f"{wl} · {SA.jalali_text(day)}" + (f" · <b>{SA.PROVISIONAL}</b>" if live_any else "") +
        f": <b>{cn.get('فوری', 0)} هشدار فوری</b> و <b>{cn.get('مهم', 0)} هشدار مهم</b> و {cn.get('پایش', 0)} هشدار پایشی؛ "
        f"هشدار فوری به مدیر شیفت، حراست و مدیر پارکینگ و کسری وصول به اپراتور همان گیت/شیفت پیامک می‌شود "
        f"(جمع بخش‌های پیامک: {int(sum(SA.sms_segments(m_) for m_ in al['message'])) if len(al) else 0}).")
    st.caption("وضعیت شیفت‌ها: " + " · ".join(f"{s}: {({'جاری': SA.PROVISIONAL, 'پایان‌یافته': 'پایان‌یافته', 'آینده': 'هنوز شروع نشده'})[v]}" for s, v in states.items()))
    if live_any:
        ls = next(s for s, v in states.items() if v == "جاری")
        st.info(f"⏳ {SA.PROVISIONAL}: شیفت {ls} هنوز در جریان است؛ داده‌ی امروز تا ساعت {fa(f'{NOW:%H:%M}')} (ساعت سرور) ارزیابی شده و با ثبت تردد جدید تغییر می‌کند.", icon="⏳")
    elif all(v == "آینده" for v in states.values()):
        st.info("این شیفت هنوز شروع نشده است؛ داده‌ای برای ارزیابی وجود ندارد.", icon="🕒")
    if D["errors"]:
        st.caption("⚠️ برخی جدول‌ها خوانده نشد؛ جزئیات در تب «راه‌اندازی» ← چک‌لیست سلامت سیستم.")
    header_cards([
        ("هشدار فوری", num(int(cn.get("فوری", 0))), "پیامک به مدیر شیفت، حراست، مدیر پارکینگ", "bad" if cn.get("فوری", 0) else "ok"),
        ("هشدار مهم", num(int(cn.get("مهم", 0))), "پیامک به مدیر شیفت", "warn" if cn.get("مهم", 0) else "ok"),
        ("هشدار پایشی", num(int(cn.get("پایش", 0))), "فقط روی داشبورد", "neutral"),
        ("کسری وصول اپراتور", num(n_a11), (f"{num(n_nophone)} مورد «شماره اپراتور نامشخص»" if n_nophone else "همه‌ی اپراتورها دارای شماره‌اند") if n_a11 else "موردی ثبت نشده", "warn" if n_nophone else ("bad" if n_a11 else "ok")),
    ])
    h = R(1, 175)
    hk = (f"{day:%Y%m%d}", sel, f"{NOW:%Y%m%d%H%M}", float(TOL), nocash, len(D["tx"]))
    if st.session_state.get("_al_hist_key") != hk:
        with st.spinner("در حال محاسبه‌ی تاریخچه‌ی ۷ روزه…"):
            try:
                st.session_state["_al_hist"] = SA.history_matrix(D["tx"], D["view"], D["anpr"], D["dl"], day, None if all_on else sel, **kw)
            except Exception:  # noqa: BLE001
                st.session_state["_al_hist"] = pd.DataFrame()
        st.session_state["_al_hist_key"] = hk
    hist = st.session_state.get("_al_hist", pd.DataFrame())
    T = st.tabs(["فوری", "مهم", "پایش", "قوانین", "پیامک", "راه‌اندازی"])
    with T[0]:
        _sev_tab("فوری", al, hist, day, sel, all_on, live_any, h)
    with T[1]:
        _sev_tab("مهم", al, hist, day, sel, all_on, live_any, h)
    with T[2]:
        _monitor_tab(al, D, day, h, live_any)
    with T[3]:
        _rules_tab()
    with T[4]:
        _sms_tab(al, D, sec, rec, h)
    with T[5]:
        _setup_tab(D, sec, rec)


# ============================================================ ثبت داشبوردها و صفحه‌ی اصلی
DASH = [
    dict(k="mgmt", t="مدیریتی", sub="مدیرعامل و مدیریت ارشد", ac=YEL, fn=page1,
         icon="<path d='M4 20V10M10 20V4M16 20v-7M22 20H2'/>"),
    dict(k="audit", t="حراست و ممیزی", sub="ریشه‌یابی ناهنجاری و تخلف", ac=MINT, fn=page2,
         icon="<path d='M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z'/><path d='M8.5 12l2.5 2.5L15.5 9.5'/>"),
    dict(k="ops", t="عملیات و اپراتور", sub="مدیر شیفت و مدیر پارکینگ", ac=TEAL, fn=page3,
         icon="<circle cx='9' cy='8' r='3'/><path d='M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6'/><circle cx='17' cy='9' r='2.2'/><path d='M17 14c2.5 0 4 1.8 4 4'/>"),
    dict(k="tech", t="فنی و تجهیزات", sub="ANPR، راهبند و ممیزی سیستم", ac=CORAL, fn=page_tech,
         icon="<rect x='3' y='7' width='18' height='12' rx='2'/><circle cx='12' cy='13' r='3.2'/><path d='M8 7l1.5-3h5L16 7'/>"),
    dict(k="fin", t="مالی و حسابداری", sub="کارتخوان، تخفیف و درآمد", ac=GRN, fn=page4,
         icon="<rect x='3' y='6' width='18' height='13' rx='2'/><path d='M3 10h18'/><circle cx='16.5' cy='14.5' r='1.3'/>"),
    dict(k="alert", t="هشدار لحظه‌ای شیفت", sub="هشدار و پیامک به مسئولان", ac=YEL, fn=page5,
         icon="<path d='M6 16V11a6 6 0 1112 0v5l2 2H4z'/><path d='M10 21a2 2 0 004 0'/>"),
]
KEYS = [d["k"] for d in DASH]


def _icon_svg(paths, color):
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='{color}' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'>{paths}</svg>")


def tile(d):
    html = (f'<div class="tile" style="--ac:{d["ac"]}">{_icon_svg(d["icon"], d["ac"])}<div class="tt">{d["t"]}</div><div class="ts">{d["sub"]}</div></div>')
    try:
        box = st.container(key=f"tile_{d['k']}")
    except TypeError:  # نسخه‌ی قدیمی Streamlit بدون key برای container
        box = st.container()
    with box:
        st.markdown(html, unsafe_allow_html=True)
        st.button(d["t"], key=f"tilebtn_{d['k']}", on_click=go_to, args=(d["k"],), use_container_width=True)


def home():
    # کارت‌های متریک (درآمد، زیان مشکوک و چیپ‌های مالی) از صفحه‌ی اصلی حذف شدند؛ توابع kpi/fin_chips برای سازگاری در فایل باقی مانده‌اند
    st.markdown('<div class="hometitle">سنجمان · پایش و ممیزی پارکینگ</div><div class="homesub">برای ورود، داشبورد مخصوص بخش خود را انتخاب کنید</div>', unsafe_allow_html=True)
    spacer(16)
    for i, row in enumerate((DASH[:3], DASH[3:])):
        if i:
            spacer(22)  # فاصله‌ی بین دو ردیف کاشی
        for col, d in zip(st.columns(3, gap="large"), row):
            with col:
                tile(d)


_cur = st.session_state.get("dash")
CUR = next((d for d in DASH if d["k"] == _cur), None)
if CUR and CUR["k"] != "alert":
    with st.sidebar:
        st.markdown("#### فیلتر داشبورد")
        g_shift = st.radio("شیفت", ["همه شیفت‌ها", "صبح", "عصر", "شب"], horizontal=True, key="g_shift")
        gates = an.gate_filter_options(GATES, VW0)  # همه‌ی گیت‌های dbo.Gates (حتی بدون تردد) + IDهای خروجِ بدون نگاشت
        g_gate = st.selectbox("گیت خروج", ["همه گیت‌ها"] + list(gates.OutputGate), key="g_gate", help="فیلتر روی ستون OutputGateID؛ فهرست از dbo.Gates ساخته می‌شود (گیت بدون خروج در بازه علامت‌گذاری شده است)")
    gid = None
    if g_shift != "همه شیفت‌ها":
        TX, VW = TX[TX.Shift == g_shift], VW[VW.Shift == g_shift]
        TXH, VWH = TXH[TXH.Shift == g_shift], VWH[VWH.Shift == g_shift]
    if g_gate != "همه گیت‌ها":
        gid = gates.loc[gates.OutputGate == g_gate, "OutputGateID"].iloc[0]
        TX, VW = TX[TX.OutputGateID == gid], VW[VW.OutputGateID == gid]
        TXH, VWH = TXH[TXH.OutputGateID == gid], VWH[VWH.OutputGateID == gid]
    FT, FV = an.fin_filter(FT0, FV0, None if g_shift == "همه شیفت‌ها" else g_shift, gid)
    FTH, FVH = an.fin_filter(FT0H, FV0H, None if g_shift == "همه شیفت‌ها" else g_shift, gid)

if CUR is None:
    home()
else:
    fn = CUR.get("fn")
    if callable(fn):
            fn()

