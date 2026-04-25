import streamlit as st
import pandas as pd
import sys
import os
import json

st.set_page_config(page_title="A股智能分析系统", layout="wide", page_icon="📈")

PROJECT_DIR = os.path.dirname(__file__)
WATCHLIST_FILE = os.path.join(PROJECT_DIR, "watchlist.json")

sys.path.insert(0, PROJECT_DIR)

from tradingagents.dataflows.tencent_data import (
    get_Tencent_stock_data,
    get_Tencent_indicators,
    get_Tencent_news,
    get_Tencent_fundamentals,
)
import akshare as ak

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_watchlist(watchlist):
    with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=2)

def get_stock_name(code):
    try:
        df = ak.stock_individual_info_em(symbol=code)
        for _, row in df.iterrows():
            if row.get('item') == '股票名称':
                return row.get('value', code)
    except:
        pass
    return code

def analyze_signals(df):
    if df.empty or len(df) < 20:
        return {}
    
    close = df['收盘'].astype(float)
    current_price = close.iloc[-1]
    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else ma20
    
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1] if len(close) >= 14 else 50
    
    signals = []
    action = "HOLD"
    
    if current_price > ma20 and ma10 > ma20:
        signals.append("✅ 短期强势")
        action = "BUY"
    elif current_price < ma20 and ma10 < ma20:
        signals.append("⚠️ 短期弱势")
        action = "SELL"
    
    if ma5 > ma10:
        signals.append("📈 均线多头排列")
        if action != "SELL":
            action = "BUY"
    elif ma5 < ma10:
        signals.append("📉 均线空头排列")
        if action != "BUY":
            action = "SELL"
    
    if rsi > 70:
        signals.append(f"⚠️ RSI超买 ({rsi:.1f})")
        action = "SELL"
    elif rsi < 30:
        signals.append(f"✅ RSI超卖 ({rsi:.1f})")
        action = "BUY"
    
    if ma5 > ma20 and ma10 > ma20:
        signals.append("✅ 中期上升趋势")
    
    return {
        "action": action,
        "current_price": current_price,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma50": ma50,
        "rsi": rsi,
        "signals": signals
    }

st.title("📈 A股智能分析系统")

if 'watchlist' not in st.session_state:
    st.session_state.watchlist = load_watchlist()

with st.sidebar:
    st.header("🎯 股票仓库")
    
    new_stock = st.text_input("添加股票", placeholder="输入股票代码")
    if st.button("➕ 添加到仓库") and new_stock:
        if new_stock not in st.session_state.watchlist:
            name = get_stock_name(new_stock)
            st.session_state.watchlist.append({"code": new_stock, "name": name})
            save_watchlist(st.session_state.watchlist)
            st.success(f"已添加 {name}")
    
    st.markdown("---")
    st.markdown("### 我的股票仓���")
    for i, stock in enumerate(st.session_state.watchlist):
        col1, col2 = st.columns([3, 1])
        with col1:
            st.write(f"{stock.get('name', stock.get('code'))} ({stock.get('code')})")
        with col2:
            if st.button("❌", key=f"del_{i}"):
                st.session_state.watchlist.pop(i)
                save_watchlist(st.session_state.watchlist)
                st.rerun()
    
    st.markdown("---")
    st.markdown("### 常用股票")
    quick_stocks = {
        "贵州茅台": "600519",
        "宁德时代": "300750", 
        "比亚迪": "002594",
        "招商银行": "600036",
        "中国平安": "601318",
        "五粮液": "000858",
        "美的集团": "000333",
        "隆基绿能": "601012",
    }
    for name, code in quick_stocks.items():
        if st.button(f"{name} ({code})"):
            st.session_state.selected_stock = code

default_stock = getattr(st.session_state, 'selected_stock', '600519')
stock_code = st.text_input("输入股票代码分析", value=default_stock)

if stock_code:
    try:
        stock_name = get_stock_name(stock_code)
        
        col_info, col_action = st.columns([3, 1])
        
        with col_info:
            st.header(f"📊 {stock_name} ({stock_code})")
        
        with col_action:
            if st.button("❤️ 添加到仓库"):
                if stock_code not in [s.get('code') for s in st.session_state.watchlist]:
                    st.session_state.watchlist.append({"code": stock_code, "name": stock_name})
                    save_watchlist(st.session_state.watchlist)
                    st.success("已添加!")
        
        df = get_Tencent_stock_data(stock_code, "2024-01-01", str(pd.to_datetime.today()))
        
        if not df.empty:
            analysis = analyze_signals(df)
            
            st.markdown("---")
            st.subheader("🎯 交易信号")
            
            signal_col1, signal_col2, signal_col3 = st.columns(3)
            
            with signal_col1:
                action = analysis.get("action", "HOLD")
                color = "green" if action == "BUY" else ("red" if action == "SELL" else "gray")
                st.markdown(f"""
                <div style='padding:20px;background-color:{color};border-radius:10px;text-align:center'>
                    <h2 style='color:white;margin:0'>{action}</h2>
                    <p style='color:white;margin:0'>建议</p>
                </div>
                """, unsafe_allow_html=True)
            
            with signal_col2:
                st.metric("当前价格", f"{analysis.get('current_price', 0):.2f}")
                st.metric("MA5", f"{analysis.get('ma5', 0):.2f}")
                st.metric("MA20", f"{analysis.get('ma20', 0):.2f}")
            
            with signal_col3:
                rsi = analysis.get("rsi", 0)
                rsi_color = "green" if rsi < 30 else ("red" if rsi > 70 else "orange")
                st.markdown(f"RSI(14): **{rsi:.1f}**")
                for signal in analysis.get("signals", []):
                    st.write(signal)
            
            st.markdown("---")
            
            tab1, tab2, tab3, tab4 = st.tabs(["📈 行情", "📉 指标", "📰 新闻", "🏢 基本面"])
            
            with tab1:
                st.subheader("历史行情")
                df_display = df.copy()
                df_display.index = df_display.index.strftime('%Y-%m-%d')
                st.dataframe(df_display[::-1], use_container_width=True)
                
                st.subheader("价格走势")
                chart_data = df[['收盘', '开盘', '最高', '最低']].copy()
                chart_data.index = pd.to_datetime(chart_data.index)
                st.line_chart(chart_data)
                
                st.subheader("成交量")
                volume = df['成交量'].astype(float)
                st.bar_chart(volume)
            
            with tab2:
                st.subheader("技术指标")
                indicators = [
                    ("rsi", "RSI相对强弱指数"),
                    ("macd", "MACD"),
                    ("close_10_ema", "EMA10"),
                    ("close_50_sma", "SMA50"),
                    ("boll_ub", "布林上轨"),
                    ("atr", "ATR真实波幅"),
                ]
                for ind, name in indicators:
                    st.markdown(f"**{name}**")
                    result = get_Tencent_indicators(stock_code, ind, str(pd.to_datetime.today()), 30)
                    st.text(result[:500] if len(result) > 500 else result)
                    st.markdown("---")
            
            with tab3:
                st.subheader("最新新闻")
                news = get_Tencent_news(stock_code)
                if news:
                    for n in news:
                        with st.expander(f"{n.get('title', '')[:40]}..."):
                            st.markdown(f"**{n.get('title', '')}**")
                            st.caption(n.get('date', ''))
                            st.write(n.get('content', '')[:800])
                else:
                    st.info("暂无新闻")
            
            with tab4:
                st.subheader("基本面数据")
                try:
                    fundamentals = get_Tencent_fundamentals(stock_code)
                    if fundamentals:
                        col1, col2 = st.columns(2)
                        items = list(fundamentals.items())
                        for i, (key, value) in enumerate(items[:20]):
                            with col1 if i % 2 == 0 else col2:
                                st.markdown(f"**{key}**: {value}")
                    else:
                        st.info("暂无数据")
                except Exception as e:
                    st.error(str(e))
        
        else:
            st.warning("暂无数据")
            
    except Exception as e:
        st.error(f"获取数据失败: {str(e)}")
        import traceback
        with st.expander("查看错误详情"):
            st.code(traceback.format_exc())