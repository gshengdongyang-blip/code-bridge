#!/bin/bash
set -e

echo "============================================"
echo "  读码平台桥接器 - macOS 打包脚本"
echo "============================================"
echo

# 检查 Python3
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 python3, 请先安装"
    exit 1
fi

# 安装依赖
echo "[1/3] 安装依赖包..."
pip3 install -r requirements.txt -q

# 打包
echo "[2/3] 正在打包 (PyInstaller)..."
pyinstaller --onefile --windowed --name "CodeBridge" \
    --clean --noconfirm \
    main.py

echo "[3/3] 打包完成!"
echo
echo "  生成文件: dist/CodeBridge (macOS 可执行)"
echo "  配置文件: 运行后自动在同目录生成 config.json"
echo
echo "  注意: macOS 打出的包只能在 macOS 运行。"
echo "  如需 Windows exe, 请在 Windows 机器上运行 build.bat。"
