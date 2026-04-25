import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from dotenv import load_dotenv

load_dotenv()

# Create a custom config
config = DEFAULT_CONFIG.copy()
config["deep_think_llm"] = "claude-sonnet-4-6"  
config["quick_think_llm"] = "claude-sonnet-4-6"  
config["backend_url"] = "https://api.clawsocket.com/v1"
config["max_debate_rounds"] = 1  # Increase debate rounds

# Configure data vendors (default uses yfinance, no extra API keys needed)
config["data_vendors"] = {
    "core_stock_apis": "tencent",           
    "technical_indicators": "tencent",      
    "fundamental_data": "tencent",          
    "news_data": "tencent",                 
}

# Initialize with custom config
ta = TradingAgentsGraph(debug=True, config=config)

# forward propagate
_, decision = ta.propagate("600519", "2024-05-01")
print(decision)

# Memorize mistakes and reflect
# ta.reflect_and_remember(1000) # parameter is the position returns
