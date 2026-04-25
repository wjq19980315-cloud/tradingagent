@echo off
cd /d "%~dp0"
python -m streamlit run stock_app.py --server.port 8501 --server.address localhost
pause