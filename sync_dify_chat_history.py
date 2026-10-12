"""Sync Dify console chat history to local agent_memory files."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from env_loader import load_env

load_env()

BASE_DIR = Path(__file__).resolve().parent
MEMORY_DIR = BASE_DIR / "agent_memory"
HISTORY_JSON = MEMORY_DIR / "dify_chat_history.json"
CHAT_LOG = MEMORY_DIR / "chat_log.md"
DIFY_BASE = os.environ.get("DIFY_CONSOLE_URL", "http://localhost:8080")
ADMIN_EMAIL = os.environ.get("DIFY_ADMIN_EMAIL", "admin@gravity.local")
ADMIN_PASSWORD = os.environ.get("DIFY_ADMIN_PASSWORD", "")
AGENT_APP_NAME = os.environ.get("DIFY_AGENT_APP_NAME", "graviyt管家")


def _login() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Origin": DIFY_BASE, "Referer": f"{DIFY_BASE}/"})
    pwd = base64.b64encode(ADMIN_PASSWORD.encode()).decode()
    resp = session.post(
        f"{DIFY_BASE}/console/api/login",
        json={
            "email": ADMIN_EMAIL,
            "password": pwd,
            "remember_me": True,
            "language": "zh-Hant",
        },
        timeout=30,
    )
    resp.raise_for_status()
    if session.cookies.get("csrf_token"):
        session.headers["X-CSRF-Token"] = session.cookies["csrf_token"]
    return session


def _find_agent_app_id(session: requests.Session) -> str:
    resp = session.get(f"{DIFY_BASE}/console/api/apps", params={"page": 1, "limit": 50}, timeout=30)
    resp.raise_for_status()
    for app in resp.json().get("data", []):
        if app.get("mode") != "agent-chat":
            continue
        name = app.get("name", "")
        if AGENT_APP_NAME in name or "管家" in name or "agent" in name.lower():
            return app["id"]
    for app in resp.json().get("data", []):
        if app.get("mode") == "agent-chat":
            return app["id"]
    raise RuntimeError("找不到 agent-chat 應用")


def _fetch_messages(session: requests.Session, app_id: str, conversation_id: str) -> list[dict]:
    messages: list[dict] = []
    page = 1
    while True:
        resp = session.get(
            f"{DIFY_BASE}/console/api/apps/{app_id}/chat-messages",
            params={"conversation_id": conversation_id, "limit": 50, "page": page},
            timeout=30,
        )
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if not items:
            break
        for item in items:
            messages.append(
                {
                    "id": item.get("id"),
                    "role": "user" if item.get("query") else "assistant",
                    "query": item.get("query", ""),
                    "answer": item.get("answer", ""),
                    "created_at": item.get("created_at"),
                }
            )
        if not data.get("has_more"):
            break
        page += 1
    return messages


def sync_history() -> dict:
    session = _login()
    app_id = _find_agent_app_id(session)
    resp = session.get(
        f"{DIFY_BASE}/console/api/apps/{app_id}/chat-conversations",
        params={"page": 1, "limit": 100, "sort_by": "-updated_at"},
        timeout=30,
    )
    resp.raise_for_status()
    conversations = resp.json().get("data", [])

    payload = {
        "synced_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "app_id": app_id,
        "conversation_count": len(conversations),
        "conversations": [],
    }

    for conv in conversations:
        conv_id = conv.get("id")
        if not conv_id:
            continue
        messages = _fetch_messages(session, app_id, conv_id)
        payload["conversations"].append(
            {
                "id": conv_id,
                "name": conv.get("name", ""),
                "updated_at": conv.get("updated_at"),
                "message_count": len(messages),
                "messages": messages,
            }
        )

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_lines = [
        "",
        f"## {datetime.now().strftime('%Y-%m-%d %H:%M')} Dify 聊天同步",
        f"- 已同步 {len(conversations)} 個對話到 `agent_memory/dify_chat_history.json`",
    ]
    if conversations:
        latest = conversations[0]
        summary_lines.append(f"- 最新對話：{latest.get('name', latest.get('id'))}")
    CHAT_LOG.parent.mkdir(parents=True, exist_ok=True)
    if CHAT_LOG.exists():
        CHAT_LOG.write_text(CHAT_LOG.read_text(encoding="utf-8") + "\n".join(summary_lines) + "\n", encoding="utf-8")
    else:
        CHAT_LOG.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    return payload


def main() -> None:
    data = sync_history()
    print(f"[INFO] Synced {data['conversation_count']} Dify conversations")
    print(f"     file: {HISTORY_JSON}")


if __name__ == "__main__":
    main()
