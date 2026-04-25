import os
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        del os.environ[k]

import akshare as ak
df = ak.stock_zh_a_hist(symbol='600519', start_date='20240101', end_date='20240501', adjust='qfq')
print('Success:', df.shape)