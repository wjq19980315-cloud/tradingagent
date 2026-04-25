@echo off
chcp 65001 >nul
title A股智能分析系统
echo ============================================
echo   A股智能分析系统 - 启动中...
echo ============================================
echo.

set NO_PROXY=*
set PYTHONUTF8=1

cd /d "D:\trading agent\TradingAgents"

:: 启动Flask后台
start /B python app.py

:: 等待3秒让服务启动
timeout /t 3 /nobreak >nul

:: 打开浏览器
start http://localhost:5000

echo   服务已启动！浏览器已打开。
echo   如需关闭，请直接关闭此窗口。
echo ============================================

:: 保持窗口运行
pause >nul
