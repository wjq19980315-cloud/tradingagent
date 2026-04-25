"""统计查询一个股票代码的各项消耗"""
import requests, json, time, sys

s = requests.Session()
s.trust_env = False

code = sys.argv[1] if len(sys.argv) > 1 else '300308'

print(f"======= 股票 {code} 查询消耗统计 =======\n")

# ─── 1. 前端 API 返回数据量 ───
print("【1】前端 Flask API 数据量统计")
print("-" * 45)

t0 = time.time()
r = s.get(f'http://localhost:5000/api/analyze?code={code}', timeout=60)
elapsed = time.time() - t0

if r.status_code != 200:
    print(f"  请求失败: {r.status_code}")
    exit(1)

raw = r.content
data = r.json()

total_bytes = len(raw)
total_chars = len(r.text)

# 拆分各部分大小
analysis = data.get('analysis', {})
news = data.get('news', [])
fundamentals = data.get('fundamentals', {})

analysis_bytes = len(json.dumps(analysis, ensure_ascii=False).encode('utf-8'))
news_bytes = len(json.dumps(news, ensure_ascii=False).encode('utf-8'))
fund_bytes = len(json.dumps(fundamentals, ensure_ascii=False).encode('utf-8'))

# 分析 analysis 内部各子项
kline_bytes = len(json.dumps(analysis.get('kline_data', []), ensure_ascii=False).encode('utf-8'))
indicator_bytes = len(json.dumps(analysis.get('indicator_data', {}), ensure_ascii=False).encode('utf-8'))
bspoints_bytes = len(json.dumps(analysis.get('buy_sell_points', []), ensure_ascii=False).encode('utf-8'))
recent_bytes = len(json.dumps(analysis.get('recent_rows', []), ensure_ascii=False).encode('utf-8'))
signals_bytes = len(json.dumps(analysis.get('signals', []), ensure_ascii=False).encode('utf-8'))
advice_bytes = len(json.dumps(analysis.get('beginner_advice', {}), ensure_ascii=False).encode('utf-8'))
timing_bytes = len(json.dumps(analysis.get('buy_timing', []), ensure_ascii=False).encode('utf-8'))
rest_bytes = analysis_bytes - kline_bytes - indicator_bytes - bspoints_bytes - recent_bytes - signals_bytes - advice_bytes - timing_bytes

# Token 估算 (中文约 1.5 字符/token, 英文约 4 字符/token, JSON混合约 3 字符/token)
est_tokens_json = total_chars // 3

print(f"  股票名称: {data.get('name')} ({code})")
print(f"  数据来源: {data.get('source')}")
print(f"  请求耗时: {elapsed:.2f} 秒")
print()
print(f"  总返回数据:")
print(f"    字节数:  {total_bytes:,} bytes ({total_bytes/1024:.1f} KB)")
print(f"    字符数:  {total_chars:,}")
print(f"    等效token(估): ~{est_tokens_json:,} tokens")
print()
print(f"  各模块数据量:")
print(f"    analysis 合计:    {analysis_bytes:>8,} bytes ({analysis_bytes/1024:.1f} KB)")
print(f"      ├ K线数据(120日): {kline_bytes:>8,} bytes")
print(f"      ├ 技术指标数据:   {indicator_bytes:>8,} bytes")
print(f"      ├ 买卖信号点:     {bspoints_bytes:>8,} bytes")
print(f"      ├ 近期行情(30日): {recent_bytes:>8,} bytes")
print(f"      ├ 信号标签:       {signals_bytes:>8,} bytes")
print(f"      ├ 新手建议:       {advice_bytes:>8,} bytes")
print(f"      ├ 买入时机:       {timing_bytes:>8,} bytes")
print(f"      └ 指标数值等:     {rest_bytes:>8,} bytes")
print(f"    news(新闻):       {news_bytes:>8,} bytes ({len(news)} 条)")
print(f"    fundamentals:     {fund_bytes:>8,} bytes ({len(fundamentals)} 项)")
print()
print(f"  数据条数:")
print(f"    K线数据:      {len(analysis.get('kline_data', []))} 条")
print(f"    买卖信号:     {len(analysis.get('buy_sell_points', []))} 条")
print(f"    近期行情:     {len(analysis.get('recent_rows', []))} 条")
print(f"    买入时机:     {len(analysis.get('buy_timing', []))} 条")
print(f"    技术信号:     {len(analysis.get('signals', []))} 条")
print(f"    新闻:         {len(news)} 条")

# 统计网络请求次数 (根据代码分析)
print()
print(f"  底层网络请求(估算):")
print(f"    K线数据接口:    1~2 次 (东方财富/腾讯容错)")
print(f"    股票名称接口:   1~3 次 (三源容错)")
print(f"    新闻接口:       1~2 次 (akshare/东方财富)")
print(f"    基本面接口:     1~2 次 (akshare/东方财富)")
print(f"    合计:           约 4~9 次 HTTP 请求")

# ─── 2. LLM 多Agent流程 token 消耗估算 ───
print()
print()
print("【2】LLM 多Agent完整分析流程 token 消耗估算")
print("-" * 45)
print("  (基于代码分析的理论估算，实际依赖模型和数据量)")
print()

# 分析各个 Agent 的 prompt 和预期输出
agents = [
    {
        'name': '市场分析师 (Market Analyst)',
        'stage': 'Stage I - 分析团队',
        'input_desc': 'System Prompt + 股票OHLCV数据 + 技术指标数据',
        'input_tokens': 3000,
        'output_tokens': 1500,
        'tool_calls': 3,
    },
    {
        'name': '社交媒体分析师 (Social Media Analyst)',
        'stage': 'Stage I - 分析团队',
        'input_desc': 'System Prompt + 社交媒体/新闻情绪数据',
        'input_tokens': 2500,
        'output_tokens': 1200,
        'tool_calls': 2,
    },
    {
        'name': '新闻分析师 (News Analyst)',
        'stage': 'Stage I - 分析团队',
        'input_desc': 'System Prompt + 公司新闻 + 宏观新闻',
        'input_tokens': 3000,
        'output_tokens': 1500,
        'tool_calls': 2,
    },
    {
        'name': '基本面分析师 (Fundamentals Analyst)',
        'stage': 'Stage I - 分析团队',
        'input_desc': 'System Prompt + 财务报表 + 公司信息',
        'input_tokens': 3500,
        'output_tokens': 1500,
        'tool_calls': 4,
    },
    {
        'name': '看多研究员 (Bull Researcher)',
        'stage': 'Stage II - 研究辩论',
        'input_desc': 'System Prompt + 4份分析报告 + 对方观点',
        'input_tokens': 5000,
        'output_tokens': 2000,
        'tool_calls': 0,
    },
    {
        'name': '看空研究员 (Bear Researcher)',
        'stage': 'Stage II - 研究辩论',
        'input_desc': 'System Prompt + 4份分析报告 + 对方观点',
        'input_tokens': 5000,
        'output_tokens': 2000,
        'tool_calls': 0,
    },
    {
        'name': '研究经理 (Research Manager)',
        'stage': 'Stage II - 研究辩论',
        'input_desc': 'System Prompt + 多空辩论记录',
        'input_tokens': 6000,
        'output_tokens': 2000,
        'tool_calls': 0,
    },
    {
        'name': '交易员 (Trader)',
        'stage': 'Stage III - 交易',
        'input_desc': 'System Prompt + 投资计划 + 历史记忆',
        'input_tokens': 4000,
        'output_tokens': 1500,
        'tool_calls': 0,
    },
    {
        'name': '激进风控 (Aggressive)',
        'stage': 'Stage IV - 风控辩论',
        'input_desc': 'System Prompt + 交易提案 + 对方观点',
        'input_tokens': 3500,
        'output_tokens': 1500,
        'tool_calls': 0,
    },
    {
        'name': '保守风控 (Conservative)',
        'stage': 'Stage IV - 风控辩论',
        'input_desc': 'System Prompt + 交易提案 + 对方观点',
        'input_tokens': 3500,
        'output_tokens': 1500,
        'tool_calls': 0,
    },
    {
        'name': '中性风控 (Neutral)',
        'stage': 'Stage IV - 风控辩论',
        'input_desc': 'System Prompt + 交易提案 + 对方观点',
        'input_tokens': 3500,
        'output_tokens': 1500,
        'tool_calls': 0,
    },
    {
        'name': '投资组合经理 (Portfolio Manager)',
        'stage': 'Stage V - 最终决策',
        'input_desc': 'System Prompt + 所有报告 + 风控意见',
        'input_tokens': 8000,
        'output_tokens': 2500,
        'tool_calls': 0,
    },
]

total_input = 0
total_output = 0
total_tools = 0
current_stage = ''
for a in agents:
    if a['stage'] != current_stage:
        current_stage = a['stage']
        print(f"  {current_stage}")
    total_input += a['input_tokens']
    total_output += a['output_tokens']
    total_tools += a['tool_calls']
    total = a['input_tokens'] + a['output_tokens']
    print(f"    {a['name']}")
    print(f"      输入: ~{a['input_tokens']:,} tokens | 输出: ~{a['output_tokens']:,} tokens | 合计: ~{total:,} tokens")
    if a['tool_calls']:
        print(f"      工具调用: {a['tool_calls']} 次")

total_all = total_input + total_output

print()
print(f"  ─── 汇总 (1轮辩论配置) ───")
print(f"    总 Input tokens:   ~{total_input:,}")
print(f"    总 Output tokens:  ~{total_output:,}")
print(f"    总 Token 消耗:     ~{total_all:,}")
print(f"    总工具调用:        {total_tools} 次")
print()

# 费用估算
print(f"  ─── 费用估算 ───")
models = [
    ('Claude Sonnet 4', 3.0, 15.0),
    ('GPT-4o', 2.5, 10.0),
    ('GPT-4o-mini', 0.15, 0.6),
    ('DeepSeek-V3', 0.27, 1.10),
    ('Qwen-Plus', 0.13, 0.52),
]
for name, inp_price, out_price in models:
    cost = (total_input / 1_000_000 * inp_price) + (total_output / 1_000_000 * out_price)
    print(f"    {name:20s}:  ${cost:.4f} (~{cost*7.2:.2f} 元)")

print()
print()
print("【3】总结对比")
print("-" * 45)
print(f"  前端技术分析 (Flask API):")
print(f"    不消耗 LLM token，纯本地计算")
print(f"    返回数据量: {total_bytes/1024:.1f} KB")
print(f"    耗时: {elapsed:.1f} 秒")
print(f"    费用: 0 元")
print()
print(f"  LLM 多Agent完整分析 (main.py):")
print(f"    消耗约 {total_all:,} tokens")
print(f"    耗时: 约 60~180 秒 (取决于模型速度)")
print(f"    费用: 约 $0.01~$0.36 (取决于模型)")
