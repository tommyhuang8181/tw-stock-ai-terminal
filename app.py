import streamlit as st
import yfinance as yf
import google.generativeai as genai
import pandas as pd
import numpy as np
import twstock
import requests
import feedparser
from urllib.parse import quote
from FinMind.data import DataLoader

# ==========================================
# 1. 網頁基本設定 (必須放在所有 st 指令的最前面)
# ==========================================
st.set_page_config(page_title="AI 財務顧問", layout="wide")
st.title("AI 財務顧問")


# ==========================================
# 2. 核心工具與 GitHub 模組區
# ==========================================

# 【模組 A】自動取得可用的 Gemini 模型 (結合 gemini-3.8-flash 與自動偵測防呆)
def get_gemini_model(api_key):
    genai.configure(api_key=api_key)
    preferred_model = "gemini-3.8-flash"
    try:
        available_models = [
            m.name for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
        ]
        # 若清單中有 gemini-3.8-flash 則優先使用，否則自動挑選第一個可用模型以防 404 報錯
        for m_name in available_models:
            if preferred_model in m_name:
                return genai.GenerativeModel(preferred_model)
        if available_models:
            return genai.GenerativeModel(available_models[0])
    except Exception:
        pass
    return genai.GenerativeModel(preferred_model)


# 【模組 B】twstock 自動識別上市櫃、中文股名與四大買賣點
@st.cache_data(ttl=3600, show_spinner=False)
def get_twstock_extra_info(raw_code, run_bfp=True):
    clean_code = str(raw_code).strip().upper().replace(".TW", "").replace(".TWO", "")
    full_ticker = str(raw_code).strip().upper()
    stock_name = "未知"
    bfp_text = "未啟用"

    # 超能力 A：自動查中文股名與判斷上市(.TW) / 上櫃(.TWO)
    if clean_code in twstock.codes:
        info = twstock.codes[clean_code]
        stock_name = info.name
        suffix = ".TWO" if info.market == "上櫃" else ".TW"
        full_ticker = f"{clean_code}{suffix}"
    elif not full_ticker.endswith((".TW", ".TWO")):
        full_ticker = f"{clean_code}.TW"

    # 超能力 B：計算四大買賣點 (針對 4 碼純數字個股，並設下防呆保護避免超時當機)
    if run_bfp:
        if clean_code.isdigit() and len(clean_code) == 4:
            try:
                s = twstock.Stock(clean_code)
                bfp = twstock.BestFourPoint(s).best_four_point()
                if bfp:
                    action = "🔴 買點" if bfp[0] else "🟢 賣點"
                    bfp_text = f"{action}: {bfp[1]}"
                else:
                    bfp_text = "⚪ 觀望 (無明顯訊號)"
            except Exception:
                bfp_text = "暫無籌碼訊號"
        else:
            bfp_text = "無訊號 (或為ETF)"

    return full_ticker, stock_name, bfp_text


# 【模組 C】量化指標計算小幫手 (MA20, MA60, RSI, 半年最大跌幅)
def calc_quant_metrics(ticker, fallback_price):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="6mo")
        if hist.empty or "Close" not in hist:
            return fallback_price, fallback_price, fallback_price, 50.0, 0.0, "資料不足", "未知產業"

        close = hist["Close"].dropna()
        if close.empty:
            return fallback_price, fallback_price, fallback_price, 50.0, 0.0, "資料不足", "未知產業"

        current_price = float(close.iloc[-1])

        # 計算月線 (MA20) 與季線 (MA60)
        ma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else current_price
        ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else current_price

        # 計算 RSI(14)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        if len(close) >= 15 and pd.notna(loss.iloc[-1]) and loss.iloc[-1] != 0:
            rs = gain.iloc[-1] / loss.iloc[-1]
            rsi = float(100 - (100 / (1 + rs)))
        else:
            rsi = 50.0

        # 計算半年最大跌幅 (Max Drawdown)
        roll_max = close.cummax()
        drawdown = (close - roll_max) / roll_max
        mdd = float(drawdown.min() * 100) if not drawdown.empty else 0.0

        # 判斷技術位階
        if current_price >= ma20 and current_price >= ma60:
            status = "站上月季線 (多頭)"
        elif current_price < ma20 and current_price >= ma60:
            status = "跌破月線、守季線 (整理)"
        elif current_price >= ma20 and current_price < ma60:
            status = "站上月線、受制季線 (反彈)"
        else:
            status = "跌破月季線 (弱勢)"

        # 獨立保護 stock.info，避免 Yahoo 連線緩慢影響已算好的技術指標
        try:
            sector = stock.info.get("sector", "ETF或未知產業")
        except Exception:
            sector = "ETF或未知產業"

        return current_price, ma20, ma60, rsi, mdd, status, sector
    except Exception:
        return fallback_price, fallback_price, fallback_price, 50.0, 0.0, "讀取失敗", "未知產業"


# 【模組 D】追蹤全球與台股關鍵指數
@st.cache_data(ttl=300, show_spinner=False)
def get_market_indices():
    indices = {
        "台股加權 (^TWII)": "^TWII",
        "櫃買指數 (^TWOII)": "^TWOII",
        "費城半導體 (^SOX)": "^SOX",
        "標普500 (^GSPC)": "^GSPC",
        "VIX 恐慌指數 (^VIX)": "^VIX",
        "美元兌台幣 (TWD=X)": "TWD=X"
    }
    results = []
    for name, ticker in indices.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d").dropna(subset=["Close"])
            if len(hist) >= 2:
                close = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                pct = (close - prev) / prev * 100
                results.append({
                    "指數名稱": name,
                    "最新報價": f"{close:,.2f}",
                    "單日漲跌": f"{pct:+.2f}%"
                })
        except Exception:
            pass
    return pd.DataFrame(results)


# 【模組 E】抓取個股/關鍵字最新財經新聞 (附帶超連結)
def get_stock_news(keyword="台積電", max_items=6):
    encoded_kw = quote(f"{keyword} 股票 財經")
    url = f"https://news.google.com/rss/search?q={encoded_kw}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    news_list = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:max_items]:
            news_list.append({
                "title": entry.title,
                "link": entry.link,
                "published": entry.published[:16] if hasattr(entry, "published") else "近期發布"
            })
    except Exception:
        pass
    return news_list


# 【模組 F】追蹤台股近期法說會與重大展望動態 (FinMind + Google News 雙引擎)
def get_twse_conferences(raw_code="2330", stock_name=""):
    clean_id = str(raw_code).strip().upper().replace(".TW", "").replace(".TWO", "")
    try:
        dl = DataLoader()
        news_df = dl.taiwan_stock_news(
            stock_id=clean_id,
            start_date=(pd.Timestamp.today() - pd.Timedelta(days=21)).strftime("%Y-%m-%d")
        )
        if not news_df.empty and "title" in news_df.columns:
            mask = news_df["title"].str.contains("法說|展望|財報|營收|資本支出|毛利", na=False)
            filtered = news_df[mask].tail(5)
            if not filtered.empty:
                records = []
                for _, r in filtered.iterrows():
                    records.append({
                        "title": r.get("title", "無標題"),
                        "link": r.get("link", "#"),
                        "published": str(r.get("date", ""))[:10]
                    })
                return records
    except Exception:
        pass

    # 備援方案：直接從新聞搜尋「股名 + 法說會 展望」最新動態
    search_term = f"{clean_id} {stock_name} 法說會 展望" if stock_name != "未知" else f"{clean_id} 法說會 展望"
    return get_stock_news(keyword=search_term, max_items=5)


# ==========================================
# 3. 側邊欄與對話記憶設定
# ==========================================
api_key = st.sidebar.text_input("🔑 請輸入 Gemini API Key", type="password")

st.sidebar.subheader("🧭 選擇 Agent 功能")
option = st.sidebar.selectbox(
    "你想進行什麼分析？",
    (
        "1. 投資組合全能健檢 (Multi-Agent)",
        "2. 發掘潛在交易機會",
        "3. 客觀圖表與技術面分析",
        "4. 檢視近期交易心理漏洞",
        "5. 全球指數、新聞與法說會情報中心"
    )
)

st.sidebar.divider()
if st.sidebar.button("🗑️ 清空歷史對話記憶"):
    st.session_state.messages = []
    if "chat_session" in st.session_state:
        del st.session_state.chat_session
    st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []


# ==========================================
# 功能 1：投資組合全能健檢 (Multi-Agent)
# ==========================================
if option == "1. 投資組合全能健檢 (Multi-Agent)":
    st.subheader("📊 投資組合配置設定")
    st.caption("💡 直接輸入台股代號（如 `2330`、`0050`、`8069`），系統會透過 `twstock` 自動辨識上市櫃與中文股名！")

    default_data = pd.DataFrame({
        "代碼": ["2330", "0050", "2313", "00982A"],
        "持有股數": [10, 100, 70, 800],
        "均價": [1260.8, 103.7, 236.58, 23.22],
        "目標佔比 (%)": [10.0, 30.0, 15.0, 45.0]
    })

    edited_df = st.data_editor(default_data, num_rows="dynamic", width="stretch")
    enable_bfp = st.checkbox("⚡ 同步啟用 twstock「四大買賣點」短線訊號偵測 (勾選後分析需多等約 5~10 秒)", value=True)

    if st.button("🚀 啟動 Multi-Agent 深度健檢"):
        if not api_key:
            st.warning("⚠️ 請先在左側欄位貼上你的 Gemini API Key！")
        else:
            with st.spinner("量化 Agent 與 twstock 正在抓取即時報價、技術指標與四大買賣點..."):
                try:
                    portfolio_rows = []
                    total_market_value = 0.0
                    total_cost = 0.0

                    for idx, row in edited_df.iterrows():
                        raw_code = str(row["代碼"]).strip()
                        shares = float(row["持有股數"]) if pd.notna(row["持有股數"]) else 0.0
                        avg_cost = float(row["均價"]) if pd.notna(row["均價"]) else 0.0
                        target_w = float(row["目標佔比 (%)"]) if pd.notna(row["目標佔比 (%)"]) else 0.0

                        if not raw_code or raw_code in ["nan", "None", ""] or shares <= 0:
                            continue

                        full_ticker, stock_name, bfp_signal = get_twstock_extra_info(raw_code, run_bfp=enable_bfp)
                        cur_price, ma20, ma60, rsi, mdd, status, sector = calc_quant_metrics(full_ticker, avg_cost)

                        mkt_val = shares * cur_price
                        cost_val = shares * avg_cost
                        total_market_value += mkt_val
                        total_cost += cost_val

                        portfolio_rows.append({
                            "代碼": full_ticker,
                            "名稱": stock_name,
                            "現價": round(cur_price, 2),
                            "均價": round(avg_cost, 2),
                            "損益 (%)": ((cur_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0,
                            "市值": mkt_val,
                            "目標佔比": target_w,
                            "月線(MA20)": round(ma20, 1),
                            "季線(MA60)": round(ma60, 1),
                            "RSI(14)": round(rsi, 1),
                            "半年最大跌幅": round(mdd, 1),
                            "技術位階": status,
                            "四大買賣點": bfp_signal,
                            "產業": sector
                        })

                    if not portfolio_rows:
                        st.warning("⚠️ 請確認表格內至少有一檔有效股票且持有股數大於 0！")
                    else:
                        st.session_state.total_mv = total_market_value
                        st.session_state.total_cost = total_cost

                        display_table = []
                        context_lines = []

                        for item in portfolio_rows:
                            actual_w = (item["市值"] / total_market_value * 100) if total_market_value > 0 else 0.0
                            diff_w = actual_w - item["目標佔比"]

                            display_table.append({
                                "代碼": item["代碼"],
                                "名稱": item["名稱"],
                                "現價": item["現價"],
                                "均價": item["均價"],
                                "損益 (%)": f"{item['損益 (%)']:+.1f}%",
                                "實際佔比": f"{actual_w:.1f}%",
                                "目標佔比": f"{item['目標佔比']:.1f}%",
                                "偏離度": f"{diff_w:+.1f}%",
                                "月線(MA20)": item["月線(MA20)"],
                                "季線(MA60)": item["季線(MA60)"],
                                "RSI(14)": item["RSI(14)"],
                                "半年最大跌幅": f"{item['半年最大跌幅']:.1f}%",
                                "技術位階": item["技術位階"],
                                "twstock 四大買賣點": item["四大買賣點"]
                            })

                            context_lines.append(
                                f"- {item['代碼']} ({item['名稱']}, 產業:{item['產業']}): "
                                f"現價 {item['現價']}, 均價 {item['均價']}, 損益 {item['損益 (%)']:+.1f}%, "
                                f"實際佔比 {actual_w:.1f}% (目標 {item['目標佔比']:.1f}%, 偏離 {diff_w:+.1f}%), "
                                f"MA20={item['月線(MA20)']}, MA60={item['季線(MA60)']}, RSI={item['RSI(14)']}, "
                                f"半年MDD={item['半年最大跌幅']:.1f}%, 位階={item['技術位階']}, "
                                f"twstock四大買賣點訊號={item['四大買賣點']}"
                            )

                        st.session_state.quant_df = pd.DataFrame(display_table)

                        total_pnl = total_market_value - total_cost
                        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
                        context_str = "\n".join(context_lines)

                        prompt = f"""
                        你現在是一個由三位華爾街頂尖專家組成的「Multi-Agent 毒舌財務顧問團」：
                        1. 【宏觀與風險控管專家】：專門拆解表面產業標籤，找出底層資產的隱藏相關性與單一宏觀毀滅事件。
                        2. 【量化與技術面交易員】：根據表格中的 MA20、MA60、RSI、半年最大跌幅(MDD) 以及 `twstock 四大買賣點訊號`，冷酷點出哪些標的動能轉弱、哪些正在接刀。
                        3. 【資產再平衡精算師】：針對「實際佔比」與「目標佔比」的偏離度，給出精確到股數與金額的加減碼調整建議。

                        以下為使用者的台股投資組合量化數據：
                        - 總市值：NT$ {total_market_value:,.0f} / 總成本：NT$ {total_cost:,.0f} / 未實現損益：NT$ {total_pnl:+,.0f} ({total_pnl_pct:+.2f}%)
                        - 持股明細：
                        {context_str}

                        請用繁體中文、直接犀利、不留情面的華爾街顧問語氣，依序由這三位 Agent 給出深度診斷報告，最後給出一份「具體行動清單」。
                        """

                        model = get_gemini_model(api_key)
                        chat = model.start_chat(history=[])
                        response = chat.send_message(prompt)

                        st.session_state.messages = [
                            {"role": "user", "content": "請根據最新抓取的台股量化指標與四大買賣點，幫我進行 Multi-Agent 投資組合健檢。"},
                            {"role": "assistant", "content": response.text}
                        ]
                        st.session_state.chat_session = chat

                except Exception as e:
                    st.error(f"分析過程發生錯誤：{e}")

    if "quant_df" in st.session_state:
        st.divider()
        mv = st.session_state.total_mv
        cost = st.session_state.total_cost
        pnl = mv - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0

        col1, col2, col3 = st.columns(3)
        col1.metric("💰 投資組合總市值", f"NT$ {mv:,.0f}")
        col2.metric("💵 總投入成本", f"NT$ {cost:,.0f}")
        col3.metric("📈 總未實現損益", f"NT$ {pnl:+,.0f}", f"{pnl_pct:+.2f}%")

        st.subheader("📋 即時量化指標、部位偏離度與四大買賣點總覽")
        st.dataframe(st.session_state.quant_df, width="stretch")


# ==========================================
# 功能 2：發掘潛在交易機會
# ==========================================
elif option == "2. 發掘潛在交易機會":
    st.subheader("💡 發掘潛在交易機會")
    user_query = st.text_input("你想尋找哪種類型的台股機會？ (例如：低本益比散熱族群、高殖利率防禦股)")
    if st.button("開始搜尋與分析"):
        if not api_key:
            st.warning("⚠️ 請先輸入 API Key！")
        elif not user_query.strip():
            st.warning("⚠️ 請輸入你想尋找的台股機會類型！")
        else:
            with st.spinner("AI 顧問正在掃描市場機會..."):
                try:
                    model = get_gemini_model(api_key)
                    chat = model.start_chat(history=[])
                    prompt = f"身為華爾街犀利財務顧問，請針對以下台股方向尋找交易機會並嚴格點出潛在風險：{user_query}"
                    response = chat.send_message(prompt)

                    st.session_state.messages = [
                        {"role": "user", "content": f"請幫我尋找台股交易機會：{user_query}"},
                        {"role": "assistant", "content": response.text}
                    ]
                    st.session_state.chat_session = chat
                except Exception as e:
                    st.error(f"發生錯誤：{e}")


# ==========================================
# 功能 3：客觀圖表與技術面分析
# ==========================================
elif option == "3. 客觀圖表與技術面分析":
    st.subheader("📈 個股客觀圖表與技術面檢視")
    raw_input = st.text_input("輸入欲分析的台股代號 (直接輸入如 2330 或 8069 即可)", value="2330")
    if st.button("取得技術面數據與四大買賣點分析"):
        if not api_key:
            st.warning("⚠️ 請先輸入 API Key！")
        else:
            with st.spinner("正在抓取股價歷史、技術指標與 twstock 四大買賣點..."):
                try:
                    full_ticker, stock_name, bfp_signal = get_twstock_extra_info(raw_input, run_bfp=True)
                    cur_price, ma20, ma60, rsi, mdd, status, _ = calc_quant_metrics(full_ticker, 0.0)

                    st.markdown(f"#### 🎯 標的：{full_ticker} ({stock_name}) | 四大買賣點：`{bfp_signal}`")

                    stock = yf.Ticker(full_ticker)
                    hist = stock.history(period="6mo")
                    if not hist.empty and "Close" in hist:
                        st.line_chart(hist["Close"])
                    else:
                        st.info("暫無法繪製歷史走勢圖，請確認股票代號是否正確。")

                    model = get_gemini_model(api_key)
                    chat = model.start_chat(history=[])
                    prompt = f"""
                    請客觀分析台股 {full_ticker} ({stock_name}) 的近期技術面與動能風險：
                    - 最新收盤價：{cur_price} (月線 MA20={ma20:.1f}, 季線 MA60={ma60:.1f})
                    - RSI(14)：{rsi:.1f} / 半年最大回撤：{mdd:.1f}% / 位階：{status}
                    - twstock 四大買賣點訊號：{bfp_signal}
                    請用犀利客觀的交易員角度給出關鍵支撐壓力與操作建議。
                    """
                    response = chat.send_message(prompt)

                    st.session_state.messages = [
                        {"role": "user", "content": f"請幫我進行 {full_ticker} ({stock_name}) 的技術面與四大買賣點分析。"},
                        {"role": "assistant", "content": response.text}
                    ]
                    st.session_state.chat_session = chat
                except Exception as e:
                    st.error(f"發生錯誤：{e}")


# ==========================================
# 功能 4：檢視近期交易心理漏洞
# ==========================================
elif option == "4. 檢視近期交易心理漏洞":
    st.subheader("🔍 檢視近期交易盲點與心理漏洞")
    trade_habit = st.text_area("描述你最近的幾筆交易操作或心態困擾 (例如：常在殺盤時恐慌出場，或抱不住賺錢的股票)")
    if st.button("開始心理與策略審查"):
        if not api_key:
            st.warning("⚠️ 請先輸入 API Key！")
        elif not trade_habit.strip():
            st.warning("⚠️ 請先描述你的交易操作或心態困擾！")
        else:
            with st.spinner("顧問正在審查你的交易心理..."):
                try:
                    model = get_gemini_model(api_key)
                    chat = model.start_chat(history=[])
                    prompt = f"身為直言不諱的華爾街交易心理教練，針對投資人的操作習慣進行心理與策略漏洞審查：{trade_habit}"
                    response = chat.send_message(prompt)

                    st.session_state.messages = [
                        {"role": "user", "content": f"請審查我的交易習慣與心理盲點：{trade_habit}"},
                        {"role": "assistant", "content": response.text}
                    ]
                    st.session_state.chat_session = chat
                except Exception as e:
                    st.error(f"發生錯誤：{e}")


# ==========================================
# 功能 5：全球指數、新聞與法說會情報中心
# ==========================================
elif option == "5. 全球指數、新聞與法說會情報中心":
    st.subheader("🌐 全球與台股關鍵指數即時看板")
    with st.spinner("正在同步全球重要指數..."):
        idx_df = get_market_indices()
        if not idx_df.empty:
            st.dataframe(idx_df, width="stretch")
        else:
            st.info("目前無法取得即時指數，請稍後再試。")

    st.divider()
    st.subheader("📰 個股即時新聞與法說會追蹤")
    target_input = st.text_input("輸入想追蹤新聞與法說會的股號或名稱 (例: 2330、2313 或 台積電)", value="2330")

    if st.button("📡 抓取最新情報並讓 AI 毒舌解讀"):
        with st.spinner("正在檢索 Google 財經新聞與公開資訊觀測站/FinMind 法說會動態..."):
            full_ticker, stock_name, _ = get_twstock_extra_info(target_input, run_bfp=False)
            search_kw = f"{target_input} {stock_name}" if stock_name != "未知" else target_input

            col_news, col_conf = st.columns(2)

            news_items = get_stock_news(search_kw, max_items=6)
            news_summary_for_ai = ""
            with col_news:
                st.markdown(f"#### 🔥 {search_kw} 最新財經新聞")
                if news_items:
                    for item in news_items:
                        st.markdown(f"- [{item['title']}]({item['link']}) `({item['published']})`")
                        news_summary_for_ai += f"- {item['title']} ({item['published']})\n"
                else:
                    st.write("暫無相關新聞")

            conf_items = get_twse_conferences(target_input, stock_name)
            conf_summary_for_ai = ""
            with col_conf:
                st.markdown(f"#### 🎙️ {search_kw} 近期法說會與展望動態")
                if conf_items:
                    for item in conf_items:
                        pub_date = item.get("published", item.get("date", ""))
                        st.markdown(f"- [{item['title']}]({item['link']}) `({pub_date})`")
                        conf_summary_for_ai += f"- {item['title']} ({pub_date})\n"
                else:
                    st.write("暫無近期法說會動態")

            if not api_key:
                st.warning("⚠️ 已為您列出新聞與法說會連結！若需 AI 深度解讀，請先在左側輸入 Gemini API Key。")
            else:
                try:
                    indices_str = idx_df.to_string(index=False) if not idx_df.empty else "無指數資料"
                    intel_prompt = f"""
                    你是一位華爾街頂尖的首席情報分析師。請根據以下即時抓取到的「宏觀大盤指數」、「個股最新新聞」與「法說會/營運展望動態」，針對標的 `{search_kw}` 進行冷酷、一針見血的情報解讀：

                    【當前全球與台股大盤指數】
                    {indices_str}

                    【最新財經新聞標題】
                    {news_summary_for_ai}

                    【近期法說會與財報展望動態】
                    {conf_summary_for_ai}

                    請回答以下三個核心問題：
                    1. **市場情緒溫度計**：目前的媒體與法說會風向是在「吹捧利多」還是「釋放悲觀訊號」？
                    2. **利多出盡 vs 實質成長**：這些消息背後有哪些是散戶容易被騙進場的「假利多」？法說會或財報真正該盯緊的關鍵指標是什麼？
                    3. **結合大盤位階的操作建議**：對照當前的加權指數、費半與 VIX 恐慌指數，現在適合追價、逢低佈局還是避開？
                    """
                    model = get_gemini_model(api_key)
                    chat = model.start_chat(history=[])
                    response = chat.send_message(intel_prompt)

                    st.session_state.messages = [
                        {"role": "user", "content": f"請幫我解讀 {search_kw} 的最新新聞、法說會動態與大盤指數風險。"},
                        {"role": "assistant", "content": response.text}
                    ]
                    st.session_state.chat_session = chat
                except Exception as e:
                    st.error(f"AI 分析發生錯誤：{e}")


# ==========================================
# 4. 跨功能通用：持續與 AI 財務顧問對話區塊
# ==========================================
if st.session_state.messages:
    st.divider()
    st.subheader("💬 與 AI 財務顧問深入對話")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    if user_input := st.chat_input("繼續追問 AI 顧問團 (例如：如果把台積電減碼 10% 轉進 0050，風險會降低多少？)"):
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.write(user_input)

        with st.chat_message("assistant"):
            with st.spinner("顧問團思考中..."):
                try:
                    if "chat_session" in st.session_state:
                        response = st.session_state.chat_session.send_message(user_input)
                        reply_text = response.text
                    else:
                        reply_text = "請先點擊上方的分析按鈕以建立對話背景！"
                    st.write(reply_text)
                    st.session_state.messages.append({"role": "assistant", "content": reply_text})
                except Exception as e:
                    st.error(f"回覆時發生錯誤 (若為 429 代表超過每分鐘免費次數限制，請稍等 1 分鐘後再試)：{e}")
