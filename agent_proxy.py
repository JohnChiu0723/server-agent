from __future__ import annotations

import asyncio
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, Callable

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import agent_tools as at
import cbm_tools as cbm
from agent_infra import CIRCUIT_BREAKER_HTTP_STATUSES, InfrastructureFailure


APP_TITLE = "GRAVITY Agent Workspace"
DEFAULT_ROOT = "/workspace/host"
DEFAULT_TIMEOUT = 900
MAX_TIMEOUT = 7200
MAX_OUTPUT_CHARS = int(os.environ.get("AGENT_PROXY_MAX_OUTPUT_CHARS", "16000"))
TOOL_REQUEST_TIMEOUT = int(os.environ.get("AGENT_PROXY_TOOL_TIMEOUT", "30"))
TOOL_LONG_TIMEOUT = int(os.environ.get("AGENT_PROXY_LONG_TOOL_TIMEOUT", "600"))
TOOL_THREAD_POOL_SIZE = int(os.environ.get("AGENT_PROXY_THREAD_POOL", "32"))
TOKEN = os.environ.get("AGENT_PROXY_TOKEN", "").strip()
AUTH_DISABLED = os.environ.get("AGENT_PROXY_AUTH_DISABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
ROOT = Path(os.environ.get("AGENT_PROXY_ROOT", DEFAULT_ROOT)).resolve()
_tool_executor: ThreadPoolExecutor | None = None

INFRA_CIRCUIT_BREAKER_ERROR = "INFRASTRUCTURE_CRITICAL_FAILURE"
INFRA_CIRCUIT_BREAKER_STATUS = int(os.environ.get("AGENT_PROXY_CIRCUIT_BREAKER_STATUS", "503"))
INFRA_CIRCUIT_BREAKER_MESSAGE = (
    "宿主機通訊通道 (Host Bridge) 已斷線，或沙盒鑑權失效。"
    "此為底層系統錯誤，AI 無法透過指令自動修復。請聯絡管理員檢查容器狀態。"
)

_HOST_BRIDGE_MARKERS = (
    "host bridge 斷線",
    "host bridge disconnected",
    "無法連線 windows host bridge",
    "無法連線 host bridge",
    "connection refused",
    "connectionrefusederror",
    "failed to establish a new connection",
    "max retries exceeded",
    "host.docker.internal:9879",
    "host_bridge_url",
    "name or service not known",
    "nodename nor servname provided",
)

_SANDBOX_401_MARKERS = (
    "http 401",
    "http 403",
    "401 unauthorized",
    "403 forbidden",
    "status_code=401",
    "status_code=403",
    "workflow api http 401",
    "workflow api http 403",
)


app = FastAPI(title=APP_TITLE, version="1.2.0")


@app.exception_handler(HTTPException)
async def _passthrough_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
    """熔斷 503/424 必須原樣輸出 JSON 本體，禁止包裝成 200 或嵌套 detail 導致 Dify 誤判成功。"""
    if exc.status_code in CIRCUIT_BREAKER_HTTP_STATUSES:
        body = exc.detail if isinstance(exc.detail, dict) else _circuit_breaker_payload()
        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            headers={"X-Infrastructure-Circuit-Breaker": "1"},
        )
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.on_event("startup")
def _configure_thread_pool() -> None:
    global _tool_executor
    _tool_executor = ThreadPoolExecutor(
        max_workers=TOOL_THREAD_POOL_SIZE,
        thread_name_prefix="agent-tool",
    )


@app.on_event("shutdown")
def _shutdown_thread_pool() -> None:
    global _tool_executor
    if _tool_executor is not None:
        _tool_executor.shutdown(wait=False, cancel_futures=True)
        _tool_executor = None


class ExecRequest(BaseModel):
    command: str = Field(..., min_length=1, description="Shell command executed with /bin/bash -lc")
    cwd: str | None = Field(default=None, description="Relative path under AGENT_PROXY_ROOT or absolute path inside it")
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)
    env: dict[str, str] | None = Field(default=None, description="Additional environment variables")


class HealthCheckSpec(BaseModel):
    url: str | None = None
    command: str | None = None
    timeout: int = Field(default=30, ge=1, le=300)
    expected_status: int = Field(default=200, ge=100, le=599)


class SelfHealRequest(ExecRequest):
    retries: int = Field(default=2, ge=0, le=5)
    cooldown_seconds: int = Field(default=3, ge=0, le=60)
    fix_commands: list[str] = Field(default_factory=list)
    diagnostics_commands: list[str] = Field(default_factory=list)
    healthcheck: HealthCheckSpec | None = None


class PathRequest(BaseModel):
    path: str = Field(..., description="相對於專案根目錄的路徑")


class ReadFileRequest(PathRequest):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=0, ge=0, description="0 表示讀到結尾或上限")


class WriteFileRequest(PathRequest):
    content: str


class GrepRequest(BaseModel):
    pattern: str
    path: str = "."
    glob: str = "*"


class ServerTypeRequest(BaseModel):
    server_type: str = Field(default="main", description="main 或 mirror")


class ServerActionRequest(ServerTypeRequest):
    action: str = Field(..., description="start / stop / restart")


class RconRequest(ServerTypeRequest):
    command: str


class LogsRequest(ServerTypeRequest):
    lines: int = Field(default=50, ge=1, le=500)


class UpdatePropertyRequest(ServerTypeRequest):
    key: str
    value: str


class PowerShellRequest(BaseModel):
    command: str = Field(..., min_length=1)
    cwd: str = Field(default="")
    timeout: int = Field(default=0, ge=0, le=MAX_TIMEOUT)


class FetchUrlRequest(BaseModel):
    url: str = Field(..., min_length=1)
    method: str = Field(default="GET")
    body: str = Field(default="")
    headers: dict[str, str] | None = None
    timeout: int = Field(default=0, ge=0, le=MAX_TIMEOUT)


class HostPathRequest(BaseModel):
    path: str = Field(..., min_length=1)


class HostWriteRequest(BaseModel):
    path: str = Field(..., min_length=1)
    content: str


class CodebaseMemoryRequest(BaseModel):
    tool: str = Field(
        ...,
        description=(
            "codebase-memory-mcp 工具名：index_repository, search_graph, trace_path, "
            "get_architecture, query_graph, search_code, get_code_snippet, list_projects, "
            "index_status, detect_changes, get_graph_schema, manage_adr, delete_project, ingest_traces"
        ),
    )
    arguments: dict[str, Any] = Field(default_factory=dict, description="傳給 CBM 的 JSON 參數")


class ServerBackendEngineeringRequest(BaseModel):
    query: str = Field(..., min_length=1, description="使用者完整維運問題")
    conversation_history: str = Field(default="", description="對話上下文摘要")
    task_mode: str = Field(default="logic", description="logic=日誌診斷 | code=MCDR/Docker | calc_vars=配置精算")


def _tool_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def _collect_text_fragments(value: Any, out: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            out.append(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_text_fragments(item, out)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_text_fragments(item, out)
        return
    out.append(str(value))


def _text_blob(*parts: Any) -> str:
    fragments: list[str] = []
    for part in parts:
        _collect_text_fragments(part, fragments)
    return " ".join(fragments).lower()


def _text_indicates_host_bridge_failure(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _HOST_BRIDGE_MARKERS)


def _text_indicates_sandbox_401(text: str) -> bool:
    low = text.lower()
    if any(marker in low for marker in _SANDBOX_401_MARKERS):
        return True
    return ("401" in low or "403" in low) and (
        "unauthorized" in low or "forbidden" in low or "workflow" in low or "sandbox" in low
    )


def _is_infra_fatal_exception(exc: BaseException) -> bool:
    if isinstance(exc, InfrastructureFailure):
        return True
    if isinstance(exc, HTTPException) and exc.status_code in CIRCUIT_BREAKER_HTTP_STATUSES:
        return True
    if isinstance(exc, ConnectionRefusedError):
        return True
    if isinstance(
        exc,
        (
            requests.ConnectionError,
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ReadTimeout,
            requests.exceptions.Timeout,
        ),
    ):
        return True
    return _text_indicates_host_bridge_failure(str(exc)) or _text_indicates_sandbox_401(str(exc))


_HOST_BRIDGE_TOOLS = frozenset(
    {
        "run_powershell",
        "fetch_url",
        "host_read_file",
        "host_write_file",
        "host_list_dir",
    }
)


def _is_infra_fatal_result(result: Any, tool_name: str) -> bool:
    if not isinstance(result, dict):
        return False
    if tool_name in _HOST_BRIDGE_TOOLS and result.get("timed_out"):
        return True
    blob = _text_blob(
        result.get("error"),
        result.get("stderr"),
        result.get("content"),
        result.get("summary_for_user"),
        result.get("result"),
        result.get("log"),
    )
    if tool_name == "server_backend_engineering":
        return _text_indicates_sandbox_401(blob) or _text_indicates_host_bridge_failure(blob)
    if tool_name in _HOST_BRIDGE_TOOLS:
        return _text_indicates_host_bridge_failure(blob)
    return _text_indicates_host_bridge_failure(blob) or _text_indicates_sandbox_401(blob)


def _circuit_breaker_payload() -> dict[str, Any]:
    return {
        "ok": False,
        "error": INFRA_CIRCUIT_BREAKER_ERROR,
        "message": INFRA_CIRCUIT_BREAKER_MESSAGE,
    }


def _raise_circuit_breaker() -> None:
    """硬熔斷：HTTP 503 強制 Dify 判定工具節點失敗，終止 ReAct 迭代（禁止模型編劇排查）。"""
    status = INFRA_CIRCUIT_BREAKER_STATUS if INFRA_CIRCUIT_BREAKER_STATUS in CIRCUIT_BREAKER_HTTP_STATUSES else 503
    raise HTTPException(status_code=status, detail=_circuit_breaker_payload())


def _preflight_host_bridge(tool_name: str) -> None:
    if tool_name not in _HOST_BRIDGE_TOOLS:
        return
    if not at.check_host_bridge_alive():
        _raise_circuit_breaker()


async def _execute_tool(
    tool_name: str,
    fn: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """中央工具路由：基建致命錯誤時以 HTTP 503/424 硬熔斷，不讓模型消化錯誤內容。"""
    _preflight_host_bridge(tool_name)
    try:
        result = await _invoke_tool(fn, *args, **kwargs)
    except HTTPException as http_exc:
        if http_exc.status_code in CIRCUIT_BREAKER_HTTP_STATUSES:
            raise
        raise http_exc
    except InfrastructureFailure:
        _raise_circuit_breaker()
    except Exception as exc:
        if _is_infra_fatal_exception(exc):
            _raise_circuit_breaker()
        raise _tool_error(exc) from exc

    if _is_infra_fatal_result(result, tool_name):
        _raise_circuit_breaker()
    return result


async def _invoke_tool(
    fn: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """在獨立執行緒執行同步工具；超時由工具內部 subprocess/requests 控制。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_tool_executor, partial(fn, *args, **kwargs))


def verify_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
    api_key: str | None = Header(default=None, alias="api-key"),
) -> None:
    if AUTH_DISABLED:
        return

    if not TOKEN:
        raise HTTPException(status_code=500, detail="AGENT_PROXY_TOKEN is not configured")

    bearer = ""
    if authorization:
        bearer = authorization.removeprefix("Bearer ").strip()

    if bearer == TOKEN or (api_key and api_key.strip() == TOKEN):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def _safe_cwd(raw_cwd: str | None) -> Path:
    if not raw_cwd:
        return ROOT

    candidate = Path(raw_cwd)
    resolved = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    if not str(resolved).startswith(str(ROOT)):
        raise HTTPException(status_code=403, detail=f"Working directory escapes root: {raw_cwd}")
    return resolved


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n...<truncated>"


def _base_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    env = dict(os.environ)
    env["PROJECT_ROOT"] = str(ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})
    return env


def _kill_subprocess(exc: subprocess.TimeoutExpired) -> None:
    proc = getattr(exc, "process", None)
    if proc is None:
        return
    try:
        proc.kill()
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
        pass


def _run_shell(command: str, cwd: Path, timeout: int, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.time()
    try:
        completed = subprocess.run(
            ["/bin/bash", "-lc", command],
            cwd=str(cwd),
            env=_base_env(extra_env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _kill_subprocess(exc)
        duration = round(time.time() - started, 3)
        msg = f"Tool execution timed out after {timeout} seconds."
        return {
            "command": command,
            "cwd": str(cwd),
            "exit_code": -1,
            "duration_seconds": duration,
            "stdout": "",
            "stderr": msg,
            "ok": False,
        }
    duration = round(time.time() - started, 3)
    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "duration_seconds": duration,
        "stdout": _truncate(completed.stdout),
        "stderr": _truncate(completed.stderr),
        "ok": completed.returncode == 0,
    }


def _run_diagnostic_commands(commands: list[str], cwd: Path) -> list[dict[str, Any]]:
    return [_run_shell(command, cwd=cwd, timeout=90) for command in commands if command.strip()]


def _check_health(spec: HealthCheckSpec | None, cwd: Path) -> dict[str, Any]:
    if spec is None:
        return {"ok": True, "mode": "none"}

    if spec.url:
        try:
            response = requests.get(spec.url, timeout=spec.timeout)
            ok = response.status_code == spec.expected_status
            return {
                "ok": ok,
                "mode": "url",
                "url": spec.url,
                "status_code": response.status_code,
                "expected_status": spec.expected_status,
                "body": _truncate(response.text),
            }
        except requests.RequestException as exc:
            return {"ok": False, "mode": "url", "url": spec.url, "error": str(exc)}

    if spec.command:
        result = _run_shell(spec.command, cwd=cwd, timeout=spec.timeout)
        result["mode"] = "command"
        return result

    return {"ok": True, "mode": "none"}


@app.get("/health")
def health(_: None = Depends(verify_auth)) -> dict[str, Any]:
    versions: dict[str, Any] = {}
    for binary in ("python3", "docker", "java"):
        try:
            versions[binary] = _run_shell(f"{binary} --version", cwd=ROOT, timeout=20)
        except Exception as exc:  # pragma: no cover - defensive only
            versions[binary] = {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "service": APP_TITLE,
        "root": str(ROOT),
        "auth_disabled": AUTH_DISABLED,
        "host_bridge": at.host_bridge_health(),
        "versions": versions,
    }


@app.post("/exec")
def exec_command(payload: ExecRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    cwd = _safe_cwd(payload.cwd)
    return _run_shell(payload.command, cwd=cwd, timeout=payload.timeout, extra_env=payload.env)


@app.post("/self-heal")
def self_heal(payload: SelfHealRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    cwd = _safe_cwd(payload.cwd)
    primary = _run_shell(payload.command, cwd=cwd, timeout=payload.timeout, extra_env=payload.env)
    initial_health = _check_health(payload.healthcheck, cwd=cwd)

    healed = primary["ok"] and initial_health["ok"]
    attempts: list[dict[str, Any]] = []

    diagnostics_commands = payload.diagnostics_commands or [
        "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'",
        "docker compose ps",
    ]

    for retry_index in range(1, payload.retries + 1):
        if healed:
            break

        attempt: dict[str, Any] = {
            "retry": retry_index,
            "before_fix_diagnostics": _run_diagnostic_commands(diagnostics_commands, cwd=cwd),
            "fix_results": _run_diagnostic_commands(payload.fix_commands, cwd=cwd),
        }

        if payload.cooldown_seconds:
            time.sleep(payload.cooldown_seconds)

        attempt["rerun"] = _run_shell(payload.command, cwd=cwd, timeout=payload.timeout, extra_env=payload.env)
        attempt["healthcheck"] = _check_health(payload.healthcheck, cwd=cwd)
        healed = attempt["rerun"]["ok"] and attempt["healthcheck"]["ok"]
        attempts.append(attempt)

    return {
        "ok": healed,
        "root": str(ROOT),
        "initial_run": primary,
        "initial_healthcheck": initial_health,
        "attempts": attempts,
    }


# ── Workspace tools (Cursor-style) ───────────────────────────────────────────


@app.post("/tool/read_file")
async def tool_read_file(payload: ReadFileRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("read_file", at.read_file, payload.path, payload.offset, payload.limit)


@app.post("/tool/write_file")
async def tool_write_file(payload: WriteFileRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("write_file", at.write_file, payload.path, payload.content)


@app.post("/tool/list_files")
async def tool_list_files(payload: PathRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("list_files", at.list_files, payload.path)


@app.post("/tool/grep_files")
async def tool_grep_files(payload: GrepRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("grep_files", at.grep_files, payload.pattern, payload.path, payload.glob)


# ── Minecraft server tools ─────────────────────────────────────────────────────


@app.post("/tool/run_diagnostic")
async def tool_run_diagnostic(payload: ServerTypeRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("run_diagnostic", at.run_diagnostic, payload.server_type)


@app.post("/tool/server_status")
async def tool_server_status(payload: ServerTypeRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("server_status", at.get_server_status, payload.server_type)


@app.post("/tool/server_action")
async def tool_server_action(payload: ServerActionRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("server_action", at.server_action, payload.server_type, payload.action)


@app.post("/tool/rcon")
async def tool_rcon(payload: RconRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("rcon", at.run_rcon, payload.server_type, payload.command)


@app.post("/tool/mcdr")
async def tool_mcdr(payload: RconRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("mcdr", at.run_mcdr, payload.server_type, payload.command)


@app.post("/tool/logs")
async def tool_logs(payload: LogsRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("logs", at.get_logs, payload.server_type, payload.lines)


@app.post("/tool/update_property")
async def tool_update_property(payload: UpdatePropertyRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool(
        "update_property",
        at.update_server_property,
        payload.server_type,
        payload.key,
        payload.value,
    )


# ── Windows host bridge tools ──────────────────────────────────────────────────


@app.post("/tool/run_powershell")
async def tool_run_powershell(payload: PowerShellRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool(
        "run_powershell",
        at.run_powershell,
        payload.command,
        payload.cwd,
        payload.timeout,
    )


@app.post("/tool/fetch_url")
async def tool_fetch_url(payload: FetchUrlRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool(
        "fetch_url",
        at.fetch_url,
        payload.url,
        payload.method,
        payload.body,
        payload.headers,
        payload.timeout,
    )


@app.post("/tool/host_read_file")
async def tool_host_read_file(payload: HostPathRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("host_read_file", at.host_read_file, payload.path)


@app.post("/tool/host_write_file")
async def tool_host_write_file(payload: HostWriteRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("host_write_file", at.host_write_file, payload.path, payload.content)


@app.post("/tool/host_list_dir")
async def tool_host_list_dir(payload: HostPathRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("host_list_dir", at.host_list_dir, payload.path)


@app.post("/tool/codebase_memory")
async def tool_codebase_memory(payload: CodebaseMemoryRequest, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("codebase_memory", cbm.invoke, payload.tool, payload.arguments)


@app.get("/tool/codebase_memory/health")
async def tool_codebase_memory_health(_: None = Depends(verify_auth)) -> dict[str, Any]:
    return await _execute_tool("codebase_memory_health", cbm.health)


@app.post("/tool/server_backend_engineering")
async def tool_server_backend_engineering(
    payload: ServerBackendEngineeringRequest,
    _: None = Depends(verify_auth),
) -> dict[str, Any]:
    mode = payload.task_mode.strip().lower()
    if mode not in {"logic", "code", "calc_vars"}:
        mode = "logic"
    return await _execute_tool(
        "server_backend_engineering",
        at.server_backend_engineering,
        payload.query,
        conversation_history=payload.conversation_history,
        task_mode=mode,
    )
