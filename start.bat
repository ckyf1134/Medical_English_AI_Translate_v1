@echo off
chcp 65001 >nul
setlocal EnableExtensions

rem v1.0 is self-contained: runtime, models and vendor all live inside this folder.
set "ROOT=%~dp0"
set "PYTHON_EXE=%ROOT%runtime\python\python.exe"
set "WEBUI_APP=%ROOT%webui\app.py"
set "MODEL_STORE=%ROOT%models\ollama"
set "OLLAMA_EXE=%ROOT%runtime\ollama\ollama.exe"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] 找不到本文件夹内的 Python 运行时：
    echo         %PYTHON_EXE%
    echo         说明 v1.0 文件夹不完整，请重新完整复制。
    pause
    exit /b 1
)
if not exist "%WEBUI_APP%" (
    echo [ERROR] 找不到 WebUI：%WEBUI_APP%
    pause
    exit /b 1
)
if not exist "%MODEL_STORE%" (
    echo [ERROR] 找不到模型仓库：%MODEL_STORE%
    echo         说明 v1.0 文件夹不完整，请重新完整复制约 18GB。
    pause
    exit /b 1
)
if not exist "%OLLAMA_EXE%" (
    echo [ERROR] 找不到自带的 Ollama：%OLLAMA_EXE%
    pause
    exit /b 1
)

set "OLLAMA_MODELS=%MODEL_STORE%"
set "OLLAMA_LIBRARY_PATH=%ROOT%runtime\ollama\lib\ollama"
set "OLLAMA_HOST=127.0.0.1:11434"
set "OLLAMA_MAX_LOADED_MODELS=1"
set "OLLAMA_NUM_PARALLEL=1"
set "PYTHONUTF8=1"

netstat -ano | findstr /R /C:":11434 .*LISTENING" >nul 2>&1
if errorlevel 1 (
    echo.
    echo 127.0.0.1:11434 上没有 Ollama 在运行。
    echo 请先在另一个窗口手动启动它（本脚本故意不自动拉起后台进程，避免被安全软件误报）：
    echo.
    echo     cd /d "%ROOT%"
    echo     set OLLAMA_MODELS=%MODEL_STORE%
    echo     set OLLAMA_LIBRARY_PATH=%ROOT%runtime\ollama\lib\ollama
    echo     runtime\ollama\ollama.exe serve
    echo.
    echo 按任意键仍然启动 WebUI（界面会显示“Ollama 未运行”）...
    pause >nul
)

echo 正在启动 v1.0 WebUI，浏览器将打开 http://127.0.0.1:8765
echo.
"%PYTHON_EXE%" "%WEBUI_APP%" %*

echo.
echo WebUI 已停止。
pause
