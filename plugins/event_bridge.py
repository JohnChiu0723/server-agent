"""
event_bridge.py — Streamlit 事件橋接插件
==========================================
MCDR 插件：監聽伺服器事件，透過 TCP Socket 即時推送到 Streamlit 面板。

設定：
  啟動時讀取環境變數 MC_EVENT_PORT 決定推送埠號。
  主服  → 29001（由 server_control.py 注入環境變數）
  鏡像服 → 29002

事件格式（JSON）：
  {"type": "player_join",   "player": "...", "message": "..."}
  {"type": "player_leave",  "player": "...", "message": "..."}
  {"type": "server_start",  "message": "..."}
  {"type": "server_ready",  "message": "..."}
  {"type": "server_stop",   "message": "..."}
  {"type": "chat",          "player": "...", "message": "..."}
  {"type": "error",         "message": "..."}
  {"type": "warn",          "message": "..."}
"""

from __future__ import annotations

import os
import socket
import json

from mcdreforged.api.all import (
    PluginServerInterface,
    Info,
    new_thread,
)

PLUGIN_METADATA = {
    "id":          "event_bridge",
    "version":     "1.1.0",
    "name":        "Streamlit Event Bridge",
    "description": "Pushes MCDR server events to Streamlit panel via TCP socket",
    "author":      "ServerManager",
}

# 由環境變數決定推送埠（server_control.py 在啟動時注入）
_PORT: int = int(os.environ.get("MC_EVENT_PORT", "29001"))


# ─── Socket 推送（非阻塞，失敗靜默）───────────────────────────────────────────

@new_thread("event_bridge_send")
def _push(event_type: str, **kwargs):
    """連線至 app.py 的 Socket 監聽器並發送 JSON 事件。"""
    payload = json.dumps({"type": event_type, **kwargs}, ensure_ascii=False)
    try:
        with socket.create_connection(("127.0.0.1", _PORT), timeout=1.5) as s:
            s.sendall(payload.encode("utf-8"))
    except Exception:
        pass  # 前端未啟動或端口未就緒時靜默失敗


# ─── MCDR 生命週期 ─────────────────────────────────────────────────────────────

def on_load(server: PluginServerInterface, old_module) -> None:
    server.logger.info(f"[EventBridge] Loaded, pushing events to 127.0.0.1:{_PORT}")
    _push("plugin_loaded", message="EventBridge 已載入")


def on_unload(server: PluginServerInterface) -> None:
    _push("plugin_unloaded", message="EventBridge 已卸載")


# ─── 伺服器生命週期事件 ────────────────────────────────────────────────────────

def on_server_start(server: PluginServerInterface) -> None:
    _push("server_start", message="伺服器程序啟動中…")


def on_server_startup(server: PluginServerInterface) -> None:
    _push("server_ready", message="伺服器已就緒，等待玩家連線")


def on_server_stop(server: PluginServerInterface, return_code: int) -> None:
    _push("server_stop", message=f"伺服器已停止（退出碼 {return_code}）")


# ─── 玩家事件 ──────────────────────────────────────────────────────────────────

def on_player_joined(
    server: PluginServerInterface, player: str, info: Info
) -> None:
    _push("player_join", player=player, message=f"{player} 加入了遊戲")


def on_player_left(server: PluginServerInterface, player: str) -> None:
    _push("player_leave", player=player, message=f"{player} 離開了遊戲")


# ─── 伺服器訊息過濾 ────────────────────────────────────────────────────────────

def on_info(server: PluginServerInterface, info: Info) -> None:
    content = info.content

    # 玩家聊天
    if info.is_player:
        _push("chat", player=info.player,
              message=f"<{info.player}> {content}")
        return

    if not info.is_from_server:
        return

    # 關鍵字匹配 → 推送警告 / 錯誤
    content_lo = content.lower()
    if any(kw in content_lo for kw in ("exception", "stacktrace", "crash", "fatal")):
        _push("error", message=content[:250])
    elif "error" in content_lo:
        _push("error", message=content[:200])
    elif "warn" in content_lo:
        _push("warn", message=content[:200])
    # 伺服器就緒訊息（Fabric / Vanilla 格式）
    elif "done" in content_lo and ("seconds" in content_lo or "for help" in content_lo):
        _push("server_ready", message=content[:150])
