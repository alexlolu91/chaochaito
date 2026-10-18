#!/bin/bash
# Doble clic en macOS para arrancar ChaoChaito Quiniela
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Falta Python 3. Instálalo desde https://www.python.org/downloads/ y vuelve a abrir este archivo."
  read -n 1 -s -r -p "Pulsa una tecla para cerrar"; exit 1
fi
if [ ! -d .venv ]; then
  echo "Preparando la app (solo la primera vez)..."
  python3 -m venv .venv || { echo "No se pudo crear el entorno"; read -n 1 -s -r; exit 1; }
fi
.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt || { echo "Error instalando dependencias"; read -n 1 -s -r; exit 1; }
.venv/bin/pip install -q --disable-pip-version-check "curl_cffi>=0.7" 2>/dev/null || echo "Aviso: no se pudo instalar curl_cffi (lectura de Loterías menos fiable)"
( sleep 2; open http://127.0.0.1:5000 ) &
echo "ChaoChaito Quiniela en marcha. Cierra esta ventana para pararla."
.venv/bin/python app.py
