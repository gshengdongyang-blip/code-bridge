#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读码平台桥接器 — 仓库对接服务
从海康读码平台接收 TCP 融合数据（单号/重量/尺寸），调用仓库 API 完成签收与出库。
"""

import os
import sys
import json
import time
import socket
import subprocess
import threading
import queue
import base64
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime
from typing import Optional, Dict, Any

import requests

# ═══════════════════════════════════════════════════════════════════
#  蓝色科技风主题色板 (全部 #RRGGBB, 无透明度)
# ═══════════════════════════════════════════════════════════════════

THEME = {
    "bg":            "#eef4fb",   # 浅蓝灰 主背景
    "card_bg":       "#ffffff",   # 白色 卡片背景
    "card_hover":    "#e3f0fc",   # 卡片悬停 浅蓝
    "border":        "#bbdefb",   # 浅蓝边框
    "accent":        "#2196f3",   # 主蓝
    "accent_hover":  "#42a5f5",   # 主蓝悬停
    "accent_dark":   "#1976d2",   # 主蓝按下
    "success":       "#00c853",   # 成功绿
    "danger":        "#ff1744",   # 失败红
    "warning":       "#ff9100",   # 警告橙
    "text":          "#1a2a3a",   # 主文字 深蓝灰
    "text_secondary":"#546e7a",   # 次要文字
    "text_muted":    "#90a4ae",   # 暗文字
    "input_bg":      "#ffffff",   # 输入框背景
    "input_fg":      "#1a2a3a",   # 输入框文字
}


# ═══════════════════════════════════════════════════════════════════
#  语音播报 (跨平台)
# ═══════════════════════════════════════════════════════════════════

def speak(text: str):
    """非阻塞语音播报"""
    def _speak():
        try:
            if sys.platform == "darwin":
                subprocess.run(["say", "-v", "Tingting", text],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform.startswith("win"):
                # Windows SAPI
                subprocess.run(
                    ["powershell", "-Command",
                     f"Add-Type -AssemblyName System.Speech; "
                     f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{text}')"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=0x08000000)  # CREATE_NO_WINDOW
        except Exception:
            pass
    threading.Thread(target=_speak, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════
#  默认配置
# ═══════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "api": {
        "base_url": "https://dev.fbm.feizuo.com/prod-api",
        "timeout": 10,
        "retry_count": 3
    },
    "tcp": {
        "mode": "server",
        "listen_port": 5000,
        "platform_host": "192.168.1.100",
        "platform_port": 5000
    },
    "data_format": {
        "type": "auto",
        "delimiter": ",",
        "fields": ["barcode", "weight", "length", "width", "height"],
        "record_delimiter": "\n"
    },
    "unit_conversion": {
        "weight_multiplier": 1000,
        "dimension_multiplier": 1
    },
    "workflow": {
        "auto_sign": True,
        "auto_weighing": True,
        "upload_image": False
    },
    "notify": {
        "voice_enabled": True
    }
}


# ═══════════════════════════════════════════════════════════════════
#  配置管理
# ═══════════════════════════════════════════════════════════════════

def get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_config() -> dict:
    path = os.path.join(get_app_dir(), "config.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # merge defaults to fill missing keys
            for section, defaults in DEFAULT_CONFIG.items():
                if section not in cfg:
                    cfg[section] = defaults
                elif isinstance(defaults, dict):
                    for k, v in defaults.items():
                        cfg[section].setdefault(k, v)
            return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    path = os.path.join(get_app_dir(), "config.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存配置失败: {e}")


# ═══════════════════════════════════════════════════════════════════
#  仓库 API 客户端
# ═══════════════════════════════════════════════════════════════════

class WarehouseApiClient:
    def __init__(self, cfg: dict):
        self.base_url = cfg["api"]["base_url"].rstrip("/")
        self.timeout = cfg["api"].get("timeout", 10)
        self.retry_count = cfg["api"].get("retry_count", 3)
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def send_package_info(self, tickets_num: str, image_b64: str = None) -> dict:
        data = {"ticketsNum": tickets_num}
        if image_b64:
            data["packageImgUrl"] = image_b64
        return self._post("/depot/depot/sendPackageInfo", data)

    def package_weighing(self, tickets_num: str, weight, length, width, height,
                         image_b64: str = None) -> dict:
        data = {"ticketsNum": tickets_num}
        if weight is not None:
            data["weight"] = weight
        if length is not None:
            data["length"] = length
        if width is not None:
            data["width"] = width
        if height is not None:
            data["height"] = height
        if image_b64:
            data["packageImgUrl"] = image_b64
        return self._post("/depot/depot/packageWeighing", data)

    def upload_package_img(self, tickets_num: str, image_b64: str) -> dict:
        data = {"ticketsNum": tickets_num, "packageImgUrl": image_b64}
        return self._post("/depot/depot/uploadPackageImg", data)

    def _post(self, path: str, data: dict) -> dict:
        url = self.base_url + path
        for attempt in range(self.retry_count):
            try:
                resp = self.session.post(url, json=data, timeout=self.timeout)
                return resp.json()
            except requests.exceptions.ConnectionError as e:
                err = f"连接失败: {e}"
            except requests.exceptions.Timeout:
                err = "请求超时"
            except Exception as e:
                err = str(e)
            if attempt < self.retry_count - 1:
                time.sleep(1)
        return {"code": 500, "msg": f"请求失败: {err}"}


# ═══════════════════════════════════════════════════════════════════
#  数据解析
# ═══════════════════════════════════════════════════════════════════

_JSON_FIELD_ALIASES = {
    "barcode": ["barcode", "Barcode", "code", "Code", "ticketsNum",
                "ticket", "no", "trackingNo", "tracking"],
    "weight":  ["weight", "Weight", "wei"],
    "length":  ["length", "Length", "len", "l", "L"],
    "width":   ["width", "Width", "wid", "w", "W"],
    "height":  ["height", "Height", "hei", "h", "H"],
}


def parse_record(record: str, cfg: dict) -> Optional[dict]:
    record = record.strip()
    if not record:
        return None

    fmt = cfg.get("data_format", {})
    fmt_type = fmt.get("type", "auto")

    # 尝试 JSON
    if fmt_type in ("auto", "json"):
        try:
            data = json.loads(record)
            if isinstance(data, dict):
                result = {}
                for canonical, aliases in _JSON_FIELD_ALIASES.items():
                    for alias in aliases:
                        if alias in data:
                            result[canonical] = str(data[alias])
                            break
                if result.get("barcode"):
                    return result
        except (json.JSONDecodeError, ValueError):
            pass

    # 分隔符解析
    if fmt_type in ("auto", "delimiter"):
        delimiter = fmt.get("delimiter", ",")
        fields = fmt.get("fields", ["barcode", "weight", "length", "width", "height"])
        parts = record.split(delimiter)
        result = {}
        for i, field in enumerate(fields):
            if i < len(parts):
                result[field] = parts[i].strip()
        if result.get("barcode"):
            return result

    return None


def _safe_float(val) -> Optional[float]:
    if not val:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════
#  TCP 桥接
# ═══════════════════════════════════════════════════════════════════

class TcpBridge:
    def __init__(self, cfg: dict, on_data, on_log):
        self.cfg = cfg
        self.on_data = on_data
        self.on_log = on_log
        self._running = False
        self._thread = None
        self._server_sock = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

    def _run(self):
        mode = self.cfg["tcp"].get("mode", "server")
        if mode == "server":
            self._run_server()
        else:
            self._run_client()

    def _run_server(self):
        port = self.cfg["tcp"].get("listen_port", 5000)
        while self._running:
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(("0.0.0.0", port))
                srv.listen(5)
                srv.settimeout(1.0)
                self._server_sock = srv
                self.on_log("INFO", f"TCP 服务端启动, 监听端口 {port}, 等待读码平台连接...")
                while self._running:
                    try:
                        conn, addr = srv.accept()
                        t = threading.Thread(target=self._handle,
                                             args=(conn, addr), daemon=True)
                        t.start()
                    except socket.timeout:
                        continue
                srv.close()
            except Exception as e:
                self.on_log("ERROR", f"TCP 服务端错误: {e}")
                time.sleep(3)

    def _run_client(self):
        host = self.cfg["tcp"].get("platform_host", "192.168.1.100")
        port = self.cfg["tcp"].get("platform_port", 5000)
        while self._running:
            try:
                self.on_log("INFO", f"连接读码平台 {host}:{port} ...")
                conn = socket.create_connection((host, port), timeout=10)
                self.on_log("INFO", "连接成功")
                self._handle(conn, (host, port))
            except Exception as e:
                self.on_log("ERROR", f"连接失败: {e}, 3 秒后重试")
                time.sleep(3)

    def _handle(self, conn, addr):
        self.on_log("INFO", f"连接建立: {addr[0]}:{addr[1]}")
        buf = b""
        record_delim = self.cfg["data_format"].get("record_delimiter", "\n")
        delim_byte = record_delim.encode("utf-8")
        try:
            while self._running:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while delim_byte in buf:
                    line, buf = buf.split(delim_byte, 1)
                    record = line.decode("utf-8", errors="replace").strip()
                    if record:
                        self.on_data(record)
        except Exception as e:
            self.on_log("ERROR", f"连接异常: {e}")
        finally:
            conn.close()
            self.on_log("INFO", f"连接断开: {addr[0]}:{addr[1]}")


# ═══════════════════════════════════════════════════════════════════
#  GUI 应用
# ═══════════════════════════════════════════════════════════════════

class BridgeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.api_client: Optional[WarehouseApiClient] = None
        self.tcp_bridge: Optional[TcpBridge] = None
        self.running = False
        self.stats = {"received": 0, "success": 0, "fail": 0}
        self.data_queue: queue.Queue = queue.Queue()
        self.log_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._load_config_to_ui()
        self._poll_queues()

    # ── UI 构建 ──────────────────────────────────────────────────────

    def _build_ui(self):
        self.root.title("读码平台桥接器")
        self.root.geometry("980x720")
        self.root.minsize(820, 620)
        self.root.configure(bg=THEME["bg"])

        style = ttk.Style()
        style.theme_use("clam")

        # ── 全局配色 ──
        style.configure(".", background=THEME["bg"], foreground=THEME["text"],
                        font=("Microsoft YaHei", 10))
        style.configure("TFrame", background=THEME["bg"])
        style.configure("Card.TFrame", background=THEME["card_bg"])

        # ── 标题头 ──
        header = tk.Frame(self.root, bg=THEME["card_bg"], height=56)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="⚡ 读码平台桥接器",
                 font=("Microsoft YaHei", 16, "bold"),
                 fg=THEME["accent"], bg=THEME["card_bg"]).pack(side=tk.LEFT, padx=16)
        tk.Label(header, text="Code Platform Bridge · 仓库对接服务",
                 font=("Microsoft YaHei", 9),
                 fg=THEME["text_muted"], bg=THEME["card_bg"]).pack(side=tk.LEFT, padx=4)
        self.lbl_header_status = tk.Label(header, text="● 离线", font=("Microsoft YaHei", 10),
                                           fg=THEME["danger"], bg=THEME["card_bg"])
        self.lbl_header_status.pack(side=tk.RIGHT, padx=16)

        # ── ttk 样式 ──
        style.configure("TLabel", background=THEME["bg"], foreground=THEME["text"],
                        font=("Microsoft YaHei", 9))
        style.configure("Card.TLabel", background=THEME["card_bg"], foreground=THEME["text"])
        style.configure("Secondary.TLabel", background=THEME["bg"],
                        foreground=THEME["text_secondary"], font=("Microsoft YaHei", 8))
        style.configure("Muted.TLabel", background=THEME["bg"],
                        foreground=THEME["text_muted"], font=("Microsoft YaHei", 8))

        style.configure("TEntry", fieldbackground=THEME["input_bg"],
                        foreground=THEME["input_fg"], insertcolor=THEME["text"],
                        bordercolor=THEME["border"], lightcolor=THEME["border"],
                        darkcolor=THEME["border"], font=("Consolas", 10), padding=4)
        style.map("TEntry", bordercolor=[("focus", THEME["accent"])],
                  lightcolor=[("focus", THEME["accent"])])

        style.configure("TCheckbutton", background=THEME["bg"], foreground=THEME["text"],
                        font=("Microsoft YaHei", 9))
        style.configure("TRadiobutton", background=THEME["bg"], foreground=THEME["text"],
                        font=("Microsoft YaHei", 9))

        # 主按钮 (蓝色)
        style.configure("Accent.TButton", background=THEME["accent"],
                        foreground="#ffffff", font=("Microsoft YaHei", 10, "bold"),
                        padding=(14, 6), borderwidth=0)
        style.map("Accent.TButton",
                  background=[("active", THEME["accent_hover"]), ("pressed", THEME["accent_dark"])])

        # 次要按钮
        style.configure("TButton", background=THEME["card_bg"], foreground=THEME["text"],
                        font=("Microsoft YaHei", 10), padding=(12, 6), borderwidth=0)
        style.map("TButton", background=[("active", THEME["card_hover"])])

        # 标签框架
        style.configure("TLabelframe", background=THEME["bg"], bordercolor=THEME["border"])
        style.configure("TLabelframe.Label", background=THEME["bg"],
                        foreground=THEME["accent"], font=("Microsoft YaHei", 11, "bold"))

        # ── 菜单 ──
        menubar = tk.Menu(self.root, bg=THEME["card_bg"], fg=THEME["text"],
                          activebackground=THEME["accent"], activeforeground="#ffffff",
                          borderwidth=0)
        help_menu = tk.Menu(menubar, tearoff=0, bg=THEME["card_bg"], fg=THEME["text"],
                            activebackground=THEME["accent"], activeforeground="#ffffff")
        help_menu.add_command(label="读码平台数据模板配置指南",
                              command=self._show_template_guide)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.config(menu=menubar)

        # ── 配置区 ──
        cfg_frame = ttk.LabelFrame(self.root, text=" 连接配置 ", padding=12)
        cfg_frame.pack(fill=tk.X, padx=12, pady=(12, 6))

        # API 地址
        row = 0
        ttk.Label(cfg_frame, text="仓库 API 地址:").grid(
            row=row, column=0, sticky=tk.W, padx=4, pady=6)
        self.ent_api_url = ttk.Entry(cfg_frame, width=52)
        self.ent_api_url.grid(row=row, column=1, columnspan=5, sticky=tk.EW, padx=4, pady=6)

        # TCP 模式
        row += 1
        ttk.Label(cfg_frame, text="TCP 模式:").grid(
            row=row, column=0, sticky=tk.W, padx=4, pady=6)
        self.var_tcp_mode = tk.StringVar(value="server")
        ttk.Radiobutton(cfg_frame, text="服务端 (平台连我)",
                        variable=self.var_tcp_mode, value="server").grid(
            row=row, column=1, sticky=tk.W, padx=4)
        ttk.Radiobutton(cfg_frame, text="客户端 (我连平台)",
                        variable=self.var_tcp_mode, value="client").grid(
            row=row, column=2, sticky=tk.W, padx=4)

        # 端口 / 平台地址
        row += 1
        ttk.Label(cfg_frame, text="监听端口:").grid(
            row=row, column=0, sticky=tk.W, padx=4, pady=6)
        self.ent_listen_port = ttk.Entry(cfg_frame, width=10)
        self.ent_listen_port.grid(row=row, column=1, sticky=tk.W, padx=4, pady=6)

        ttk.Label(cfg_frame, text="平台地址:").grid(
            row=row, column=2, sticky=tk.W, padx=4, pady=6)
        self.ent_platform_addr = ttk.Entry(cfg_frame, width=24)
        self.ent_platform_addr.grid(row=row, column=3, columnspan=2, sticky=tk.W, padx=4, pady=6)

        # 数据格式
        row += 1
        ttk.Label(cfg_frame, text="数据分隔符:").grid(
            row=row, column=0, sticky=tk.W, padx=4, pady=6)
        self.ent_delimiter = ttk.Entry(cfg_frame, width=6)
        self.ent_delimiter.grid(row=row, column=1, sticky=tk.W, padx=4, pady=6)

        ttk.Label(cfg_frame, text="字段顺序:").grid(
            row=row, column=2, sticky=tk.W, padx=4, pady=6)
        self.ent_fields = ttk.Entry(cfg_frame, width=30)
        self.ent_fields.grid(row=row, column=3, columnspan=2, sticky=tk.EW, padx=4, pady=6)

        # 单位转换
        row += 1
        ttk.Label(cfg_frame, text="重量倍率:").grid(
            row=row, column=0, sticky=tk.W, padx=4, pady=6)
        self.ent_weight_mult = ttk.Entry(cfg_frame, width=8)
        self.ent_weight_mult.grid(row=row, column=1, sticky=tk.W, padx=4, pady=6)
        ttk.Label(cfg_frame, text="(kg→g=1000)", style="Muted.TLabel").grid(
            row=row, column=2, sticky=tk.W, padx=4, pady=6)

        ttk.Label(cfg_frame, text="尺寸倍率:").grid(
            row=row, column=3, sticky=tk.W, padx=4, pady=6)
        self.ent_dim_mult = ttk.Entry(cfg_frame, width=8)
        self.ent_dim_mult.grid(row=row, column=4, sticky=tk.W, padx=4, pady=6)

        # 工作流
        row += 1
        ttk.Label(cfg_frame, text="工作流:").grid(
            row=row, column=0, sticky=tk.W, padx=4, pady=6)
        self.var_auto_sign = tk.BooleanVar(value=True)
        self.var_auto_weighing = tk.BooleanVar(value=True)
        self.var_upload_img = tk.BooleanVar(value=False)
        self.var_voice = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg_frame, text="自动签收",
                        variable=self.var_auto_sign).grid(
            row=row, column=1, sticky=tk.W, padx=4)
        ttk.Checkbutton(cfg_frame, text="自动出库",
                        variable=self.var_auto_weighing).grid(
            row=row, column=2, sticky=tk.W, padx=4)
        ttk.Checkbutton(cfg_frame, text="上传图片",
                        variable=self.var_upload_img).grid(
            row=row, column=3, sticky=tk.W, padx=4)
        ttk.Checkbutton(cfg_frame, text="🔊 语音提醒",
                        variable=self.var_voice).grid(
            row=row, column=4, sticky=tk.W, padx=4)

        cfg_frame.columnconfigure(1, weight=1)

        # ── 日志区 ──
        log_frame = ttk.LabelFrame(self.root, text=" 实时日志 ", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, font=("Consolas", 10), height=18,
            bg="#f8fbff", fg="#263238", insertbackground=THEME["text"],
            selectbackground=THEME["accent"], selectforeground="#ffffff",
            borderwidth=0, relief=tk.FLAT, padx=8, pady=6)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        # 日志颜色标签
        self.log_text.tag_config("INFO", foreground=THEME["success"])
        self.log_text.tag_config("ERROR", foreground=THEME["danger"])
        self.log_text.tag_config("WARN", foreground=THEME["warning"])

        # ── 底栏 ──
        bottom = tk.Frame(self.root, bg=THEME["bg"])
        bottom.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=(6, 12))

        # 按钮先 pack(RIGHT) 确保始终可见
        right_frame = tk.Frame(bottom, bg=THEME["bg"])
        right_frame.pack(side=tk.RIGHT)
        self.btn_save = ttk.Button(right_frame, text="保存配置",
                                   command=self._save_and_apply)
        self.btn_save.pack(side=tk.LEFT, padx=4)
        self.btn_start = ttk.Button(right_frame, text="▶ 启动服务",
                                    style="Accent.TButton",
                                    command=self._toggle_service)
        self.btn_start.pack(side=tk.LEFT, padx=4)

        # 统计卡片 (pack LEFT 在按钮之后, 避免挤压按钮)
        def make_stat(parent, label, color):
            card = tk.Frame(parent, bg=THEME["card_bg"], bd=0,
                            highlightbackground=THEME["border"], highlightthickness=1)
            tk.Label(card, text=label, bg=THEME["card_bg"], fg=THEME["text_muted"],
                     font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=8, pady=8)
            lbl = tk.Label(card, text="0", bg=THEME["card_bg"], fg=color,
                           font=("Consolas", 16, "bold"))
            lbl.pack(side=tk.LEFT, padx=(2, 10))
            return lbl

        self.lbl_received = make_stat(bottom, "收到", THEME["text"])
        self.lbl_received.master.pack(side=tk.LEFT, padx=4)
        self.lbl_success = make_stat(bottom, "成功", THEME["success"])
        self.lbl_success.master.pack(side=tk.LEFT, padx=4)
        self.lbl_fail = make_stat(bottom, "失败", THEME["danger"])
        self.lbl_fail.master.pack(side=tk.LEFT, padx=4)

    # ── 语音辅助 ─────────────────────────────────────────────────────

    def _speak(self, text: str):
        """仅在语音开启时播报"""
        if self.var_voice.get():
            speak(text)

    # ── 配置读写 ─────────────────────────────────────────────────────

    def _load_config_to_ui(self):
        self.ent_api_url.insert(0, self.cfg["api"]["base_url"])
        self.var_tcp_mode.set(self.cfg["tcp"].get("mode", "server"))
        self.ent_listen_port.insert(0, str(self.cfg["tcp"].get("listen_port", 5000)))
        addr = f'{self.cfg["tcp"].get("platform_host", "")}:{self.cfg["tcp"].get("platform_port", "")}'
        self.ent_platform_addr.insert(0, addr)
        self.ent_delimiter.insert(0, self.cfg["data_format"].get("delimiter", ","))
        self.ent_fields.insert(0, ",".join(self.cfg["data_format"].get("fields", [])))
        self.ent_weight_mult.insert(0, str(self.cfg["unit_conversion"].get("weight_multiplier", 1000)))
        self.ent_dim_mult.insert(0, str(self.cfg["unit_conversion"].get("dimension_multiplier", 1)))
        self.var_auto_sign.set(self.cfg["workflow"].get("auto_sign", True))
        self.var_auto_weighing.set(self.cfg["workflow"].get("auto_weighing", True))
        self.var_upload_img.set(self.cfg["workflow"].get("upload_image", False))
        self.var_voice.set(self.cfg.get("notify", {}).get("voice_enabled", True))

    def _save_config_from_ui(self):
        self.cfg["api"]["base_url"] = self.ent_api_url.get().strip()
        self.cfg["tcp"]["mode"] = self.var_tcp_mode.get()
        self.cfg["tcp"]["listen_port"] = int(self.ent_listen_port.get().strip() or "5000")
        addr = self.ent_platform_addr.get().strip()
        if ":" in addr:
            host, port = addr.rsplit(":", 1)
            self.cfg["tcp"]["platform_host"] = host
            try:
                self.cfg["tcp"]["platform_port"] = int(port)
            except ValueError:
                self.cfg["tcp"]["platform_port"] = 5000
        self.cfg["data_format"]["delimiter"] = self.ent_delimiter.get().strip() or ","
        self.cfg["data_format"]["fields"] = [
            f.strip() for f in self.ent_fields.get().split(",") if f.strip()
        ]
        try:
            self.cfg["unit_conversion"]["weight_multiplier"] = float(self.ent_weight_mult.get().strip() or "1000")
        except ValueError:
            self.cfg["unit_conversion"]["weight_multiplier"] = 1000
        try:
            self.cfg["unit_conversion"]["dimension_multiplier"] = float(self.ent_dim_mult.get().strip() or "1")
        except ValueError:
            self.cfg["unit_conversion"]["dimension_multiplier"] = 1
        self.cfg["workflow"]["auto_sign"] = self.var_auto_sign.get()
        self.cfg["workflow"]["auto_weighing"] = self.var_auto_weighing.get()
        self.cfg["workflow"]["upload_image"] = self.var_upload_img.get()
        self.cfg.setdefault("notify", {})["voice_enabled"] = self.var_voice.get()

    def _save_and_apply(self):
        self._save_config_from_ui()
        save_config(self.cfg)
        messagebox.showinfo("提示", "配置已保存")

    # ── 队列轮询 (线程安全 GUI 更新) ─────────────────────────────────

    def _poll_queues(self):
        # 处理日志
        try:
            while True:
                level, msg = self.log_queue.get_nowait()
                self._log(level, msg)
        except queue.Empty:
            pass
        # 处理数据
        try:
            while True:
                record = self.data_queue.get_nowait()
                self._process_record(record)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queues)

    # ── 日志 ─────────────────────────────────────────────────────────

    def _log(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {level:5s} {msg}\n", level)
        self.log_text.see(tk.END)
        # 限制日志行数
        if int(self.log_text.index("end-1c").split(".")[0]) > 2000:
            self.log_text.delete("1.0", "500.0")

    def _log_q(self, level: str, msg: str):
        """从子线程安全地写日志"""
        self.log_queue.put((level, msg))

    # ── TCP 回调 ──────────────────────────────────────────────────────

    def _on_tcp_data(self, record: str):
        self.data_queue.put(record)

    def _on_tcp_log(self, level: str, msg: str):
        self.log_queue.put((level, msg))

    # ── 处理一条数据记录 ─────────────────────────────────────────────

    def _process_record(self, record: str):
        self.stats["received"] += 1
        parsed = parse_record(record, self.cfg)
        if not parsed:
            self._log("ERROR", f"解析失败: {record[:200]}")
            self.stats["fail"] += 1
            self._update_stats()
            return

        barcode = parsed.get("barcode", "")
        weight_str = parsed.get("weight", "")
        length_str = parsed.get("length", "")
        width_str = parsed.get("width", "")
        height_str = parsed.get("height", "")

        self._log("INFO",
                  f"收到: 单号={barcode} 重量={weight_str} "
                  f"尺寸={length_str}x{width_str}x{height_str}")

        # 单位转换
        w_mult = self.cfg["unit_conversion"]["weight_multiplier"]
        d_mult = self.cfg["unit_conversion"]["dimension_multiplier"]

        weight = _safe_float(weight_str)
        if weight is not None:
            weight = int(weight * w_mult) if w_mult != 1 else weight

        length = _safe_float(length_str)
        width = _safe_float(width_str)
        height = _safe_float(height_str)
        if length is not None:
            length = int(length * d_mult) if d_mult != 1 else length
        if width is not None:
            width = int(width * d_mult) if d_mult != 1 else width
        if height is not None:
            height = int(height * d_mult) if d_mult != 1 else height

        if not self.api_client:
            self._log("ERROR", "API 客户端未初始化, 请先启动服务")
            self.stats["fail"] += 1
            self._update_stats()
            return

        # ── 签收 ──
        if self.cfg["workflow"]["auto_sign"]:
            resp = self.api_client.send_package_info(barcode)
            code = resp.get("code", 500)
            if code == 200:
                data = resp.get("data", {})
                ptype = data.get("packageTypeName", "")
                self._log("INFO", f"签收成功: {ptype}")
            else:
                err_msg = resp.get('msg', '未知错误')
                self._log("ERROR", f"签收失败: {err_msg}")
                self._speak("签收失败")
                self.stats["fail"] += 1
                self._update_stats()
                return

        # ── 出库 ──
        if self.cfg["workflow"]["auto_weighing"]:
            resp = self.api_client.package_weighing(
                barcode, weight, length, width, height)
            code = resp.get("code", 500)
            if code == 200:
                data = resp.get("data", {})
                if data:
                    self._log("INFO",
                              f"出库成功: {data.get('logisticsName', '')} / "
                              f"{data.get('logisticsTransmodeName', '')}")
                else:
                    self._log("INFO", "出库成功")
                self._speak("出库成功")
                self.stats["success"] += 1
            else:
                err_msg = resp.get('msg', '未知错误')
                self._log("ERROR", f"出库失败: {err_msg}")
                self._speak("出库失败")
                self.stats["fail"] += 1

        self._update_stats()

    def _update_stats(self):
        self.lbl_received.config(text=str(self.stats["received"]))
        self.lbl_success.config(text=str(self.stats["success"]))
        self.lbl_fail.config(text=str(self.stats["fail"]))

    # ── 服务控制 ─────────────────────────────────────────────────────

    def _toggle_service(self):
        if not self.running:
            self._start_service()
        else:
            self._stop_service()

    def _start_service(self):
        self._save_config_from_ui()
        save_config(self.cfg)

        self.api_client = WarehouseApiClient(self.cfg)
        self.tcp_bridge = TcpBridge(self.cfg, self._on_tcp_data, self._on_tcp_log)
        self.tcp_bridge.start()

        self.running = True
        self.btn_start.config(text="■ 停止服务")
        self.lbl_header_status.config(text="● 在线", fg=THEME["success"])
        self._log("INFO", "─" * 56)
        self._log("INFO", "服务已启动")
        self._log("INFO", f"API 地址: {self.cfg['api']['base_url']}")
        mode = self.cfg["tcp"]["mode"]
        if mode == "server":
            self._log("INFO", f"TCP 模式: 服务端, 端口 {self.cfg['tcp']['listen_port']}")
        else:
            self._log("INFO",
                      f"TCP 模式: 客户端, 目标 "
                      f"{self.cfg['tcp']['platform_host']}:{self.cfg['tcp']['platform_port']}")
        self._log("INFO", f"自动签收: {self.cfg['workflow']['auto_sign']}  "
                          f"自动出库: {self.cfg['workflow']['auto_weighing']}  "
                          f"语音: {self.cfg['notify']['voice_enabled']}")
        self._log("INFO", "─" * 56)
        self._speak("服务已启动")

    def _stop_service(self):
        if self.tcp_bridge:
            self.tcp_bridge.stop()
        self.running = False
        self.btn_start.config(text="▶ 启动服务")
        self.lbl_header_status.config(text="● 离线", fg=THEME["danger"])
        self._log("WARN", "服务已停止")
        self._speak("服务已停止")

    # ── 帮助 ─────────────────────────────────────────────────────────

    def _show_template_guide(self):
        guide = (
            "═══ 读码平台数据模板配置指南 ═══\n\n"
            "在「海康读码平台 → 通信配置 → TCP 输出插件」中配置数据模板。\n\n"
            "推荐格式 1 (JSON, 推荐):\n"
            '  {"barcode":"${Barcode}","weight":"${Weight}",'
            '"length":"${Length}","width":"${Width}","height":"${Height}"}\\n\n\n'
            "推荐格式 2 (分隔符, 简单):\n"
            "  ${Barcode},${Weight},${Length},${Width},${Height}\\n\n\n"
            "字段通配符参考:\n"
            "  ${Barcode}  - 单号\n"
            "  ${Weight}    - 重量 (kg)\n"
            "  ${Length}    - 长度\n"
            "  ${Width}     - 宽度\n"
            "  ${Height}    - 高度\n"
            "  ${Volume}    - 体积\n"
            "  ${OCR}       - OCR 文本\n"
            "  ${CartNo}    - 小车号\n"
            "  ${Timestamp} - 时间戳\n\n"
            "本程序配置:\n"
            "  - 数据分隔符: 与模板一致 (默认逗号 ,)\n"
            "  - 字段顺序: barcode,weight,length,width,height\n"
            "  - 重量倍率: 1000 (kg→g), 如已是 g 则填 1\n"
            "  - 尺寸倍率: 1 (mm→mm), 如是 cm 则填 10\n\n"
            "注意: 模板末尾需要换行符 \\n 作为记录分隔符。"
        )
        messagebox.showinfo("数据模板配置指南", guide)

    def _show_about(self):
        messagebox.showinfo("关于",
            "读码平台桥接器 v1.0\n\n"
            "从海康读码平台接收 TCP 融合数据,\n"
            "调用仓库 API 完成签收与出库。\n\n"
            "技术栈: Python + tkinter + requests")


# ═══════════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    app = BridgeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
