"""Agent proxy helpers — workspace files + Minecraft server control."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_infra import InfrastructureFailure

ROOT = Path(os.environ.get("AGENT_PROXY_ROOT", "/workspace/host")).resolve()
MAX_READ_CHARS = int(os.environ.get("AGENT_PROXY_MAX_READ_CHARS", "80000"))
MAX_GREP_MATCHES = int(os.environ.get("AGENT_PROXY_MAX_GREP_MATCHES", "80"))
TOOL_REQUEST_TIMEOUT = int(os.environ.get("AGENT_PROXY_TOOL_TIMEOUT", "30"))
TOOL_LONG_TIMEOUT = int(os.environ.get("AGENT_PROXY_LONG_TOOL_TIMEOUT", "600"))
RCON_HOST = os.environ.get("AGENT_PROXY_RCON_HOST", "host.docker.internal")
HOST_BRIDGE_URL = os.environ.get("HOST_BRIDGE_URL", "http://host.docker.internal:9879").rstrip("/")
HOST_BRIDGE_TIMEOUT = int(os.environ.get("HOST_BRIDGE_TIMEOUT", "600"))

# Deny escaping project root; allow full tree under ROOT (Cursor-style workspace access).
_DENY_PARTS = {".git", "__pycache__", "node_modules", "dify-data", ".cbm-cache"}
_GREP_EXCLUDE_DIRS = (
    ".git",
    "__pycache__",
    "node_modules",
    "dify-data",
    ".cbm-cache",
    "minecraft-server/_archive",
)


def _timeout_message() -> str:
    return f"Tool execution timed out after {TOOL_REQUEST_TIMEOUT} seconds."


def _kill_subprocess(exc: subprocess.TimeoutExpired) -> None:
    proc = getattr(exc, "process", None)
    if proc is None:
        return
    try:
        proc.kill()
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
        pass


def _grep_timeout_result(pattern: str, path: str, glob: str) -> dict[str, Any]:
    msg = _timeout_message()
    return {
        "pattern": pattern,
        "path": path or ".",
        "glob": glob,
        "files_scanned": 0,
        "matches": [{"path": "(timeout)", "line": 0, "text": msg}],
        "truncated": True,
    }


def norm_rel_path(raw: str) -> str:
    p = raw.replace("\\", "/").lstrip("/")
    if ".." in p.split("/"):
        raise ValueError("路徑不可包含 ..")
    return p


def resolve_path(rel: str) -> Path:
    rel = norm_rel_path(rel)
    target = (ROOT / rel).resolve()
    if not str(target).startswith(str(ROOT)):
        raise ValueError(f"不允許存取：{rel}")
    return target


def read_file(rel: str, offset: int = 0, limit: int = 0) -> dict[str, Any]:
    fp = resolve_path(rel)
    if not fp.is_file():
        raise ValueError(f"檔案不存在：{rel}")
    text = fp.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if offset or limit:
        end = offset + limit if limit > 0 else None
        snippet = "\n".join(lines[offset:end])
    else:
        snippet = text
    truncated = False
    if len(snippet) > MAX_READ_CHARS:
        snippet = snippet[:MAX_READ_CHARS]
        truncated = True
    return {
        "path": rel,
        "total_lines": len(lines),
        "content": snippet,
        "truncated": truncated,
    }


def write_file(rel: str, content: str) -> dict[str, Any]:
    fp = resolve_path(rel)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8", newline="\n")
    return {"path": rel, "result": "已寫入", "bytes": len(content.encode("utf-8"))}


def list_files(rel: str = ".", max_entries: int = 300) -> dict[str, Any]:
    root = resolve_path(rel or ".")
    if not root.is_dir():
        raise ValueError(f"目錄不存在：{rel}")
    entries: list[dict[str, Any]] = []
    for child in sorted(root.iterdir())[:max_entries]:
        if child.name in _DENY_PARTS:
            continue
        rel_child = str(child.relative_to(ROOT)).replace("\\", "/")
        entries.append(
            {
                "name": child.name + ("/" if child.is_dir() else ""),
                "path": rel_child,
                "type": "dir" if child.is_dir() else "file",
            }
        )
    return {"path": rel or ".", "entries": entries, "count": len(entries)}


def grep_files(pattern: str, path: str = ".", glob: str = "*") -> dict[str, Any]:
    if not pattern.strip():
        raise ValueError("pattern 不可為空")
    root = resolve_path(path or ".")
    if not root.exists():
        raise ValueError(f"路徑不存在：{path or '.'}")

    cmd: list[str] = [
        "grep",
        "-r",
        "-n",
        "-I",
        "-E",
        "-m",
        str(MAX_GREP_MATCHES),
    ]
    for part in _GREP_EXCLUDE_DIRS:
        cmd.extend(["--exclude-dir", part])
    if glob != "*":
        cmd.extend(["--include", glob])
    cmd.extend(["-e", pattern, str(root)])

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TOOL_REQUEST_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _kill_subprocess(exc)
        return _grep_timeout_result(pattern, path, glob)

    matches: list[dict[str, Any]] = []
    files_scanned = set()
    root_is_file = root.is_file()
    default_rel = str(root.relative_to(ROOT)).replace("\\", "/") if root_is_file else ""
    for line in (completed.stdout or "").splitlines():
        if len(matches) >= MAX_GREP_MATCHES:
            break
        if ":" not in line:
            continue
        if root_is_file:
            line_no_str, text = line.split(":", 1)
            file_part = default_rel
        else:
            file_part, rest = line.split(":", 1)
            if ":" not in rest:
                continue
            line_no_str, text = rest.split(":", 1)
        try:
            line_no = int(line_no_str)
        except ValueError:
            continue
        try:
            rel = str(Path(file_part).resolve().relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            rel = file_part if file_part else default_rel
        files_scanned.add(rel)
        matches.append({"path": rel, "line": line_no, "text": text[:500]})

    return {
        "pattern": pattern,
        "path": path or ".",
        "glob": glob,
        "files_scanned": len(files_scanned),
        "matches": matches,
        "truncated": len(matches) >= MAX_GREP_MATCHES,
    }


def _server_control():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import server_control as sc  # noqa: WPS433

    for cfg in sc.SERVERS.values():
        cfg["rcon_host"] = RCON_HOST
    return sc


def run_diagnostic(server_type: str = "main") -> dict[str, Any]:
    sc = _server_control()
    st = server_type if server_type in sc.SERVERS else "main"
    compose = ROOT / "docker-compose.yml"
    compose_snip = ""
    if compose.is_file():
        compose_snip = "\n".join(compose.read_text(encoding="utf-8", errors="replace").splitlines()[:80])
    return {
        "server_type": st,
        "container_status": sc.get_container_status(st),
        "stats": sc.get_container_stats(st),
        "properties": sc.read_server_properties(st),
        "latest_log_tail": sc.get_latest_logs(st, lines=30),
        "docker_compose_head": compose_snip,
        "workspace_root": str(ROOT),
    }


def get_server_status(server_type: str = "main") -> dict[str, Any]:
    sc = _server_control()
    st = server_type if server_type in sc.SERVERS else "main"
    return {
        "server_type": st,
        "container_status": sc.get_container_status(st),
        "stats": sc.get_container_stats(st),
        "properties": sc.read_server_properties(st),
    }


def server_action(server_type: str, action: str) -> dict[str, Any]:
    sc = _server_control()
    st = server_type if server_type in sc.SERVERS else "main"
    action = action.lower()
    if action == "start":
        result = sc.start_server(st)
    elif action == "stop":
        result = sc.stop_server(st)
    elif action == "restart":
        result = sc.restart_server(st)
    else:
        raise ValueError("action 必須是 start / stop / restart")
    return {"server_type": st, "action": action, "result": result}


def run_rcon(server_type: str, command: str) -> dict[str, Any]:
    sc = _server_control()
    st = server_type if server_type in sc.SERVERS else "main"
    cmd = command.strip()
    if not cmd:
        raise ValueError("command 不可為空")
    if not cmd.startswith("/"):
        cmd = "/" + cmd
    return {"server_type": st, "command": cmd, "result": sc.send_rcon_command(st, cmd)}


def run_mcdr(server_type: str, command: str) -> dict[str, Any]:
    sc = _server_control()
    st = server_type if server_type in sc.SERVERS else "main"
    cmd = command.strip()
    if not cmd:
        raise ValueError("command 不可為空")
    if not cmd.startswith("!!"):
        cmd = "!!" + cmd
    return {"server_type": st, "command": cmd, "result": sc.send_rcon_command(st, cmd)}


def get_logs(server_type: str, lines: int = 50) -> dict[str, Any]:
    sc = _server_control()
    st = server_type if server_type in sc.SERVERS else "main"
    return {"server_type": st, "log": sc.get_latest_logs(st, lines=lines)}


def update_server_property(server_type: str, key: str, value: str) -> dict[str, Any]:
    sc = _server_control()
    st = server_type if server_type in sc.SERVERS else "main"
    if not key.strip():
        raise ValueError("key 不可為空")
    return {"server_type": st, "result": sc.update_server_properties(st, key.strip(), value.strip())}


# ── Windows 宿主機橋接（PowerShell / 上網 / 任意路徑）────────────────────────


def _host_bridge_connection_error_text(text: str) -> bool:
    low = text.lower()
    markers = (
        "無法連線 windows host bridge",
        "無法連線 host bridge",
        "host bridge 斷線",
        "connection refused",
        "failed to establish a new connection",
        "host.docker.internal:9879",
    )
    return any(m in low for m in markers)


def check_host_bridge_alive(timeout: float = 2.0) -> bool:
    import requests

    try:
        resp = requests.get(f"{HOST_BRIDGE_URL}/health", timeout=timeout)
        if resp.status_code != 200:
            return False
        body = resp.json() if resp.content else {}
        return bool(body.get("ok", False))
    except requests.RequestException:
        return False


def _host_bridge_post(path: str, payload: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
    import requests

    url = f"{HOST_BRIDGE_URL}{path}"
    req_timeout = timeout if timeout is not None else TOOL_REQUEST_TIMEOUT
    try:
        resp = requests.post(url, json=payload, timeout=req_timeout)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise ValueError(data.get("error") or f"host bridge HTTP {resp.status_code}")
        return data
    except requests.Timeout:
        return {"ok": False, "error": _timeout_message(), "timed_out": True}
    except requests.RequestException as exc:
        raise InfrastructureFailure(
            f"無法連線 Windows Host Bridge ({HOST_BRIDGE_URL})。"
            "請在宿主機執行 python manage.py sync 啟動橋接服務。"
            f"詳情：{exc}"
        ) from exc


def host_bridge_health() -> dict[str, Any]:
    import requests

    try:
        resp = requests.get(f"{HOST_BRIDGE_URL}/health", timeout=10)
        return {"ok": resp.status_code == 200, "url": HOST_BRIDGE_URL, "body": resp.json()}
    except requests.RequestException as exc:
        return {"ok": False, "url": HOST_BRIDGE_URL, "error": str(exc)}


def run_powershell(command: str, cwd: str = "", timeout: int = 0) -> dict[str, Any]:
    if not command.strip():
        raise ValueError("command 不可為空")
    payload: dict[str, Any] = {"command": command}
    if cwd.strip():
        payload["cwd"] = cwd
    req_timeout = timeout if timeout > 0 else TOOL_REQUEST_TIMEOUT
    if timeout > 0:
        req_timeout = min(timeout, TOOL_LONG_TIMEOUT)
    data = _host_bridge_post("/exec", payload, timeout=req_timeout)
    if data.get("timed_out"):
        msg = data.get("error") or _timeout_message()
        return {
            "command": command,
            "cwd": cwd or None,
            "ok": False,
            "error": msg,
            "stdout": "",
            "stderr": msg,
            "exit_code": -1,
        }
    return {"command": command, "cwd": cwd or None, **data}


def fetch_url(
    url: str,
    method: str = "GET",
    body: str = "",
    headers: dict[str, str] | None = None,
    timeout: int = 0,
) -> dict[str, Any]:
    if not url.strip():
        raise ValueError("url 不可為空")
    payload: dict[str, Any] = {"url": url.strip(), "method": method.upper()}
    if body:
        payload["body"] = body
    if headers:
        payload["headers"] = headers
    try:
        data = _host_bridge_post("/fetch", payload, timeout=timeout or TOOL_REQUEST_TIMEOUT)
        if data.get("timed_out"):
            msg = data.get("error") or _timeout_message()
            return {
                "ok": False,
                "status_code": 0,
                "url": url,
                "content": msg,
                "truncated": False,
                "via": "host_bridge",
            }
        return data
    except InfrastructureFailure:
        raise
    except ValueError as exc:
        if _host_bridge_connection_error_text(str(exc)):
            raise InfrastructureFailure(str(exc)) from exc
        import requests as req

        req_timeout = timeout if timeout > 0 else TOOL_REQUEST_TIMEOUT
        try:
            resp = req.request(
                method.upper(),
                url,
                data=body or None,
                headers=headers,
                timeout=req_timeout,
            )
        except req.Timeout:
            return {
                "ok": False,
                "status_code": 0,
                "url": url,
                "content": _timeout_message(),
                "truncated": False,
                "via": "container_fallback",
            }
        text = resp.text
        truncated = len(text) > MAX_READ_CHARS
        if truncated:
            text = text[:MAX_READ_CHARS]
        return {
            "ok": True,
            "status_code": resp.status_code,
            "url": url,
            "content": text,
            "truncated": truncated,
            "via": "container_fallback",
        }


def host_read_file(path: str) -> dict[str, Any]:
    if not path.strip():
        raise ValueError("path 不可為空")
    data = _host_bridge_post("/read_file", {"path": path}, timeout=TOOL_REQUEST_TIMEOUT)
    if data.get("timed_out"):
        msg = data.get("error") or _timeout_message()
        return {"ok": False, "path": path, "content": msg, "error": msg}
    if not data.get("ok", True) and "content" not in data:
        data.setdefault("content", data.get("error") or _timeout_message())
    return data


def host_write_file(path: str, content: str) -> dict[str, Any]:
    if not path.strip():
        raise ValueError("path 不可為空")
    data = _host_bridge_post("/write_file", {"path": path, "content": content}, timeout=TOOL_REQUEST_TIMEOUT)
    if data.get("timed_out"):
        msg = data.get("error") or _timeout_message()
        return {"ok": False, "path": path, "error": msg, "result": msg}
    return data


def host_list_dir(path: str = "C:\\") -> dict[str, Any]:
    data = _host_bridge_post("/list_dir", {"path": path or "C:\\"}, timeout=TOOL_REQUEST_TIMEOUT)
    if data.get("timed_out"):
        msg = data.get("error") or _timeout_message()
        return {"ok": False, "path": path or "C:\\", "entries": [], "error": msg}
    if "entries" not in data and not data.get("ok", True):
        data.setdefault("entries", [])
        data.setdefault("path", path or "C:\\")
    return data


def server_backend_engineering(
    query: str,
    conversation_history: str = "",
    task_mode: str = "logic",
) -> dict[str, Any]:
    """呼叫 GRAVITY 伺服器後端工程 Workflow（Flash + Reasoner + JSON 洗白）。"""
    import importlib.util

    wf_client = ROOT / "dify-workflow" / "workflow_client.py"
    app_dir = Path(__file__).resolve().parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    spec = importlib.util.spec_from_file_location("gravity_workflow_client", wf_client)
    if spec is None or spec.loader is None:
        raise ValueError("找不到 dify-workflow/workflow_client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        return mod.run_backend_engineering(
            query,
            conversation_history=conversation_history,
            task_mode=task_mode,
        )
    except InfrastructureFailure:
        raise
    except Exception as exc:
        msg = str(exc)
        if "http 401" in msg.lower() or "http 403" in msg.lower():
            raise InfrastructureFailure(msg) from exc
        if "timeout" in msg.lower() or "timed out" in msg.lower():
            msg = _timeout_message()
        return {"ok": False, "error": msg, "summary_for_user": f"後端工程 Pipeline 失敗：{msg}"}
