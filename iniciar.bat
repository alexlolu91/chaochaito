@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Creando entorno de Python...
  python -m venv .venv || (echo Instala Python 3.10+ desde python.org & pause & exit /b 1)
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check "curl_cffi>=0.7"
start "" http://127.0.0.1:5000
.venv\Scripts\python app.py
pause
