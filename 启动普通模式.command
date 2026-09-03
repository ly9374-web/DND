#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
APP_FILE="streamlit_app.py"
REQUIREMENTS_FILE="$PROJECT_DIR/requirements.txt"
# 精确版本锁定清单存在时优先使用（换机重装可还原一致的依赖版本）
if [[ -f "$PROJECT_DIR/requirements.lock.txt" ]]; then
  REQUIREMENTS_FILE="$PROJECT_DIR/requirements.lock.txt"
fi
# 虚拟环境位于 iCloud 之外，避免被 iCloud 同步/物化破坏
VENV_DIR="/Users/jason/.local/share/project-venvs/DND/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
START_PORT="${STREAMLIT_PORT:-8502}"
PORT="$START_PORT"

pause_on_error() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    echo
    echo "Streamlit startup failed. See the message above for details."
    if [[ -t 0 ]]; then
      read -r -p "Press Enter to close this window..."
    fi
  fi
}
trap pause_on_error EXIT

if ! [[ "$START_PORT" =~ ^[0-9]+$ ]] || [[ "$START_PORT" -lt 1 || "$START_PORT" -gt 65535 ]]; then
  echo "Invalid STREAMLIT_PORT: $START_PORT"
  exit 1
fi

cd "$PROJECT_DIR"

if [[ ! -f "$APP_FILE" ]]; then
  echo "Cannot find $APP_FILE in $PROJECT_DIR"
  exit 1
fi

export VS_IMAGE_NOVEL_DATA_DIR="$PROJECT_DIR/.python_app_data"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Cannot find python3. Please install Python 3 first."
    exit 1
  fi

  echo "Creating virtual environment in .venv..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
NEED_INSTALL=0
if [[ ! -f "$VENV_DIR/.requirements-installed" ]]; then
  NEED_INSTALL=1
elif [[ "$REQUIREMENTS_FILE" -nt "$VENV_DIR/.requirements-installed" ]]; then
  NEED_INSTALL=1
elif ! "$VENV_PYTHON" -m streamlit --version >/dev/null 2>&1; then
  NEED_INSTALL=1
fi

if [[ "$NEED_INSTALL" -eq 1 ]]; then
  echo "Installing Python dependencies..."
  "$VENV_PYTHON" -m pip install --upgrade pip
  if ! "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS_FILE"; then
    if [[ "$REQUIREMENTS_FILE" != "$PROJECT_DIR/requirements.txt" ]]; then
      echo "锁定清单安装失败，回退到 requirements.txt（版本可能与原环境略有差异）..."
      "$VENV_PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt"
    else
      exit 1
    fi
  fi
  touch "$VENV_DIR/.requirements-installed"
fi

if command -v lsof >/dev/null 2>&1; then
  while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
    if [[ "$PORT" -ge 65535 ]]; then
      echo "Cannot find an available port from $START_PORT to 65535."
      exit 1
    fi
    PORT=$((PORT + 1))
  done
fi

if [[ "$PORT" != "$START_PORT" ]]; then
  echo "端口 $START_PORT 已被占用，自动改用端口 ${PORT}。"
fi

echo
echo "Starting independent ordinary Streamlit edition..."
echo "Project: $PROJECT_DIR"
echo "Data: $VS_IMAGE_NOVEL_DATA_DIR"
echo "Local URL: http://localhost:$PORT"
echo

"$VENV_PYTHON" -m streamlit run "$APP_FILE" --server.port "$PORT" --server.headless false
