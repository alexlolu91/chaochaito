#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Creando entorno de Python..."
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt || exit 1
fi
( sleep 2; (xdg-open http://127.0.0.1:5000 || open http://127.0.0.1:5000) >/dev/null 2>&1 ) &
.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt
.venv/bin/pip install -q --disable-pip-version-check "curl_cffi>=0.7" 2>/dev/null
.venv/bin/python app.py
