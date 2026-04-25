import pandas as pd
import akshare as ak
from datetime import datetime, timedelta
from typing import Optional
import os
import requests

os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'

# Create a custom session without proxy
_session = requests.Session()
_session.trust_env = False

# Monkey patch requests to use our session
import requests.api
_original_get = requests.api.get
def _patched_get(url, **kwargs):
    kwargs.setdefault('session', _session)
    return _original_get(url, **kwargs)
requests.api.get = _patched_get

def _convert_akshare_time(df: pd.DataFrame) -> pd.DataFrame:
    """Convert akshare date column to standard format."""
    if df is None or df.empty:
        return df
    if '日期' in df.columns:
        df['日期'] = pd.to_datetime(df['日期'])
        df.set_index('日期', inplace=True)
    return df

def get_AKShare_stock_data(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Get A股 stock data using akshare."""
    try:
        stock = symbol.replace('.SH', '').replace('.SZ', '')
        if not stock.isdigit():
            stock = symbol
        
        df = ak.stock_zh_a_hist(symbol=stock, start_date=start_date.replace('-', ''), end_date=end_date.replace('-', ''), adjust="qfq")
        df = _convert_akshare_time(df)
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        raise Exception(f"akshare get stock data failed: {str(e)}")

def get_AKShare_indicators(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int = 30
) -> pd.DataFrame:
    """Get technical indicators using akshare."""
    try:
        stock = symbol.replace('.SH', '').replace('.SZ', '')
        if not stock.isdigit():
            stock = symbol
        
        end_date = datetime.strptime(curr_date, "%Y-%m-%d")
        start_date = (end_date - timedelta(days=look_back_days * 2)).strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(symbol=stock, start_date=start_date, end_date=end_str, adjust="qfq")
        
        if df is None or df.empty:
            return pd.DataFrame()
        
        df.columns = [c.lower() for c in df.columns]
        
        if '收盘' in df.columns:
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
                return pd.DataFrame()
            
            return result.tail(look_back_days)
        
        return pd.DataFrame()
    except Exception as e:
        raise Exception(f"akshare get indicators failed: {str(e)}")

def get_AKShare_fundamentals(symbol: str) -> dict:
    """Get A股 fundamental data using akshare."""
    try:
        stock = symbol.replace('.SH', '').replace('.SZ', '')
        if not stock.isdigit():
            stock = symbol
            
        df = ak.stock_individual_info_em(symbol=stock)
        result = {}
        for _, row in df.iterrows():
            result[row['item']] = row['value']
        return result
    except Exception as e:
        raise Exception(f"akshare get fundamentals failed: {str(e)}")

def get_AKShare_news(symbol: str = None) -> list:
    """Get A股 news using akshare."""
    try:
        df = ak.stock_news_em(symbol=symbol if symbol else "A股")
        news_list = []
        for _, row in df.head(10).iterrows():
            news_list.append({
                'title': row.get('新闻标题', ''),
                'content': row.get('新闻内容', ''),
                'date': str(row.get('发布时间', ''))
            })
        return news_list
    except Exception as e:
        raise Exception(f"akshare get news failed: {str(e)}")

def get_AKShare_global_news() -> list:
    """Get global news using akshare."""
    return get_AKShare_news()

def get_AKShare_balance_sheet(symbol: str) -> pd.DataFrame:
    """Get balance sheet using akshare."""
    try:
        stock = symbol.replace('.SH', '').replace('.SZ', '')
        if not stock.isdigit():
            stock = symbol
        df = ak.stock_balance_sheet_em(symbol=stock, indicator="合并报表")
        return df
    except Exception as e:
        raise Exception(f"akshare get balance sheet failed: {str(e)}")

def get_AKShare_cashflow(symbol: str) -> pd.DataFrame:
    """Get cashflow using akshare."""
    try:
        stock = symbol.replace('.SH', '').replace('.SZ', '')
        if not stock.isdigit():
            stock = symbol
        df = ak.stock_cash_flow_em(symbol=stock, indicator="合并报表")
        return df
    except Exception as e:
        raise Exception(f"akshare get cashflow failed: {str(e)}")

def get_AKShare_income_statement(symbol: str) -> pd.DataFrame:
    """Get income statement using akshare."""
    try:
        stock = symbol.replace('.SH', '').replace('.SZ', '')
        if not stock.isdigit():
            stock = symbol
        df = ak.stock_income_em(symbol=stock, indicator="合并报表")
        return df
    except Exception as e:
        raise Exception(f"akshare get income statement failed: {str(e)}")