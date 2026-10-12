import base64
import os
import json
import requests

BASE = "http://localhost:8080"
s = requests.Session()
s.headers.update({"Origin": BASE, "Referer": BASE + "/"})
pwd = base64.b64encode(os.environ["DIFY_ADMIN_PASSWORD"].encode()).decode()
s.post(
    BASE + "/console/api/login",
    json={"email": "admin@gravity.local", "password": pwd, "remember_me": True, "language": "zh-Hant"},
    timeout=30,
)
if s.cookies.get("access_token"):
    s.headers["Authorization"] = "Bearer " + s.cookies["access_token"]
if s.cookies.get("csrf_token"):
    s.headers["X-CSRF-Token"] = s.cookies["csrf_token"]

apps = s.get(BASE + "/console/api/apps", params={"page": 1, "limit": 50}, timeout=30).json()["data"]
app = next(a for a in apps if a.get("mode") == "agent-chat")
mc = s.get(BASE + f"/console/api/apps/{app['id']}", timeout=30).json().get("model_config") or {}
tools = (mc.get("agent_mode") or {}).get("tools") or []
names = [t.get("tool_name") for t in tools]
enabled = sum(1 for t in tools if t.get("enabled"))
print("app", app["id"], app.get("name"))
print("tool_count", len(tools), "enabled", enabled)
print("has_fetch_url", "fetch_url" in names)
print("tools:", ", ".join(names))
api = s.get(
    BASE + "/console/api/workspaces/current/tool-provider/api/tools",
    params={"provider": "gravity_agent_workspace"},
    timeout=30,
).json()
print("provider_tool_count", len(api))
print("provider_has_fetch_url", any(t.get("name") == "fetch_url" for t in api if isinstance(t, dict)))
model = mc.get("model") or {}
agent_mode = mc.get("agent_mode") or {}
print("llm", model.get("provider"), model.get("name"), "strategy", agent_mode.get("strategy"))
