"""
呼叫 GRAVITY 伺服器後端工程 Workflow（/v1/workflows/run）。
供 agent_proxy 的 server_backend_engineering 工具使用。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
WORKFLOW_TIMEOUT = int(os.environ.get("DIFY_WORKFLOW_TIMEOUT", "600"))


def _load_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if val:
        return val
    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def dify_api_base() -> str:
    explicit = _load_env("DIFY_API_BASE")
    if explicit:
        return explicit.rstrip("/")
    if Path("/workspace/host").exists():
        return "http://nginx"
    return _load_env("DIFY_CONSOLE_URL") or "http://localhost:8080"


def run_backend_engineering(
    query: str,
    *,
    conversation_history: str = "",
    task_mode: str = "logic",
    user: str = "gravity-agent",
) -> dict[str, Any]:
    api_key = _load_env("DIFY_WORKFLOW_API_KEY")
    if not api_key:
        raise ValueError("缺少 DIFY_WORKFLOW_API_KEY。請先執行：python manage.py sync")

    task_mode = task_mode if task_mode in {"logic", "code", "calc_vars"} else "logic"
    url = f"{dify_api_base().rstrip('/')}/v1/workflows/run"
    body = {
        "inputs": {
            "query": query.strip(),
            "conversation_history": conversation_history or "",
            "task_mode": task_mode,
        },
        "response_mode": "blocking",
        "user": user,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    resp = requests.post(url, headers=headers, json=body, timeout=WORKFLOW_TIMEOUT)
    if resp.status_code in (401, 403):
        from agent_infra import InfrastructureFailure

        raise InfrastructureFailure(f"Workflow API HTTP {resp.status_code}: {resp.text[:800]}")
    if resp.status_code >= 400:
        raise ValueError(f"Workflow API HTTP {resp.status_code}: {resp.text[:800]}")

    data = (resp.json() or {}).get("data") or {}
    outputs = data.get("outputs") if isinstance(data.get("outputs"), dict) else {}

    reasoning = str(outputs.get("reasoning_text") or "")
    sanitized = outputs.get("sanitized_json")
    raw_json = outputs.get("raw_json") or ""

    summary_parts = [reasoning.strip()] if reasoning.strip() else []
    if sanitized:
        summary_parts.append(
            "\n---\n**配置 JSON（洗白後）**\n```json\n"
            + json.dumps(sanitized, ensure_ascii=False, indent=2)
            + "\n```"
        )

    return {
        "ok": True,
        "task_mode": task_mode,
        "workflow_run_id": data.get("id"),
        "status": data.get("status"),
        "reasoning_text": reasoning,
        "json_success": outputs.get("json_success"),
        "sanitized_json": sanitized,
        "raw_json": raw_json,
        "json_warnings": outputs.get("json_warnings"),
        "summary_for_user": "\n".join(summary_parts).strip(),
        "elapsed_time": data.get("elapsed_time"),
    }
