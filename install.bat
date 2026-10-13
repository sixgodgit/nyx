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

:: v3.x 的旧安装脚本复制过的 Hermes 插件文件：留着会和现在的插件同时生效，同一句话可能被记两遍
if exist "%LOCALAPPDATA%\hermes\plugins\memory\nexsandglass" echo ⚠️  发现旧版安装留下的 %LOCALAPPDATA%\hermes\plugins\memory\nexsandglass ，请删除它
if exist "%LOCALAPPDATA%\hermes\plugins\sandglass" echo ⚠️  发现旧版安装留下的 %LOCALAPPDATA%\hermes\plugins\sandglass ，请删除它
if exist "%USERPROFILE%\.hermes\plugins\memory\nexsandglass" echo ⚠️  发现旧版安装留下的 %USERPROFILE%\.hermes\plugins\memory\nexsandglass ，请删除它
if exist "%USERPROFILE%\.hermes\plugins\sandglass" echo ⚠️  发现旧版安装留下的 %USERPROFILE%\.hermes\plugins\sandglass ，请删除它

echo.
echo ✅ 已安装 nyx-memory
echo    Hermes：config.yaml 里设 memory.provider: nyx，然后重启 Hermes
echo    MCP：   python -m nexsandglass.interfaces.sandglass_mcp
echo    自检：  python -m nexsandglass.doctor
endlocal
