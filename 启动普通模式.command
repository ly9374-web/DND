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
# 虚拟环境位于 iCloud/网盘之外，避免被同步/物化破坏；$HOME 自动适配任意用户/任意 Mac
VENV_DIR="$HOME/.local/share/project-venvs/DND/.venv"
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

# 虚拟环境不可用（不存在/损坏/Python 版本过旧）时自动重建；venv 内没有用户数据
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_OK=0
if [[ -x "$VENV_PYTHON" ]] && "$VENV_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  VENV_OK=1
fi
if [[ "$VENV_OK" -eq 0 ]]; then
  if [[ -d "$VENV_DIR" ]]; then
    echo "虚拟环境不可用或 Python 版本过旧，正在重建..."
    rm -rf "$VENV_DIR"
  fi
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "找不到 python3。请先从 https://www.python.org/downloads/ 安装 Python 3.12+，然后重新双击本文件。"
    exit 1
  fi
  if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "Python 版本过低（需要 3.10+，推荐 3.12+）：$("$PYTHON_BIN" -V 2>&1)"
    echo "请从 https://www.python.org/downloads/ 安装新版后重新双击本文件。"
    exit 1
  fi
  echo "首次启动：正在创建虚拟环境..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# 锁定清单依赖需要 Python 3.12+；低于 3.12 自动改用 requirements.txt（pip 会自动选择兼容版本）
if ! "$VENV_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
  if [[ "$REQUIREMENTS_FILE" != "$PROJECT_DIR/requirements.txt" ]]; then
    echo "当前 Python 低于 3.12，锁定清单依赖需要 3.12+，改用 requirements.txt 安装兼容版本..."
    REQUIREMENTS_FILE="$PROJECT_DIR/requirements.txt"
  fi
fi

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

"$VENV_PYTHON" -m streamlit run "$APP_FILE" --server.port "$PORT" --server.headless false --server.showEmailPrompt false --browser.gatherUsageStats false
