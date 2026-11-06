@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto prerequisites
where npm >nul 2>nul
if errorlevel 1 goto prerequisites
python -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 goto prerequisites
if not exist "%~dp0NetMind Agent\node_modules\.bin\vite.cmd" goto prerequisites

start "NetMind Backend" cmd /d /k "cd /d ""%~dp0backend"" && python -m uvicorn main:app --host 127.0.0.1 --port 8000"
start "NetMind Frontend" cmd /d /k "cd /d ""%~dp0NetMind Agent"" && npm run dev -- --host 127.0.0.1"
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:5173
exit /b 0

:prerequisites
echo Install the Python and Node.js dependencies using README.md before starting.
pause
exit /b 1
