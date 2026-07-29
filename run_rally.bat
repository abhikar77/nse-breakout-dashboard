@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
streamlit run app_rally.py --server.port 8502
pause
