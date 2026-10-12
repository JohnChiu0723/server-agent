"""
setup_dify_gravity.py — GRAVITY 自架 Dify 一鍵接線
=================================================
使用：python manage.py sync  或  python -m core.setup_dify_gravity
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

from core import dsl_builder
from env_loader import load_env

load_env()
from core.setup_dify_workflow import ensure_agent_backend_tool, ensure_workflow_tool_registered, find_workflow_app_id

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIFY_BASE = os.environ.get("DIFY_CONSOLE_URL", "http://localhost:8080")
ADMIN_EMAIL = os.environ.get("DIFY_ADMIN_EMAIL", "admin@gravity.local")
ADMIN_PASSWORD = os.environ.get("DIFY_ADMIN_PASSWORD", "")
AGENT_APP_NAME = os.environ.get("DIFY_AGENT_APP_NAME", "graviyt管家")
TOOL_PROVIDER = "gravity_agent_workspace"
OPENAPI_FILE = PROJECT_ROOT / "dify-agent-proxy-openapi.yaml"
PROMPT_FILE = PROJECT_ROOT / "dify_agent_system_prompt.txt"
HOST_BRIDGE_SCRIPT = PROJECT_ROOT / "docker" / "host-bridge" / "host_bridge.ps1"
HOST_BRIDGE_TASK = "GRAVITY-Host-Bridge"
AGENT_PROXY_URL = os.environ.get("AGENT_PROXY_URL", "http://127.0.0.1:8788")

EMBEDDING_PROVIDER = "langgenius/ollama/ollama"
EMBEDDING_MODEL = "bge-m3"
OLLAMA_BASE_URL = os.environ.get("DIFY_OLLAMA_BASE_URL", "http://ollama:11434")
RERANK_PROVIDER = "langgenius/huggingface_tei/huggingface_tei"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
TEI_SERVER_URL = os.environ.get("DIFY_TEI_SERVER_URL", "http://tei-reranker:80")


def _load_env_value(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if val:
        return val
    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def _write_env_value(name: str, value: str) -> None:
    env_file = PROJECT_ROOT / ".env"
    lines: list[str] = []
    found = False
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                lines.append(f"{name}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{name}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_agent_token() -> str:
    token = _load_env_value("AGENT_PROXY_API_KEY")
    return token or "server-steward-agent-proxy"


class DifySession:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "Origin": DIFY_BASE,
            "Referer": f"{DIFY_BASE}/",
        })

    def login(self) -> None:
        pwd = base64.b64encode(ADMIN_PASSWORD.encode()).decode()
        resp = self.session.post(
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
        if self.session.cookies.get("access_token"):
            self.session.headers["Authorization"] = (
                f"Bearer {self.session.cookies['access_token']}"
            )
        if self.session.cookies.get("csrf_token"):
            self.session.headers["X-CSRF-Token"] = self.session.cookies["csrf_token"]

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.session.get(f"{DIFY_BASE}{path}", timeout=kwargs.pop("timeout", 30), **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.session.post(f"{DIFY_BASE}{path}", timeout=kwargs.pop("timeout", 60), **kwargs)


def wait_for_dify(sess: DifySession, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = sess.get("/console/api/setup")
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def ensure_host_bridge() -> None:
    """註冊監督排程並啟動 Windows Host Bridge（無需任何 .bat/.exe）。"""
    print("[0/5] 確保 Windows Host Bridge（PowerShell / 上網 / 本機）…")
    if sys.platform != "win32":
        print("  [WARN] 非 Windows 宿主機")
        return
    if not HOST_BRIDGE_SCRIPT.is_file():
        print(f"  [ERROR] 缺少 {HOST_BRIDGE_SCRIPT}")
        return

    port = int(os.environ.get("HOST_BRIDGE_PORT", "9879"))
    health_url = f"http://127.0.0.1:{port}/health"
    try:
        r = requests.get(health_url, timeout=2)
        if r.status_code == 200:
            print("  [INFO] Host Bridge 已在運行")
            # still refresh the keeper task registration below
        else:
            raise RuntimeError(f"health {r.status_code}")
    except Exception:
        pass
    else:
        # healthy — still ensure scheduled keeper exists
        keep_register = PROJECT_ROOT / "scripts" / "Register-Host-Bridge-Task.ps1"
        if keep_register.is_file():
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(keep_register),
                    "-TaskName",
                    HOST_BRIDGE_TASK,
                    "-DelaySeconds",
                    "20",
                    "-Port",
                    str(port),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        return

    keep_register = PROJECT_ROOT / "scripts" / "Register-Host-Bridge-Task.ps1"
    keep_script = PROJECT_ROOT / "scripts" / "Keep-Host-Bridge.ps1"
    if keep_register.is_file():
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        gravity = Path(local) / "GRAVITY"
        gravity.mkdir(parents=True, exist_ok=True)
        marker = gravity / "project-root.txt"
        if not marker.is_file():
            marker.write_text(str(PROJECT_ROOT), encoding="utf-16")
        reg = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(keep_register),
                "-TaskName",
                HOST_BRIDGE_TASK,
                "-DelaySeconds",
                "20",
                "-Port",
                str(port),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if reg.returncode == 0:
            print(f"  [INFO] 已註冊監督排程 {HOST_BRIDGE_TASK}")
        else:
            print(f"  [ERROR] 註冊監督排程失敗: {(reg.stderr or reg.stdout or '')[:200]}")

    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    if keep_script.is_file():
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(keep_script),
                "-Port",
                str(port),
            ],
            creationflags=flags,
        )
    else:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(HOST_BRIDGE_SCRIPT),
            ],
            creationflags=flags,
        )

    for _ in range(15):
        time.sleep(2)
        try:
            r = requests.get(health_url, timeout=5)
            if r.status_code == 200:
                print("  [INFO] Host Bridge 已啟動（監督常駐 + 掛掉自動重啟）")
                return
        except Exception:
            pass
    print("  [ERROR] Host Bridge 啟動失敗，請以系統管理員執行 python manage.py install-autostart")


def ensure_codebase_memory() -> None:
    """重建 agent_proxy（內含 codebase-memory-mcp）並索引專案。"""
    print("[0.5/6] 安裝 codebase-memory-mcp 到 agent_proxy 並索引專案…")
    compose = ["docker", "compose", "build", "agent_proxy"]
    up = ["docker", "compose", "up", "-d", "agent_proxy"]
    try:
        subprocess.run(compose, cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
        subprocess.run(up, cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        print(f"  [ERROR] agent_proxy 建置失敗: {(exc.stderr or exc.stdout or exc)[:400]}")
        return

    health_url = f"{AGENT_PROXY_URL.rstrip('/')}/health"
    for _ in range(30):
        try:
            if requests.get(health_url, timeout=3).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        print("  [ERROR] agent_proxy 未就緒，略過 CBM 索引")
        return

    try:
        h = requests.get(f"{AGENT_PROXY_URL.rstrip('/')}/tool/codebase_memory/health", timeout=30)
        if h.status_code == 200 and not (h.json() or {}).get("installed"):
            print("  [ERROR] codebase-memory-mcp 二進位未就緒")
            return
    except Exception as exc:
        print(f"  [ERROR] CBM health 失敗: {exc}")
        return

    try:
        listed = requests.post(
            f"{AGENT_PROXY_URL.rstrip('/')}/tool/codebase_memory",
            json={"tool": "list_projects", "arguments": {}},
            timeout=60,
        ).json()
        projects = ((listed.get("result") or {}).get("projects") or []) if listed.get("ok") else []
        if projects:
            print(f"  [INFO] codebase-memory 已索引 {len(projects)} 個專案（{projects[0].get('nodes', '?')} nodes）")
            return
    except Exception:
        pass

    try:
        r = requests.post(
            f"{AGENT_PROXY_URL.rstrip('/')}/tool/codebase_memory",
            json={"tool": "index_repository", "arguments": {"repo_path": "/workspace/host"}},
            timeout=3600,
        )
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and data.get("ok"):
            print("  [INFO] 專案已索引至 codebase-memory 圖譜")
        else:
            print(f"  [ERROR] 索引回應: {r.status_code} {str(data)[:300]}")
    except Exception as exc:
        print(f"  [ERROR] 索引失敗: {exc}")


def verify_local_models(sess: DifySession) -> None:
    print("[1/5] 檢查本地 Embedding / Rerank 模型…")
    for mtype, expected_model in [("text-embedding", EMBEDDING_MODEL), ("rerank", RERANK_MODEL)]:
        r = sess.get(f"/console/api/workspaces/current/default-model?model_type={mtype}")
        r.raise_for_status()
        data = r.json().get("data", {})
        model = data.get("model", "")
        provider = (data.get("provider") or {}).get("provider", "")
        ok = expected_model in model
        mark = "OK" if ok else "!!"
        print(f"  [{mark}] {mtype}: {provider} / {model}")
        if not ok:
            print(f"    請到 Dify → 設定 → 模型供應商 手動設為 {expected_model}")


def fix_local_model_endpoints(sess: DifySession) -> None:
    """Dify 容器內應走 docker 網路，勿用 host.docker.internal（Windows 上常連不上）。"""
    fixes = [
        (
            EMBEDDING_PROVIDER,
            EMBEDDING_MODEL,
            "text-embedding",
            {"base_url": OLLAMA_BASE_URL, "context_size": "4096"},
            "Ollama",
        ),
        (
            RERANK_PROVIDER,
            RERANK_MODEL,
            "rerank",
            {
                "server_url": TEI_SERVER_URL,
                "invoke_timeout": "60",
                "max_retries": "3",
                "context_size": 8192,
            },
            "TEI Rerank",
        ),
    ]
    for provider, model, model_type, want_creds, label in fixes:
        r = sess.get(
            f"/console/api/workspaces/current/model-providers/{provider}/models/credentials",
            params={"model": model, "model_type": model_type},
        )
        if r.status_code != 200:
            print(f"  [ERROR] 讀取 {label} 憑證失敗: {r.status_code}")
            continue
        body = r.json()
        creds = body.get("credentials") or {}
        cid = body.get("current_credential_id")
        cname = body.get("current_credential_name") or "default"
        if not cid:
            print(f"  [ERROR] {label} 無憑證，請先在 Dify 設定模型")
            continue
        key = "base_url" if "base_url" in want_creds else "server_url"
        current = (creds.get(key) or "").rstrip("/")
        target = want_creds[key].rstrip("/")
        if current == target:
            print(f"  [INFO] {label} -> {target}")
            continue
        payload = {
            "model": model,
            "model_type": model_type,
            "credentials": {**creds, **want_creds},
            "name": cname,
            "credential_id": cid,
        }
        ur = sess.session.put(
            f"{DIFY_BASE}/console/api/workspaces/current/model-providers/{provider}/models/credentials",
            json=payload,
            timeout=60,
        )
        if ur.status_code == 200:
            print(f"  [INFO] {label} {current or '(空)'} -> {target}")
        else:
            print(f"  [ERROR] {label} 更新失敗: {ur.status_code} {ur.text[:200]}")


def find_agent_app(sess: DifySession) -> str:
    r = sess.get("/console/api/apps", params={"page": 1, "limit": 50})
    r.raise_for_status()
    for app in r.json().get("data", []):
        if app.get("mode") == "agent-chat":
            name = app.get("name", "")
            if AGENT_APP_NAME in name or "管家" in name or "agent" in name.lower():
                return app["id"]
    apps = r.json().get("data", [])
    for app in apps:
        if app.get("mode") == "agent-chat":
            return app["id"]
    raise RuntimeError("找不到 agent-chat 應用，請先在 Dify 建立 Agent")


def import_workspace_tools(sess: DifySession, token: str) -> None:
    print("[2/5] 匯入 GRAVITY Agent Workspace 工具…")
    if not OPENAPI_FILE.is_file():
        raise FileNotFoundError(f"缺少 {OPENAPI_FILE}")

    schema = OPENAPI_FILE.read_text(encoding="utf-8")
    payload = {
        "provider": TOOL_PROVIDER,
        "icon": {"content": "🛠️", "background": "#007AFF"},
        "credentials": {
            "auth_type": "none",
        },
        "schema_type": "openapi",
        "schema": schema,
        "labels": ["utilities"],
    }

    existing = sess.get("/console/api/workspaces/current/tool-providers", params={"type": "api"})
    providers = existing.json() if existing.status_code == 200 else []
    names = {p.get("name") for p in providers if isinstance(p, dict)}
    if TOOL_PROVIDER in names:
        payload["original_provider"] = TOOL_PROVIDER
        r = sess.post("/console/api/workspaces/current/tool-provider/api/update", json=payload)
    else:
        r = sess.post("/console/api/workspaces/current/tool-provider/api/add", json=payload)

    if r.status_code not in (200, 201):
        raise RuntimeError(f"工具匯入失敗: {r.status_code} {r.text[:500]}")
    print("  [INFO] 工具已匯入 / 更新")


def get_api_provider_id(sess: DifySession) -> str:
    r = sess.get("/console/api/workspaces/current/tool-providers", params={"type": "api"})
    r.raise_for_status()
    for p in r.json():
        if isinstance(p, dict) and p.get("name") == TOOL_PROVIDER:
            return p["id"]
    raise RuntimeError(f"找不到 API 工具供應商：{TOOL_PROVIDER}")


def list_api_tools(sess: DifySession) -> list[dict]:
    r = sess.get(
        "/console/api/workspaces/current/tool-provider/api/tools",
        params={"provider": TOOL_PROVIDER},
    )
    r.raise_for_status()
    return r.json()


def attach_tools_and_rerank(sess: DifySession, app_id: str) -> None:
    print("[3/5] 掛載工具到 Agent + 本地 Rerank…")
    r = sess.get(f"/console/api/apps/{app_id}")
    r.raise_for_status()
    app = r.json()
    mc = app.get("model_config") or {}

    api_tools = list_api_tools(sess)
    provider_uuid = get_api_provider_id(sess)
    agent_tools = []
    for t in api_tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name:
            continue
        agent_tools.append({
            "enabled": True,
            "provider_type": "api",
            "provider_id": provider_uuid,
            "provider_name": TOOL_PROVIDER,
            "tool_name": name,
            "tool_label": (t.get("label") or {}).get("zh_Hans") or name,
            "tool_parameters": {},
        })

    try:
        workflow_app_id = find_workflow_app_id(sess)
        if workflow_app_id:
            workflow_tool_id = ensure_workflow_tool_registered(sess, workflow_app_id)
            wf_entry = dsl_builder.build_workflow_agent_tool_entry(workflow_tool_id=workflow_tool_id)
            agent_tools = [
                t for t in agent_tools if t.get("tool_name") != dsl_builder.WORKFLOW_TOOL_NAME
            ]
            agent_tools.append(wf_entry)
            print(f"  [INFO] 已加入 Workflow 工具 {dsl_builder.WORKFLOW_TOOL_NAME}")
        else:
            print("  [ERROR] 找不到 Workflow 應用，略過 server_backend_engineering 掛載")
    except Exception as exc:
        print(f"  [ERROR] Workflow 工具掛載失敗: {exc}")

    mc["agent_mode"] = {
        "enabled": True,
        "strategy": "react",
        "max_iteration": 99,
        "tools": agent_tools,
    }

    model_cfg = mc.get("model") or {}
    if isinstance(model_cfg, dict):
        params = dict(model_cfg.get("completion_params") or {})
        params.setdefault("temperature", 0.2)
        params.setdefault("top_p", 0.9)
        model_cfg["completion_params"] = params
        mc["model"] = model_cfg

    ds = mc.get("dataset_configs") or {}
    ds["metadata_filtering_mode"] = "disabled"
    ds["reranking_enable"] = True
    ds["reranking_mode"] = "reranking_model"
    ds["reranking_model"] = {
        "reranking_provider_name": RERANK_PROVIDER,
        "reranking_model_name": RERANK_MODEL,
    }
    ds.setdefault("retrieval_model", "multiple")
    ds.setdefault("top_k", 4)
    mc["dataset_configs"] = ds

    if PROMPT_FILE.is_file():
        mc["pre_prompt"] = PROMPT_FILE.read_text(encoding="utf-8")

    body = {
        "pre_prompt": mc.get("pre_prompt", ""),
        "opening_statement": mc.get("opening_statement", ""),
        "suggested_questions": mc.get("suggested_questions", []),
        "model": mc.get("model"),
        "user_input_form": mc.get("user_input_form", []),
        "dataset_query_variable": mc.get("dataset_query_variable", ""),
        "more_like_this": mc.get("more_like_this", {"enabled": False}),
        "sensitive_word_avoidance": mc.get("sensitive_word_avoidance", {"enabled": False}),
        "speech_to_text": mc.get("speech_to_text", {"enabled": False}),
        "text_to_speech": mc.get("text_to_speech", {"enabled": False}),
        "file_upload": mc.get("file_upload", {}),
        "retriever_resource": mc.get("retriever_resource", {"enabled": True}),
        "agent_mode": mc["agent_mode"],
        "dataset_configs": mc.get("dataset_configs"),
        "prompt_type": mc.get("prompt_type", "simple"),
    }

    r = sess.post(f"/console/api/apps/{app_id}/model-config", json=body)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"更新 Agent 失敗: {r.status_code} {r.text[:800]}")
    print(f"  [INFO] 已掛載 {len(agent_tools)} 個工具（含 Workflow server_backend_engineering）")
    print(f"  [INFO] 知識庫 Rerank -> {RERANK_MODEL}")


def ensure_app_api_key(sess: DifySession, app_id: str) -> str:
    """建立或讀取 App Service API Key，供 run_agent_background.py 使用。"""
    existing = _load_env_value("DIFY_APP_API_KEY")
    if existing.startswith("app-"):
        print("  [INFO] 已有 DIFY_APP_API_KEY")
        return existing

    r = sess.get(f"/console/api/apps/{app_id}/api-keys", timeout=30)
    r.raise_for_status()
    keys = (r.json() or {}).get("data") or []
    for item in keys:
        token = (item or {}).get("token", "")
        if token.startswith("app-"):
            _write_env_value("DIFY_APP_API_KEY", token)
            print("  [INFO] 已從 Dify 讀取 App API Key")
            return token

    r = sess.post(f"/console/api/apps/{app_id}/api-keys", timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"建立 App API Key 失敗: {r.status_code} {r.text[:300]}")
    token = (r.json() or {}).get("token", "")
    if not token.startswith("app-"):
        raise RuntimeError("建立 App API Key 成功但回傳格式異常")
    _write_env_value("DIFY_APP_API_KEY", token)
    print("  [INFO] 已建立 App API Key -> .env DIFY_APP_API_KEY")
    return token


def publish_site(sess: DifySession, app_id: str) -> str:
    print("[4/6] 確認已發布聊天頁…")
    r = sess.post(f"/console/api/apps/{app_id}/site", json={}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"發布聊天頁失敗: {r.status_code} {r.text[:300]}")
    code = (r.json() or {}).get("code") or (r.json() or {}).get("access_token")
    if not code:
        raise RuntimeError("無法取得聊天頁 code")
    chat_url = f"{DIFY_BASE}/chat/{code}"
    print(f"  [INFO] 已發布聊天頁：{chat_url}")
    return chat_url


def sync_chat_history_local() -> None:
    print("[5/6] 同步 Dify 聊天歷史到本地…")
    from sync_dify_chat_history import sync_history

    data = sync_history()
    print(f"  [INFO] 已同步 {data['conversation_count']} 個對話到 agent_memory/dify_chat_history.json")


def print_summary(sess: DifySession, app_id: str, provider_id: str, chat_url: str) -> None:
    print("完成")
    print()
    print("=" * 56)
    print(f"  Dify 面板：     {DIFY_BASE}")
    print(f"  聊天頁（建議）： {chat_url}")
    print(f"  Agent 應用 ID： {app_id}")
    print(f"  Embedding：     Ollama / {EMBEDDING_MODEL}")
    print(f"  Rerank：        TEI / {RERANK_MODEL}")
    print(f"  工具 Provider： {provider_id}")
    print(f"  工具 API：      http://agent_proxy:8788")
    print(f"  工作區根目錄：  伺服器管家 (/workspace/host)")
    print("=" * 56)


def main() -> None:
    print("GRAVITY Dify 本地模型 + Cursor 工具接線\n")
    sess = DifySession()
    if not wait_for_dify(sess):
        print("[ERROR] Dify is not ready; verify docker compose is running")
        sys.exit(1)

    sess.login()
    ensure_host_bridge()
    ensure_codebase_memory()
    verify_local_models(sess)
    fix_local_model_endpoints(sess)
    token = _load_agent_token()
    import_workspace_tools(sess, token)
    app_id = find_agent_app(sess)
    attach_tools_and_rerank(sess, app_id)
    try:
        ensure_agent_backend_tool(sess, app_id)
    except Exception as exc:
        print(f"  [ERROR] 後端工程工具提示詞接線: {exc}")
    ensure_app_api_key(sess, app_id)
    chat_url = publish_site(sess, app_id)
    sync_chat_history_local()
    print_summary(sess, app_id, get_api_provider_id(sess), chat_url)


if __name__ == "__main__":
    main()
