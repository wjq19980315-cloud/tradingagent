import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
import requests

_session = requests.Session()

def _parse_tencent_kline(text: str, symbol: str) -> pd.DataFrame:
    """Parse Tencent API response to DataFrame."""
    import re
    var_match = re.match(r'kline_dayqfq=(.+)', text)
    if var_match:
        import json
        data = json.loads(var_match.group(1))
        if data.get('code') != 0:
            return pd.DataFrame()
        
        qfqday = data.get('data', {}).get(symbol, {}).get('qfqday', [])
        if not qfqday:
            return pd.DataFrame()
        
        # Handle variable length rows (some have extra data)
        rows = []
        for row in qfqday:
            if isinstance(row, list) and len(row) >= 6:
                rows.append(row[:6])  # Take first 6 columns
        
        df = pd.DataFrame(rows, columns=['日期', '开盘', '收盘', '最高', '最低', '成交量'])
        
        # Parse numeric columns
        for col in ['开盘', '收盘', '最高', '最低', '成交量']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        df['日期'] = pd.to_datetime(df['日期'])
        df.set_index('日期', inplace=True)
        return df
    
    return pd.DataFrame()

def get_Tencent_stock_data(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Get A股 stock data using Tencent API."""
    try:
        # Convert symbol to sh/sz format
        stock = symbol.replace('.SH', '').replace('.SZ', '')
        if stock.isdigit():
            if stock.startswith('6'):
                market = 'sh' + stock
            else:
                market = 'sz' + stock
        else:
            market = symbol.lower()
        
        # Calculate days for lookback (从今天往回算)
        today = datetime.today()
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        days = max((today - start_dt).days + 30, 365)  # 至少请求一年数据
        
        url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
        params = {
            '_var': 'kline_dayqfq',
            'param': f'{market},day,,,{days},qfq'
        }
        
        r = _session.get(url, params=params, timeout=30)
        r.raise_for_status()
        
        df = _parse_tencent_kline(r.text, market)
        
        if not df.empty:
            # Filter by date range
            df = df[(df.index >= start_date) & (df.index <= end_date)]
        
        return df
        
    except Exception as e:
        raise Exception(f"Tencent get stock data failed: {str(e)}")

def get_Tencent_indicators(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int = 30
) -> str:
    """Get technical indicators using Tencent API data."""
    try:
        # Get stock data first
        stock = symbol.replace('.SH', '').replace('.SZ', '')
        if stock.isdigit():
            if stock.startswith('6'):
                market = 'sh' + stock
            else:
                market = 'sz' + stock
        else:
            market = symbol.lower()
        
        end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = (end_dt - timedelta(days=look_back_days * 2)).strftime("%Y-%m-%d")
        
        df = get_Tencent_stock_data(symbol, start_dt, curr_date)
        
        if df is None or df.empty:
            return f"No data available for {symbol}"
        
        close = df['收盘'].astype(float)
        
        if indicator == 'close_10_ema':
            result = close.ewm(span=10, adjust=False).mean()
        elif indicator == 'close_50_sma':
            result = close.rolling(window=50).mean()
        elif indicator == 'close_200_sma':
            result = close.rolling(window=200).mean()
        elif indicator == 'rsi':
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            result = 100 - (100 / (1 + rs))
        elif indicator == 'macd':
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            result = ema12 - ema26
        elif indicator == 'macdh':
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd = ema12 - ema26
            signal = macd.ewm(span=9, adjust=False).mean()
            result = macd - signal
        elif indicator == 'boll_ub':
            sma = close.rolling(window=20).mean()
            std = close.rolling(window=20).std()
            result = sma + (std * 2)
        elif indicator == 'atr':
            high = df['最高'].astype(float)
            low = df['最低'].astype(float)
            close_prev = close.shift(1)
            tr1 = high - low
            tr2 = (high - close_prev).abs()
            tr3 = (low - close_prev).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            result = tr.rolling(window=14).mean()
        else:
            return f"Unknown indicator: {indicator}"
        
        result_series = result.tail(look_back_days)
        return f"{indicator} for {symbol}:\n{result_series.to_string()}"
        
    except Exception as e:
        raise Exception(f"Tencent get indicators failed: {str(e)}")

def get_Tencent_fundamentals(symbol: str) -> dict:
    """Get A股 fundamental data using Tencent API."""
    try:
        stock = symbol.replace('.SH', '').replace('.SZ', '')
        if stock.isdigit():
            if stock.startswith('6'):
                market = 'sh' + stock
            else:
                market = 'sz' + stock
        else:
            market = symbol.lower()
        
        url = 'https://web.ifzq.gtimg.cn/appstock/app/fundamental/info'
        params = {'param': f'{market},moneyflow'}
        
        r = _session.get(url, params=params, timeout=30)
        import json
        data = json.loads(r.text)
        
        result = {}
        if 'data' in data and market in data['data']:
            info = data['data'][market].get('data', {})
            result = info
        
        return result
    except Exception as e:
        raise Exception(f"Tencent get fundamentals failed: {str(e)}")

def get_Tencent_balance_sheet(symbol: str) -> pd.DataFrame:
    raise Exception("Tencent balance sheet not implemented")

def get_Tencent_cashflow(symbol: str) -> pd.DataFrame:
    raise Exception("Tencent cashflow not implemented")

def get_Tencent_income_statement(symbol: str) -> pd.DataFrame:
    raise Exception("Tencent income statement not implemented")

def get_Tencent_news(ticker: str = None, start_date: str = None, end_date: str = None) -> list:
    """Get A股 news using akshare."""
    try:
        import akshare as ak
        
        stock = ticker.replace('.SH', '').replace('.SZ', '') if ticker else '600519'
        if not stock.isdigit():
            stock = '600519'
            
        df = ak.stock_news_em(symbol=stock)
        news_list = []
        for _, row in df.head(10).iterrows():
            news_list.append({
                'title': row.get('新闻标题', ''),
                'content': row.get('新闻内容', ''),
                'date': str(row.get('发布时间', ''))
            })
        
        return news_list[:10]
    except Exception as e:
        raise Exception(f"Tencent (akshare) get news failed: {str(e)}")

def get_Tencent_global_news() -> list:
    return get_Tencent_news(ticker=None)