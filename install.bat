@echo off
chcp 65001 >nul
:: Nyx 夜神 —— 从源码仓库安装（Windows）
::
:: 一般用户直接：pip install nyx-memory（或带语义检索：pip install "nyx-memory[vector]"）
:: 这个脚本用于从克隆下来的仓库安装：
::   install.bat            基础安装（零依赖）
::   install.bat --vector   加语义检索（本地多语言模型，首次运行下载约 470MB）
setlocal
cd /d "%~dp0"

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ❌ 需要 Python 3.10 或更新版本，并且 python 在 PATH 里
    exit /b 1
)

set EXTRAS=
if "%~1"=="--vector" set EXTRAS=[vector]
python -m pip install ".%EXTRAS%"
if errorlevel 1 (
    echo ❌ pip 安装失败
    exit /b 1
)

echo.
echo ✅ 已安装 nyx-memory

:: Hermes：0.19.0 只按目录找记忆插件、不看 pip 入口点 —— nexsandglass_hermes install 找到 Hermes 用的 Python、
:: 装进去、放入口文件、设 memory.provider（旧版安装留下的插件目录也会报出来）
where hermes >nul 2>&1
if errorlevel 1 (
    echo    Hermes：装好 Hermes 之后运行 python -m nexsandglass_hermes install，然后重启 Hermes
) else (
    if "%~1"=="--vector" (python -m nexsandglass_hermes install --vector) else (python -m nexsandglass_hermes install)
)
echo    MCP：   python -m nexsandglass.interfaces.sandglass_mcp
echo    自检：  python -m nexsandglass.doctor
endlocal
