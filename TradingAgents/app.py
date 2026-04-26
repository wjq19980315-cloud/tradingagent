"""
A股智能分析系统 - 增强版
功能: 股票分析报告、买卖时间建议、定期股票推荐
数据源: 东方财富直连 + 腾讯财经 + AKShare (多源容错)
"""
from flask import Flask, request, redirect, url_for, jsonify
import pandas as pd
import numpy as np
import sys
import os
import io
import json
import time
import threading
import traceback
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
sys.path.insert(0, os.path.dirname(__file__))

import requests as req

app = Flask(__name__)
app.secret_key = 'stock_analyzer_v2'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")
RECOMMEND_CACHE_FILE = os.path.join(BASE_DIR, "recommend_cache.json")

# ─── 代理绕过 Session ─────────────────────────────────────
_session = req.Session()
_session.trust_env = False
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'


# ═══════════════════════════════════════════════════════════
# 数据获取层 - 多源容错
# ═══════════════════════════════════════════════════════════

def _get_market_prefix(code):
    """根据股票代码判断市场前缀"""
    code = code.strip()
    if code.startswith('6') or code.startswith('9'):
        return '1', 'sh'  # 上海
    else:
        return '0', 'sz'  # 深圳


def fetch_stock_data_eastmoney(code, days=365):
    """东方财富直连API获取K线数据（绕过代理）"""
    try:
        secid_prefix, _ = _get_market_prefix(code)
        url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
        params = {
            'fields1': 'f1,f2,f3,f4,f5,f6',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116',
            'ut': '7eea3edcaed734bea9cbfc24409ed989',
            'klt': '101',
            'fqt': '1',
            'secid': f'{secid_prefix}.{code}',
            'beg': start_date,
            'end': end_date,
        }
        r = _session.get(url, params=params, timeout=15)
        data = r.json()
        klines = data.get('data', {}).get('klines', [])
        if not klines:
            return None

        rows = []
        for k in klines:
            parts = k.split(',')
            rows.append({
                '日期': parts[0],
                '开盘': float(parts[1]),
                '收盘': float(parts[2]),
                '最高': float(parts[3]),
                '最低': float(parts[4]),
                '成交量': float(parts[5]),
                '成交额': float(parts[6]),
                '振幅': float(parts[7]),
                '涨跌幅': float(parts[8]),
                '涨跌额': float(parts[9]),
                '换手率': float(parts[10]),
            })
        df = pd.DataFrame(rows)
        df['日期'] = pd.to_datetime(df['日期'])
        df.set_index('日期', inplace=True)
        return df
    except Exception:
        return None


def fetch_stock_data_tencent(code, days=365):
    """腾讯财经API获取K线数据"""
    try:
        _, prefix = _get_market_prefix(code)
        market = prefix + code
        url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
        params = {
            '_var': 'kline_dayqfq',
            'param': f'{market},day,,,{days},qfq'
        }
        r = _session.get(url, params=params, timeout=15)
        import re
        var_match = re.match(r'kline_dayqfq=(.+)', r.text)
        if not var_match:
            return None
        data = json.loads(var_match.group(1))
        if data.get('code') != 0:
            return None
        qfqday = data.get('data', {}).get(market, {}).get('qfqday', [])
        if not qfqday:
            return None

        rows = []
        for row in qfqday:
            if isinstance(row, list) and len(row) >= 6:
                rows.append({
                    '日期': row[0],
                    '开盘': float(row[1]),
                    '收盘': float(row[2]),
                    '最高': float(row[3]),
                    '最低': float(row[4]),
                    '成交量': float(row[5]),
                })
        df = pd.DataFrame(rows)
        df['日期'] = pd.to_datetime(df['日期'])
        df.set_index('日期', inplace=True)
        return df
    except Exception:
        return None


def fetch_stock_data(code, days=365):
    """多源容错获取股票数据"""
    # 优先东方财富（数据最全）
    df = fetch_stock_data_eastmoney(code, days)
    if df is not None and not df.empty:
        return df, '东方财富'
    # 备选腾讯
    df = fetch_stock_data_tencent(code, days)
    if df is not None and not df.empty:
        return df, '腾讯财经'
    return None, None


def fetch_stock_name(code):
    """获取股票名称（多源容错）"""
    # 方案1：东方财富
    try:
        secid_prefix, _ = _get_market_prefix(code)
        url = 'https://push2.eastmoney.com/api/qt/stock/get'
        params = {
            'secid': f'{secid_prefix}.{code}',
            'fields': 'f57,f58',
            'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        }
        r = _session.get(url, params=params, timeout=5)
        data = r.json()
        name = data.get('data', {}).get('f58', '')
        if name and name != code:
            return name
    except Exception:
        pass
    # 方案2：腾讯实时接口
    try:
        _, prefix = _get_market_prefix(code)
        market = prefix + code
        url = f'https://qt.gtimg.cn/q={market}'
        r = _session.get(url, timeout=5)
        text = r.text
        parts = text.split('~')
        if len(parts) > 1 and parts[1]:
            return parts[1]
    except Exception:
        pass
    # 方案3：akshare
    try:
        import akshare as ak
        df = ak.stock_individual_info_em(symbol=code)
        for _, row in df.iterrows():
            if row.get('item') == '股票名称':
                return str(row.get('value', code))
    except Exception:
        pass
    return code


def fetch_stock_news(code):
    """获取股票新闻（多源容错）"""
    # 方案1：akshare（东方财富新闻）
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=code)
        news_list = []
        for _, row in df.head(15).iterrows():
            news_list.append({
                'title': str(row.get('新闻标题', '')),
                'content': str(row.get('新闻内容', '')),
                'date': str(row.get('发布时间', '')),
                'source': str(row.get('文章来源', '')),
            })
        if news_list:
            return news_list
    except Exception:
        pass
    # 方案2：东方财富公告API
    try:
        secid_prefix, _ = _get_market_prefix(code)
        url = 'https://np-anotice-stock.eastmoney.com/api/security/ann'
        params = {
            'sr': '-1', 'page_size': '10', 'page_index': '1',
            'ann_type': 'A', 'stock_list': code,
            'f_node': '0', 's_node': '0',
        }
        r = _session.get(url, params=params, timeout=10)
        data = r.json()
        items = data.get('data', {}).get('list', [])
        news_list = []
        for item in items[:10]:
            news_list.append({
                'title': item.get('title', ''),
                'content': item.get('title', ''),
                'date': item.get('notice_date', '')[:10],
                'source': '东方财富公告',
            })
        if news_list:
            return news_list
    except Exception:
        pass
    return []


def fetch_fundamentals(code):
    """获取基本面数据"""
    try:
        import akshare as ak
        df = ak.stock_individual_info_em(symbol=code)
        result = {}
        for _, row in df.iterrows():
            result[str(row['item'])] = str(row['value'])
        return result
    except Exception:
        pass
    # 备选：东方财富
    try:
        secid_prefix, _ = _get_market_prefix(code)
        url = 'https://push2.eastmoney.com/api/qt/stock/get'
        params = {
            'secid': f'{secid_prefix}.{code}',
            'fields': 'f57,f58,f59,f162,f167,f168,f169,f170,f171,f177,f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f55,f60,f71,f116,f117,f127,f152,f161,f164,f167',
            'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        }
        r = _session.get(url, params=params, timeout=10)
        data = r.json().get('data', {})
        fields_map = {
            'f43': '最新价', 'f44': '最高', 'f45': '最低', 'f46': '今开',
            'f47': '成交量', 'f48': '成交额', 'f50': '量比', 'f52': '市净率',
            'f55': '每股收益', 'f116': '总市值', 'f117': '流通市值',
            'f162': '市盈率(动)', 'f167': '市净率',
        }
        result = {}
        for k, v in fields_map.items():
            val = data.get(k)
            if val is not None and val != '-':
                if k in ('f116', 'f117', 'f48'):
                    val = f"{val/100000000:.2f}亿" if isinstance(val, (int, float)) else str(val)
                elif k in ('f43', 'f44', 'f45', 'f46', 'f55'):
                    val = f"{val/100:.2f}" if isinstance(val, (int, float)) else str(val)
                else:
                    val = str(val)
                result[v] = val
        return result
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════
# 技术分析引擎
# ═══════════════════════════════════════════════════════════

def compute_full_analysis(df):
    """完整技术分析：均线、MACD、KDJ、RSI、布林带、成交量"""
    if df is None or df.empty or len(df) < 20:
        return None

    close = df['收盘'].astype(float)
    high = df['最高'].astype(float)
    low = df['最低'].astype(float)
    volume = df['成交量'].astype(float)
    current = close.iloc[-1]

    # --- 均线 ---
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    # --- RSI ---
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss
    rsi = (100 - (100 / (1 + rs)))
    rsi_val = rsi.iloc[-1] if not rsi.empty else 50

    # --- MACD ---
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_dif = ema12 - ema26
    macd_dea = macd_dif.ewm(span=9, adjust=False).mean()
    macd_hist = 2 * (macd_dif - macd_dea)

    # --- KDJ ---
    low_n = low.rolling(9).min()
    high_n = high.rolling(9).max()
    rsv = (close - low_n) / (high_n - low_n) * 100
    rsv = rsv.fillna(50)
    k_val = rsv.ewm(alpha=1/3, adjust=False).mean()
    d_val = k_val.ewm(alpha=1/3, adjust=False).mean()
    j_val = 3 * k_val - 2 * d_val

    # --- 布林带 ---
    boll_mid = close.rolling(20).mean()
    boll_std = close.rolling(20).std()
    boll_up = boll_mid + 2 * boll_std
    boll_dn = boll_mid - 2 * boll_std

    # --- ATR ---
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    # --- 成交量均线 ---
    vol_ma5 = volume.rolling(5).mean()
    vol_ma10 = volume.rolling(10).mean()

    # ═══ 综合评分 ═══
    score = 50  # 基础分
    signals = []

    # 均线系统 (30分)
    ma5_v = ma5.iloc[-1] if not ma5.empty else current
    ma10_v = ma10.iloc[-1] if not ma10.empty else current
    ma20_v = ma20.iloc[-1] if not ma20.empty else current
    ma60_v = ma60.iloc[-1] if len(ma60.dropna()) > 0 else current

    if current > ma5_v > ma10_v > ma20_v:
        score += 15
        signals.append(('均线多头排列', 'bullish'))
    elif current < ma5_v < ma10_v < ma20_v:
        score -= 15
        signals.append(('均线空头排列', 'bearish'))
    
    if current > ma20_v:
        score += 5
        signals.append(('站上20日线', 'bullish'))
    else:
        score -= 5
        signals.append(('跌破20日线', 'bearish'))

    # MA金叉死叉
    if len(ma5) >= 2 and len(ma10) >= 2:
        if ma5.iloc[-1] > ma10.iloc[-1] and ma5.iloc[-2] <= ma10.iloc[-2]:
            score += 10
            signals.append(('MA5/10金叉', 'bullish'))
        elif ma5.iloc[-1] < ma10.iloc[-1] and ma5.iloc[-2] >= ma10.iloc[-2]:
            score -= 10
            signals.append(('MA5/10死叉', 'bearish'))

    # RSI (20分)
    if rsi_val > 80:
        score -= 15
        signals.append((f'RSI严重超买({rsi_val:.1f})', 'bearish'))
    elif rsi_val > 70:
        score -= 8
        signals.append((f'RSI超买({rsi_val:.1f})', 'bearish'))
    elif rsi_val < 20:
        score += 15
        signals.append((f'RSI严重超卖({rsi_val:.1f})', 'bullish'))
    elif rsi_val < 30:
        score += 8
        signals.append((f'RSI超卖({rsi_val:.1f})', 'bullish'))
    else:
        signals.append((f'RSI中性({rsi_val:.1f})', 'neutral'))

    # MACD (20分)
    dif_v = macd_dif.iloc[-1] if not macd_dif.empty else 0
    dea_v = macd_dea.iloc[-1] if not macd_dea.empty else 0
    hist_v = macd_hist.iloc[-1] if not macd_hist.empty else 0

    if len(macd_dif) >= 2:
        if macd_dif.iloc[-1] > macd_dea.iloc[-1] and macd_dif.iloc[-2] <= macd_dea.iloc[-2]:
            score += 12
            signals.append(('MACD金叉', 'bullish'))
        elif macd_dif.iloc[-1] < macd_dea.iloc[-1] and macd_dif.iloc[-2] >= macd_dea.iloc[-2]:
            score -= 12
            signals.append(('MACD死叉', 'bearish'))

    if hist_v > 0:
        score += 3
    else:
        score -= 3

    # KDJ (15分)
    k_v = k_val.iloc[-1] if not k_val.empty else 50
    d_v = d_val.iloc[-1] if not d_val.empty else 50
    j_v = j_val.iloc[-1] if not j_val.empty else 50

    if k_v > 80 and d_v > 80:
        score -= 8
        signals.append((f'KDJ超买区(K:{k_v:.0f})', 'bearish'))
    elif k_v < 20 and d_v < 20:
        score += 8
        signals.append((f'KDJ超卖区(K:{k_v:.0f})', 'bullish'))

    if len(k_val) >= 2:
        if k_val.iloc[-1] > d_val.iloc[-1] and k_val.iloc[-2] <= d_val.iloc[-2]:
            score += 8
            signals.append(('KDJ金叉', 'bullish'))
        elif k_val.iloc[-1] < d_val.iloc[-1] and k_val.iloc[-2] >= d_val.iloc[-2]:
            score -= 8
            signals.append(('KDJ死叉', 'bearish'))

    # 布林带 (10分)
    boll_up_v = boll_up.iloc[-1] if not boll_up.empty else current
    boll_dn_v = boll_dn.iloc[-1] if not boll_dn.empty else current
    boll_mid_v = boll_mid.iloc[-1] if not boll_mid.empty else current

    if current >= boll_up_v:
        score -= 8
        signals.append(('触及布林上轨', 'bearish'))
    elif current <= boll_dn_v:
        score += 8
        signals.append(('触及布林下轨', 'bullish'))

    # 量能 (5分)
    vol_v = volume.iloc[-1] if not volume.empty else 0
    vol_ma5_v = vol_ma5.iloc[-1] if not vol_ma5.empty else vol_v

    if vol_v > vol_ma5_v * 1.5 and close.iloc[-1] > close.iloc[-2]:
        score += 5
        signals.append(('放量上涨', 'bullish'))
    elif vol_v > vol_ma5_v * 1.5 and close.iloc[-1] < close.iloc[-2]:
        score -= 5
        signals.append(('放量下跌', 'bearish'))

    # 限制评分范围
    score = max(0, min(100, score))

    # 决策 + 新手友好建议
    if score >= 70:
        action = 'BUY'
        action_text = '建议买入'
    elif score >= 55:
        action = 'HOLD_BUY'
        action_text = '偏多持有'
    elif score >= 45:
        action = 'HOLD'
        action_text = '观望等待'
    elif score >= 30:
        action = 'HOLD_SELL'
        action_text = '偏空持有'
    else:
        action = 'SELL'
        action_text = '建议卖出'

    # ═══ 新手友好：通俗建议 + 仓位 + 风险提示 + 买入时机 ═══
    beginner_advice = {}
    if score >= 70:
        beginner_advice = {
            'summary': '这只股票目前走势不错，多个技术指标都在发出看涨信号，可以考虑买入。',
            'position': '建议用总资金的 10%~20% 试探性买入，不要一次性全买。',
            'timing': '现在可以先买一部分（比如计划的1/3），如果之后回调到20日均线附近可以再补一些。',
            'stoploss': f'建议设置止损位在 {round(current * 0.93, 2)} 元附近（约-7%），跌破就先卖出保住本金。',
            'risk': '即使信号看好，也不要借钱炒股，更不要把全部积蓄放进来。股市有风险，任何分析都不能保证100%正确。',
            'level': 'safe',
        }
    elif score >= 55:
        beginner_advice = {
            'summary': '这只股票整体偏好，但还没有特别强的买入信号，可以少量参与或继续观察。',
            'position': '建议仓位控制在 5%~10%，或者先加入自选股观察几天。',
            'timing': '不着急买入，可以等股价回调到均线支撑位再考虑，或者等MACD出现金叉信号。',
            'stoploss': f'如果买入，建议止损设在 {round(current * 0.95, 2)} 元（约-5%）。',
            'risk': '虽然趋势偏好，但目前没有很强的买点，追高容易被套。耐心等一等往往更好。',
            'level': 'caution',
        }
    elif score >= 45:
        beginner_advice = {
            'summary': '这只股票目前方向不明确，多空力量胶着，建议新手先不要操作。',
            'position': '建议暂时不要买入，先放入自选股观察。',
            'timing': '等到趋势明确再做决定。关注是否会突破20日均线或者跌破支撑位。',
            'stoploss': '如果已经持有，可以暂时持有观望，但要设好止损线。',
            'risk': '方向不明的时候是最容易亏钱的，新手最好的策略就是"看不懂就不做"。',
            'level': 'warning',
        }
    elif score >= 30:
        beginner_advice = {
            'summary': '这只股票目前走势偏弱，下跌风险较大，新手不建议现在买入。',
            'position': '不建议新增仓位。如果已经持有，可以考虑减仓一半。',
            'timing': '等股价企稳、出现明确的止跌信号（比如放量阳线站上均线）再考虑。',
            'stoploss': '如果持有，请务必设好止损，亏损超过8%就果断离场。',
            'risk': '下跌趋势中抄底是新手最大的亏钱原因之一。"不要接飞刀"，等企稳再说。',
            'level': 'danger',
        }
    else:
        beginner_advice = {
            'summary': '这只股票目前处于明显的下跌趋势中，风险很高，强烈建议不要买入！',
            'position': '空仓观望！如果已持有，建议尽快减仓或清仓止损。',
            'timing': '至少等到股价站稳20日均线以上、MACD翻红，才可以考虑重新关注。',
            'stoploss': '如果持有且已经亏损，不要死扛等解套，越早止损越好。',
            'risk': '多个指标同时看空，继续下跌的概率很大。保住本金永远是第一位的，亏了钱可以再赚，但亏光了就没机会了。',
            'level': 'danger',
        }

    # ═══ 买入时机推荐 ═══
    buy_timing = []
    # 判断是否接近支撑位
    if boll_dn_v and current < boll_mid_v and current > boll_dn_v:
        dist_pct = (current - boll_dn_v) / boll_dn_v * 100
        if dist_pct < 3:
            buy_timing.append({
                'condition': '接近布林带下轨支撑',
                'target_price': round(boll_dn_v, 2),
                'description': f'股价已接近布林带下轨 {round(boll_dn_v,2)} 元，如果能在此处止跌企稳，是不错的低吸机会。',
            })
    # MA20支撑
    if current > ma20_v and (current - ma20_v) / ma20_v < 0.02:
        buy_timing.append({
            'condition': '回踩20日均线',
            'target_price': round(ma20_v, 2),
            'description': f'股价回到20日均线 {round(ma20_v,2)} 元附近获得支撑，均线附近是比较好的买入位置。',
        })
    # MACD即将金叉
    if dif_v < dea_v and abs(dif_v - dea_v) < abs(dif_v) * 0.1 and dif_v < 0:
        buy_timing.append({
            'condition': 'MACD即将金叉',
            'target_price': round(current, 2),
            'description': 'MACD的DIF线正在接近DEA线，如果向上突破形成金叉，就是买入信号。可以先关注，金叉确认后再买。',
        })
    # KDJ超卖
    if k_v < 25 and d_v < 25:
        buy_timing.append({
            'condition': 'KDJ超卖区域',
            'target_price': round(current, 2),
            'description': f'KDJ指标已进入超卖区（K:{k_v:.0f}），意味着短期跌得比较多了，反弹的可能性在增加。但要等K线向上穿D线确认。',
        })
    # RSI超卖
    if rsi_val < 35:
        buy_timing.append({
            'condition': 'RSI低位',
            'target_price': round(current, 2),
            'description': f'RSI只有 {rsi_val:.1f}，说明最近卖压很重，但也意味着超跌反弹的机会在酝酿。',
        })
    # 如果当前就是买入信号
    if score >= 70:
        buy_timing.insert(0, {
            'condition': '当前即可买入',
            'target_price': round(current, 2),
            'description': '综合评分已达买入标准，如果你看好这只股票的基本面，现在可以分批建仓。',
        })

    if not buy_timing and score < 55:
        buy_timing.append({
            'condition': '暂无合适买点',
            'target_price': None,
            'description': '目前还没有出现好的买入信号，建议耐心等待。可以把这只股票加入自选，等出现技术信号再操作。',
        })

    # ═══ 找出历史买卖信号点 ═══
    buy_sell_points = []
    for i in range(2, len(df)):
        # MACD金叉买点
        if macd_dif.iloc[i] > macd_dea.iloc[i] and macd_dif.iloc[i-1] <= macd_dea.iloc[i-1]:
            buy_sell_points.append({
                'date': str(df.index[i])[:10],
                'type': 'buy',
                'price': float(close.iloc[i]),
                'reason': 'MACD金叉',
                'tip': '两条MACD线交叉向上，表示趋势可能转好'
            })
        # MACD死叉卖点
        elif macd_dif.iloc[i] < macd_dea.iloc[i] and macd_dif.iloc[i-1] >= macd_dea.iloc[i-1]:
            buy_sell_points.append({
                'date': str(df.index[i])[:10],
                'type': 'sell',
                'price': float(close.iloc[i]),
                'reason': 'MACD死叉',
                'tip': '两条MACD线交叉向下，表示趋势可能转弱'
            })
        # RSI超卖买点
        if i >= 14 and rsi.iloc[i] < 30 and rsi.iloc[i-1] >= 30:
            buy_sell_points.append({
                'date': str(df.index[i])[:10],
                'type': 'buy',
                'price': float(close.iloc[i]),
                'reason': 'RSI超卖',
                'tip': '短期跌得太多了，可能会反弹'
            })
        # RSI超买卖点
        if i >= 14 and rsi.iloc[i] > 70 and rsi.iloc[i-1] <= 70:
            buy_sell_points.append({
                'date': str(df.index[i])[:10],
                'type': 'sell',
                'price': float(close.iloc[i]),
                'reason': 'RSI超买',
                'tip': '短期涨得太多了，可能会回调'
            })

    # 只保留最近30个信号
    buy_sell_points = buy_sell_points[-30:]

    # ═══ 构建K线图数据 ═══
    chart_df = df.tail(120)
    kline_data = []
    for idx, row in chart_df.iterrows():
        d = {
            'date': str(idx)[:10],
            'open': float(row['开盘']),
            'close': float(row['收盘']),
            'high': float(row['最高']),
            'low': float(row['最低']),
            'volume': float(row['成交量']),
        }
        kline_data.append(d)

    # 指标数据（最近120日）
    indicator_data = {
        'ma5': [round(v, 2) if not pd.isna(v) else None for v in ma5.tail(120).values],
        'ma10': [round(v, 2) if not pd.isna(v) else None for v in ma10.tail(120).values],
        'ma20': [round(v, 2) if not pd.isna(v) else None for v in ma20.tail(120).values],
        'macd_dif': [round(v, 4) if not pd.isna(v) else None for v in macd_dif.tail(120).values],
        'macd_dea': [round(v, 4) if not pd.isna(v) else None for v in macd_dea.tail(120).values],
        'macd_hist': [round(v, 4) if not pd.isna(v) else None for v in macd_hist.tail(120).values],
        'rsi': [round(v, 2) if not pd.isna(v) else None for v in rsi.tail(120).values],
        'k': [round(v, 2) if not pd.isna(v) else None for v in k_val.tail(120).values],
        'd': [round(v, 2) if not pd.isna(v) else None for v in d_val.tail(120).values],
        'j': [round(v, 2) if not pd.isna(v) else None for v in j_val.tail(120).values],
        'boll_up': [round(v, 2) if not pd.isna(v) else None for v in boll_up.tail(120).values],
        'boll_mid': [round(v, 2) if not pd.isna(v) else None for v in boll_mid.tail(120).values],
        'boll_dn': [round(v, 2) if not pd.isna(v) else None for v in boll_dn.tail(120).values],
    }

    # 最近交易日数据
    recent_rows = []
    for i in range(len(df)-1, max(-1, len(df)-31), -1):
        row = df.iloc[i]
        c = float(row['收盘'])
        o = float(row['开盘'])
        chg = ((c - o) / o * 100) if o > 0 else 0
        recent_rows.append({
            'date': str(df.index[i])[:10],
            'open': f"{o:.2f}",
            'close': f"{c:.2f}",
            'high': f"{float(row['最高']):.2f}",
            'low': f"{float(row['最低']):.2f}",
            'volume': f"{float(row['成交量']):.0f}",
            'change': f"{chg:+.2f}",
            'change_pct': f"{float(row.get('涨跌幅', chg)):.2f}" if '涨跌幅' in df.columns else f"{chg:+.2f}",
        })

    return {
        'score': round(score, 1),
        'action': action,
        'action_text': action_text,
        'current_price': round(current, 2),
        'ma5': round(ma5_v, 2),
        'ma10': round(ma10_v, 2),
        'ma20': round(ma20_v, 2),
        'ma60': round(ma60_v, 2) if not pd.isna(ma60_v) else None,
        'rsi': round(rsi_val, 1),
        'macd_dif': round(dif_v, 4),
        'macd_dea': round(dea_v, 4),
        'macd_hist': round(hist_v, 4),
        'kdj_k': round(k_v, 1),
        'kdj_d': round(d_v, 1),
        'kdj_j': round(j_v, 1),
        'boll_up': round(boll_up_v, 2),
        'boll_mid': round(boll_mid_v, 2),
        'boll_dn': round(boll_dn_v, 2),
        'atr': round(atr.iloc[-1], 2) if not atr.empty and not pd.isna(atr.iloc[-1]) else 0,
        'signals': signals,
        'beginner_advice': beginner_advice,
        'buy_timing': buy_timing,
        'buy_sell_points': buy_sell_points,
        'kline_data': kline_data,
        'indicator_data': indicator_data,
        'recent_rows': recent_rows,
    }


# ═══════════════════════════════════════════════════════════
# 股票推荐引擎
# ═══════════════════════════════════════════════════════════

def scan_top_stocks():
    """扫描创业板潜力股并生成推荐（偏向非主流中小成长股）"""
    # 创业板(300xxx) + 少量中小盘标的，避开最主流的大白马
    stock_pool = [
        # 创业板成长股
        '300124',  # 汇川技术 - 工控龙头
        '300015',  # 爱尔眼科 - 眼科连锁
        '300274',  # 阳光电源 - 光伏逆变器
        '300142',  # 沃森生物 - 疫苗
        '300033',  # 同花顺 - 金融信息
        '300122',  # 智飞生物 - 疫苗
        '300413',  # 芒果超媒 - 新媒体
        '300496',  # 中科创达 - 智能OS
        '300782',  # 卓胜微 - 射频芯片
        '300661',  # 圣邦股份 - 模拟芯片
        '300223',  # 北京君正 - AI芯片
        '300308',  # 中际旭创 - 光模块
        '300457',  # 赢合科技 - 锂电设备
        '300014',  # 亿纬锂能 - 锂电池
        '300285',  # 国瓷材料 - 电子陶瓷
        '300136',  # 信维通信 - 天线
        '300454',  # 深信服 - 网络安全
        '300253',  # 卫宁健康 - 医疗信息化
        '300529',  # 健帆生物 - 血液灌流
        '300999',  # 金龙鱼 - 粮油
        '300763',  # 锦浪科技 - 光伏逆变器
        '300394',  # 天孚通信 - 光通信
        '300357',  # 我武生物 - 脱敏
        '300759',  # 康龙化成 - CRO
        '300347',  # 泰格医药 - 临床CRO
        '300377',  # 赢时胜 - 金融科技
        '300373',  # 扬杰科技 - 功率半导体
        '300450',  # 先导智能 - 锂电设备
        '300390',  # 天华新能 - 锂盐
        '300750',  # 宁德时代 - 作为创业板对比参考
    ]
    results = []
    for code in stock_pool:
        try:
            df, source = fetch_stock_data(code, 180)
            if df is None or df.empty or len(df) < 30:
                continue
            analysis = compute_full_analysis(df)
            if analysis is None:
                continue
            name = fetch_stock_name(code)

            # 为推荐生成买入时机简述
            buy_hint = ''
            bt = analysis.get('buy_timing', [])
            if bt:
                top = bt[0]
                buy_hint = top.get('condition', '')
                if top.get('target_price'):
                    buy_hint += f" ({top['target_price']}元)"
            else:
                buy_hint = '暂无明确买点'

            results.append({
                'code': code,
                'name': name,
                'score': analysis['score'],
                'action': analysis['action'],
                'action_text': analysis['action_text'],
                'price': analysis['current_price'],
                'rsi': analysis['rsi'],
                'signals_summary': ', '.join([s[0] for s in analysis['signals'][:3]]),
                'buy_hint': buy_hint,
            })
        except Exception:
            continue

    # 按评分排序
    results.sort(key=lambda x: x['score'], reverse=True)
    return results


def get_cached_recommendations():
    """获取缓存的推荐（每4小时更新一次）"""
    try:
        if os.path.exists(RECOMMEND_CACHE_FILE):
            with open(RECOMMEND_CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            if time.time() - cache.get('timestamp', 0) < 14400:  # 4小时
                return cache.get('data', [])
    except Exception:
        pass
    return None


def save_recommendations_cache(data):
    try:
        with open(RECOMMEND_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'timestamp': time.time(), 'data': data}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# Watchlist
# ═══════════════════════════════════════════════════════════

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_watchlist(wl):
    with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(wl, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════════════════════

@app.route('/api/analyze')
def api_analyze():
    """分析单只股票"""
    code = request.args.get('code', '').strip()
    if not code:
        return jsonify({'error': '请输入股票代码'}), 400
    try:
        df, source = fetch_stock_data(code, 365)
        if df is None or df.empty:
            return jsonify({'error': f'未找到股票 {code} 的数据'}), 404
        analysis = compute_full_analysis(df)
        if analysis is None:
            return jsonify({'error': '数据不足，无法分析'}), 400
        name = fetch_stock_name(code)
        news = fetch_stock_news(code)
        fundamentals = fetch_fundamentals(code)
        return jsonify({
            'code': code,
            'name': name,
            'source': source,
            'analysis': analysis,
            'news': news[:10],
            'fundamentals': fundamentals,
        })
    except Exception as e:
        return jsonify({'error': f'分析失败: {str(e)}'}), 500


@app.route('/api/recommend')
def api_recommend():
    """获取股票推荐列表"""
    cached = get_cached_recommendations()
    if cached:
        return jsonify({'data': cached, 'cached': True})
    # 同步扫描（可能较慢）
    data = scan_top_stocks()
    save_recommendations_cache(data)
    return jsonify({'data': data, 'cached': False})


@app.route('/api/recommend/refresh')
def api_recommend_refresh():
    """强制刷新推荐"""
    data = scan_top_stocks()
    save_recommendations_cache(data)
    return jsonify({'data': data, 'cached': False})


@app.route('/api/watchlist', methods=['GET'])
def api_watchlist_get():
    return jsonify(load_watchlist())


@app.route('/api/watchlist', methods=['POST'])
def api_watchlist_add():
    data = request.get_json()
    code = data.get('code', '').strip()
    if not code:
        return jsonify({'error': '请输入股票代码'}), 400
    wl = load_watchlist()
    if code not in [s['code'] for s in wl]:
        name = fetch_stock_name(code)
        wl.append({'code': code, 'name': name})
        save_watchlist(wl)
    return jsonify(wl)


@app.route('/api/watchlist/<int:idx>', methods=['DELETE'])
def api_watchlist_delete(idx):
    wl = load_watchlist()
    if 0 <= idx < len(wl):
        wl.pop(idx)
        save_watchlist(wl)
    return jsonify(wl)


# ═══════════════════════════════════════════════════════════
# 主页面
# ═══════════════════════════════════════════════════════════

@app.route('/')
def index():
    return HTML_PAGE


HTML_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股智能分析系统</title>
<style>
:root {
    --bg: #0a0a1a;
    --card: #12122a;
    --card2: #1a1a3a;
    --border: #2a2a4a;
    --accent: #00d4ff;
    --accent2: #7c3aed;
    --green: #00c853;
    --red: #ff1744;
    --text: #e0e0e0;
    --text2: #888;
    --gold: #ffd700;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, 'Segoe UI', Roboto, 'Microsoft YaHei', sans-serif; background: var(--bg); color: var(--text); min-height:100vh; }
a { color: var(--accent); text-decoration:none; }

/* 顶部导航 */
.navbar { background: var(--card); border-bottom: 1px solid var(--border); padding: 12px 0; position: sticky; top:0; z-index:100; }
.navbar .inner { max-width:1400px; margin:0 auto; padding:0 20px; display:flex; align-items:center; justify-content:space-between; }
.navbar .logo { font-size:22px; font-weight:700; color:var(--accent); }
.navbar .nav-tabs { display:flex; gap:5px; }
.navbar .nav-tabs button {
    padding:8px 20px; background:transparent; border:1px solid var(--border);
    color:var(--text2); border-radius:8px; cursor:pointer; font-size:14px; transition:all .2s;
}
.navbar .nav-tabs button.active,
.navbar .nav-tabs button:hover { background:var(--accent); color:#000; border-color:var(--accent); }

.container { max-width:1400px; margin:0 auto; padding:20px; }

/* 搜索框 */
.search-section { text-align:center; margin:30px 0; }
.search-bar { display:inline-flex; gap:10px; align-items:center; }
.search-bar input {
    padding:14px 24px; font-size:18px; border:2px solid var(--border); border-radius:12px;
    background:var(--card); color:#fff; width:300px; outline:none; transition:border .2s;
}
.search-bar input:focus { border-color:var(--accent); }
.search-bar button {
    padding:14px 30px; font-size:16px; background:linear-gradient(135deg, var(--accent), var(--accent2));
    border:none; border-radius:12px; cursor:pointer; color:#fff; font-weight:600; transition:transform .1s;
}
.search-bar button:hover { transform:scale(1.03); }
.search-bar button:active { transform:scale(0.97); }

.quick-tags { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; margin-top:15px; }
.quick-tags button {
    padding:8px 16px; font-size:13px; background:var(--card2); border:1px solid var(--border);
    border-radius:20px; cursor:pointer; color:var(--text2); transition:all .2s;
}
.quick-tags button:hover { border-color:var(--accent); color:var(--accent); }

/* 页面区域 */
.page { display:none; }
.page.active { display:block; }

/* 加载动画 */
.loading { text-align:center; padding:60px; }
.spinner { width:40px; height:40px; border:4px solid var(--border); border-top:4px solid var(--accent);
    border-radius:50%; animation:spin 0.8s linear infinite; margin:0 auto 15px; }
@keyframes spin { 100% { transform:rotate(360deg); } }

/* 评分仪表 */
.score-section { text-align:center; margin:30px 0; }
.score-gauge { display:inline-block; position:relative; width:200px; height:100px; }
.score-gauge svg { width:200px; height:100px; }
.score-number { font-size:42px; font-weight:700; }
.score-label { font-size:20px; margin-top:5px; }

/* 信号卡片 */
.signal-banner { padding:25px; border-radius:16px; text-align:center; margin:20px 0; }
.signal-banner.buy { background:linear-gradient(135deg, #00c853, #00897b); }
.signal-banner.sell { background:linear-gradient(135deg, #ff1744, #c62828); }
.signal-banner.hold { background:linear-gradient(135deg, #424242, #616161); }
.signal-banner.hold_buy { background:linear-gradient(135deg, #00897b, #424242); }
.signal-banner.hold_sell { background:linear-gradient(135deg, #c62828, #424242); }
.signal-banner h2 { font-size:36px; color:#fff; }
.signal-banner p { color:rgba(255,255,255,.8); font-size:16px; margin-top:5px; }

/* 指标卡片 */
.metrics-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(140px,1fr)); gap:12px; margin:20px 0; }
.metric-card { background:var(--card); padding:16px; border-radius:12px; text-align:center; border:1px solid var(--border); }
.metric-card .label { font-size:12px; color:var(--text2); margin-bottom:6px; }
.metric-card .val { font-size:22px; font-weight:700; }
.metric-card .val.up { color:var(--green); }
.metric-card .val.down { color:var(--red); }
.metric-card .val.neutral { color:var(--accent); }

/* 信号标签 */
.signal-tags { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; margin:20px 0; }
.signal-tag { padding:8px 16px; border-radius:20px; font-size:13px; border:1px solid var(--border); }
.signal-tag.bullish { background:rgba(0,200,83,.15); border-color:var(--green); color:var(--green); }
.signal-tag.bearish { background:rgba(255,23,68,.15); border-color:var(--red); color:var(--red); }
.signal-tag.neutral { background:rgba(0,212,255,.1); border-color:var(--accent); color:var(--accent); }

/* 子Tab */
.sub-tabs { display:flex; gap:0; border-bottom:2px solid var(--border); margin:25px 0 0; overflow-x:auto; }
.sub-tab {
    padding:12px 24px; background:transparent; color:var(--text2); cursor:pointer;
    border:none; font-size:15px; white-space:nowrap; transition:all .2s;
}
.sub-tab.active { color:var(--accent); border-bottom:3px solid var(--accent); }
.sub-panel { display:none; padding-top:20px; }
.sub-panel.active { display:block; }

/* 图表容器 */
.chart-container { background:var(--card); border-radius:12px; padding:20px; margin:15px 0; border:1px solid var(--border); position:relative; }
.chart-container canvas { width:100%; }
.chart-container h4 { color:var(--accent); margin-bottom:10px; font-size:14px; }

/* 表格 */
.data-table { width:100%; border-collapse:collapse; font-size:13px; }
.data-table th { background:var(--card2); color:var(--accent); padding:10px 8px; text-align:center; border:1px solid var(--border); position:sticky; top:0; }
.data-table td { padding:8px; text-align:center; border:1px solid var(--border); }
.data-table tr:hover { background:var(--card2); }

/* 新闻 */
.news-card { background:var(--card); padding:18px; margin:12px 0; border-radius:12px; border:1px solid var(--border); transition:border .2s; }
.news-card:hover { border-color:var(--accent); }
.news-card h4 { color:#fff; margin-bottom:6px; font-size:15px; }
.news-card .meta { color:var(--text2); font-size:12px; margin-bottom:8px; }
.news-card p { color:#aaa; font-size:13px; line-height:1.6; }

/* 基本面 */
.fund-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(200px,1fr)); gap:12px; }
.fund-item { background:var(--card); padding:14px; border-radius:10px; border:1px solid var(--border); }
.fund-item .label { color:var(--text2); font-size:12px; }
.fund-item .value { color:#fff; font-size:15px; margin-top:4px; font-weight:500; }

/* 新手指南 */
.advice-box { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:22px; margin:15px 0; }
.advice-box h4 { margin-bottom:10px; font-size:16px; }
.advice-box .advice-item { padding:12px 16px; margin:8px 0; border-radius:10px; border-left:4px solid var(--accent); background:var(--card2); }
.advice-box .advice-item.safe { border-left-color:var(--green); }
.advice-box .advice-item.caution { border-left-color:#ffb300; }
.advice-box .advice-item.warning { border-left-color:#ff9800; }
.advice-box .advice-item.danger { border-left-color:var(--red); }
.advice-box .advice-item .atitle { font-weight:600; margin-bottom:4px; font-size:14px; }
.advice-box .advice-item .adesc { color:var(--text2); font-size:13px; line-height:1.7; }
.timing-card { background:var(--card2); border:1px solid var(--border); border-radius:10px; padding:16px; margin:10px 0; }
.timing-card .t-cond { font-weight:600; color:var(--accent); font-size:14px; }
.timing-card .t-price { color:var(--gold); font-size:13px; margin:4px 0; }
.timing-card .t-desc { color:var(--text2); font-size:13px; line-height:1.6; }
.risk-banner { background:linear-gradient(135deg,#1a1a2e,#2a1a1a); border:1px solid #ff174433; border-radius:12px; padding:18px; margin:15px 0; }
.risk-banner .risk-title { color:var(--red); font-weight:600; font-size:15px; margin-bottom:6px; }
.risk-banner .risk-text { color:#ccc; font-size:13px; line-height:1.7; }

/* 买卖点 */
.bs-timeline { max-height:400px; overflow-y:auto; }
.bs-item { display:flex; align-items:center; gap:12px; padding:10px 15px; margin:6px 0; background:var(--card); border-radius:10px; border:1px solid var(--border); }
.bs-dot { width:12px; height:12px; border-radius:50%; flex-shrink:0; }
.bs-dot.buy { background:var(--green); box-shadow:0 0 8px rgba(0,200,83,.4); }
.bs-dot.sell { background:var(--red); box-shadow:0 0 8px rgba(255,23,68,.4); }
.bs-info { flex:1; }
.bs-info .date { color:var(--text2); font-size:12px; }
.bs-info .reason { font-size:13px; }
.bs-price { font-weight:600; font-size:15px; }

/* 推荐列表 */
.rec-card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:18px; margin:10px 0; display:flex; align-items:center; gap:15px; cursor:pointer; transition:all .2s; }
.rec-card:hover { border-color:var(--accent); transform:translateX(3px); }
.rec-rank { font-size:24px; font-weight:700; color:var(--gold); min-width:35px; text-align:center; }
.rec-rank.top3 { color:var(--gold); }
.rec-rank:not(.top3) { color:var(--text2); }
.rec-info { flex:1; }
.rec-info .name { font-size:16px; font-weight:600; color:#fff; }
.rec-info .code { color:var(--text2); font-size:13px; }
.rec-info .summary { color:var(--text2); font-size:12px; margin-top:4px; }
.rec-info .buy-hint { color:var(--gold); font-size:11px; margin-top:3px; font-style:italic; }
.rec-score { text-align:center; min-width:70px; }
.rec-score .num { font-size:28px; font-weight:700; }
.rec-score .label { font-size:11px; color:var(--text2); }
.rec-action { padding:6px 14px; border-radius:8px; font-size:13px; font-weight:600; }
.rec-action.buy { background:rgba(0,200,83,.2); color:var(--green); }
.rec-action.sell { background:rgba(255,23,68,.2); color:var(--red); }
.rec-action.hold { background:rgba(100,100,100,.2); color:var(--text2); }

/* 自选股 */
.wl-section { background:var(--card); border-radius:16px; padding:25px; margin-top:30px; border:1px solid var(--border); }
.wl-section h3 { color:var(--accent); margin-bottom:15px; font-size:18px; }
.wl-add { display:flex; gap:10px; margin-bottom:15px; }
.wl-add input { flex:1; padding:10px 16px; background:var(--bg); border:1px solid var(--border); border-radius:8px; color:#fff; outline:none; }
.wl-add button { padding:10px 20px; background:var(--green); border:none; border-radius:8px; color:#fff; cursor:pointer; font-weight:600; }
.wl-item { display:flex; justify-content:space-between; align-items:center; padding:12px 16px; background:var(--bg); margin:6px 0; border-radius:10px; }
.wl-item .info strong { color:#fff; }
.wl-item .info span { color:var(--text2); margin-left:8px; font-size:13px; }
.wl-item .btns { display:flex; gap:8px; }
.wl-item .btns button { padding:6px 14px; border:none; border-radius:6px; cursor:pointer; font-size:12px; }
.wl-item .view-btn { background:var(--card2); color:var(--accent); }
.wl-item .del-btn { background:#c62828; color:#fff; }

.empty-msg { text-align:center; color:var(--text2); padding:30px; }

/* 响应式 */
@media(max-width:768px) {
    .navbar .inner { flex-direction:column; gap:10px; padding:0 12px; }
    .navbar .nav-tabs { width:100%; overflow-x:auto; -webkit-overflow-scrolling:touch; }
    .navbar .nav-tabs button { padding:8px 14px; font-size:13px; flex-shrink:0; }
    .container { padding:12px; }
    .search-bar { flex-direction:column; width:100%; }
    .search-bar input { width:100%; font-size:16px; padding:12px 16px; }
    .search-bar button { width:100%; padding:14px; font-size:16px; }
    .quick-tags { gap:6px; }
    .quick-tags button { padding:7px 12px; font-size:12px; }
    .metrics-grid { grid-template-columns:repeat(3,1fr); gap:8px; }
    .metric-card { padding:10px 6px; }
    .metric-card .val { font-size:16px; }
    .metric-card .label { font-size:11px; }
    .fund-grid { grid-template-columns:repeat(2,1fr); gap:8px; }
    .fund-item .value { font-size:14px; }
    .rec-card { flex-direction:column; text-align:center; padding:14px; }
    .signal-banner h2 { font-size:24px; }
    .signal-banner p { font-size:14px; }
    .score-gauge { width:150px; height:75px; }
    .score-gauge svg { width:150px; height:75px; }
    .score-number { font-size:32px; }
    .chart-container { padding:12px; overflow-x:auto; -webkit-overflow-scrolling:touch; }
    .data-table { font-size:11px; display:block; overflow-x:auto; -webkit-overflow-scrolling:touch; }
    .data-table th, .data-table td { padding:6px 4px; white-space:nowrap; }
    .news-card { padding:14px; }
    .advice-box { padding:16px; }
    .bs-item { padding:8px 10px; gap:8px; }
    .wl-add { flex-direction:column; }
    .wl-add input { width:100%; }
    .sub-tabs { gap:0; }
    .sub-tab { padding:10px 14px; font-size:13px; }
}
@media(max-width:480px) {
    .metrics-grid { grid-template-columns:repeat(2,1fr); }
    .fund-grid { grid-template-columns:1fr 1fr; }
    .navbar .logo { font-size:18px; }
}
</style>
</head>
<body>

<div class="navbar">
  <div class="inner">
    <div class="logo">A股智能分析系统</div>
    <div class="nav-tabs">
      <button class="active" onclick="switchPage('analyze')">股票分析</button>
      <button onclick="switchPage('recommend')">今日推荐</button>
      <button onclick="switchPage('watchlist')">我的自选</button>
    </div>
  </div>
</div>

<div class="container">

<!-- ====== 分析页 ====== -->
<div id="page-analyze" class="page active">
  <div class="search-section">
    <div class="search-bar">
      <input id="stockInput" type="text" placeholder="输入A股代码，如 600519" onkeydown="if(event.key==='Enter')doAnalyze()">
      <button onclick="doAnalyze()">开始分析</button>
    </div>
    <div class="quick-tags">
      <button onclick="analyzeStock('600519')">贵州茅台</button>
      <button onclick="analyzeStock('300750')">宁德时代</button>
      <button onclick="analyzeStock('002594')">比亚迪</button>
      <button onclick="analyzeStock('600036')">招商银行</button>
      <button onclick="analyzeStock('601318')">中国平安</button>
      <button onclick="analyzeStock('000858')">五粮液</button>
      <button onclick="analyzeStock('000333')">美的集团</button>
      <button onclick="analyzeStock('601012')">隆基绿能</button>
      <button onclick="analyzeStock('600900')">长江电力</button>
      <button onclick="analyzeStock('601888')">中国中免</button>
    </div>
  </div>

  <div id="analyzeResult"></div>
</div>

<!-- ====== 推荐页 ====== -->
<div id="page-recommend" class="page">
  <h2 style="text-align:center;margin:30px 0 10px;color:var(--accent);">创业板潜力股推荐</h2>
  <p style="text-align:center;color:var(--text2);margin-bottom:20px;">聚焦创业板中小成长股，基于技术指标综合评分，含买入时机建议 | 每4小时更新</p>
  <div style="text-align:center;margin-bottom:20px;">
    <button onclick="refreshRecommend()" style="padding:10px 25px;background:var(--accent2);border:none;border-radius:8px;color:#fff;cursor:pointer;font-size:14px;">刷新推荐</button>
  </div>
  <div id="recommendResult"><div class="loading"><div class="spinner"></div><p>正在扫描市场...</p></div></div>
</div>

<!-- ====== 自选股页 ====== -->
<div id="page-watchlist" class="page">
  <div class="wl-section">
    <h3>我的自选股</h3>
    <div class="wl-add">
      <input id="wlInput" placeholder="输入股票代码" onkeydown="if(event.key==='Enter')addWatchlist()">
      <button onclick="addWatchlist()">添加</button>
    </div>
    <div id="watchlistContent"></div>
  </div>
</div>

</div>

<script>
// ═══ 页面切换 ═══
function switchPage(name) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('page-' + name).classList.add('active');
    document.querySelectorAll('.nav-tabs button').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
    if (name === 'recommend') loadRecommendations();
    if (name === 'watchlist') loadWatchlist();
}

// ═══ 分析 ═══
function doAnalyze() {
    const code = document.getElementById('stockInput').value.trim();
    if (code) analyzeStock(code);
}

function analyzeStock(code) {
    document.getElementById('stockInput').value = code;
    const container = document.getElementById('analyzeResult');
    container.innerHTML = '<div class="loading"><div class="spinner"></div><p>正在分析 ' + code + ' ...</p></div>';

    fetch('/api/analyze?code=' + code)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                container.innerHTML = '<div style="text-align:center;color:var(--red);padding:40px;">' + data.error + '</div>';
                return;
            }
            renderAnalysis(data, container);
        })
        .catch(err => {
            container.innerHTML = '<div style="text-align:center;color:var(--red);padding:40px;">请求失败: ' + err + '</div>';
        });
}

function renderAnalysis(data, container) {
    const a = data.analysis;
    const sc = a.score;
    const scColor = sc >= 70 ? 'var(--green)' : sc >= 45 ? 'var(--accent)' : 'var(--red)';
    const bannerClass = a.action.toLowerCase().replace('_', '_');

    let signalTagsHTML = a.signals.map(s => {
        return '<span class="signal-tag ' + s[1] + '">' + s[0] + '</span>';
    }).join('');

    // 评分弧度
    const angle = (sc / 100) * 180;
    const rad = angle * Math.PI / 180;
    const r = 80;
    const cx = 100, cy = 90;
    const x = cx + r * Math.cos(Math.PI - rad);
    const y = cy - r * Math.sin(Math.PI - rad);
    const largeArc = angle > 90 ? 1 : 0;

    let html = '';
    // 标题
    html += '<h2 style="text-align:center;margin:20px 0 5px;color:#fff;">' + data.name + ' <span style="color:var(--text2);font-size:16px;">' + data.code + '</span></h2>';
    html += '<p style="text-align:center;color:var(--text2);font-size:13px;">数据来源: ' + data.source + '</p>';

    // 评分仪表
    html += '<div class="score-section">';
    html += '<div class="score-gauge">';
    html += '<svg viewBox="0 0 200 110">';
    html += '<path d="M 20 90 A 80 80 0 0 1 180 90" fill="none" stroke="var(--border)" stroke-width="12" stroke-linecap="round"/>';
    html += '<path d="M 20 90 A 80 80 0 ' + largeArc + ' 1 ' + x.toFixed(1) + ' ' + y.toFixed(1) + '" fill="none" stroke="' + scColor + '" stroke-width="12" stroke-linecap="round"/>';
    html += '</svg>';
    html += '</div>';
    html += '<div class="score-number" style="color:' + scColor + ';">' + sc + '</div>';
    html += '<div class="score-label" style="color:' + scColor + ';">' + a.action_text + '</div>';
    html += '</div>';

    // 信号Banner
    html += '<div class="signal-banner ' + bannerClass + '"><h2>' + a.action_text + '</h2><p>综合评分 ' + sc + '/100</p></div>';

    // 指标卡片
    html += '<div class="metrics-grid">';
    const metrics = [
        ['当前价', a.current_price, 'neutral'],
        ['MA5', a.ma5, a.ma5 > a.current_price ? 'down' : 'up'],
        ['MA10', a.ma10, a.ma10 > a.current_price ? 'down' : 'up'],
        ['MA20', a.ma20, a.ma20 > a.current_price ? 'down' : 'up'],
        ['RSI(14)', a.rsi, a.rsi > 70 ? 'down' : a.rsi < 30 ? 'up' : 'neutral'],
        ['MACD', a.macd_dif.toFixed(2), a.macd_dif > 0 ? 'up' : 'down'],
        ['KDJ-K', a.kdj_k, a.kdj_k > 80 ? 'down' : a.kdj_k < 20 ? 'up' : 'neutral'],
        ['布林中轨', a.boll_mid, 'neutral'],
        ['ATR', a.atr, 'neutral'],
    ];
    metrics.forEach(m => {
        html += '<div class="metric-card"><div class="label">' + m[0] + '</div><div class="val ' + m[2] + '">' + m[1] + '</div></div>';
    });
    html += '</div>';

    // 信号标签
    html += '<div class="signal-tags">' + signalTagsHTML + '</div>';

    // 子Tab
    html += '<div class="sub-tabs">';
    html += '<button class="sub-tab active" onclick="showSubTab(\'beginner\',this)">新手指南</button>';
    html += '<button class="sub-tab" onclick="showSubTab(\'chart\',this)">K线图表</button>';
    html += '<button class="sub-tab" onclick="showSubTab(\'bspoints\',this)">买卖信号</button>';
    html += '<button class="sub-tab" onclick="showSubTab(\'history\',this)">历史数据</button>';
    html += '<button class="sub-tab" onclick="showSubTab(\'news\',this)">相关新闻</button>';
    html += '<button class="sub-tab" onclick="showSubTab(\'fund\',this)">基本面</button>';
    html += '</div>';

    // ═══ 新手指南 Tab ═══
    const adv = a.beginner_advice || {};
    const lvl = adv.level || 'caution';
    html += '<div id="sub-beginner" class="sub-panel active">';
    html += '<div class="advice-box">';
    html += '<h4 style="color:var(--accent);">新手操作建议</h4>';
    html += '<div class="advice-item ' + lvl + '"><div class="atitle">总体判断</div><div class="adesc">' + escHTML(adv.summary || '') + '</div></div>';
    html += '<div class="advice-item ' + lvl + '"><div class="atitle">仓位建议</div><div class="adesc">' + escHTML(adv.position || '') + '</div></div>';
    html += '<div class="advice-item ' + lvl + '"><div class="atitle">买入时机</div><div class="adesc">' + escHTML(adv.timing || '') + '</div></div>';
    html += '<div class="advice-item ' + lvl + '"><div class="atitle">止损设置</div><div class="adesc">' + escHTML(adv.stoploss || '') + '</div></div>';
    html += '</div>';

    // 买入时机推荐
    const bt = a.buy_timing || [];
    if (bt.length > 0) {
        html += '<div class="advice-box"><h4 style="color:var(--gold);">买入时机参考</h4>';
        bt.forEach(t => {
            html += '<div class="timing-card">';
            html += '<div class="t-cond">' + escHTML(t.condition) + '</div>';
            if (t.target_price) html += '<div class="t-price">参考价位: ' + t.target_price + ' 元</div>';
            html += '<div class="t-desc">' + escHTML(t.description) + '</div>';
            html += '</div>';
        });
        html += '</div>';
    }

    // 风险提示
    html += '<div class="risk-banner">';
    html += '<div class="risk-title">风险提示</div>';
    html += '<div class="risk-text">' + escHTML(adv.risk || '股市有风险，入市需谨慎。以上分析仅供参考，不构成投资建议。') + '</div>';
    html += '</div>';
    html += '</div>';

    // K线图表
    html += '<div id="sub-chart" class="sub-panel">';
    html += '<div class="chart-container"><h4>K线走势 + 均线</h4><canvas id="klineCanvas" height="350"></canvas></div>';
    html += '<div class="chart-container"><h4>MACD</h4><canvas id="macdCanvas" height="150"></canvas></div>';
    html += '<div class="chart-container"><h4>RSI</h4><canvas id="rsiCanvas" height="120"></canvas></div>';
    html += '<div class="chart-container"><h4>KDJ</h4><canvas id="kdjCanvas" height="120"></canvas></div>';
    html += '</div>';

    // 买卖信号
    html += '<div id="sub-bspoints" class="sub-panel">';
    html += '<h3 style="color:var(--accent);margin-bottom:15px;">最近买卖信号点</h3>';
    html += '<div class="bs-timeline">';
    if (a.buy_sell_points && a.buy_sell_points.length > 0) {
        a.buy_sell_points.slice().reverse().forEach(p => {
            html += '<div class="bs-item"><div class="bs-dot ' + p.type + '"></div>';
            html += '<div class="bs-info"><div class="date">' + p.date + '</div><div class="reason">' + p.reason + (p.tip ? ' - ' + p.tip : '') + '</div></div>';
            html += '<div class="bs-price" style="color:' + (p.type === 'buy' ? 'var(--green)' : 'var(--red)') + ';">' + (p.type === 'buy' ? 'BUY ' : 'SELL ') + p.price.toFixed(2) + '</div></div>';
        });
    } else {
        html += '<div class="empty-msg">近期无明显买卖信号</div>';
    }
    html += '</div></div>';

    // 历史数据
    html += '<div id="sub-history" class="sub-panel">';
    html += '<div style="overflow-x:auto;"><table class="data-table"><tr><th>日期</th><th>开盘</th><th>收盘</th><th>最高</th><th>最低</th><th>成交量</th><th>涨跌幅</th></tr>';
    a.recent_rows.forEach(r => {
        const chgVal = parseFloat(r.change);
        const chgColor = chgVal >= 0 ? 'var(--green)' : 'var(--red)';
        html += '<tr><td>' + r.date + '</td><td>' + r.open + '</td><td style="color:' + chgColor + '">' + r.close + '</td>';
        html += '<td>' + r.high + '</td><td>' + r.low + '</td><td>' + r.volume + '</td>';
        html += '<td style="color:' + chgColor + '">' + r.change + '%</td></tr>';
    });
    html += '</table></div></div>';

    // 新闻
    html += '<div id="sub-news" class="sub-panel">';
    if (data.news && data.news.length > 0) {
        data.news.forEach(n => {
            html += '<div class="news-card"><h4>' + escHTML(n.title) + '</h4>';
            html += '<div class="meta">' + escHTML(n.date) + (n.source ? ' | ' + escHTML(n.source) : '') + '</div>';
            html += '<p>' + escHTML(n.content).substring(0, 200) + '</p></div>';
        });
    } else {
        html += '<div class="empty-msg">暂无相关新闻</div>';
    }
    html += '</div>';

    // 基本面
    html += '<div id="sub-fund" class="sub-panel"><div class="fund-grid">';
    if (data.fundamentals && Object.keys(data.fundamentals).length > 0) {
        Object.entries(data.fundamentals).forEach(([k, v]) => {
            html += '<div class="fund-item"><div class="label">' + escHTML(k) + '</div><div class="value">' + escHTML(String(v)) + '</div></div>';
        });
    } else {
        html += '<div class="empty-msg" style="grid-column:1/-1;">暂无基本面数据</div>';
    }
    html += '</div></div>';

    container.innerHTML = html;

    // 保存图表数据供切Tab时延迟绘制
    window._chartData = { kline: a.kline_data, ind: a.indicator_data };
    window._chartDrawn = false;

    // 不在这里绘制，等用户切到K线Tab再画
}

function showSubTab(id, btn) {
    btn.parentElement.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    const parent = btn.parentElement.parentElement;
    parent.querySelectorAll('.sub-panel').forEach(p => p.classList.remove('active'));
    parent.querySelector('#sub-' + id).classList.add('active');
    // 切到K线Tab时绘制图表
    if (id === 'chart' && window._chartData && !window._chartDrawn) {
        setTimeout(() => {
            drawKline(window._chartData.kline, window._chartData.ind);
            drawMACD(window._chartData.kline, window._chartData.ind);
            drawRSI(window._chartData.kline, window._chartData.ind);
            drawKDJ(window._chartData.kline, window._chartData.ind);
            window._chartDrawn = true;
        }, 50);
    }
}

function escHTML(s) { return s ? s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') : ''; }

// ═══ 图表绘制 ═══
function drawKline(kdata, ind) {
    const canvas = document.getElementById('klineCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.parentElement.clientWidth - 40;
    const H = 350;
    if (W <= 0) return; // Tab隐藏时宽度为0，跳过
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx.scale(dpr, dpr);

    const n = kdata.length;
    if (n === 0) return;
    const padL = 60, padR = 10, padT = 20, padB = 50;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const barW = Math.max(2, chartW / n - 1);

    // 计算价格范围
    let minP = Infinity, maxP = -Infinity;
    kdata.forEach(d => { minP = Math.min(minP, d.low); maxP = Math.max(maxP, d.high); });
    const range = maxP - minP || 1;
    const yScale = chartH / (range * 1.05);
    const toY = v => padT + chartH - (v - minP + range * 0.025) * yScale;
    const toX = i => padL + (i + 0.5) * (chartW / n);

    // 背景
    ctx.fillStyle = '#12122a';
    ctx.fillRect(0, 0, W, H);

    // 网格
    ctx.strokeStyle = '#1a1a3a';
    ctx.lineWidth = 0.5;
    for (let i = 0; i < 5; i++) {
        const y = padT + (chartH / 4) * i;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
        const price = maxP - (range * 1.05 / 4) * i + range * 0.025;
        ctx.fillStyle = '#666';
        ctx.font = '11px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(price.toFixed(2), padL - 5, y + 4);
    }

    // K线
    kdata.forEach((d, i) => {
        const x = toX(i);
        const isUp = d.close >= d.open;
        ctx.strokeStyle = isUp ? '#ff4444' : '#00cc66';
        ctx.fillStyle = isUp ? '#ff4444' : '#00cc66';

        // 影线
        ctx.beginPath();
        ctx.moveTo(x, toY(d.high));
        ctx.lineTo(x, toY(d.low));
        ctx.lineWidth = 1;
        ctx.stroke();

        // 实体
        const bodyTop = toY(Math.max(d.open, d.close));
        const bodyBot = toY(Math.min(d.open, d.close));
        const bodyH = Math.max(1, bodyBot - bodyTop);
        if (isUp) {
            ctx.strokeRect(x - barW/2, bodyTop, barW, bodyH);
        } else {
            ctx.fillRect(x - barW/2, bodyTop, barW, bodyH);
        }
    });

    // 均线
    function drawLine(data, color) {
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.2;
        let started = false;
        data.forEach((v, i) => {
            if (v === null) return;
            const x = toX(i), y = toY(v);
            if (!started) { ctx.moveTo(x, y); started = true; }
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
    }
    if (ind.ma5) drawLine(ind.ma5, '#ffeb3b');
    if (ind.ma10) drawLine(ind.ma10, '#ff9800');
    if (ind.ma20) drawLine(ind.ma20, '#e91e63');

    // 布林带
    if (ind.boll_up && ind.boll_dn) {
        ctx.globalAlpha = 0.3;
        drawLine(ind.boll_up, '#7c3aed');
        drawLine(ind.boll_dn, '#7c3aed');
        ctx.globalAlpha = 1;
    }

    // X轴日期
    ctx.fillStyle = '#666';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(n / 8));
    for (let i = 0; i < n; i += step) {
        ctx.fillText(kdata[i].date.substring(5), toX(i), H - 5);
    }

    // 图例
    ctx.font = '11px sans-serif';
    const legends = [['MA5','#ffeb3b'],['MA10','#ff9800'],['MA20','#e91e63'],['BOLL','#7c3aed']];
    legends.forEach((l, i) => {
        const lx = padL + i * 70;
        ctx.fillStyle = l[1];
        ctx.fillRect(lx, H - 35, 12, 3);
        ctx.fillText(l[0], lx + 18, H - 31);
    });
}

function drawMACD(kdata, ind) {
    const canvas = document.getElementById('macdCanvas');
    if (!canvas || !ind.macd_dif) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.parentElement.clientWidth - 40;
    const H = 150;
    if (W <= 0) return;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.scale(dpr, dpr);

    const n = ind.macd_dif.length;
    const padL = 60, padR = 10, padT = 10, padB = 20;
    const chartW = W - padL - padR, chartH = H - padT - padB;

    let maxV = 0;
    ind.macd_dif.forEach(v => { if (v !== null) maxV = Math.max(maxV, Math.abs(v)); });
    ind.macd_dea.forEach(v => { if (v !== null) maxV = Math.max(maxV, Math.abs(v)); });
    ind.macd_hist.forEach(v => { if (v !== null) maxV = Math.max(maxV, Math.abs(v)); });
    maxV = maxV || 1;

    const mid = padT + chartH / 2;
    const scale = (chartH / 2) / maxV;
    const toX = i => padL + (i + 0.5) * (chartW / n);
    const toY = v => mid - v * scale;

    ctx.fillStyle = '#12122a'; ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = '#1a1a3a'; ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(padL, mid); ctx.lineTo(W - padR, mid); ctx.stroke();

    // 柱状图
    const barW = Math.max(2, chartW / n - 1);
    ind.macd_hist.forEach((v, i) => {
        if (v === null) return;
        ctx.fillStyle = v >= 0 ? 'rgba(255,68,68,0.7)' : 'rgba(0,204,102,0.7)';
        const h = Math.abs(v) * scale;
        ctx.fillRect(toX(i) - barW/2, v >= 0 ? mid - h : mid, barW, Math.max(1, h));
    });

    // DIF DEA线
    function drawL(data, color) {
        ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 1.2;
        let s = false;
        data.forEach((v, i) => {
            if (v === null) return;
            if (!s) { ctx.moveTo(toX(i), toY(v)); s = true; } else ctx.lineTo(toX(i), toY(v));
        });
        ctx.stroke();
    }
    drawL(ind.macd_dif, '#ffeb3b');
    drawL(ind.macd_dea, '#ff9800');
}

function drawRSI(kdata, ind) {
    const canvas = document.getElementById('rsiCanvas');
    if (!canvas || !ind.rsi) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.parentElement.clientWidth - 40;
    const H = 120;
    if (W <= 0) return;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.scale(dpr, dpr);

    const n = ind.rsi.length;
    const padL = 60, padR = 10, padT = 10, padB = 15;
    const chartW = W - padL - padR, chartH = H - padT - padB;
    const toX = i => padL + (i + 0.5) * (chartW / n);
    const toY = v => padT + chartH - (v / 100) * chartH;

    ctx.fillStyle = '#12122a'; ctx.fillRect(0, 0, W, H);

    // 超买超卖区域
    ctx.fillStyle = 'rgba(255,23,68,0.08)'; ctx.fillRect(padL, toY(100), chartW, toY(70) - toY(100));
    ctx.fillStyle = 'rgba(0,200,83,0.08)'; ctx.fillRect(padL, toY(30), chartW, toY(0) - toY(30));

    // 参考线
    [30, 50, 70].forEach(v => {
        ctx.strokeStyle = v === 50 ? '#444' : '#333'; ctx.lineWidth = 0.5;
        ctx.setLineDash([3,3]); ctx.beginPath(); ctx.moveTo(padL, toY(v)); ctx.lineTo(W-padR, toY(v)); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = '#666'; ctx.font = '10px sans-serif'; ctx.textAlign = 'right';
        ctx.fillText(v, padL - 5, toY(v) + 4);
    });

    // RSI线
    ctx.beginPath(); ctx.strokeStyle = '#7c3aed'; ctx.lineWidth = 1.5;
    let s = false;
    ind.rsi.forEach((v, i) => {
        if (v === null) return;
        if (!s) { ctx.moveTo(toX(i), toY(v)); s = true; } else ctx.lineTo(toX(i), toY(v));
    });
    ctx.stroke();
}

function drawKDJ(kdata, ind) {
    const canvas = document.getElementById('kdjCanvas');
    if (!canvas || !ind.k) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.parentElement.clientWidth - 40;
    const H = 120;
    if (W <= 0) return;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.scale(dpr, dpr);

    const n = ind.k.length;
    const padL = 60, padR = 10, padT = 10, padB = 15;
    const chartW = W - padL - padR, chartH = H - padT - padB;

    let minV = 0, maxV = 100;
    ind.j.forEach(v => { if (v !== null) { minV = Math.min(minV, v); maxV = Math.max(maxV, v); }});
    const range = maxV - minV || 1;

    const toX = i => padL + (i + 0.5) * (chartW / n);
    const toY = v => padT + chartH - ((v - minV) / range) * chartH;

    ctx.fillStyle = '#12122a'; ctx.fillRect(0, 0, W, H);

    function drawL(data, color) {
        ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 1.2;
        let s = false;
        data.forEach((v, i) => { if (v === null) return; if (!s) { ctx.moveTo(toX(i), toY(v)); s = true; } else ctx.lineTo(toX(i), toY(v)); });
        ctx.stroke();
    }
    drawL(ind.k, '#ffeb3b');
    drawL(ind.d, '#ff9800');
    drawL(ind.j, '#7c3aed');

    // 图例
    ctx.font = '10px sans-serif';
    [['K','#ffeb3b'],['D','#ff9800'],['J','#7c3aed']].forEach((l, i) => {
        ctx.fillStyle = l[1]; ctx.fillRect(padL + i * 50, H - 13, 10, 3);
        ctx.fillText(l[0], padL + i * 50 + 14, H - 9);
    });
}

// ═══ 推荐 ═══
let recLoaded = false;
function loadRecommendations() {
    if (recLoaded) return;
    recLoaded = true;
    fetch('/api/recommend')
        .then(r => r.json())
        .then(data => renderRecommendations(data.data))
        .catch(err => {
            document.getElementById('recommendResult').innerHTML = '<div class="empty-msg">加载失败: ' + err + '</div>';
        });
}

function refreshRecommend() {
    document.getElementById('recommendResult').innerHTML = '<div class="loading"><div class="spinner"></div><p>正在扫描30只标的...</p></div>';
    recLoaded = false;
    fetch('/api/recommend/refresh')
        .then(r => r.json())
        .then(data => { recLoaded = true; renderRecommendations(data.data); })
        .catch(err => {
            document.getElementById('recommendResult').innerHTML = '<div class="empty-msg">刷新失败: ' + err + '</div>';
        });
}

function renderRecommendations(list) {
    if (!list || list.length === 0) {
        document.getElementById('recommendResult').innerHTML = '<div class="empty-msg">暂无推荐</div>';
        return;
    }
    let html = '';
    list.forEach((item, i) => {
        const rank = i + 1;
        const scColor = item.score >= 70 ? 'var(--green)' : item.score >= 45 ? 'var(--accent)' : 'var(--red)';
        let actionClass = 'hold';
        if (item.action === 'BUY' || item.action === 'HOLD_BUY') actionClass = 'buy';
        else if (item.action === 'SELL' || item.action === 'HOLD_SELL') actionClass = 'sell';

        html += '<div class="rec-card" onclick="switchToAnalyze(\'' + item.code + '\')">';
        html += '<div class="rec-rank ' + (rank <= 3 ? 'top3' : '') + '">' + rank + '</div>';
        html += '<div class="rec-info"><div class="name">' + escHTML(item.name) + '</div>';
        html += '<div class="code">' + item.code + ' | ' + item.price + '</div>';
        html += '<div class="summary">' + escHTML(item.signals_summary) + '</div>';
        if (item.buy_hint) html += '<div class="buy-hint">' + escHTML(item.buy_hint) + '</div>';
        html += '</div>';
        html += '<div class="rec-score"><div class="num" style="color:' + scColor + '">' + item.score + '</div><div class="label">评分</div></div>';
        html += '<div class="rec-action ' + actionClass + '">' + escHTML(item.action_text) + '</div>';
        html += '</div>';
    });
    document.getElementById('recommendResult').innerHTML = html;
}

function switchToAnalyze(code) {
    document.querySelectorAll('.nav-tabs button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.nav-tabs button')[0].classList.add('active');
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('page-analyze').classList.add('active');
    analyzeStock(code);
}

// ═══ 自选股 ═══
function loadWatchlist() {
    fetch('/api/watchlist')
        .then(r => r.json())
        .then(data => renderWatchlist(data))
        .catch(() => {});
}

function renderWatchlist(list) {
    const c = document.getElementById('watchlistContent');
    if (!list || list.length === 0) {
        c.innerHTML = '<div class="empty-msg">自选股为空，快添加一些吧</div>';
        return;
    }
    let html = '';
    list.forEach((s, i) => {
        html += '<div class="wl-item"><div class="info"><strong>' + escHTML(s.name) + '</strong><span>' + s.code + '</span></div>';
        html += '<div class="btns"><button class="view-btn" onclick="switchToAnalyze(\'' + s.code + '\')">分析</button>';
        html += '<button class="del-btn" onclick="delWatchlist(' + i + ')">删除</button></div></div>';
    });
    c.innerHTML = html;
}

function addWatchlist() {
    const code = document.getElementById('wlInput').value.trim();
    if (!code) return;
    fetch('/api/watchlist', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({code: code}) })
        .then(r => r.json())
        .then(data => { document.getElementById('wlInput').value = ''; renderWatchlist(data); });
}

function delWatchlist(idx) {
    fetch('/api/watchlist/' + idx, { method: 'DELETE' })
        .then(r => r.json())
        .then(data => renderWatchlist(data));
}

// 初始加载自选股
loadWatchlist();
</script>
</body>
</html>'''


if __name__ == '__main__':
    print("=" * 50)
    print("  A股智能分析系统 v2.0")
    print("  请打开浏览器访问: http://localhost:5000")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False)
