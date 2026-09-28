@echo off
chcp 65001 >nul
echo ============================================
echo   读码平台桥接器 - Windows 打包脚本
echo ============================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python, 请先安装 Python 3.8+
    pause
    exit /b 1
)

REM 安装依赖
echo [1/3] 安装依赖包...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

REM 打包
echo [2/3] 正在打包 (PyInstaller)...
pyinstaller --onefile --windowed --name "CodeBridge" ^
    --clean --noconfirm ^
    --add-data "main.py;." ^
    main.py

if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo [3/3] 打包完成!
echo.
echo   生成文件: dist\CodeBridge.exe
echo   配置文件: 运行后自动在同目录生成 config.json
echo.
pause
