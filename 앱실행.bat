@echo off
chcp 65001 > nul
title 견적 단가 AI 분석 시스템
cd /d "%~dp0"
echo.
echo  =====================================================
echo   협력사 견적 단가 AI 비교분석 시스템 - 현대위아 구매본부
echo  =====================================================
echo.
echo  서버를 시작합니다... 잠시 기다려 주세요.
echo  브라우저가 자동으로 열립니다: http://localhost:8501
echo.
echo  종료하려면 이 창을 닫으세요.
echo.
python -m streamlit run app.py --server.port 8501 --server.headless false --browser.gatherUsageStats false
pause
