"""
server_control.py — 伺服器管家後端控制核心 (MCDR + RCON)
==========================================================
主服與鏡像服均由 Windows 本機的 MCDReforged 管理。
透過 RCON 發送遊戲指令，以 UTF-8 讀取目錄中的 latest.log。
"""

import os
import socket
import struct
import subprocess

from env_loader import load_env

load_env()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── 伺服器設定表（機密一律由 .env / 環境變數提供）────────────────────────────────
SERVERS: dict[str, dict] = {
    "main": {
        "label":         "主伺服器",
        "service":       "mc-server",
        "container":     "mc-server",
        "rcon_host":     os.getenv("RCON_HOST_MAIN", "localhost"),
        "rcon_port":     int(os.getenv("RCON_PORT_MAIN", "25575")),
        "rcon_password": os.getenv("RCON_PASSWORD_MAIN", ""),
        "data_path":     os.path.join(BASE_DIR, "minecraft-server"),
        "mem_limit_gb":  16.0,
    },
    "mirror": {
        "label":         "鏡像伺服器",
        "service":       "gravity-mirror",
        "container":     "gravity-mirror",
        "rcon_host":     os.getenv("RCON_HOST_MIRROR", "localhost"),
        "rcon_port":     int(os.getenv("RCON_PORT_MIRROR", "25576")),
        "rcon_password": os.getenv("RCON_PASSWORD_MIRROR", ""),
        "data_path":     os.path.join(BASE_DIR, "minecraft-mirror"),
        "mem_limit_gb":  10.0,
    },
}


# ─── RCON（純 socket 實作，無 signal 問題） ──────────────────────────────────

def send_rcon_command(server_type: str, command: str) -> str:
    """
    透過 RCON 協定發送指令到 Minecraft 伺服器。
    使用 socket + struct 手動實作，避免 mcrcon 的 signal.alarm() thread 安全問題。
    """
    cfg = SERVERS[server_type]
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((cfg["rcon_host"], cfg["rcon_port"]))

        req_id = 1

        def _send_packet(pkt_type: int, payload: bytes):
            nonlocal req_id
            body = struct.pack("<ii", req_id, pkt_type) + payload + b"\x00\x00"
            pkt = struct.pack("<i", len(body)) + body
            sock.send(pkt)
            return req_id

        def _recv_response() -> bytes:
            raw_len = sock.recv(4)
            if len(raw_len) < 4:
                return b""
            pkt_len = struct.unpack("<i", raw_len)[0]
            data = b""
            while len(data) < pkt_len:
                chunk = sock.recv(pkt_len - len(data))
                if not chunk:
                    break
                data += chunk
            return data

        # RCON packet types: 3 = auth, 2 = exec command
        _send_packet(3, cfg["rcon_password"].encode("utf-8"))
        _recv_response()

        _send_packet(2, command.encode("utf-8"))
        resp = _recv_response()

        sock.close()

        if len(resp) >= 8:
            result = resp[8:-2].decode("utf-8", errors="replace")
            return result.strip()
        return "(empty response)"

    except ConnectionRefusedError:
        return "[ERROR] RCON 連線被拒絕（伺服器是否已啟動？）"
    except socket.timeout:
        return "[ERROR] RCON 連線超時（請確認伺服器正在運行）"
    except Exception as exc:
        return f"[ERROR] RCON 錯誤：{exc}"


# ─── 日誌 / Properties ────────────────────────────────────────────────────────

def get_latest_logs(server_type, lines=50):
    """讀取主服或鏡像服 latest.log 最後 N 行（UTF-8）。"""
    if server_type not in SERVERS:
        return "（未知伺服器類型：{}）".format(server_type)
    log_path = os.path.join(SERVERS[server_type]["data_path"], "logs", "latest.log")
    if not os.path.isfile(log_path):
        return "（尚無日誌檔案，或伺服器從未啟動）"
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except Exception as exc:
        return "（日誌讀取失敗：{}）".format(exc)


def read_latest_log(server_type, max_lines=200):
    """向後相容別名。"""
    return get_latest_logs(server_type, lines=max_lines)


def read_server_properties(server_type: str) -> dict[str, str]:
    props_path = os.path.join(SERVERS[server_type]["data_path"], "server.properties")
    if not os.path.exists(props_path):
        return {}
    props: dict[str, str] = {}
    try:
        with open(props_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    props[k.strip()] = v.strip()
    except Exception:
        pass
    return props


def update_server_properties(server_type: str, key: str, value: str) -> str:
    props_path = os.path.join(SERVERS[server_type]["data_path"], "server.properties")
    if not os.path.exists(props_path):
        return "[ERROR] 找不到 server.properties"
    try:
        with open(props_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        new_lines, updated = [], False
        for line in lines:
            if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                new_lines.append(f"{key}={value}\n")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"{key}={value}\n")
        with open(props_path, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(new_lines)
        return f"已更新 {key}={value}（重啟後生效）"
    except Exception as exc:
        return f"[ERROR] 寫入失敗：{exc}"


# ─── 伺服器狀態（Windows 原生 MCDR，不再使用 Docker 容器） ─────────────────

def get_server_status(server_type: str) -> str:
    """以 RCON 探測本機 MCDR 是否在線（agent_proxy 經 host.docker.internal 連線）。"""
    if server_type not in SERVERS:
        return "unknown"
    result = send_rcon_command(server_type, "list")
    if result.startswith("[ERROR]"):
        return "stopped"
    return "running"


def get_container_status(server_type: str) -> str:
    """向後相容別名（agent_tools / Dify 工具仍呼叫此名稱）。"""
    return get_server_status(server_type)


def get_container_stats(server_type: str) -> dict:
    status = get_server_status(server_type)
    return {"status": status, "mode": "windows-native"}


def start_server(server_type: str) -> str:
    cfg = SERVERS[server_type]
    return f"[{cfg['label']}] 請使用 Start-Main-Server.bat 啟動（本機 Windows）"


def stop_server(server_type: str) -> str:
    cfg = SERVERS[server_type]
    return f"[{cfg['label']}] 請在 MCDR 控制台輸入 !!stop"


def restart_server(server_type: str) -> str:
    cfg = SERVERS[server_type]
    return f"[{cfg['label']}] 請重新執行 Start-Main-Server.bat"
