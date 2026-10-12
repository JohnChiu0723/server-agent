"""codebase-memory-mcp CLI bridge for GRAVITY agent_proxy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

CBM_BIN = os.environ.get("CBM_BIN", "/usr/local/bin/codebase-memory-mcp")
CBM_INDEX_TIMEOUT = int(os.environ.get("CBM_TIMEOUT", "3600"))
TOOL_REQUEST_TIMEOUT = int(os.environ.get("AGENT_PROXY_TOOL_TIMEOUT", "30"))
CBM_MAX_OUTPUT = int(os.environ.get("CBM_MAX_OUTPUT_CHARS", "120000"))
DEFAULT_REPO = os.environ.get("CBM_DEFAULT_REPO", "/workspace/host")

ALLOWED_TOOLS = frozenset(
    {
        "index_repository",
        "list_projects",
        "delete_project",
        "index_status",
        "search_graph",
        "trace_path",
        "detect_changes",
        "query_graph",
        "get_graph_schema",
        "get_code_snippet",
        "get_architecture",
        "search_code",
        "manage_adr",
        "ingest_traces",
    }
)


def _cbm_available() -> bool:
    return bool(shutil.which(CBM_BIN) or Path(CBM_BIN).is_file())


def _cbm_timeout(tool_name: str) -> int:
    if tool_name == "index_repository":
        return CBM_INDEX_TIMEOUT
    return TOOL_REQUEST_TIMEOUT


def _timeout_message(limit: int) -> str:
    return f"Tool execution timed out after {limit} seconds."


def _kill_subprocess(exc: subprocess.TimeoutExpired) -> None:
    proc = getattr(exc, "process", None)
    if proc is None:
        return
    try:
        proc.kill()
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
        pass


def _timeout_result(tool_name: str, args: dict[str, Any], limit: int) -> dict[str, Any]:
    msg = _timeout_message(limit)
    cache_dir = os.environ.get("CBM_CACHE_DIR", "/workspace/host/.cbm-cache")
    return {
        "ok": False,
        "tool": tool_name,
        "arguments": args,
        "exit_code": -1,
        "result": {"error": msg},
        "stderr": msg,
        "truncated": False,
        "parse_error": None,
        "cbm_bin": CBM_BIN,
        "cache_dir": cache_dir,
    }


def invoke(tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    name = (tool or "").strip()
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"不支援的 CBM 工具: {name}. 可用: {', '.join(sorted(ALLOWED_TOOLS))}")
    if not _cbm_available():
        raise RuntimeError(f"codebase-memory-mcp 未安裝（預期路徑: {CBM_BIN}）")

    args = dict(arguments or {})
    if name == "index_repository" and not args.get("repo_path"):
        args["repo_path"] = DEFAULT_REPO

    payload = json.dumps(args, ensure_ascii=False)
    env = os.environ.copy()
    cache_dir = env.setdefault("CBM_CACHE_DIR", os.environ.get("CBM_CACHE_DIR", "/workspace/host/.cbm-cache"))
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    limit = _cbm_timeout(name)
    try:
        proc = subprocess.run(
            [CBM_BIN, "cli", name, payload],
            capture_output=True,
            text=True,
            timeout=limit,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        _kill_subprocess(exc)
        return _timeout_result(name, args, limit)

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    truncated = len(stdout) > CBM_MAX_OUTPUT
    if truncated:
        stdout = stdout[:CBM_MAX_OUTPUT]

    parsed: Any = None
    parse_error = ""
    if stdout.strip():
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            parse_error = str(exc)

    ok = proc.returncode == 0 and not parse_error
    return {
        "ok": ok,
        "tool": name,
        "arguments": args,
        "exit_code": proc.returncode,
        "result": parsed if parsed is not None else stdout,
        "stderr": stderr[:8000] if stderr else "",
        "truncated": truncated,
        "parse_error": parse_error or None,
        "cbm_bin": CBM_BIN,
        "cache_dir": env.get("CBM_CACHE_DIR"),
    }


def health() -> dict[str, Any]:
    info: dict[str, Any] = {
        "installed": _cbm_available(),
        "bin": CBM_BIN,
        "default_repo": DEFAULT_REPO,
        "cache_dir": os.environ.get("CBM_CACHE_DIR", "/workspace/host/.cbm-cache"),
        "tools": sorted(ALLOWED_TOOLS),
    }
    if not info["installed"]:
        info["ok"] = False
        return info
    try:
        listed = invoke("list_projects", {})
        info["ok"] = listed.get("ok", False)
        info["projects"] = listed.get("result")
    except Exception as exc:
        info["ok"] = False
        info["error"] = str(exc)
    return info


def ensure_indexed(repo_path: str | None = None) -> dict[str, Any]:
    path = (repo_path or DEFAULT_REPO).strip()
    status = invoke("index_status", {"repo_path": path})
    if status.get("ok") and isinstance(status.get("result"), dict):
        result = status["result"]
        if result.get("indexed") or result.get("status") in ("indexed", "ready", "ok"):
            return {"ok": True, "action": "skip", "status": status["result"]}
    return invoke("index_repository", {"repo_path": path})
