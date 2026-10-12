"""
run_agent_background.py — 不依賴瀏覽器分頁的 Dify Agent 長任務執行器
====================================================================
Dify 聊天頁使用 SSE 串流，關閉分頁會中斷 Agent。此腳本透過 Service API
以 streaming 模式維持連線直到任務完成（Agent 模式不支援 blocking）。

用法：
  py run_agent_background.py "請檢查 MC 主服日誌並回報錯誤"
  py run_agent_background.py --conversation-id <uuid> "延續上一輪對話"
  py run_agent_background.py --file task.txt

環境變數（或 .env）：
  DIFY_APP_API_KEY   — App API Key（setup_dify_gravity.py 會自動建立）
  DIFY_CONSOLE_URL   — 預設 http://localhost:8080
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
MEMORY_DIR = BASE_DIR / "agent_memory"
RUN_LOG = MEMORY_DIR / "background_runs.jsonl"
DIFY_BASE = os.environ.get("DIFY_CONSOLE_URL", "http://localhost:8080").rstrip("/")
DEFAULT_USER = os.environ.get("DIFY_BG_USER", "gravity-background")


def _load_env_key(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if val:
        return val
    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def _parse_sse_line(line: str) -> dict | None:
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if payload == "[DONE]":
        return {"event": "done"}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def run_agent_stream(
    query: str,
    *,
    conversation_id: str | None = None,
    user: str = DEFAULT_USER,
    timeout: int = 86400,
) -> dict:
    api_key = _load_env_key("DIFY_APP_API_KEY")
    if not api_key:
        raise RuntimeError(
            "缺少 DIFY_APP_API_KEY。請執行 python setup_dify_gravity.py 或手動在 .env 設定。"
        )

    url = f"{DIFY_BASE}/v1/chat-messages"
    body: dict = {
        "inputs": {},
        "query": query,
        "response_mode": "streaming",
        "user": user,
    }
    if conversation_id:
        body["conversation_id"] = conversation_id

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    t0 = time.time()
    answer_parts: list[str] = []
    thought_parts: list[str] = []
    message_id = ""
    conv_id = conversation_id or ""
    last_event = ""
    tool_events: list[dict] = []

    print(f"[{datetime.now().strftime('%H:%M:%S')}] [INFO] Background agent started (streaming)")
    print(f"  query: {query[:120]}{'…' if len(query) > 120 else ''}")

    with requests.post(url, headers=headers, json=body, stream=True, timeout=(30, timeout)) as resp:
        resp.raise_for_status()
        resp.encoding = "utf-8"
        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            event = _parse_sse_line(raw_line)
            if not event:
                continue
            if event.get("event") == "done":
                break

            ev = event.get("event", "")
            if ev:
                last_event = ev

            if ev == "agent_thought":
                thought = event.get("thought") or ""
                if thought:
                    thought_parts.append(thought)
                    print(f"  [THINK] {thought[:100]}{'…' if len(thought) > 100 else ''}")
            elif ev == "agent_message" or ev == "message":
                chunk = event.get("answer") or ""
                if chunk:
                    answer_parts.append(chunk)
            elif ev in ("tool_call", "tool_response", "agent_tool_call", "agent_tool_response"):
                tool_events.append(event)
                label = event.get("tool") or event.get("tool_name") or ev
                print(f"  [TOOL] {label}")
            elif ev == "message_end":
                message_id = event.get("id") or event.get("message_id") or message_id
                conv_id = event.get("conversation_id") or conv_id
                meta = event.get("metadata") or {}
                if isinstance(meta, dict) and meta.get("usage"):
                    print(f"  [DONE] tokens: {meta.get('usage')}")
                break
            elif ev == "error":
                raise RuntimeError(event.get("message") or str(event))

            mid = event.get("message_id") or event.get("id")
            if mid:
                message_id = mid
            cid = event.get("conversation_id")
            if cid:
                conv_id = cid

    elapsed = round(time.time() - t0, 1)
    answer = "".join(answer_parts).strip()
    result = {
        "ok": True,
        "query": query,
        "answer": answer,
        "thought": "\n".join(thought_parts),
        "conversation_id": conv_id,
        "message_id": message_id,
        "last_event": last_event,
        "tool_event_count": len(tool_events),
        "elapsed_seconds": elapsed,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")

    print()
    print(f"[INFO] Completed in {elapsed}s, conversation_id={conv_id or '—'}")
    print("-" * 56)
    print(answer or "(no text response; check the Dify chat page or agent_memory)")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Dify Agent 背景執行（不需開瀏覽器）")
    parser.add_argument("query", nargs="?", help="要送給 Agent 的指令")
    parser.add_argument("--file", "-f", help="從檔案讀取指令（UTF-8）")
    parser.add_argument("--conversation-id", "-c", help="延續既有對話 ID")
    parser.add_argument("--timeout", type=int, default=86400, help="最長等待秒數（預設 24h）")
    parser.add_argument("--sync", action="store_true", help="完成後同步聊天歷史到 agent_memory")
    args = parser.parse_args()

    if args.file:
        query = Path(args.file).read_text(encoding="utf-8").strip()
    elif args.query:
        query = args.query.strip()
    else:
        parser.error("query or --file is required")

    if not query:
        print("[ERROR] Query is empty", file=sys.stderr)
        sys.exit(1)

    try:
        result = run_agent_stream(
            query,
            conversation_id=args.conversation_id,
            timeout=args.timeout,
        )
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            detail = exc.response.text[:500]
        print(f"[ERROR] HTTP error: {exc}\n{detail}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"[ERROR] Execution failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.sync:
        try:
            from sync_dify_chat_history import sync_history

            data = sync_history()
            print(f"[INFO] Synced {data['conversation_count']} conversations to agent_memory")
        except Exception as exc:
            print(f"[WARN] Chat history sync failed (task itself completed): {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
