import requests
import os

# Create a session with no proxy
session = requests.Session()
session.trust_env = False  # Don't use environment variables for proxy

# Direct URL test
url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
params = {
    "fields1": "f1,f2,f3,f4,f5,f6",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
    "ut": "7eea3edcaed734bea9cbfc24409ed989",
    "klt": "101",
    "fqt": "1",
    "secid": "1.600519",
    "beg": "20240101",
    "end": "20240501"
}

try:
    r = session.get(url, params=params, timeout=30)
    print("Status:", r.status_code)
    print("Content:", r.text[:500])
except Exception as e:
    print("Error:", e)