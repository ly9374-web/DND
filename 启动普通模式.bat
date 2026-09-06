@echo off
chcp 65001 >nul
setlocal EnableExtensions

rem ============================================
rem  DND 普通模式独立版 - Windows 启动脚本
rem  双击即可启动；所有路径自动推导，可在任意电脑使用
rem  自动处理：虚拟环境创建、依赖安装、坏环境重建、
rem  端口占用换端口、跳过 Streamlit 首次运行时的邮箱询问
rem ============================================

rem %~dp0 = 本脚本所在目录（自带结尾反斜杠，兼容任意盘符和含空格/中文的路径）
set "PROJECT_DIR=%~dp0"
set "APP_FILE=streamlit_app.py"

rem 依赖清单：优先使用锁定版本清单，不存在时回退到 requirements.txt
set "REQUIREMENTS_FILE=%PROJECT_DIR%requirements.lock.txt"
if not exist "%REQUIREMENTS_FILE%" set "REQUIREMENTS_FILE=%PROJECT_DIR%requirements.txt"

rem 虚拟环境放在用户目录下（与 Mac 版位置一致，不随项目移动，换机自动重建）
set "VENV_DIR=%USERPROFILE%\.local\share\project-venvs\DND\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

set "START_PORT=8502"
if defined STREAMLIT_PORT set "START_PORT=%STREAMLIT_PORT%"
set "PORT=%START_PORT%"

cd /d "%PROJECT_DIR%"

if not exist "%APP_FILE%" (
  echo 在 %PROJECT_DIR% 下找不到 %APP_FILE%
  pause
  exit /b 1
)

set "VS_IMAGE_NOVEL_DATA_DIR=%PROJECT_DIR%.python_app_data"

rem ---------- 现有虚拟环境不可用（损坏 / Python 过旧）时删除重建，venv 内没有用户数据 ----------
if exist "%VENV_PYTHON%" goto venv_check
goto venv_rebuild

:venv_check
"%VENV_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto venv_ready
echo 虚拟环境不可用或 Python 版本过旧，正在重建...
rmdir /s /q "%VENV_DIR%"

:venv_rebuild
rem ---------- 选择可用的 Python 3（优先 py 启动器，其次 PATH 里的 python） ----------
set "PY_CMD="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"
if defined PY_CMD goto venv_create
python -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY_CMD=python"
if defined PY_CMD goto venv_create

echo 找不到可用的 Python 3。
echo 请从 https://www.python.org/downloads/ 安装 Python 3.12 或更新版本，
echo 安装时务必勾选 "Add python.exe to PATH"，装好后重新双击本文件。
pause
exit /b 1

:venv_create
rem Python 版本门槛：streamlit 1.59 需要 3.10+（推荐 3.12+）
%PY_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto venv_create2
echo Python 版本过低（需要 3.10+，推荐 3.12+）。当前版本：
%PY_CMD% --version
echo 请从 https://www.python.org/downloads/ 安装新版后重新双击本文件。
pause
exit /b 1

:venv_create2
echo 首次启动：正在创建虚拟环境（约 1 分钟）...
%PY_CMD% -m venv "%VENV_DIR%"
if exist "%VENV_PYTHON%" goto venv_ready
echo 虚拟环境创建失败，请确认 Python 安装正常。
pause
exit /b 1

:venv_ready
rem ---------- 判断是否需要（重新）安装依赖 ----------
if exist "%VENV_DIR%\.requirements-installed" goto check_streamlit
goto install_deps

:check_streamlit
"%VENV_PYTHON%" -m streamlit --version >nul 2>nul
if not errorlevel 1 goto skip_install

:install_deps
rem 锁定清单依赖需要 Python 3.12+；低于 3.12 改用 requirements.txt（pip 自动选兼容版本）
"%VENV_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
if not errorlevel 1 goto install_run
set "REQUIREMENTS_FILE=%PROJECT_DIR%requirements.txt"
echo 当前 Python 低于 3.12，锁定清单依赖需要 3.12+，改用 requirements.txt 安装兼容版本...

:install_run
echo 正在安装 Python 依赖（首次启动需要几分钟，取决于网速）...
"%VENV_PYTHON%" -m pip install --upgrade pip
"%VENV_PYTHON%" -m pip install -r "%REQUIREMENTS_FILE%"
if not errorlevel 1 goto install_done

rem 锁定清单安装失败时回退 requirements.txt
if "%REQUIREMENTS_FILE%"=="%PROJECT_DIR%requirements.txt" goto install_fail
echo 锁定清单安装失败，回退到 requirements.txt（版本可能与原环境略有差异）...
set "REQUIREMENTS_FILE=%PROJECT_DIR%requirements.txt"
"%VENV_PYTHON%" -m pip install -r "%REQUIREMENTS_FILE%"
if errorlevel 1 goto install_fail

:install_done
type nul > "%VENV_DIR%\.requirements-installed"
goto skip_install

:install_fail
echo 依赖安装失败，请检查网络后重新双击本文件。
pause
exit /b 1

:skip_install
rem ---------- 端口被占用时自动向上寻找可用端口 ----------
:checkport
netstat -an | findstr /c:":%PORT% " | findstr /i "LISTENING" >nul 2>nul
if errorlevel 1 goto portready
if %PORT% GEQ 65535 (
  echo 从 %START_PORT% 到 65535 之间找不到可用端口。
  pause
  exit /b 1
)
set /a PORT+=1
goto checkport

:portready
if not "%PORT%"=="%START_PORT%" echo 端口 %START_PORT% 已被占用，自动改用端口 %PORT%。

echo.
echo 正在启动独立普通模式 Streamlit ...
echo 项目目录: %PROJECT_DIR%
echo 数据目录: %VS_IMAGE_NOVEL_DATA_DIR%
echo 本地地址: http://localhost:%PORT%
echo.

rem --server.showEmailPrompt false：跳过 Streamlit 首次运行时的邮箱询问
"%VENV_PYTHON%" -m streamlit run "%APP_FILE%" --server.port %PORT% --server.headless false --server.showEmailPrompt false --browser.gatherUsageStats false
if errorlevel 1 pause
