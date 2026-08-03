@echo off
chcp 65001 >nul
echo ╔══════════════════════════════════╗
echo ║  NexSandglass V3.4.1 安装程序    ║
echo ║  沙漏记忆 · 跨会话 · 零外部依赖  ║
echo ╚══════════════════════════════════╝
echo.

:: 1. 创建数据目录
if not exist "%USERPROFILE%\.neurobase\scripts" mkdir "%USERPROFILE%\.neurobase\scripts"
if not exist "%USERPROFILE%\.neurobase\persona" mkdir "%USERPROFILE%\.neurobase\persona"
if not exist "%USERPROFILE%\.neurobase\archive" mkdir "%USERPROFILE%\.neurobase\archive"
if not exist "%LOCALAPPDATA%\hermes\plugins\memory\nexsandglass" mkdir "%LOCALAPPDATA%\hermes\plugins\memory\nexsandglass"
if not exist "%LOCALAPPDATA%\hermes\plugins\sandglass" mkdir "%LOCALAPPDATA%\hermes\plugins\sandglass"
echo ✅ 数据目录已创建

:: 2. 安装 Python 包
python -m pip install "%~dp0"
if errorlevel 1 (
    echo ⚠️ pip 安装失败，请手动执行：python -m pip install "%~dp0"
) else (
    echo ✅ NexSandglass Python 包已安装
)

:: 3. 部署 Hermes 插件
copy /Y "%~dp0nexsandglass\core\memory_provider.py" "%LOCALAPPDATA%\hermes\plugins\memory\nexsandglass\__init__.py" >nul 2>&1
copy /Y "%~dp0nexsandglass\interfaces\plugin.py" "%LOCALAPPDATA%\hermes\plugins\sandglass\__init__.py" >nul 2>&1
echo ✅ Hermes 插件已部署（memory_provider + gateway）

echo.
echo 🚀 重启 Hermes Gateway 即可自动落沙
echo.
pause
