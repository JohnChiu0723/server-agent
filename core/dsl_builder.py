"""
建構可匯入 Dify 的 Workflow DSL（mode: workflow）。
Pipeline：RAG → Flash 提取 → Reasoner → Template JSON 清洗 → 結束
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = PROJECT_ROOT / "dify-workflow"

N_START = "1000001"
N_KR = "1000002"
N_FLASH = "1000003"
N_REASONER = "1000004"
N_TEMPLATE = "1000005"
N_END = "1000006"

WORKFLOW_APP_NAME = "GRAVITY 伺服器後端工程 Pipeline"
WORKFLOW_TOOL_NAME = "server_backend_engineering"
WORKFLOW_TOOL_LABEL = "伺服器後端工程"
WORKFLOW_TOOL_DESCRIPTION = (
    "GRAVITY 雙模型 Pipeline：config.yml、MCDR 代碼、日誌診斷、配置精算"
)
WORKFLOW_TOOL_ICON = {"content": "🛠️", "background": "#D1E9FF"}
WORKFLOW_TOOL_PARAMETERS: list[dict[str, str]] = [
    {"name": "query", "description": "使用者問題或工程任務描述", "form": "llm"},
    {"name": "conversation_history", "description": "相關對話歷史摘要", "form": "llm"},
    {
        "name": "task_mode",
        "description": "logic=診斷, code=MCDR代碼, calc_vars=配置JSON",
        "form": "llm",
    },
]
DSL_VERSION = "0.7.0"

DEEPSEEK_PROVIDER = "langgenius/deepseek/deepseek"
FLASH_MODEL = "deepseek-chat"
REASONER_MODEL = "deepseek-reasoner"
RERANK_PROVIDER = "langgenius/huggingface_tei/huggingface_tei"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"

# Jinja2 模板：去掉 Reasoner 輸出前後的 ```json / ``` 標記（無需 Sandbox）
JSON_STRIP_TEMPLATE = (
    "{%- set t = reasoner_output | default('', true) | trim -%}\n"
    "{%- if t[:7] == '```json' -%}\n"
    "{%- set t = t[7:] | trim -%}\n"
    "{%- elif t[:3] == '```' -%}\n"
    "{%- set t = t[3:] | trim -%}\n"
    "{%- endif -%}\n"
    "{%- if t[-3:] == '```' -%}\n"
    "{%- set t = t[:-3] | trim -%}\n"
    "{%- endif -%}\n"
    "{{ t }}"
)


def _uid() -> str:
    return str(uuid.uuid4())


def _read_text(rel: str) -> str:
    return (WORKFLOW_DIR / rel).read_text(encoding="utf-8")


def _adapt_prompt(raw: str, *, node_b: bool = False) -> str:
    text = raw
    text = text.replace("{{#sys.query#}}", f"{{{{#{N_START}.query#}}}}")
    text = text.replace("{{#sys.conversation_history#}}", f"{{{{#{N_START}.conversation_history#}}}}")
    if node_b:
        text = text.replace("{{#node_a_output#}}", f"{{{{#{N_FLASH}.text#}}}}")
        text = text.replace("{{#task_mode#}}", f"{{{{#{N_START}.task_mode#}}}}")
    return text


def _node_shell(node_id: str, x: int, y: int, data: dict[str, Any], height: int = 98) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "custom",
        "position": {"x": x, "y": y},
        "positionAbsolute": {"x": x, "y": y},
        "sourcePosition": "right",
        "targetPosition": "left",
        "width": 244,
        "height": height,
        "selected": False,
        "data": data,
    }


def _edge(source: str, target: str, source_type: str, target_type: str) -> dict[str, Any]:
    return {
        "id": f"{source}-source-{target}-target",
        "source": source,
        "sourceHandle": "source",
        "target": target,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
        "data": {
            "isInIteration": False,
            "isInLoop": False,
            "sourceType": source_type,
            "targetType": target_type,
        },
    }


def _llm_prompt(text: str, role: str = "system") -> dict[str, str]:
    return {"id": _uid(), "role": role, "text": text}


def build_workflow_tool_register_payload(*, workflow_app_id: str) -> dict[str, Any]:
    """Dify Workflow-as-Tool 註冊 payload（POST workflow/create 或 update）。"""
    return {
        "workflow_app_id": workflow_app_id,
        "name": WORKFLOW_TOOL_NAME,
        "label": WORKFLOW_TOOL_LABEL,
        "description": WORKFLOW_TOOL_DESCRIPTION,
        "icon": WORKFLOW_TOOL_ICON,
        "parameters": WORKFLOW_TOOL_PARAMETERS,
        "labels": ["utilities"],
    }


def build_workflow_agent_tool_entry(*, workflow_tool_id: str) -> dict[str, Any]:
    """Agent agent_mode.tools 內的 workflow 工具節點。"""
    return {
        "enabled": True,
        "provider_type": "workflow",
        "provider_id": workflow_tool_id,
        "provider_name": WORKFLOW_TOOL_NAME,
        "tool_name": WORKFLOW_TOOL_NAME,
        "tool_label": WORKFLOW_TOOL_LABEL,
        "tool_parameters": {},
    }


def build_workflow_dsl(*, dataset_ids: list[str]) -> str:
    if not dataset_ids:
        raise ValueError("至少需一個知識庫 dataset_id")

    flash_prompt = _adapt_prompt(_read_text("prompts/node_a_flash_extractor.txt"))
    reasoner_prompt = _adapt_prompt(_read_text("prompts/node_b_reasoner.txt"), node_b=True)

    nodes: list[dict[str, Any]] = [
        _node_shell(
            N_START, 30, 280,
            {
                "type": "start",
                "title": "開始",
                "variables": [
                    {"label": "查詢", "variable": "query", "type": "paragraph", "required": True, "max_length": 8000},
                    {"label": "對話歷史", "variable": "conversation_history", "type": "paragraph", "required": False, "max_length": 12000},
                    {"label": "工程模式", "variable": "task_mode", "type": "select", "required": True, "options": ["logic", "code", "calc_vars"], "default": "logic"},
                ],
            },
            height=140,
        ),
        _node_shell(
            N_KR, 300, 280,
            {
                "type": "knowledge-retrieval",
                "title": "RAG 檢索",
                "query_variable_selector": [N_START, "query"],
                "dataset_ids": dataset_ids,
                "retrieval_mode": "multiple",
                "multiple_retrieval_config": {
                    "top_k": 8,
                    "score_threshold": 0.5,
                    "reranking_enable": True,
                    "reranking_mode": "reranking_model",
                    "reranking_model": {"provider": RERANK_PROVIDER, "model": RERANK_MODEL},
                },
                "metadata_filtering_mode": "disabled",
            },
            height=120,
        ),
        _node_shell(
            N_FLASH, 570, 280,
            {
                "type": "llm",
                "title": "節點 A — Flash 條列提取",
                "model": {
                    "provider": DEEPSEEK_PROVIDER,
                    "name": FLASH_MODEL,
                    "mode": "chat",
                    "completion_params": {"temperature": 0.1, "max_tokens": 1024},
                },
                "prompt_template": [
                    _llm_prompt(flash_prompt, "system"),
                    _llm_prompt(f"{{{{#{N_START}.query#}}}}", "user"),
                ],
                "context": {"enabled": True, "variable_selector": [N_KR, "result"]},
                "vision": {"enabled": False},
                "variables": [],
            },
        ),
        _node_shell(
            N_REASONER, 840, 280,
            {
                "type": "llm",
                "title": "節點 B — Reasoner 後端工程",
                "model": {
                    "provider": DEEPSEEK_PROVIDER,
                    "name": REASONER_MODEL,
                    "mode": "chat",
                    "completion_params": {"temperature": 0.0, "max_tokens": 4096},
                },
                "prompt_template": [
                    _llm_prompt(reasoner_prompt, "system"),
                    _llm_prompt(f"執行。查詢：{{{{#{N_START}.query#}}}}", "user"),
                ],
                "context": {"enabled": False, "variable_selector": []},
                "vision": {"enabled": False},
                "variables": [],
            },
        ),
        _node_shell(
            N_TEMPLATE, 1110, 280,
            {
                "type": "template-transform",
                "title": "JSON 標籤清洗",
                "variables": [
                    {"variable": "reasoner_output", "value_selector": [N_REASONER, "text"]},
                ],
                "template": JSON_STRIP_TEMPLATE,
            },
            height=54,
        ),
        _node_shell(
            N_END, 1380, 280,
            {
                "type": "end",
                "title": "結束",
                "outputs": [
                    {"variable": "reasoning_text", "value_selector": [N_REASONER, "text"]},
                    {"variable": "raw_json", "value_selector": [N_TEMPLATE, "output"]},
                    {"variable": "sanitized_json", "value_selector": [N_TEMPLATE, "output"]},
                ],
            },
            height=90,
        ),
    ]

    edges = [
        _edge(N_START, N_KR, "start", "knowledge-retrieval"),
        _edge(N_KR, N_FLASH, "knowledge-retrieval", "llm"),
        _edge(N_FLASH, N_REASONER, "llm", "llm"),
        _edge(N_REASONER, N_TEMPLATE, "llm", "template-transform"),
        _edge(N_TEMPLATE, N_END, "template-transform", "end"),
    ]

    doc: dict[str, Any] = {
        "kind": "app",
        "version": DSL_VERSION,
        "app": {
            "name": WORKFLOW_APP_NAME,
            "description": "GRAVITY 伺服器後端工程：Flash 提取 → Reasoner → Template JSON 清洗",
            "icon": "🛠️",
            "icon_type": "emoji",
            "icon_background": "#D1E9FF",
            "mode": "workflow",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [],
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": {
                "file_upload": {"enabled": False, "image": {"enabled": False}},
                "opening_statement": "",
                "retriever_resource": {"enabled": True},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "suggested_questions": [],
                "suggested_questions_after_answer": {"enabled": False},
                "text_to_speech": {"enabled": False},
            },
            "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.7}},
        },
    }

    return yaml.dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False)
