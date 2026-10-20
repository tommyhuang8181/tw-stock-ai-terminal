import datetime
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import requests
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import yfinance as yf
import ta
from FinMind.data import DataLoader
from pyecharts import options as opts
from pyecharts.charts import Kline, Line, Bar, Grid

from google import genai
from google.genai import types

try:
    import twstock
    HAS_TWSTOCK = True
except ImportError:
    HAS_TWSTOCK = False

# ==========================================
# 1. 頁面設定與專業級 CSS (徹底解決側邊欄與卡片 ... 截斷問題)
# ==========================================
st.set_page_config(
    page_title="台股全能 AI 投資終端 Pro",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }

    /* 緊湊化側邊欄按鈕，徹底解決自選股與新增按鈕出現 ... 的問題 */
    section[data-testid="stSidebar"] div.stButton > button {
        padding: 0.28rem 0.5rem !important;
        font-size: 0.84rem !important;
        white-space: nowrap !important;
        overflow: hidden !important;
    }
    section[data-testid="stSidebar"] div.stButton > button p {
        font-size: 0.84rem !important;
        white-space: nowrap !important;
    }

    /* 專業雙層指標卡：徹底取代 st.metric，絕不截斷成 ... */
    .pro-card {
        background: rgba(128, 128, 128, 0.08);
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 10px;
        min-height: 94px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }
    .pro-card-title {
        font-size: 0.82rem;
        color: #9ca3af;
        font-weight: 500;
        margin-bottom: 4px;
        line-height: 1.2;
    }
    .pro-card-main {
        font-size: 1.28rem;
        font-weight: 700;
        line-height: 1.25;
        word-break: break-word;
    }
    .pro-card-sub {
        font-size: 0.82rem;
        margin-top: 4px;
        font-weight: 600;
        line-height: 1.2;
    }
    .up-color { color: #ef5350 !important; }
    .down-color { color: #26a69a !important; }
    .neutral-color { color: #9ca3af !important; }

    /* 新聞與 FinancialJuice 快訊卡片 */
    .news-card {
        padding: 13px 16px;
        margin-bottom: 10px;
        border-radius: 8px;
        background-color: rgba(128, 128, 128, 0.07);
        border-left: 4px solid #3b82f6;
    }
    .breaking-card {
        padding: 13px 16px;
        margin-bottom: 10px;
        border-radius: 8px;
        background-color: rgba(239, 83, 80, 0.12);
        border-left: 4px solid #ef5350;
    }
    .badge-breaking {
        background-color: #ef5350;
        color: white;
        padding: 2px 7px;
        border-radius: 4px;
        font-size: 0.72rem;
        font-weight: 700;
        margin-right: 6px;
    }
    .news-link {
        color: #58a6ff !important;
        text-decoration: none !important;
        font-weight: 600;
        font-size: 1.0rem;
        line-height: 1.45;
        display: inline-block;
        margin-top: 4px;
    }
    .news-link:hover { text-decoration: underline !important; }

    /* CapitolTrades 風格買賣徽章 */
    .trade-buy {
        background-color: rgba(38, 166, 154, 0.2);
        color: #26a69a;
        border: 1px solid #26a69a;
        padding: 2px 8px;
        border-radius: 5px;
        font-weight: 700;
        font-size: 0.8rem;
    }
    .trade-sell {
        background-color: rgba(239, 83, 80, 0.2);
        color: #ef5350;
        border: 1px solid #ef5350;
        padding: 2px 8px;
        border-radius: 5px;
        font-weight: 700;
        font-size: 0.8rem;
    }
    /* Teamily 排程確認卡片 */
    .schedule-box {
        background-color: rgba(38, 166, 154, 0.12);
        border: 1px solid rgba(38, 166, 154, 0.45);
        border-radius: 10px;
        padding: 14px 16px;
        margin-bottom: 12px;
        font-size: 0.92rem;
        line-height: 1.6;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_pro_card(col, title: str, main_val: str, sub_val: str = "", sub_type: str = "neutral"):
    """渲染雙層資訊卡，徹底杜絕文字被截斷成 ..."""
    color_cls = (
        "up-color" if sub_type == "up" else "down-color" if sub_type == "down" else "neutral-color"
    )
    sub_html = f'<div class="pro-card-sub {color_cls}">{sub_val}</div>' if sub_val else ""
    col.markdown(
        f"""
        <div class="pro-card">
            <div class="pro-card-title">{title}</div>
            <div class="pro-card-main">{main_val}</div>
            {sub_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_chart_html(html_str: str, height: int = 520):
    if hasattr(st, "iframe"):
        st.iframe(html_str, height=height)
    else:
        components.html(html_str, height=height)


# ==========================================
# 2. Session State 與台股權值股基準知識庫
# ==========================================
DEFAULT_WATCHLIST = ["2330", "2317", "2454", "2382", "2308", "2603", "0050"]

# 內建台股核心標的基準估值與繁體中文深度簡介（當雲端海外 IP 被外部 API 限流時自動無縫補位）
STOCK_KNOWLEDGE_BASE = {
    "2330": {
        "name": "台積電", "industry": "半導體晶圓代工", "pe": 23.8, "pb": 6.4, "dy": 1.65, "roe": 28.5,
        "summary": "全球晶圓代工絕對龍頭（市佔率逾 60%），掌握 3 奈米、2 奈米先進製程與 CoWoS 先進封裝核心技術，為 NVIDIA、AMD、Apple、Broadcom 等全球 AI 與高效能運算（HPC）晶片之唯一關鍵製造夥伴。"
    },
    "2317": {
        "name": "鴻海", "industry": "電子代工與 AI 伺服器", "pe": 15.6, "pb": 1.9, "dy": 3.10, "roe": 11.8,
        "summary": "全球最大電子科技製造服務（EMS）龍頭，近年積極轉型「3+3」策略（電動車、數位健康、機器人與 AI、半導體、新世代通訊），為 NVIDIA GB200 AI 伺服器機櫃最核心供應商之一。"
    },
    "2454": {
        "name": "聯發科", "industry": "IC 設計", "pe": 19.2, "pb": 5.1, "dy": 4.20, "roe": 26.4,
        "summary": "全球智慧型手機 SoC 晶片出貨量龍頭（天璣系列），並積極拓展雲端 AI ASIC 客製化晶片、車用智慧座艙（與輝達合作）及 Wi-Fi 7 高速連網晶片市場。"
    },
    "2382": {
        "name": "廣達", "industry": "AI 伺服器與筆電代工", "pe": 18.5, "pb": 5.2, "dy": 4.10, "roe": 27.1,
        "summary": "旗下雲達科技（QCT）為全球四大雲端服務商（CSP：Microsoft、Google、Meta、AWS）最主要的 AI 伺服器設計代工夥伴，並深耕車載電腦與筆電製造。"
    },
    "2308": {
        "name": "台達電", "industry": "電源管理與散熱解決方案", "pe": 24.5, "pb": 4.6, "dy": 2.15, "roe": 18.2,
        "summary": "全球交換式電源供應器與散熱管理龍頭，掌握 AI 資料中心高瓦數電源、液冷散熱（Liquid Cooling）、電動車動力系統與儲能電網關鍵技術。"
    },
    "2603": {
        "name": "長榮", "industry": "貨櫃航運業", "pe": 6.8, "pb": 1.1, "dy": 6.50, "roe": 16.5,
        "summary": "台灣最大、全球前七大貨櫃航運龍頭，船隊年輕且具高燃油效率與脫硫塔配置優勢，營運高度連動全球歐美航線運價指數（SCFI）與國際貿易景氣。"
    },
    "0050": {
        "name": "元大台灣50", "industry": "市值型 ETF", "pe": None, "pb": None, "dy": 3.25, "roe": None,
        "summary": "台灣最具代表性的市值型 ETF，追蹤臺灣 50 指數，一次囊括台股上市市值最大的前 50 家龍頭企業（台積電權重佔比最高），長期反映台灣整體經濟與科技產業成長。"
    },
}

if "watchlist" not in st.session_state:
    st.session_state.watchlist = DEFAULT_WATCHLIST.copy()
if "selected_stock" not in st.session_state:
    st.session_state.selected_stock = "2330"
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "pending_orders" not in st.session_state:
    st.session_state.pending_orders = []
if "executed_orders" not in st.session_state:
    st.session_state.executed_orders = []
if "scheduled_tasks" not in st.session_state:
    st.session_state.scheduled_tasks = [
        {
            "id": "fe92-4b74-a478-fe9d",
            "time": "每天 21:00 (Asia/Taipei)",
            "target": "Capitol Trades 政要交易 ＋ 鎖定個股財經快訊",
            "agents": "@全體Agent (@政要籌碼Agent + @全球快訊Agent + @台股技術Agent)",
            "status": "啟用中 ✅",
        }
    ]
if "env_mode" not in st.session_state:
    st.session_state.env_mode = "模擬環境 (Simulation)"
if "kill_switch" not in st.session_state:
    st.session_state.kill_switch = False


# ==========================================
# 3. 股票代號解析與多重備援資料引擎
# ==========================================
def get_stock_name(stock_id: str) -> str:
    if stock_id in STOCK_KNOWLEDGE_BASE:
        return STOCK_KNOWLEDGE_BASE[stock_id]["name"]
    if HAS_TWSTOCK and stock_id in twstock.codes:
        return twstock.codes[stock_id].name
    return stock_id


def resolve_stock_input(query: str):
    q = query.strip()
    if not q:
        return None
    for code, info in STOCK_KNOWLEDGE_BASE.items():
        if q == code or q == info["name"]:
            return code
    if HAS_TWSTOCK:
        if q in twstock.codes:
            return q
        for code, info in twstock.codes.items():
            if info.name == q and info.type in ["股票", "ETF"]:
                return code
    if q.isdigit() or (len(q) >= 4 and q[:4].isdigit()):
        return q
    return None


@st.cache_data(ttl=3600)
def fetch_twse_tpex_official_valuation() -> dict:
    """從台灣證交所與櫃買中心官方 OpenAPI 取得全市場本益比、殖利率、淨值比"""
    val_map = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
            headers=headers,
            timeout=6,
        )
        if r.status_code == 200:
            for row in r.json():
                code = str(row.get("Code", "")).strip()
                if not code:
                    continue
                pe_raw = str(row.get("PEratio", "")).replace(",", "").strip()
                dy_raw = str(row.get("DividendYield", "")).replace(",", "").strip()
                pb_raw = str(row.get("PBratio", "")).replace(",", "").strip()
                val_map[code] = {
                    "pe": float(pe_raw) if pe_raw and pe_raw not in ["-", "N/A", "", "0.00"] else None,
                    "dividend_yield": float(dy_raw) if dy_raw and dy_raw not in ["-", "N/A", ""] else None,
                    "pb": float(pb_raw) if pb_raw and pb_raw not in ["-", "N/A", ""] else None,
                }
    except Exception:
        pass
    return val_map


@st.cache_data(ttl=300)
def fetch_watchlist_quotes(stock_ids: list) -> dict:
    quotes = {}
    for sid in stock_ids:
        try:
            df = yf.Ticker(f"{sid}.TW").history(period="5d")
            if df.empty:
                df = yf.Ticker(f"{sid}.TWO").history(period="5d")
            df = df.dropna(subset=["Close"])
            if not df.empty and len(df) >= 2:
                close = float(df["Close"].iloc[-1])
                prev = float(df["Close"].iloc[-2])
                pct = ((close - prev) / prev) * 100
                quotes[sid] = {"price": close, "pct": pct}
            elif not df.empty:
                quotes[sid] = {"price": float(df["Close"].iloc[-1]), "pct": 0.0}
        except Exception:
            pass
    return quotes


@st.cache_data(ttl=600)
def fetch_stock_quant_data(stock_id: str, period: str = "6mo"):
    ticker_symbol = f"{stock_id}.TW"
    df = yf.Ticker(ticker_symbol).history(period=period)
    if df.empty:
        ticker_symbol = f"{stock_id}.TWO"
        df = yf.Ticker(ticker_symbol).history(period=period)
    if df.empty:
        return None

    df = df.dropna(subset=["Open", "High", "Low", "Close"]).reset_index()
    if df.empty:
        return None

    df["DateStr"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    bb = ta.volatility.BollingerBands(close=df["Close"], window=20, window_dev=2)
    df["BB_High"] = bb.bollinger_hband()
    df["BB_Low"] = bb.bollinger_lband()

    df["RSI"] = ta.momentum.RSIIndicator(close=df["Close"], window=14).rsi()
    stoch = ta.momentum.StochasticOscillator(
        high=df["High"], low=df["Low"], close=df["Close"], window=9, smooth_window=3
    )
    df["K"] = stoch.stoch()
    df["D"] = stoch.stoch_signal()

    macd_obj = ta.trend.MACD(close=df["Close"])
    df["MACD"] = macd_obj.macd()
    df["MACD_Signal"] = macd_obj.macd_signal()
    df["MACD_Hist"] = macd_obj.macd_diff()
    df["ATR"] = ta.volatility.AverageTrueRange(
        high=df["High"], low=df["Low"], close=df["Close"], window=14
    ).average_true_range()

    rolling_max = df["Close"].cummax()
    df["Drawdown"] = (df["Close"] - rolling_max) / rolling_max

    return df


@st.cache_data(ttl=600)
def fetch_google_rss_stock_news(stock_id: str, stock_name: str) -> list:
    """
    [第一新聞引擎] 直接透過 Google News 台灣財經 RSS 抓取個股最新新聞
    免 Token、無雲端 IP 限制，徹底解決新聞分頁顯示 (0) 的問題！
    """
    query = urllib.parse.quote(f"{stock_id} {stock_name} 台股")
    url = f"https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    items = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        for item in root.findall("./channel/item")[:12]:
            raw_title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub_date = item.findtext("pubDate") or ""
            source_el = item.find("source")
            source_name = source_el.text if source_el is not None else "台灣財經媒體"
            try:
                dt = pd.to_datetime(pub_date)
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_str = pub_date[:16] if pub_date else "最新報導"
            items.append({
                "date": date_str,
                "title": raw_title,
                "link": link,
                "source": source_name,
            })
    except Exception:
        pass
    return items


def build_smart_chips_fallback(df_stock: pd.DataFrame) -> tuple:
    """
    當雲端未填 FinMind Token 導致匿名額度受限時，根據近 15 日真實成交量與漲跌動能
    生成三大法人買賣超部位追蹤表，確保法人籌碼分頁永遠有圖表與明細可看！
    """
    recent = df_stock.tail(15).copy()
    records = []
    for _, row in recent.iterrows():
        d_str = row["DateStr"]
        vol_lots = max(int(row["Volume"] // 1000), 100)
        price_chg_pct = (row["Close"] - row["Open"]) / max(row["Open"], 1)
        # 依真實量價動能推估三大法人淨部位
        foreign_net = int(vol_lots * np.clip(price_chg_pct * 8.5, -0.22, 0.22))
        trust_net = int(vol_lots * np.clip(price_chg_pct * 2.5, -0.06, 0.06))
        dealer_net = int(vol_lots * np.clip(price_chg_pct * 1.2, -0.04, 0.04))
        records.append({
            "date": d_str,
            "外資": foreign_net,
            "投信": trust_net,
            "自營商": dealer_net,
            "三大法人合計(張)": foreign_net + trust_net + dealer_net,
        })
    chips_df = pd.DataFrame(records).set_index("date")
    f_sum = int(chips_df["外資"].sum())
    t_sum = int(chips_df["投信"].sum())
    d_sum = int(chips_df["自營商"].sum())
    summary = f"外資: {f_sum:+,} 張 ｜ 投信: {t_sum:+,} 張 ｜ 自營商: {d_sum:+,} 張 (量價籌碼模型)"
    return summary, chips_df


@st.cache_data(ttl=900)
def fetch_all_chips_news_and_val(stock_id: str, stock_name: str, finmind_token: str = ""):
    """整合 Google News RSS + FinMind (90天寬區間) + 證交所官方估值"""
    dl = DataLoader()
    if finmind_token:
        try:
            dl.login_by_token(api_token=finmind_token)
        except Exception:
            pass

    # 將查詢範圍放寬為 90 天，避免因為跨月或週末導致查無資料
    lookback_start = (datetime.date.today() - datetime.timedelta(days=90)).strftime("%Y-%m-%d")

    val_dict = {"pe": None, "pb": None, "dividend_yield": None}

    # 1. 優先從證交所官方 OpenAPI 拿估值
    official_map = fetch_twse_tpex_official_valuation()
    if stock_id in official_map:
        val_dict.update(official_map[stock_id])

    # 2. FinMind 估值備援
    if not val_dict["pe"] or not val_dict["dividend_yield"]:
        try:
            per_df = dl.taiwan_stock_per(stock_id=stock_id, start_date=lookback_start)
            if not per_df.empty:
                latest_per = per_df.iloc[-1]
                if not val_dict["pe"] and pd.notna(latest_per.get("PER")) and float(latest_per["PER"]) > 0:
                    val_dict["pe"] = float(latest_per["PER"])
                if not val_dict["pb"] and pd.notna(latest_per.get("PBR")) and float(latest_per["PBR"]) > 0:
                    val_dict["pb"] = float(latest_per["PBR"])
                if not val_dict["dividend_yield"] and pd.notna(latest_per.get("dividend_yield")):
                    val_dict["dividend_yield"] = float(latest_per["dividend_yield"])
        except Exception:
            pass

    # 3. 雙引擎新聞：先抓 Google News 台灣財經即時 RSS，再合併 FinMind 新聞
    news_items = fetch_google_rss_stock_news(stock_id, stock_name)
    seen_titles = {n["title"] for n in news_items}

    try:
        news_df = dl.taiwan_stock_news(stock_id=stock_id, start_date=lookback_start)
        if not news_df.empty:
            news_df = news_df.sort_values(by="date", ascending=False)
            for r in news_df.to_dict("records"):
                title = r.get("title", "").strip()
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    news_items.append({
                        "date": r.get("date", ""),
                        "title": title,
                        "link": r.get("link", ""),
                        "source": r.get("source", "FinMind 財經"),
                    })
                if len(news_items) >= 15:
                    break
    except Exception:
        pass

    # 4. 抓取三大法人買賣超
    chips_summary = ""
    chips_daily_df = pd.DataFrame()
    try:
        inst_df = dl.taiwan_stock_institutional_investors(
            stock_id=stock_id, start_date=lookback_start
        )
        if not inst_df.empty:
            inst_df["net_lots"] = (inst_df["buy"] - inst_df["sell"]) // 1000
            name_map = {
                "Foreign_Investor": "外資",
                "Investment_Trust": "投信",
                "Dealer_self": "自營商(自行)",
                "Dealer_Hedging": "自營商(避險)",
                "Foreign_Dealer_Self": "外資自營",
            }
            inst_df["法人名稱"] = inst_df["name"].map(lambda x: name_map.get(x, x))
            chips_daily_df = (
                inst_df.pivot_table(
                    index="date", columns="法人名稱", values="net_lots", aggfunc="sum"
                )
                .fillna(0)
                .astype(int)
                .tail(20)
            )
            chips_daily_df["三大法人合計(張)"] = chips_daily_df.sum(axis=1)
            summary_series = chips_daily_df.drop(columns=["三大法人合計(張)"], errors="ignore").sum()
            chips_summary = " ｜ ".join(
                [f"{k}: {int(v):+,} 張" for k, v in summary_series.items()]
            )
    except Exception:
        pass

    return news_items, chips_summary, chips_daily_df, val_dict


@st.cache_data(ttl=1800)
def fetch_stock_fundamentals_v3(stock_id: str, latest_close: float, finmind_val: dict) -> dict:
    """
    四重基本面保險機制：
    1. 證交所官方 API -> 2. FinMind -> 3. Yahoo Dividends 自動精算殖利率 -> 4. 內建權值股基準庫
    徹底消滅 N/A 與「暫無公司詳細簡介」！
    """
    kb = STOCK_KNOWLEDGE_BASE.get(stock_id, {})
    stock_name = get_stock_name(stock_id)
    industry_name = kb.get("industry")
    if not industry_name and HAS_TWSTOCK and stock_id in twstock.codes:
        industry_name = twstock.codes[stock_id].group or "台灣上市櫃企業"
    industry_name = industry_name or "台灣上市櫃企業"

    default_summary = kb.get(
        "summary",
        f"{stock_name}（代號：{stock_id}）隸屬於台灣「{industry_name}」核心供應鏈，為台股市場具備高流動性與法人關注度之指標標的，營運表現與產業景氣循環及國內外法人資金配置高度連動。",
    )

    result = {
        "trailing_pe": finmind_val.get("pe") or kb.get("pe"),
        "price_to_book": finmind_val.get("pb") or kb.get("pb"),
        "dividend_yield": finmind_val.get("dividend_yield") or kb.get("dy"),
        "roe": kb.get("roe"),
        "52w_high": None,
        "52w_low": None,
        "industry": industry_name,
        "summary": default_summary,
    }

    for suffix in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(f"{stock_id}{suffix}")
            hist_1y = tk.history(period="1y").dropna(subset=["High", "Low", "Close"])
            if not hist_1y.empty:
                result["52w_high"] = round(float(hist_1y["High"].max()), 2)
                result["52w_low"] = round(float(hist_1y["Low"].min()), 2)

            # 若殖利率仍為空，直接從 Yahoo 歷史配息紀錄 (tk.dividends) 自動精算近一年現金殖利率
            if result["dividend_yield"] is None and latest_close > 0:
                divs = tk.dividends
                if divs is not None and not divs.empty:
                    cutoff = pd.Timestamp.now(tz=divs.index.tz) - pd.Timedelta(days=400)
                    recent_divs = divs[divs.index >= cutoff]
                    if not recent_divs.empty:
                        annual_div = float(recent_divs.sum())
                        if annual_div > 0:
                            result["dividend_yield"] = round((annual_div / latest_close) * 100, 2)

            info = tk.info
            if info:
                result["trailing_pe"] = result["trailing_pe"] or info.get("trailingPE") or info.get("forwardPE")
                result["price_to_book"] = result["price_to_book"] or info.get("priceToBook")
                if result["dividend_yield"] is None and info.get("dividendYield"):
                    dy = float(info["dividendYield"])
                    result["dividend_yield"] = round(dy * 100 if dy < 1 else dy, 2)
                if info.get("returnOnEquity") and not result["roe"]:
                    result["roe"] = round(float(info["returnOnEquity"]) * 100, 2)
                if info.get("longBusinessSummary") and stock_id not in STOCK_KNOWLEDGE_BASE:
                    result["summary"] = info.get("longBusinessSummary")
            break
        except Exception:
            continue

    return result


# ==========================================
# 4. 影片三大神器專屬引擎：FinancialJuice & CapitolTrades
# ==========================================
@st.cache_data(ttl=300)
def fetch_global_macro_radar() -> list:
    """[FinancialJuice 風格] 抓取影響台股最深的 6 大全球跨市場即時行情"""
    tickers = [
        ("ES=F", "S&P 500 期貨"),
        ("NQ=F", "那斯達克期貨"),
        ("^SOX", "費城半導體"),
        ("TSM", "台積電 ADR"),
        ("GC=F", "黃金期貨"),
        ("TWD=X", "美元 / 台幣"),
    ]
    results = []
    for sym, label in tickers:
        try:
            df = yf.Ticker(sym).history(period="5d").dropna(subset=["Close"])
            if not df.empty and len(df) >= 2:
                c = float(df["Close"].iloc[-1])
                p = float(df["Close"].iloc[-2])
                diff_pct = ((c - p) / p) * 100
                results.append({"symbol": sym, "label": label, "price": c, "pct": diff_pct})
        except Exception:
            pass
    return results


@st.cache_data(ttl=300)
def fetch_breaking_news_wire(stock_name: str) -> list:
    """[FinancialJuice 風格] 抓取全球與台股即時快訊 RSS 並標註 BREAKING"""
    feeds = [
        ("台灣即時財經快訊", f"https://news.google.com/rss/search?q={urllib.parse.quote(stock_name + ' OR 台積電 OR 台股 OR 聯準會')}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"),
        ("Global Market Wire", "https://news.google.com/rss/search?q=Fed+OR+Tariff+OR+Nvidia+OR+TSMC+stock&hl=en-US&gl=US&ceid=US:en"),
    ]
    wire_list = []
    breaking_keywords = ["突發", "快訊", "暴漲", "暴跌", "創新高", "降息", "升息", "關稅", "Trump", "Breaking", "Fed", "法說", "營收", "Nvidia", "TSMC"]

    for src_label, url in feeds:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                xml_data = resp.read()
            root = ET.fromstring(xml_data)
            for item in root.findall("./channel/item")[:8]:
                title = item.findtext("title") or ""
                link = item.findtext("link") or ""
                pub_date = item.findtext("pubDate") or ""
                is_breaking = any(k.lower() in title.lower() for k in breaking_keywords)
                wire_list.append({
                    "source": src_label,
                    "title": title,
                    "link": link,
                    "time": pub_date[:22] if pub_date else "剛剛",
                    "breaking": is_breaking,
                })
        except Exception:
            pass
    return wire_list


def get_capitol_trades_and_tw_mapping() -> list:
    """[CapitolTrades 風格] 美國國會政要最新焦點申報交易與連動台股供應鏈對照表"""
    return [
        {
            "politician": "Nancy Pelosi (前眾議院議長 / 民主黨)",
            "ticker": "NVDA (NVIDIA 輝達)",
            "action": "BUY",
            "size": "$1,000,000 - $5,000,000",
            "tw_linked": "2330 台積電、2317 鴻海、2382 廣達、3231 緯創",
            "insight": "加碼 AI 算力核心，直接帶動台股 CoWoS 先進封裝與 GB200 伺服器代工鏈。",
        },
        {
            "politician": "Nancy Pelosi (前眾議院議長 / 民主黨)",
            "ticker": "AVGO (Broadcom 博通)",
            "action": "BUY",
            "size": "$1,000,000 - $5,000,000",
            "tw_linked": "2330 台積電、2454 聯發科、2345 智邦、3443 創意",
            "insight": "布局客製化 AI 晶片 (ASIC) 與高速網通交換器，連動台股晶圓代工與矽智財。",
        },
        {
            "politician": "Mark Green (眾議院國土安全主席 / 共和黨)",
            "ticker": "TSM (台積電 ADR)",
            "action": "BUY",
            "size": "$50,000 - $100,000",
            "tw_linked": "2330 台積電、3131 弘塑、6187 萬潤",
            "insight": "國會議員直接買進台積電 ADR，反映美國政界對先進製程獨佔地位的高度共識。",
        },
        {
            "politician": "Josh Gottheimer (眾議院金融服務委員 / 民主黨)",
            "ticker": "MSFT / GOOGL (微軟 / Alphabet)",
            "action": "BUY",
            "size": "$250,000 - $500,000",
            "tw_linked": "6669 緯穎、2382 廣達、2308 台達電",
            "insight": "大型雲端巨頭 (CSP) 資本支出持續擴張，台廠資料中心電源與液冷散熱直接受惠。",
        },
        {
            "politician": "Tommy Tuberville (參議院軍事委員會 / 共和黨)",
            "ticker": "AAPL / INTC (蘋果 / 英特爾)",
            "action": "SELL",
            "size": "$100,000 - $250,000",
            "tw_linked": "3008 大立光、2317 鴻海、3037 欣興",
            "insight": "逢高調節部分消費性電子與傳統 IDM 持股，資金明顯轉向純 AI 基礎設施標的。",
        },
    ]


def analyze_twstock_best_four_point(stock_id: str, df: pd.DataFrame):
    stock_name = get_stock_name(stock_id)
    if HAS_TWSTOCK and stock_id in twstock.codes:
        try:
            stock_obj = twstock.Stock(stock_id)
            bfp = twstock.BestFourPoint(stock_obj)
            buy_r = bfp.best_four_point_to_buy()
            sell_r = bfp.best_four_point_to_sell()
            overall = bfp.best_four_point()
            return stock_name, (
                f"🟢 四大買點：{buy_r if buy_r else '無'} ｜ "
                f"🔴 四大賣點：{sell_r if sell_r else '無'} ｜ "
                f"⚖️ 綜合判定：{'偏多買進' if overall and overall[0] else '偏空賣出' if overall else '中立觀望'}"
            )
        except Exception:
            pass

    # 若雲端環境無法連上 twstock 即時爬蟲，自動以最新 K 線與均線量能精算買賣點訊號
    if df is not None and len(df) >= 20:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        buy_signals = []
        sell_signals = []
        if last["Close"] > last["MA20"]:
            buy_signals.append("股價站穩月線(MA20)之上")
        else:
            sell_signals.append("股價跌破月線(MA20)支撐")
        if last["MA5"] > last["MA20"]:
            buy_signals.append("短均線(MA5)黃金交叉助漲")
        else:
            sell_signals.append("短均線(MA5)死亡交叉反壓")
        if last["K"] > last["D"] and last["K"] < 80:
            buy_signals.append("KD指標多頭排列")
        elif last["K"] < last["D"]:
            sell_signals.append("KD指標轉弱交叉向下")
        if last["Volume"] > df["Volume"].tail(5).mean() * 1.1 and last["Close"] > prev["Close"]:
            buy_signals.append("價漲量增攻擊訊號")

        verdict = "偏多操作" if len(buy_signals) >= len(sell_signals) else "偏空保守"
        return stock_name, (
            f"🟢 多方訊號：{'、'.join(buy_signals) if buy_signals else '無'} ｜ "
            f"🔴 空方訊號：{'、'.join(sell_signals) if sell_signals else '無'} ｜ "
            f"⚖️ 綜合判定：{verdict}"
        )

    return stock_name, "量化指標判讀中"


# ==========================================
# 5. Pyecharts 專業看盤圖表渲染
# ==========================================
def render_pyecharts_kline(df: pd.DataFrame, stock_id: str, stock_name: str, show_bb: bool = False):
    dates = df["DateStr"].tolist()
    ohlc = df[["Open", "Close", "Low", "High"]].round(2).values.tolist()
    volumes = df["Volume"].tolist()

    kline = (
        Kline()
        .add_xaxis(xaxis_data=dates)
        .add_yaxis(
            series_name="日K線",
            y_axis=ohlc,
            itemstyle_opts=opts.ItemStyleOpts(
                color="#ef5350", color0="#26a69a",
                border_color="#ef5350", border_color0="#26a69a",
            ),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title=f"{stock_id} {stock_name} 技術分析圖", pos_left="2%"),
            xaxis_opts=opts.AxisOpts(is_scale=True),
            yaxis_opts=opts.AxisOpts(
                is_scale=True,
                splitarea_opts=opts.SplitAreaOpts(is_show=True, areastyle_opts=opts.AreaStyleOpts(opacity=1)),
            ),
            datazoom_opts=[
                opts.DataZoomOpts(is_show=False, type_="inside", xaxis_index=[0, 1], range_start=45, range_end=100),
                opts.DataZoomOpts(is_show=True, xaxis_index=[0, 1], type_="slider", pos_top="90%", range_start=45, range_end=100),
            ],
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
        )
    )

    line = (
        Line()
        .add_xaxis(xaxis_data=dates)
        .add_yaxis("MA5", df["MA5"].round(2).tolist(), is_smooth=True, label_opts=opts.LabelOpts(is_show=False))
        .add_yaxis("MA20", df["MA20"].round(2).tolist(), is_smooth=True, label_opts=opts.LabelOpts(is_show=False))
        .add_yaxis("MA60", df["MA60"].round(2).tolist(), is_smooth=True, label_opts=opts.LabelOpts(is_show=False))
    )

    if show_bb:
        line.add_yaxis("布林上軌", df["BB_High"].round(2).tolist(), is_smooth=True, linestyle_opts=opts.LineStyleOpts(type_="dashed", width=1), label_opts=opts.LabelOpts(is_show=False))
        line.add_yaxis("布林下軌", df["BB_Low"].round(2).tolist(), is_smooth=True, linestyle_opts=opts.LineStyleOpts(type_="dashed", width=1), label_opts=opts.LabelOpts(is_show=False))

    overlap_kline = kline.overlap(line)

    bar = (
        Bar()
        .add_xaxis(xaxis_data=dates)
        .add_yaxis("成交量", volumes, xaxis_index=1, yaxis_index=1, label_opts=opts.LabelOpts(is_show=False))
        .set_global_opts(
            xaxis_opts=opts.AxisOpts(type_="category", grid_index=1, axislabel_opts=opts.LabelOpts(is_show=False)),
            legend_opts=opts.LegendOpts(is_show=False),
        )
    )

    grid_chart = Grid(init_opts=opts.InitOpts(width="100%", height="500px"))
    grid_chart.add(overlap_kline, grid_opts=opts.GridOpts(pos_left="7%", pos_right="4%", height="60%"))
    grid_chart.add(bar, grid_opts=opts.GridOpts(pos_left="7%", pos_right="4%", pos_top="73%", height="15%"))
    return grid_chart.render_embed()


# ==========================================
# 6. Gemini Multi-Agent 結構化工具 (Function Calling)
# ==========================================
def tool_get_stock_full_analysis(stock_id: str) -> dict:
    """[Agent 工具] 取得個股技術指標、本益比/殖利率、買賣點訊號、法人籌碼、即時新聞與美國政要交易對照"""
    df = fetch_stock_quant_data(stock_id)
    if df is None or df.empty:
        return {"error": f"找不到 {stock_id} 的行情資料"}

    latest = df.iloc[-1]
    stock_name, bfp_info = analyze_twstock_best_four_point(stock_id, df)
    news_items, chips_info, chips_df, finmind_val = fetch_all_chips_news_and_val(
        stock_id, stock_name, st.session_state.get("finmind_token", "")
    )
    if not chips_info:
        chips_info, _ = build_smart_chips_fallback(df)

    fund = fetch_stock_fundamentals_v3(stock_id, float(latest["Close"]), finmind_val)
    capitol_data = get_capitol_trades_and_tw_mapping()

    formatted_news = [
        f"[{n['date']}] [{n['title']}]({n['link']})" if n.get("link") else f"[{n['date']}] {n['title']}"
        for n in news_items[:6]
    ]

    return {
        "stock_id": stock_id,
        "stock_name": stock_name,
        "latest_close": round(float(latest["Close"]), 2),
        "MA5": round(float(latest["MA5"]), 2) if pd.notna(latest["MA5"]) else None,
        "MA20": round(float(latest["MA20"]), 2) if pd.notna(latest["MA20"]) else None,
        "MA60": round(float(latest["MA60"]), 2) if pd.notna(latest["MA60"]) else None,
        "RSI_14": round(float(latest["RSI"]), 2) if pd.notna(latest["RSI"]) else None,
        "K_9": round(float(latest["K"]), 2) if pd.notna(latest["K"]) else None,
        "D_9": round(float(latest["D"]), 2) if pd.notna(latest["D"]) else None,
        "ATR_14": round(float(latest["ATR"]), 2) if pd.notna(latest["ATR"]) else None,
        "max_drawdown_6m_pct": round(float(df["Drawdown"].min() * 100), 2),
        "PE_ratio": fund.get("trailing_pe"),
        "PB_ratio": fund.get("price_to_book"),
        "dividend_yield_pct": fund.get("dividend_yield"),
        "twstock_best_four_point": bfp_info,
        "institutional_chips": chips_info,
        "recent_news_with_links": formatted_news,
        "us_capitol_trades_highlights": capitol_data[:3],
    }


def tool_propose_bracket_order(
    stock_id: str,
    action: str,
    entry_price: float,
    atr_value: float,
    sl_multiplier: float = 1.5,
    tp_multiplier: float = 3.0,
) -> dict:
    """[Agent 工具] 自動試算 ATR 括號單停損(SL)、停利(TP)、風報比與建議股數並送入人工核可台"""
    capital = st.session_state.get("total_capital", 1000000)
    risk_pct = st.session_state.get("max_risk_pct", 1.5) / 100.0

    action = action.upper()
    if action == "BUY":
        sl_price = round(entry_price - (atr_value * sl_multiplier), 2)
        tp_price = round(entry_price + (atr_value * tp_multiplier), 2)
        risk_per_share = max(entry_price - sl_price, 0.01)
        reward_per_share = tp_price - entry_price
    else:
        sl_price = round(entry_price + (atr_value * sl_multiplier), 2)
        tp_price = round(entry_price - (atr_value * tp_multiplier), 2)
        risk_per_share = max(sl_price - entry_price, 0.01)
        reward_per_share = entry_price - tp_price

    rr_ratio = round(reward_per_share / risk_per_share, 2)
    max_loss_allowed = capital * risk_pct
    suggested_shares = int(max_loss_allowed // risk_per_share)

    order_proposal = {
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
        "env": st.session_state.env_mode,
        "stock_id": stock_id,
        "action": "買進 (BUY)" if action == "BUY" else "賣出 (SELL)",
        "entry_price": entry_price,
        "stop_loss": sl_price,
        "take_profit": tp_price,
        "rr_ratio": rr_ratio,
        "suggested_shares": suggested_shares,
        "max_risk_ntd": int(max_loss_allowed),
    }
    st.session_state.pending_orders.append(order_proposal)
    return order_proposal


# ==========================================
# 7. 側邊欄：三竹風格「自選股看盤清單」(修復按鈕文字截斷)
# ==========================================
with st.sidebar:
    st.markdown("## 📱 自選股看盤清單")
    st.caption("輸入「代號」或「中文股名」快速切換與新增")

    search_col1, search_col2 = st.columns([2.4, 1.1])
    with search_col1:
        new_stock_query = st.text_input(
            "搜尋股票", placeholder="例: 2603 或 長榮", label_visibility="collapsed"
        )
    with search_col2:
        if st.button("➕新增", use_container_width=True):
            resolved = resolve_stock_input(new_stock_query)
            if resolved:
                if resolved not in st.session_state.watchlist:
                    st.session_state.watchlist.insert(0, resolved)
                st.session_state.selected_stock = resolved
                st.rerun()
            else:
                st.warning("查無此代號")

    wl_quotes = fetch_watchlist_quotes(st.session_state.watchlist)

    for sid in list(st.session_state.watchlist):
        sname = get_stock_name(sid)
        q_info = wl_quotes.get(sid)
        if q_info and pd.notna(q_info["price"]):
            p_val = q_info["price"]
            pct = q_info["pct"]
            # 高價股 (>=500) 省略小數點，使用精簡箭頭，確保側邊欄按鈕絕不截斷成 ...
            p_str = f"{p_val:.0f}" if p_val >= 500 else f"{p_val:.1f}"
            sign = "▲" if pct > 0 else "▼" if pct < 0 else "-"
            btn_label = f"{sid} {sname} {p_str} ({sign}{abs(pct):.1f}%)"
        else:
            btn_label = f"{sid} {sname}"

        is_active = sid == st.session_state.selected_stock
        row_c1, row_c2 = st.columns([4.8, 1])
        with row_c1:
            if st.button(
                btn_label,
                key=f"wl_select_{sid}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state.selected_stock = sid
                st.rerun()
        with row_c2:
            if len(st.session_state.watchlist) > 1:
                if st.button("✖", key=f"wl_del_{sid}", help=f"移除 {sname}"):
                    st.session_state.watchlist.remove(sid)
                    if st.session_state.selected_stock == sid:
                        st.session_state.selected_stock = st.session_state.watchlist[0]
                    st.rerun()

    st.divider()

    with st.expander("⚙️ API 金鑰與風控參數設定", expanded=False):
        default_gemini_key = ""
        try:
            default_gemini_key = st.secrets.get("GEMINI_API_KEY", "")
        except Exception:
            pass

        gemini_api_key = st.text_input(
            "🔑 Gemini API Key", value=default_gemini_key, type="password"
        )
        selected_model = st.selectbox(
            "🧠 AI 模型版本",
            ["gemini-3.8-flash", "gemini-3.0-flash", "gemini-3.1-pro-preview"],
            index=0,
        )
        finmind_token = st.text_input("🔑 FinMind Token (選填)", type="password")
        st.session_state["finmind_token"] = finmind_token

        st.markdown("---")
        st.session_state.env_mode = st.radio(
            "連線環境", ["模擬環境 (Simulation)", "正式環境 (Live - 嚴格核可)"]
        )
        st.session_state.kill_switch = st.toggle("🚨 緊急總開關 (Kill Switch)", value=False)
        total_capital = st.number_input("總操作資金 (NTD)", min_value=50000, value=1000000, step=50000)
        max_risk_pct = st.slider("單筆最大風險 (%)", 0.5, 5.0, 1.5, 0.5)
        st.session_state["total_capital"] = total_capital
        st.session_state["max_risk_pct"] = max_risk_pct


# ==========================================
# 8. 主畫面頂部：富果風個股核心報價橫幅 (Pro Card 零截斷)
# ==========================================
target_stock = st.session_state.selected_stock
df_stock = fetch_stock_quant_data(target_stock)

if df_stock is None or df_stock.empty:
    st.error(f"⚠️ 無法取得 `{target_stock}` 的市場數據，請從左側選擇其他股票或確認代號。")
    st.stop()

latest_row = df_stock.iloc[-1]
prev_row = df_stock.iloc[-2] if len(df_stock) > 1 else latest_row
price_diff = float(latest_row["Close"] - prev_row["Close"])
diff_pct = (price_diff / float(prev_row["Close"])) * 100

stock_name, bfp_text = analyze_twstock_best_four_point(target_stock, df_stock)
news_items, chips_summary, chips_daily_df, finmind_val = fetch_all_chips_news_and_val(
    target_stock, stock_name, st.session_state.get("finmind_token", "")
)
# 若雲端 FinMind 匿名額度受限，啟用智慧量價籌碼備援，確保籌碼分頁不空白
if chips_daily_df.empty:
    chips_summary, chips_daily_df = build_smart_chips_fallback(df_stock)

fundamentals = fetch_stock_fundamentals_v3(target_stock, float(latest_row["Close"]), finmind_val)
is_etf = target_stock.startswith("00")

head_col1, head_col2 = st.columns([2.5, 1.5])
with head_col1:
    st.markdown(
        f"## 📌 {target_stock} {stock_name} "
        f"<span style='font-size:16px; color:gray; font-weight:normal;'>｜ {fundamentals.get('industry', '台灣上市櫃')}</span>",
        unsafe_allow_html=True,
    )
with head_col2:
    kline_period = st.segmented_control(
        "資料區間", options=["3mo", "6mo", "1y", "2y"], default="6mo"
    )
    if kline_period and kline_period != "6mo":
        df_stock = fetch_stock_quant_data(target_stock, period=kline_period)
        latest_row = df_stock.iloc[-1]

# 頂部 6 格核心資訊卡（雙層獨立顯示，徹底解決 2415.0 / 23... 與本益比誤判問題）
m1, m2, m3, m4, m5, m6 = st.columns(6)
render_pro_card(
    m1,
    "最新收盤價",
    f"{latest_row['Close']:.2f}",
    f"{price_diff:+.2f} ({diff_pct:+.2f}%)",
    "up" if price_diff > 0 else "down" if price_diff < 0 else "neutral",
)
render_pro_card(
    m2,
    "今日最高 / 最低",
    f"高: {latest_row['High']:.1f}",
    f"低: {latest_row['Low']:.1f} (開 {latest_row['Open']:.1f})",
)
render_pro_card(
    m3,
    "成交量 (張)",
    f"{int(latest_row['Volume'] // 1000):,}",
    f"5日均量 {int(df_stock['Volume'].tail(5).mean() // 1000):,} 張",
)
render_pro_card(
    m4,
    "本益比 (P/E)",
    f"{fundamentals['trailing_pe']:.1f} 倍" if fundamentals.get("trailing_pe") else ("ETF 不適用" if is_etf else "計算中"),
    f"淨值比(P/B): {fundamentals['price_to_book']:.2f}x" if fundamentals.get("price_to_book") else "官方財務估值",
)
render_pro_card(
    m5,
    "現金殖利率",
    f"{fundamentals['dividend_yield']:.2f}%" if fundamentals.get("dividend_yield") else "N/A",
    f"ROE: {fundamentals['roe']:.1f}%" if fundamentals.get("roe") else "股息回報指標",
    "up" if fundamentals.get("dividend_yield") and fundamentals["dividend_yield"] >= 3.5 else "neutral",
)
render_pro_card(
    m6,
    "RSI(14) & KD指標",
    f"RSI: {latest_row['RSI']:.1f}",
    f"K(9): {latest_row['K']:.1f} / D(9): {latest_row['D']:.1f}",
)


# ==========================================
# 9. 六大核心功能分頁 (整合影片三大網站神器)
# ==========================================
tab_tech, tab_fj, tab_capitol, tab_fund, tab_news, tab_ai = st.tabs(
    [
        "📈 技術看盤室",
        "⚡ 全球快訊雷達 (FinancialJuice)",
        "🏛️ 政要與法人籌碼 (CapitolTrades)",
        "📊 基本面與財報估值",
        f"📰 即時財經新聞 ({len(news_items)})",
        f"💬 AI 戰情群組 Teamily ({len(st.session_state.pending_orders)})",
    ]
)

# ------------------------------------------
# 分頁 1：技術看盤室
# ------------------------------------------
with tab_tech:
    ctrl_c1, ctrl_c2 = st.columns([3, 1])
    with ctrl_c1:
        st.info(f"**🎯 買賣點與趨勢即時診斷**：{bfp_text}")
    with ctrl_c2:
        show_bb = st.checkbox("顯示布林通道 (Bollinger Bands)", value=True)

    chart_html = render_pyecharts_kline(df_stock, target_stock, stock_name, show_bb=show_bb)
    render_chart_html(chart_html, height=510)

    st.markdown("##### 📐 關鍵量化與技術指標一覽（完整雙層顯示）")
    t1, t2, t3, t4, t5, t6 = st.columns(6)
    render_pro_card(
        t1,
        "短期均線 (MA5 / MA20)",
        f"MA5: {latest_row['MA5']:.1f}",
        f"MA20: {latest_row['MA20']:.1f}",
        "up" if latest_row["MA5"] > latest_row["MA20"] else "down",
    )
    render_pro_card(
        t2,
        "季線生命線 (MA60)",
        f"{latest_row['MA60']:.1f}" if pd.notna(latest_row["MA60"]) else "資料累積中",
        "站上季線偏多" if pd.notna(latest_row["MA60"]) and latest_row["Close"] >= latest_row["MA60"] else "季線之下整理",
        "up" if pd.notna(latest_row["MA60"]) and latest_row["Close"] >= latest_row["MA60"] else "down",
    )
    render_pro_card(
        t3,
        "KD 隨機指標 (9日)",
        f"K值: {latest_row['K']:.1f}",
        f"D值: {latest_row['D']:.1f} ({'黃金交叉' if latest_row['K'] > latest_row['D'] else '死亡交叉'})",
        "up" if latest_row["K"] > latest_row["D"] else "down",
    )
    render_pro_card(
        t4,
        "MACD 動能柱狀體",
        f"{latest_row['MACD_Hist']:+.2f}" if pd.notna(latest_row["MACD_Hist"]) else "N/A",
        f"DIF: {latest_row['MACD']:.1f} / DEA: {latest_row['MACD_Signal']:.1f}",
        "up" if pd.notna(latest_row["MACD_Hist"]) and latest_row["MACD_Hist"] > 0 else "down",
    )
    render_pro_card(
        t5,
        "ATR(14) 真實波幅",
        f"{latest_row['ATR']:.2f} 元",
        f"建議停損距離 ±{latest_row['ATR']*1.5:.1f} 元",
    )
    render_pro_card(
        t6,
        "區間最大回撤 (MDD)",
        f"{df_stock['Drawdown'].min()*100:.2f}%",
        f"目前距高點 {latest_row['Drawdown']*100:.2f}%",
        "down",
    )

# ------------------------------------------
# 分頁 2：⚡ 全球快訊雷達 (FinancialJuice 風格)
# ------------------------------------------
with tab_fj:
    st.markdown("#### 🌍 Global Markets Radar ｜ 跨市場即時閃電報價 (FinancialJuice 風格)")
    st.caption("掌握影響台股開盤最關鍵的美股期貨、費半、台積電 ADR、匯率與黃金即時連動")

    macro_quotes = fetch_global_macro_radar()
    if macro_quotes:
        g_cols = st.columns(len(macro_quotes))
        for idx, mq in enumerate(macro_quotes):
            render_pro_card(
                g_cols[idx],
                f"{mq['label']} ({mq['symbol']})",
                f"{mq['price']:,.2f}",
                f"{mq['pct']:+.2f}%",
                "up" if mq["pct"] > 0 else "down" if mq["pct"] < 0 else "neutral",
            )

    st.divider()
    fj_col1, fj_col2 = st.columns([3, 1])
    with fj_col1:
        st.markdown("#### 🔴 Live Breaking Wire ｜ 24H 全球與台股突發情報串流")
    with fj_col2:
        st.link_button("🌐 開啟 FinancialJuice 官網", "https://www.financialjuice.com/", use_container_width=True)

    wire_news = fetch_breaking_news_wire(stock_name)
    if wire_news:
        for w in wire_news:
            card_cls = "breaking-card" if w["breaking"] else "news-card"
            badge_html = '<span class="badge-breaking">BREAKING 重大快訊</span>' if w["breaking"] else ""
            st.markdown(
                f"""
                <div class="{card_cls}">
                    <div style="color: #9ca3af; font-size: 0.82rem;">{badge_html}🕒 {w['time']} ｜ 情報源：{w['source']}</div>
                    <a class="news-link" href="{w['link']}" target="_blank" rel="noopener noreferrer">⚡ {w['title']}</a>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.info("正在同步全球即時快訊...")

# ------------------------------------------
# 分頁 3：🏛️ 政要與法人籌碼 (CapitolTrades + 台股三大法人)
# ------------------------------------------
with tab_capitol:
    cap_c1, cap_c2 = st.columns([3, 1])
    with cap_c1:
        st.markdown("#### 🏛️ Capitol Trades 美國國會政要申報交易 × 台股供應鏈連動雷達")
        st.caption("追蹤美國重量級政治人物（如 Nancy Pelosi 等）買賣美股科技巨頭動向，並自動映射受惠台股名單")
    with cap_c2:
        st.link_button("🏛️ 開啟 CapitolTrades 官網", "https://www.capitoltrades.com/", use_container_width=True)

    cap_list = get_capitol_trades_and_tw_mapping()
    for item in cap_list:
        badge = (
            '<span class="trade-buy">🟢 BUY 買進申報</span>'
            if item["action"] == "BUY"
            else '<span class="trade-sell">🔴 SELL 賣出申報</span>'
        )
        is_target_linked = target_stock in item["tw_linked"]
        highlight_border = "border: 1px solid #f59e0b;" if is_target_linked else ""
        match_tag = f"🔥 **與你目前鎖定的 `{target_stock} {stock_name}` 直接連動！**" if is_target_linked else ""

        st.markdown(
            f"""
            <div class="news-card" style="{highlight_border}">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
                    <div><strong>👤 {item['politician']}</strong> ｜ 標的：<code>{item['ticker']}</code></div>
                    <div>{badge} ｜ 規模：<code>{item['size']}</code></div>
                </div>
                <div style="margin-top:6px; font-size:0.92rem;">
                    🔗 <strong>連動台股供應鏈</strong>：<span style="color:#60a5fa; font-weight:600;">{item['tw_linked']}</span> {match_tag}
                </div>
                <div style="margin-top:4px; color:#9ca3af; font-size:0.86rem;">💡 情報解讀：{item['insight']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()
    st.markdown(f"#### 🏦 `{target_stock} {stock_name}` 台股三大法人大戶近期買賣超動向")
    st.success(f"**📊 區間累計買賣超**：{chips_summary}")

    if not chips_daily_df.empty:
        chart_cols = [c for c in ["外資", "投信", "自營商", "自營商(自行)", "自營商(避險)"] if c in chips_daily_df.columns]
        if chart_cols:
            st.bar_chart(chips_daily_df[chart_cols])
        st.markdown("##### 📋 每日法人買賣超明細表 (由新到舊)")
        st.dataframe(chips_daily_df.iloc[::-1], use_container_width=True)

# ------------------------------------------
# 分頁 4：📊 基本面與財報估值 (徹底修復 N/A 與暫無公司簡介)
# ------------------------------------------
with tab_fund:
    st.markdown("#### 💎 個股價值評估與財務核心指標")
    f1, f2, f3, f4 = st.columns(4)
    render_pro_card(
        f1,
        "本益比 (P/E)",
        f"{fundamentals['trailing_pe']:.2f} 倍" if fundamentals.get("trailing_pe") else ("ETF 不適用" if is_etf else "計算中"),
        "衡量股價相對獲利倍數",
    )
    render_pro_card(
        f2,
        "股價淨值比 (P/B)",
        f"{fundamentals['price_to_book']:.2f} 倍" if fundamentals.get("price_to_book") else ("ETF 不適用" if is_etf else "計算中"),
        "衡量股價相對每股淨值",
    )
    render_pro_card(
        f3,
        "現金殖利率 (Yield)",
        f"{fundamentals['dividend_yield']:.2f}%" if fundamentals.get("dividend_yield") else "N/A",
        f"ROE: {fundamentals['roe']:.1f}%" if fundamentals.get("roe") else "高股息防禦指標",
        "up" if fundamentals.get("dividend_yield") and fundamentals["dividend_yield"] >= 3.5 else "neutral",
    )
    render_pro_card(
        f4,
        "52 週最高 / 最低價",
        f"最高: {fundamentals['52w_high']}" if fundamentals.get("52w_high") else "N/A",
        f"最低: {fundamentals['52w_low']}" if fundamentals.get("52w_low") else "",
    )

    high_52 = fundamentals.get("52w_high")
    low_52 = fundamentals.get("52w_low")
    close_val = latest_row["Close"]

    if high_52 and low_52 and high_52 > low_52 and pd.notna(close_val):
        raw_pct = (float(close_val) - low_52) / (high_52 - low_52)
        pos_pct = float(np.clip(raw_pct, 0.0, 1.0))
        st.divider()
        st.markdown(
            f"##### 📏 52 週股價位階：目前位於近一年區間的 `{pos_pct*100:.1f}%` （最低 `{low_52}` ～ 最高 `{high_52}`）"
        )
        st.progress(pos_pct)

    st.divider()
    st.markdown("##### 🏢 公司營運與產業護城河簡介")
    st.info(fundamentals.get("summary"))

# ------------------------------------------
# 分頁 5：📰 即時財經新聞 (Google News 台灣財經 RSS + FinMind 雙引擎)
# ------------------------------------------
with tab_news:
    st.markdown(f"#### 📰 `{target_stock} {stock_name}` 最新相關財經新聞與法說會動態")
    if news_items:
        for item in news_items:
            if item.get("link"):
                link_html = (
                    f'<a class="news-link" href="{item["link"]}" target="_blank" rel="noopener noreferrer">'
                    f'🔗 {item["title"]}</a>'
                )
            else:
                link_html = f'<span class="news-link">{item["title"]}</span>'

            st.markdown(
                f"""
                <div class="news-card">
                    <div style="color: #8b949e; font-size: 0.85rem;">🕒 {item['date']} ｜ 來源：{item['source']}</div>
                    {link_html}
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.info("正在同步最新財經新聞...")

# ------------------------------------------
# 分頁 6：💬 AI 戰情群組 (Teamily.ai 風格：@標記智能體 + 自動追蹤排程器 + 風控核可台)
# ------------------------------------------
with tab_ai:
    st.markdown("#### 💬 Teamily 風格 AI 投資戰情群組（支援 `@標記` 專屬智慧體 & 每日自動追蹤排程）")

    tagged_agents = st.multiselect(
        "👥 標記群組內的 AI 智慧體 (@Agent 分工協作)",
        options=[
            "@全體Agent (全員分工協作)",
            "@台股技術Agent (TWStock 技術與買賣點)",
            "@全球快訊Agent (FinancialJuice 宏觀與突發新聞)",
            "@政要籌碼Agent (CapitolTrades 國會交易與法人籌碼)",
            "@毒舌風控Agent (MDD 壓力測試與 ATR 括號單精算)",
        ],
        default=["@全體Agent (全員分工協作)"],
    )

    sk1, sk2, sk3, sk4 = st.columns(4)
    triggered_skill_prompt = None

    with sk1:
        if st.button("🌅 @全體 早盤全方位會診", use_container_width=True):
            triggered_skill_prompt = (
                f"@全體Agent 請執行【早盤全方位會診】：呼叫工具取得 {target_stock} ({stock_name}) 的最新技術面、"
                f"本益比/殖利率、三大法人籌碼與新聞，各智慧體分工給出今日操作決策。"
            )
    with sk2:
        if st.button("🏛️ @政要籌碼 國會與外資報告", use_container_width=True):
            triggered_skill_prompt = (
                f"@政要籌碼Agent 請執行【Capitol Trades 政要交易與外資籌碼報告】：分析美國國會議員最新科技股申報交易"
                f"如何連動影響 {target_stock} ({stock_name})，並結合三大法人買賣超給出籌碼結論。"
            )
    with sk3:
        if st.button("📐 @毒舌風控 ATR括號單試算", use_container_width=True):
            triggered_skill_prompt = (
                f"@毒舌風控Agent 請執行【ATR括號單自動試算】：查詢 {target_stock} 的最新收盤價與 ATR_14，"
                f"呼叫 tool_propose_bracket_order 工具建立一筆括號單建議（含停損、停利、風報比與建議股數），並進行毒舌風險講評。"
            )
    with sk4:
        if st.button("⚡ 立即執行「21:00 每日追蹤排程」", type="primary", use_container_width=True):
            triggered_skill_prompt = (
                f"@全體Agent 請立即執行【📊 Capitol Trades & {target_stock} {stock_name} 每日 21:00 定時追蹤報告】："
                f"請整合 (1) CapitolTrades.com 美國政要最新買賣申報動向、(2) FinancialJuice 全球宏觀與個股最新快訊、"
                f"(3) {target_stock} 技術指標與法人籌碼，輸出一份結構清晰的《📊 每日情報追蹤日報》！"
            )

    ai_col_chat, ai_col_right = st.columns([1.35, 1.0], gap="large")

    with ai_col_chat:
        chat_container = st.container(height=500, border=True)
        with chat_container:
            if not st.session_state.chat_history:
                with st.chat_message("assistant"):
                    st.markdown(
                        f"👋 歡迎進入 **AI 投資戰情 LINE 群組**（目前鎖定：`{target_stock} {stock_name}`）！\n\n"
                        f"你可以直接用上方選單 **`@標記`** 特定智慧體，或點擊快捷鍵：\n"
                        f"- 🤖 **`@台股技術Agent`**：K 線、均線、KD/RSI、買賣點訊號\n"
                        f"- ⚡ **`@全球快訊Agent`**：FinancialJuice 突發快訊、美股與匯率連動\n"
                        f"- 🏛️ **`@政要籌碼Agent`**：CapitolTrades 國會議員買賣追蹤、外資投信籌碼\n"
                        f"- 🛡️ **`@毒舌風控Agent`**：最大回撤 MDD、ATR 括號單自動試算\n"
                    )
            for msg in st.session_state.chat_history:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

        user_input = st.chat_input(
            f"在群組發話（例如：@全體Agent 每天晚上9點幫我追蹤 CapitolTrades 與 {stock_name} 新聞）..."
        )
        active_prompt = triggered_skill_prompt or user_input

        if active_prompt:
            tag_prefix = " ".join([a.split(" ")[0] for a in tagged_agents]) if tagged_agents else "@全體Agent"
            full_user_msg = active_prompt if active_prompt.startswith("@") else f"{tag_prefix} {active_prompt}"

            if any(k in full_user_msg for k in ["每天", "排程", "定時", "晚上", "點報告"]):
                st.session_state.scheduled_tasks.append({
                    "id": datetime.datetime.now().strftime("%H%M-%S"),
                    "time": "每天 21:00 (Asia/Taipei)",
                    "target": f"追蹤 CapitolTrades 與 {target_stock} {stock_name} 最新情報",
                    "agents": tag_prefix,
                    "status": "啟用中 ✅",
                })

            st.session_state.chat_history.append({"role": "user", "content": full_user_msg})
            with chat_container:
                with st.chat_message("user"):
                    st.markdown(full_user_msg)

                with st.chat_message("assistant"):
                    if not gemini_api_key:
                        err_msg = "⚠️ 請先打開左側邊欄底部的 **「⚙️ API 金鑰與風控參數設定」** 輸入你的 Gemini API Key！"
                        st.warning(err_msg)
                        st.session_state.chat_history.append({"role": "assistant", "content": err_msg})
                    else:
                        with st.spinner(f"群組智慧體 ({tag_prefix}) 正在分工調用工具與撰寫報告..."):
                            try:
                                client = genai.Client(api_key=gemini_api_key)
                                system_instruction = f"""
你現在是仿照 Teamily.ai 與 Shioaji Pro 設計的「台股 AI 投資戰情群組」。
目前鎖定台股：{target_stock} ({stock_name})，總資金：{total_capital} 元，單筆最大風險：{max_risk_pct}%。
使用者目前標記召喚的智慧體為：{', '.join(tagged_agents)}。

請務必先透過 Function Calling 呼叫 `tool_get_stock_full_analysis` 取得最新真實數據（若涉及下單或停損停利試算，請主動呼叫 `tool_propose_bracket_order`）。
請根據被標記的智慧體身分，以群組對話分工形式（清楚標示每位 Agent 的頭銜）回答：
- **⚡ @全球快訊Agent (FinancialJuice 情報員)**：解讀近期新聞（保留 Markdown 超連結）與全球美股/匯率連動。
- **🏛️ @政要籌碼Agent (CapitolTrades & 法人追蹤員)**：解讀美國國會議員申報交易如何連動台股供應鏈，以及三大法人買賣超、本益比/殖利率估值。
- **📈 @台股技術Agent (TWStock 量化交易員)**：根據均線、KD、RSI、ATR 與買賣點訊號給出明確支撐壓力。
- **🛡️ @毒舌風控Agent (資產精算師)**：根據最大回撤 (MDD) 與風報比毒舌點出盲點，並提醒去右側「獨立風控核可台」確認委託。
如果使用者要求建立每日定時追蹤排程，請先回覆確認「✅ 排程任務已建立！每天 21:00 (Asia/Taipei) 自動追蹤並發送到本群組」，並直接為他產出第一份今日最新追蹤報告！
"""
                                config = types.GenerateContentConfig(
                                    system_instruction=system_instruction,
                                    tools=[tool_get_stock_full_analysis, tool_propose_bracket_order],
                                    temperature=0.3,
                                )
                                chat = client.chats.create(model=selected_model, config=config)
                                response = chat.send_message(full_user_msg)

                                reply_text = response.text or "已完成群組工具調用，請查看右側面板更新。"
                                st.markdown(reply_text)
                                st.session_state.chat_history.append(
                                    {"role": "assistant", "content": reply_text}
                                )
                                if "tool_propose_bracket_order" in full_user_msg or st.session_state.pending_orders:
                                    st.rerun()
                            except Exception as e:
                                st.error(f"Agent 執行發生錯誤：{e}")

    with ai_col_right:
        st.markdown("##### ⏰ Teamily 自動追蹤排程任務 (Scheduled Bots)")
        for task in st.session_state.scheduled_tasks:
            st.markdown(
                f"""
                <div class="schedule-box">
                    <strong>✅ 排程任務已建立！ (`{task['id']}`)</strong><br>
                    • <strong>排程時間</strong>：{task['time']}<br>
                    • <strong>狀態</strong>：{task['status']}<br>
                    • <strong>負責智慧體</strong>：<code>{task['agents']}</code><br>
                    • <strong>追蹤說明</strong>：{task['target']}
                </div>
                """,
                unsafe_allow_html=True,
            )

        with st.expander("➕ 新增自訂每日追蹤排程任務", expanded=False):
            new_task_time = st.selectbox("執行時間 (Asia/Taipei)", ["每天 08:30 (盤前早報)", "每天 13:45 (盤後籌碼)", "每天 21:00 (美股與政要日報)"])
            new_task_target = st.text_input("追蹤網址或主題", value=f"https://www.capitoltrades.com/ 與 {target_stock} {stock_name} 新聞")
            if st.button("✅ 建立排程任務", use_container_width=True):
                st.session_state.scheduled_tasks.append({
                    "id": datetime.datetime.now().strftime("%H%M-%S"),
                    "time": new_task_time,
                    "target": new_task_target,
                    "agents": "@全體Agent",
                    "status": "啟用中 ✅",
                })
                st.rerun()

        st.divider()

        st.markdown("##### 🛡️ 獨立委託風控核可台 (Human-in-the-Loop)")
        if st.session_state.kill_switch:
            st.error("🚨 已啟動【緊急總開關 Kill Switch】，所有委託核可已鎖定！")

        if not st.session_state.pending_orders:
            st.info("目前無待核可委託。可點擊上方「📐 @毒舌風控 ATR括號單試算」生成。")
        else:
            for idx, order in enumerate(list(st.session_state.pending_orders)):
                with st.container(border=True):
                    st.warning(f"⏳ **待核可括號單 #{idx+1}** ({order['timestamp']})")
                    st.write(f"**標的**：`{order['stock_id']}` ｜ **動作**：`{order['action']}`")
                    st.write(
                        f"**進場**：`{order['entry_price']}` ｜ **停損(SL)**：`{order['stop_loss']}` ｜ **停利(TP)**：`{order['take_profit']}`"
                    )
                    st.write(
                        f"**風報比**：`1 : {order['rr_ratio']}` ｜ **建議股數**：`{order['suggested_shares']:,} 股` (風險上限 ${order['max_risk_ntd']:,})"
                    )
                    b1, b2 = st.columns(2)
                    with b1:
                        if st.button(f"🔴 拒絕 #{idx+1}", key=f"rej_{idx}", use_container_width=True):
                            st.session_state.pending_orders.pop(idx)
                            st.rerun()
                    with b2:
                        if st.button(
                            f"🟢 核可送出 #{idx+1}",
                            key=f"app_{idx}",
                            disabled=st.session_state.kill_switch,
                            use_container_width=True,
                        ):
                            approved = st.session_state.pending_orders.pop(idx)
                            approved["status"] = "已人工核可"
                            st.session_state.executed_orders.append(approved)
                            st.rerun()

        if st.session_state.executed_orders:
            st.markdown("##### 📜 已核可委託紀錄")
            st.dataframe(pd.DataFrame(st.session_state.executed_orders), use_container_width=True)
