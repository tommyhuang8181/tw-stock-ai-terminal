import datetime
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
# 1. 頁面設定與自訂美化 CSS (修復字體截斷與新聞卡片)
# ==========================================
st.set_page_config(
    page_title="台股全能 AI 投資終端",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    div[data-testid="stMetric"] {
        background-color: rgba(128, 128, 128, 0.08);
        border: 1px solid rgba(128, 128, 128, 0.18);
        padding: 12px 14px;
        border-radius: 10px;
    }
    /* 縮小指標卡數字字體，避免 244.5... 被截斷 */
    div[data-testid="stMetricValue"] {
        font-size: 1.35rem !important;
    }
    .news-card {
        padding: 14px 18px;
        margin-bottom: 12px;
        border-radius: 8px;
        background-color: rgba(128, 128, 128, 0.07);
        border-left: 4px solid #ef5350;
        transition: background-color 0.2s;
    }
    .news-card:hover {
        background-color: rgba(128, 128, 128, 0.14);
    }
    .news-link {
        color: #58a6ff !important;
        text-decoration: none !important;
        font-weight: 600;
        font-size: 1.05rem;
        line-height: 1.5;
        display: inline-block;
        margin-top: 4px;
    }
    .news-link:hover {
        text-decoration: underline !important;
        color: #79c0ff !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_chart_html(html_str: str, height: int = 520):
    if hasattr(st, "iframe"):
        st.iframe(html_str, height=height)
    else:
        components.html(html_str, height=height)


# ==========================================
# 2. 初始化每個使用者的獨立 Session State
# ==========================================
DEFAULT_WATCHLIST = ["2330", "2317", "2454", "2382", "2308", "2603", "0050"]

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
if "env_mode" not in st.session_state:
    st.session_state.env_mode = "模擬環境 (Simulation)"
if "kill_switch" not in st.session_state:
    st.session_state.kill_switch = False


# ==========================================
# 3. 股票代號解析與快取資料引擎
# ==========================================
def get_stock_name(stock_id: str) -> str:
    if HAS_TWSTOCK and stock_id in twstock.codes:
        return twstock.codes[stock_id].name
    fallback_names = {
        "2330": "台積電", "2317": "鴻海", "2454": "聯發科",
        "2382": "廣達", "2308": "台達電", "2603": "長榮", "0050": "元大台灣50"
    }
    return fallback_names.get(stock_id, stock_id)


def resolve_stock_input(query: str):
    q = query.strip()
    if not q:
        return None
    if HAS_TWSTOCK:
        if q in twstock.codes:
            return q
        for code, info in twstock.codes.items():
            if info.name == q and info.type in ["股票", "ETF"]:
                return code
    if q.isdigit() or (len(q) >= 4 and q[:4].isdigit()):
        return q
    return None


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


@st.cache_data(ttl=900)
def fetch_finmind_data(stock_id: str, finmind_token: str = ""):
    """使用 FinMind 抓取最新新聞、三大法人買賣超與官方本益比/殖利率/股價淨值比"""
    dl = DataLoader()
    if finmind_token:
        try:
            dl.login_by_token(api_token=finmind_token)
        except Exception:
            pass

    news_start = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    chips_start = (datetime.date.today() - datetime.timedelta(days=25)).strftime("%Y-%m-%d")

    news_items = []
    chips_summary = "暫無近期法人籌碼資料"
    chips_daily_df = pd.DataFrame()
    val_dict = {"pe": None, "pb": None, "dividend_yield": None}

    # 1. 抓取最新 7 天新聞並由新到舊排序
    try:
        news_df = dl.taiwan_stock_news(stock_id=stock_id, start_date=news_start)
        if not news_df.empty:
            news_df = news_df.sort_values(by="date", ascending=False)
            seen_titles = set()
            for r in news_df.to_dict("records"):
                title = r.get("title", "").strip()
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    news_items.append({
                        "date": r.get("date", ""),
                        "title": title,
                        "link": r.get("link", ""),
                        "source": r.get("source", "財經新聞"),
                    })
                if len(news_items) >= 12:
                    break
    except Exception:
        pass

    # 2. 抓取證交所官方每日 本益比(PER)、淨值比(PBR)、殖利率(dividend_yield)
    try:
        per_df = dl.taiwan_stock_per(stock_id=stock_id, start_date=chips_start)
        if not per_df.empty:
            latest_per = per_df.iloc[-1]
            val_dict["pe"] = float(latest_per["PER"]) if pd.notna(latest_per.get("PER")) and float(latest_per["PER"]) > 0 else None
            val_dict["pb"] = float(latest_per["PBR"]) if pd.notna(latest_per.get("PBR")) and float(latest_per["PBR"]) > 0 else None
            val_dict["dividend_yield"] = float(latest_per["dividend_yield"]) if pd.notna(latest_per.get("dividend_yield")) else None
    except Exception:
        pass

    # 3. 抓取三大法人買賣超
    try:
        inst_df = dl.taiwan_stock_institutional_investors(
            stock_id=stock_id, start_date=chips_start
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
            summary_series = inst_df.groupby("法人名稱")["net_lots"].sum()
            chips_summary = " ｜ ".join(
                [f"{k}: {int(v):+,} 張" for k, v in summary_series.items()]
            )
            chips_daily_df = inst_df.pivot_table(
                index="date", columns="法人名稱", values="net_lots", aggfunc="sum"
            ).fillna(0).astype(int)
            chips_daily_df["三大法人合計(張)"] = chips_daily_df.sum(axis=1)
    except Exception:
        pass

    return news_items, chips_summary, chips_daily_df, val_dict


@st.cache_data(ttl=1800)
def fetch_stock_fundamentals(stock_id: str, finmind_val: dict) -> dict:
    """整合 FinMind 官方估值與 yfinance 基本面（避免雲端 IP 被擋出現 N/A）"""
    result = {
        "market_cap": None,
        "trailing_pe": finmind_val.get("pe"),
        "forward_pe": None,
        "price_to_book": finmind_val.get("pb"),
        "dividend_yield": finmind_val.get("dividend_yield"),
        "eps": None,
        "roe": None,
        "revenue_growth": None,
        "gross_margins": None,
        "operating_margins": None,
        "52w_high": None,
        "52w_low": None,
        "industry": "台灣上市櫃企業",
        "summary": "暫無公司詳細簡介。",
    }
    if HAS_TWSTOCK and stock_id in twstock.codes:
        result["industry"] = twstock.codes[stock_id].group or "台灣上市櫃企業"

    for suffix in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(f"{stock_id}{suffix}")
            # 用 1 年歷史 K 線直接算 52 週高低點（雲端 100% 不會被擋）
            hist_1y = tk.history(period="1y")
            if not hist_1y.empty:
                result["52w_high"] = round(float(hist_1y["High"].max()), 2)
                result["52w_low"] = round(float(hist_1y["Low"].min()), 2)

            info = tk.info
            if info:
                result["market_cap"] = info.get("marketCap") or result["market_cap"]
                result["trailing_pe"] = result["trailing_pe"] or info.get("trailingPE")
                result["forward_pe"] = info.get("forwardPE")
                result["price_to_book"] = result["price_to_book"] or info.get("priceToBook")
                if result["dividend_yield"] is None and info.get("dividendYield"):
                    dy = float(info["dividendYield"])
                    result["dividend_yield"] = dy * 100 if dy < 1 else dy
                result["eps"] = info.get("trailingEps")
                result["roe"] = info.get("returnOnEquity")
                result["revenue_growth"] = info.get("revenueGrowth")
                result["gross_margins"] = info.get("grossMargins")
                result["operating_margins"] = info.get("operatingMargins")
                result["summary"] = info.get("longBusinessSummary", result["summary"])
            break
        except Exception:
            continue
    return result


def analyze_twstock_best_four_point(stock_id: str):
    stock_name = get_stock_name(stock_id)
    bfp_summary = "歷史技術指標輔助判讀中"

    if HAS_TWSTOCK and stock_id in twstock.codes:
        try:
            stock_obj = twstock.Stock(stock_id)
            bfp = twstock.BestFourPoint(stock_obj)
            buy_r = bfp.best_four_point_to_buy()
            sell_r = bfp.best_four_point_to_sell()
            overall = bfp.best_four_point()
            bfp_summary = (
                f"🟢 四大買點：{buy_r if buy_r else '無'} ｜ "
                f"🔴 四大賣點：{sell_r if sell_r else '無'} ｜ "
                f"⚖️ 綜合判定：{'偏多買進' if overall and overall[0] else '偏空賣出' if overall else '中立觀望'}"
            )
        except Exception:
            pass
    return stock_name, bfp_summary


# ==========================================
# 4. Pyecharts 專業看盤圖表渲染
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
# 5. Gemini Multi-Agent 結構化工具 (Function Calling)
# ==========================================
def tool_get_stock_full_analysis(stock_id: str) -> dict:
    """[Agent 工具] 取得個股技術指標、基本面估值、四大買賣點、三大法人籌碼與新聞"""
    df = fetch_stock_quant_data(stock_id)
    if df is None or df.empty:
        return {"error": f"找不到 {stock_id} 的行情資料"}

    latest = df.iloc[-1]
    stock_name, bfp_info = analyze_twstock_best_four_point(stock_id)
    news_items, chips_info, _, finmind_val = fetch_finmind_data(
        stock_id, st.session_state.get("finmind_token", "")
    )
    fund = fetch_stock_fundamentals(stock_id, finmind_val)

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
        "finmind_institutional_chips": chips_info,
        "recent_news_with_links": formatted_news,
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
# 6. 側邊欄：三竹風格「自選股看盤清單」＋ 收納式設定
# ==========================================
with st.sidebar:
    st.markdown("## 📱 自選股看盤清單")
    st.caption("支援輸入「代號」或「中文股名」快速切換與新增")

    search_col1, search_col2 = st.columns([2.2, 1])
    with search_col1:
        new_stock_query = st.text_input(
            "搜尋股票", placeholder="例: 2603 或 長榮", label_visibility="collapsed"
        )
    with search_col2:
        if st.button("➕ 新增", use_container_width=True):
            resolved = resolve_stock_input(new_stock_query)
            if resolved:
                if resolved not in st.session_state.watchlist:
                    st.session_state.watchlist.insert(0, resolved)
                st.session_state.selected_stock = resolved
                st.rerun()
            else:
                st.warning("查無此代號或名稱")

    wl_quotes = fetch_watchlist_quotes(st.session_state.watchlist)

    for sid in list(st.session_state.watchlist):
        sname = get_stock_name(sid)
        q_info = wl_quotes.get(sid)
        if q_info and pd.notna(q_info["price"]):
            pct = q_info["pct"]
            arrow = "🔺" if pct > 0 else "🟢" if pct < 0 else "➖"
            btn_label = f"{sid} {sname} ｜ {q_info['price']:.1f} ({arrow}{pct:+.2f}%)"
        else:
            btn_label = f"{sid} {sname}"

        is_active = sid == st.session_state.selected_stock
        row_c1, row_c2 = st.columns([4.5, 1])
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
# 7. 主畫面頂部：富果 (Fugle) 風格個股核心報價橫幅
# ==========================================
target_stock = st.session_state.selected_stock
df_stock = fetch_stock_quant_data(target_stock)
stock_name, bfp_text = analyze_twstock_best_four_point(target_stock)
news_items, chips_summary, chips_daily_df, finmind_val = fetch_finmind_data(
    target_stock, st.session_state.get("finmind_token", "")
)
fundamentals = fetch_stock_fundamentals(target_stock, finmind_val)

if df_stock is None or df_stock.empty:
    st.error(f"⚠️ 無法取得 `{target_stock}` 的市場數據，請從左側選擇其他股票或確認代號。")
    st.stop()

latest_row = df_stock.iloc[-1]
prev_row = df_stock.iloc[-2] if len(df_stock) > 1 else latest_row
price_diff = float(latest_row["Close"] - prev_row["Close"])
diff_pct = (price_diff / float(prev_row["Close"])) * 100

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

# 頂部 6 格核心資訊卡（已優化字體大小與 FinMind 雙重估值備援）
m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("最新收盤價", f"{latest_row['Close']:.2f}", f"{price_diff:+.2f} ({diff_pct:+.2f}%)")
m2.metric("今日最高 / 最低", f"{latest_row['High']:.1f} / {latest_row['Low']:.1f}")
m3.metric("成交量 (張)", f"{int(latest_row['Volume'] // 1000):,}")
m4.metric(
    "本益比 (P/E)",
    f"{fundamentals['trailing_pe']:.1f} 倍" if fundamentals.get("trailing_pe") else "ETF/不適用",
)
m5.metric(
    "現金殖利率",
    f"{fundamentals['dividend_yield']:.2f}%" if fundamentals.get("dividend_yield") else "N/A",
)
m6.metric("RSI(14) / K值", f"{latest_row['RSI']:.1f} / {latest_row['K']:.1f}")


# ==========================================
# 8. 五大清爽模組分頁
# ==========================================
tab_tech, tab_fund, tab_chips, tab_news, tab_ai = st.tabs(
    [
        "📈 技術看盤室",
        "📊 基本面與財報估值",
        "🏦 法人籌碼追蹤",
        f"📰 即時財經新聞 ({len(news_items)})",
        f"🤖 AI 顧問團 & 模擬下單 ({len(st.session_state.pending_orders)})",
    ]
)

with tab_tech:
    ctrl_c1, ctrl_c2 = st.columns([3, 1])
    with ctrl_c1:
        st.info(f"**🎯 四大買賣點即時診斷**：{bfp_text}")
    with ctrl_c2:
        show_bb = st.checkbox("顯示布林通道 (Bollinger Bands)", value=True)

    chart_html = render_pyecharts_kline(df_stock, target_stock, stock_name, show_bb=show_bb)
    render_chart_html(chart_html, height=510)

    st.markdown("##### 📐 關鍵量化與技術指標一覽")
    t1, t2, t3, t4, t5, t6 = st.columns(6)
    t1.metric("MA5 / MA20", f"{latest_row['MA5']:.1f} / {latest_row['MA20']:.1f}")
    t2.metric("季線 (MA60)", f"{latest_row['MA60']:.1f}" if pd.notna(latest_row["MA60"]) else "N/A")
    t3.metric("KD 指標 (9日)", f"{latest_row['K']:.1f} / {latest_row['D']:.1f}")
    t4.metric("MACD 柱狀體", f"{latest_row['MACD_Hist']:+.2f}" if pd.notna(latest_row["MACD_Hist"]) else "N/A")
    t5.metric("ATR(14) 真實波幅", f"{latest_row['ATR']:.2f}")
    t6.metric("區間最大回撤 (MDD)", f"{df_stock['Drawdown'].min()*100:.2f}%")

with tab_fund:
    st.markdown("#### 💎 個股價值評估與財務核心指標")
    f1, f2, f3, f4 = st.columns(4)
    f1.metric(
        "本益比 (P/E)",
        f"{fundamentals['trailing_pe']:.2f} 倍" if fundamentals.get("trailing_pe") else "N/A",
    )
    f2.metric(
        "股價淨值比 (P/B)",
        f"{fundamentals['price_to_book']:.2f} 倍" if fundamentals.get("price_to_book") else "N/A",
    )
    f3.metric(
        "現金殖利率",
        f"{fundamentals['dividend_yield']:.2f}%" if fundamentals.get("dividend_yield") else "N/A",
    )
    f4.metric(
        "52 週最高 / 最低",
        f"{fundamentals['52w_high']} / {fundamentals['52w_low']}" if fundamentals.get("52w_high") else "N/A",
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
    st.markdown("##### 🏢 公司營運與產業簡介")
    st.write(fundamentals.get("summary", "暫無公司詳細簡介。"))

with tab_chips:
    st.markdown("#### 🏦 近 20 日三大法人買賣超統計")
    st.success(f"**📊 區間累計買賣超**：{chips_summary}")

    if not chips_daily_df.empty:
        st.markdown("##### 📈 法人每日買賣超趨勢圖 (單位：張)")
        chart_cols = [c for c in ["外資", "投信", "自營商(自行)", "自營商(避險)"] if c in chips_daily_df.columns]
        if chart_cols:
            st.bar_chart(chips_daily_df[chart_cols])

        st.markdown("##### 📋 每日法人買賣超明細表 (由新到舊)")
        st.dataframe(chips_daily_df.iloc[::-1], use_container_width=True)
    else:
        st.info("目前無此標的之三大法人明細資料。")

# ------------------------------------------
# 分頁 4：即時財經新聞 (改用標準 HTML <a> 超連結，徹底隱藏亂碼網址)
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
        st.info("近期暫無相關財經新聞。")

with tab_ai:
    st.markdown("#### ⚡ Shioaji Pro 快捷分析技能 (一鍵召喚三位專家會診)")
    sk1, sk2, sk3, sk4 = st.columns(4)
    triggered_skill_prompt = None

    with sk1:
        if st.button("🌅 /早盤全方位會診", use_container_width=True):
            triggered_skill_prompt = (
                f"請執行【/早盤全方位會診】：呼叫工具取得 {target_stock} 的技術面、基本面估值、籌碼與新聞，"
                f"由三位專家（宏觀情報專家、量化交易員、毒舌資產精算師）分別給出今日操作重點。"
            )
    with sk2:
        if st.button("💎 /基本面與估值體檢", use_container_width=True):
            triggered_skill_prompt = (
                f"請執行【/基本面與估值體檢】：針對 {target_stock} 的本益比(PE)、股價淨值比(PB)與殖利率進行評估，"
                f"判斷目前股價處於昂貴、合理還是便宜區間。"
            )
    with sk3:
        if st.button("📐 /ATR括號單自動試算", use_container_width=True):
            triggered_skill_prompt = (
                f"請執行【/ATR括號單自動試算】：先查詢 {target_stock} 的最新收盤價與 ATR_14，"
                f"接著呼叫 tool_propose_bracket_order 工具建立一筆括號單建議（含停損、停利、風報比與建議股數），"
                f"並請毒舌精算師審視風險。"
            )
    with sk4:
        if st.button("🔥 /持股毒舌壓力測試", use_container_width=True):
            triggered_skill_prompt = (
                f"請執行【/持股毒舌壓力測試】：根據 {target_stock} 近半年最大回撤 (MDD) 與法人籌碼動向，"
                f"請毒舌資產精算師毫不留情地指出現在重倉買進這檔股票可能遭遇的最慘情境與盲點。"
            )

    ai_col_chat, ai_col_order = st.columns([1.4, 1.0], gap="large")

    with ai_col_chat:
        chat_container = st.container(height=480, border=True)
        with chat_container:
            if not st.session_state.chat_history:
                with st.chat_message("assistant"):
                    st.markdown(
                        f"你好！我們是 **{target_stock} {stock_name}** 的專屬 **Multi-Agent 顧問團**：\n"
                        f"- 🌍 **宏觀與基本面情報專家**（財報估值、新聞、三大法人籌碼）\n"
                        f"- 📈 **量化技術交易員**（K線均線、KD/RSI/MACD、四大買賣點）\n"
                        f"- 🛡️ **毒舌資產精算師**（最大回撤 MDD、風報比、ATR 括號單部位控管）\n\n"
                        f"請直接點擊上方技能按鈕，或在下方輸入問題！"
                    )
            for msg in st.session_state.chat_history:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

        user_input = st.chat_input(f"詢問顧問團關於 {target_stock} {stock_name} 的任何投資問題...")
        active_prompt = triggered_skill_prompt or user_input

        if active_prompt:
            st.session_state.chat_history.append({"role": "user", "content": active_prompt})
            with chat_container:
                with st.chat_message("user"):
                    st.markdown(active_prompt)

                with st.chat_message("assistant"):
                    if not gemini_api_key:
                        err_msg = "⚠️ 請先打開左側邊欄底部的 **「⚙️ API 金鑰與風控參數設定」** 輸入你的 Gemini API Key！"
                        st.warning(err_msg)
                        st.session_state.chat_history.append({"role": "assistant", "content": err_msg})
                    else:
                        with st.spinner(f"正在使用 {selected_model} 調用工具並進行三位專家會診..."):
                            try:
                                client = genai.Client(api_key=gemini_api_key)
                                system_instruction = f"""
你現在是台股頂尖 Multi-Agent 投資顧問團隊。
目前使用者鎖定的台股為：{target_stock} ({stock_name})，總資金：{total_capital} 元，單筆最大風險上限：{max_risk_pct}%。

每次回答時，請務必先透過 Function Calling 呼叫 `tool_get_stock_full_analysis` 取得最新真實數據（若涉及下單或停損停利試算，請主動呼叫 `tool_propose_bracket_order`），並依序由以下三位專家進行結構化會診：
1. **🌍 宏觀與基本面情報專家**：解讀本益比/殖利率、近期新聞（請保留 Markdown 新聞超連結格式）與三大法人籌碼。
2. **📈 量化技術交易員**：根據均線、KD、RSI、ATR 與四大買賣點，給出明確支撐壓力與多空判斷。
3. **🛡️ 毒舌資產精算師**：根據最大回撤 (MDD) 與風報比，用犀利不留情面的語氣指出風險盲點，並提醒去右側「獨立風控核可台」確認委託。
"""
                                config = types.GenerateContentConfig(
                                    system_instruction=system_instruction,
                                    tools=[tool_get_stock_full_analysis, tool_propose_bracket_order],
                                    temperature=0.3,
                                )
                                chat = client.chats.create(model=selected_model, config=config)
                                response = chat.send_message(active_prompt)

                                reply_text = response.text or "已完成工具調用，請查看右側風控核可台。"
                                st.markdown(reply_text)
                                st.session_state.chat_history.append(
                                    {"role": "assistant", "content": reply_text}
                                )
                                if "tool_propose_bracket_order" in active_prompt or st.session_state.pending_orders:
                                    st.rerun()
                            except Exception as e:
                                st.error(f"Agent 執行發生錯誤：{e}")

    with ai_col_order:
        st.markdown("##### 🛡️ 獨立委託風控核可台 (Human-in-the-Loop)")
        if st.session_state.kill_switch:
            st.error("🚨 已啟動【緊急總開關 Kill Switch】，所有委託核可已鎖定！")

        if not st.session_state.pending_orders:
            st.info("目前無待核可委託。可點擊上方「📐 /ATR括號單自動試算」生成。")
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
