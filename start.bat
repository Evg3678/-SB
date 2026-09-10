@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo SB environment is missing. Restore .venv and install requirements.txt.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -B -c "import streamlit, pandas, plotly, numpy, openpyxl, tzdata"
if errorlevel 1 (
    echo SB dependencies are missing. Install requirements.txt into the SB .venv.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -B -c "import socket,sys; s=socket.socket(); s.settimeout(2); busy=s.connect_ex(('127.0.0.1',8504))==0; s.close(); sys.exit(1 if busy else 0)"
if errorlevel 1 (
    echo Port 8504 is already in use. Open http://127.0.0.1:8504/ in your browser.
    pause
    exit /b 0
)
"%~dp0.venv\Scripts\python.exe" -m streamlit run "%~dp0app.py" --server.port 8504 --server.address 127.0.0.1 --server.headless false
pause
