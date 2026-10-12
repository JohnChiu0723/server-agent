"""
setup_dify_workflow.py — 匯入 / 更新 GRAVITY 伺服器後端工程 Workflow
====================================================================
使用：python manage.py sync  或  python -m core.setup_dify_workflow
"""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import requests
import yaml

from core import dsl_builder
from env_loader import load_env

load_env()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = PROJECT_ROOT / "dify-workflow"
DSL_REFERENCE = WORKFLOW_DIR / "workflow" / "gravity_dual_model_pipeline.yml"
PROMPT_PATCH_MARKER = "<!-- GRAVITY_BACKEND_ENGINEERING_TOOL -->"
AGENT_PROMPT_FILE = PROJECT_ROOT / "dify_agent_system_prompt.txt"
BACKEND_TOOL_PROMPT = WORKFLOW_DIR / "prompts" / "agent_backend_tool_section.txt"

DIFY_BASE = os.environ.get("DIFY_CONSOLE_URL", "http://localhost:8080")
ADMIN_EMAIL = os.environ.get("DIFY_ADMIN_EMAIL", "admin@gravity.local")
ADMIN_PASSWORD = os.environ.get("DIFY_ADMIN_PASSWORD", "")
WORKFLOW_APP_NAME = os.environ.get("DIFY_WORKFLOW_APP_NAME", "GRAVITY 伺服器後端工程 Pipeline")
AGENT_APP_NAME = os.environ.get("DIFY_AGENT_APP_NAME", "graviyt管家")
DATASET_ID_ENV = "DIFY_KB_DATASET_ID"


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
        return self.session.post(f"{DIFY_BASE}{path}", timeout=kwargs.pop("timeout", 120), **kwargs)


def wait_for_dify(sess: DifySession, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if sess.get("/console/api/setup").status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def discover_dataset_ids(sess: DifySession) -> list[str]:
    configured = _load_env_value(DATASET_ID_ENV)
    if configured:
        print(f"  [INFO] 使用環境變數 {DATASET_ID_ENV}={configured}")
        return [configured]

    r = sess.get("/console/api/datasets", params={"page": 1, "limit": 50})
    r.raise_for_status()
    datasets = (r.json() or {}).get("data") or []
    if not datasets:
        raise RuntimeError(
            "找不到知識庫。請先在 Dify 建立知識庫，或於 .env 設定 DIFY_KB_DATASET_ID"
        )

    preferred = None
    for ds in datasets:
        name = (ds.get("name") or "").lower()
        if any(k in name for k in ("gravity", "生電", "technical", "minecraft", "管家")):
            preferred = ds["id"]
            print(f"  [INFO] 自動選擇知識庫：{ds.get('name')} ({preferred})")
            break

    if not preferred:
        preferred = datasets[0]["id"]
        print(f"  [ERROR] 使用第一個知識庫：{datasets[0].get('name')} ({preferred})")

    _write_env_value(DATASET_ID_ENV, preferred)
    return [preferred]


def find_workflow_app_id(sess: DifySession) -> str | None:
    saved = _load_env_value("DIFY_WORKFLOW_APP_ID")
    if saved:
        return saved

    r = sess.get("/console/api/apps", params={"page": 1, "limit": 100})
    r.raise_for_status()
    for app in (r.json() or {}).get("data") or []:
        if app.get("mode") != "workflow":
            continue
        name = app.get("name") or ""
        if WORKFLOW_APP_NAME in name or "後端工程" in name or "生電推理" in name:
            return app["id"]
    return None


def ensure_workflow_tool_registered(sess: DifySession, workflow_app_id: str | None = None) -> str:
    """將 Workflow 應用註冊為 Dify Workflow Tool，回傳 workflow_tool_id。"""
    app_id = workflow_app_id or find_workflow_app_id(sess)
    if not app_id:
        raise RuntimeError("找不到 Workflow 應用，請先執行 python manage.py sync")

    payload = dsl_builder.build_workflow_tool_register_payload(workflow_app_id=app_id)
    r = sess.get(
        "/console/api/workspaces/current/tool-provider/workflow/get",
        params={"workflow_app_id": app_id},
    )
    if r.status_code == 200:
        workflow_tool_id = (r.json() or {}).get("workflow_tool_id")
        if workflow_tool_id:
            update_payload = {
                "workflow_tool_id": workflow_tool_id,
                "name": payload["name"],
                "label": payload["label"],
                "description": payload["description"],
                "icon": payload["icon"],
                "parameters": payload["parameters"],
                "labels": payload.get("labels") or [],
            }
            ur = sess.post(
                "/console/api/workspaces/current/tool-provider/workflow/update",
                json=update_payload,
            )
            if ur.status_code not in (200, 201):
                raise RuntimeError(
                    f"Workflow 工具更新失敗: {ur.status_code} {ur.text[:400]}"
                )
            print(f"  [INFO] Workflow 工具已更新：{payload['name']} ({workflow_tool_id[:8]}…)")
            return workflow_tool_id

    cr = sess.post(
        "/console/api/workspaces/current/tool-provider/workflow/create",
        json=payload,
    )
    if cr.status_code not in (200, 201):
        raise RuntimeError(f"Workflow 工具建立失敗: {cr.status_code} {cr.text[:400]}")

    gr = sess.get(
        "/console/api/workspaces/current/tool-provider/workflow/get",
        params={"workflow_app_id": app_id},
    )
    gr.raise_for_status()
    workflow_tool_id = (gr.json() or {}).get("workflow_tool_id")
    if not workflow_tool_id:
        raise RuntimeError("Workflow 工具建立成功但未回傳 workflow_tool_id")
    print(f"  [INFO] Workflow 工具已建立：{payload['name']} ({workflow_tool_id[:8]}…)")
    return workflow_tool_id


def attach_workflow_tool_to_agent(sess: DifySession, agent_app_id: str, workflow_tool_id: str) -> int:
    """將 Workflow Tool 節點加入 Agent tools 陣列（保留既有 API 工具）。"""
    r = sess.get(f"/console/api/apps/{agent_app_id}")
    r.raise_for_status()
    app = r.json()
    mc = app.get("model_config") or {}
    agent_mode = dict(mc.get("agent_mode") or {})
    tools = list(agent_mode.get("tools") or [])

    wf_entry = dsl_builder.build_workflow_agent_tool_entry(workflow_tool_id=workflow_tool_id)
    tools = [t for t in tools if t.get("tool_name") != dsl_builder.WORKFLOW_TOOL_NAME]
    tools.append(wf_entry)

    agent_mode["enabled"] = True
    agent_mode.setdefault("strategy", "react")
    agent_mode.setdefault("max_iteration", 99)
    agent_mode["tools"] = tools
    mc["agent_mode"] = agent_mode

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
        "agent_mode": agent_mode,
        "dataset_configs": mc.get("dataset_configs"),
        "prompt_type": mc.get("prompt_type", "simple"),
    }
    pr = sess.post(f"/console/api/apps/{agent_app_id}/model-config", json=body)
    if pr.status_code not in (200, 201):
        raise RuntimeError(f"Agent Workflow 工具掛載失敗: {pr.status_code} {pr.text[:400]}")
    return len(tools)


def build_dsl_yaml(dataset_ids: list[str]) -> str:
    yaml_content = dsl_builder.build_workflow_dsl(dataset_ids=dataset_ids)
    DSL_REFERENCE.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# AUTO-GENERATED by core/setup_dify_workflow.py — 請勿手動編輯\n"
        "# Agent 工具名稱：server_backend_engineering（provider_type: workflow）\n"
        "# Pipeline 使用 template-transform 節點（無 Sandbox 依賴）\n"
    )
    DSL_REFERENCE.write_text(header + yaml_content, encoding="utf-8")
    return yaml_content


def import_workflow_dsl(sess: DifySession, yaml_content: str, app_id: str | None) -> str:
    payload: dict = {
        "mode": "yaml-content",
        "yaml_content": yaml_content,
        "name": WORKFLOW_APP_NAME,
        "description": "GRAVITY 伺服器後端工程 Pipeline（Flash + Reasoner + Template JSON 清洗）",
        "icon_type": "emoji",
        "icon": "⚡",
        "icon_background": "#FFEAD5",
    }
    if app_id:
        payload["app_id"] = app_id
        print(f"  [INFO] 更新既有 Workflow app_id={app_id}")
    else:
        print("  [INFO] 建立新 Workflow 應用…")

    r = sess.post("/console/api/apps/imports", json=payload)
    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"DSL 匯入失敗: {r.status_code} {r.text[:800]}")

    result = r.json() or {}
    status = result.get("status")
    import_id = result.get("id")

    if status == "pending" and import_id:
        print("  [INFO] DSL 版本需確認，執行 confirm…")
        c = sess.post(f"/console/api/apps/imports/{import_id}/confirm", json={})
        if c.status_code not in (200, 201):
            raise RuntimeError(f"DSL confirm 失敗: {c.status_code} {c.text[:500]}")
        result = c.json() or result

    if result.get("status") == "failed":
        raise RuntimeError(f"DSL 匯入失敗: {result.get('error', result)}")

    new_app_id = result.get("app_id")
    if not new_app_id:
        raise RuntimeError(f"匯入成功但未回傳 app_id: {result}")
    return new_app_id


def publish_workflow(sess: DifySession, app_id: str) -> None:
    r = sess.post(f"/console/api/apps/{app_id}/workflows/publish", json={})
    if r.status_code in (200, 201):
        print("  [INFO] Workflow 已發布")
        return
    print(f"  [INFO] publish 回應 {r.status_code}（可能已發布）: {r.text[:200]}")


def ensure_workflow_api_key(sess: DifySession, app_id: str) -> str:
    existing = _load_env_value("DIFY_WORKFLOW_API_KEY")
    if existing.startswith("app-"):
        print("  [INFO] 已有 DIFY_WORKFLOW_API_KEY")
        return existing

    r = sess.get(f"/console/api/apps/{app_id}/api-keys")
    r.raise_for_status()
    for item in (r.json() or {}).get("data") or []:
        token = (item or {}).get("token", "")
        if token.startswith("app-"):
            _write_env_value("DIFY_WORKFLOW_API_KEY", token)
            print("  [INFO] 已讀取既有 Workflow API Key")
            return token

    r = sess.post(f"/console/api/apps/{app_id}/api-keys", json={})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"建立 Workflow API Key 失敗: {r.status_code} {r.text[:300]}")
    token = (r.json() or {}).get("token", "")
    if not token.startswith("app-"):
        raise RuntimeError("Workflow API Key 格式異常")
    _write_env_value("DIFY_WORKFLOW_API_KEY", token)
    print("  [INFO] 已建立 Workflow API Key → .env")
    return token


def patch_agent_system_prompt() -> None:
    if not BACKEND_TOOL_PROMPT.is_file() or not AGENT_PROMPT_FILE.is_file():
        return

    section = BACKEND_TOOL_PROMPT.read_text(encoding="utf-8").strip()
    block = f"{PROMPT_PATCH_MARKER}\n{section}\n"
    content = AGENT_PROMPT_FILE.read_text(encoding="utf-8")

    for marker in (PROMPT_PATCH_MARKER, "<!-- GRAVITY_REDSTONE_PIPELINE_TOOL -->"):
        if marker in content:
            content = content.split(marker)[0].rstrip()

    new_content = content + "\n\n" + block
    AGENT_PROMPT_FILE.write_text(new_content, encoding="utf-8")
    print("  [INFO] 已更新 dify_agent_system_prompt.txt（server_backend_engineering 規則）")


def find_agent_app(sess: DifySession) -> str | None:
    r = sess.get("/console/api/apps", params={"page": 1, "limit": 50})
    r.raise_for_status()
    for app in (r.json() or {}).get("data") or []:
        if app.get("mode") != "agent-chat":
            continue
        name = app.get("name", "")
        if AGENT_APP_NAME in name or "管家" in name:
            return app["id"]
    return None


def ensure_agent_backend_tool(sess: DifySession, agent_app_id: str | None = None) -> None:
    """註冊 Workflow Tool 並掛載到 Agent，同時推送 server_backend_engineering 提示詞規則。"""
    workflow_key = _load_env_value("DIFY_WORKFLOW_API_KEY")
    if not workflow_key:
        print("  [ERROR] 尚未設定 DIFY_WORKFLOW_API_KEY，請先執行 python manage.py sync")
        return

    patch_agent_system_prompt()

    app_id = agent_app_id or find_agent_app(sess)
    if not app_id:
        print("  [ERROR] 找不到 Agent 應用，略過 Workflow 工具掛載")
        return

    workflow_app_id = find_workflow_app_id(sess)
    if not workflow_app_id:
        print("  [ERROR] 找不到 Workflow 應用，略過 Workflow 工具掛載")
        return

    try:
        workflow_tool_id = ensure_workflow_tool_registered(sess, workflow_app_id)
        tool_count = attach_workflow_tool_to_agent(sess, app_id, workflow_tool_id)
        print(f"  [INFO] Agent 已掛載 Workflow 工具 server_backend_engineering（共 {tool_count} 個）")
    except Exception as exc:
        print(f"  [ERROR] Workflow 工具掛載失敗: {exc}")
        return

    if not AGENT_PROMPT_FILE.is_file():
        return

    r = sess.get(f"/console/api/apps/{app_id}")
    r.raise_for_status()
    app = r.json()
    mc = app.get("model_config") or {}
    mc["pre_prompt"] = AGENT_PROMPT_FILE.read_text(encoding="utf-8")

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
        "agent_mode": mc.get("agent_mode"),
        "dataset_configs": mc.get("dataset_configs"),
        "prompt_type": mc.get("prompt_type", "simple"),
    }
    pr = sess.post(f"/console/api/apps/{app_id}/model-config", json=body)
    if pr.status_code in (200, 201):
        print("  [INFO] Agent 系統提示詞已推送（含 server_backend_engineering 規則）")
    else:
        print(f"  [ERROR] Agent 提示詞更新失敗: {pr.status_code} {pr.text[:300]}")


def print_summary(app_id: str, api_key: str) -> None:
    print()
    print("=" * 58)
    print("  GRAVITY 伺服器後端工程 Workflow 已就緒")
    print("=" * 58)
    print(f"  Dify 面板：           {DIFY_BASE}")
    print(f"  Workflow 應用 ID：      {app_id}")
    print(f"  Workflow API Key：    {api_key[:12]}…（完整見 .env）")
    print(f"  DSL 生成檔：          {DSL_REFERENCE}")
    print(f"  Agent 工具名稱：      server_backend_engineering")
    print("=" * 58)


def main() -> None:
    print("GRAVITY 雙模型 Workflow 自動匯入\n")
    sess = DifySession()
    if not wait_for_dify(sess):
        print("[ERROR] Dify is not ready")
        sys.exit(1)

    sess.login()

    print("[1/5] 探索知識庫 dataset_id…")
    dataset_ids = discover_dataset_ids(sess)

    print("[2/5] 建構 DSL…")
    yaml_content = build_dsl_yaml(dataset_ids)
    yaml.safe_load(yaml_content)
    print(f"  [INFO] DSL 大小 {len(yaml_content.encode('utf-8')) // 1024} KB")

    print("[3/5] 匯入 / 更新 Workflow…")
    existing_app_id = find_workflow_app_id(sess)
    app_id = import_workflow_dsl(sess, yaml_content, existing_app_id)
    _write_env_value("DIFY_WORKFLOW_APP_ID", app_id)
    print(f"  [INFO] app_id = {app_id}")

    print("[4/5] 發布 Workflow + API Key…")
    publish_workflow(sess, app_id)
    api_key = ensure_workflow_api_key(sess, app_id)

    print("[5/5] 註冊 Workflow Tool + 掛載到 Agent + 更新提示詞…")
    ensure_agent_backend_tool(sess)

    print_summary(app_id, api_key)


if __name__ == "__main__":
    main()
