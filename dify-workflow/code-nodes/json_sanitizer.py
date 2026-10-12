"""
高容錯 JSON 洗白器 — 用於 Dify Code 節點（掛載於 Reasoner 之後）

deepseek-reasoner 不支援 JSON Schema Mode，輸出常含：
- Markdown ```json 圍欄
- `` / `<thinking>` 思考殘留
- 截斷的尾部括號

本模組以 Regex + 括號平衡修復，盡力還原可解析 JSON。
"""

from __future__ import annotations

import json
import re
from typing import Any


# ── 剝離模式 ──────────────────────────────────────────────

_THINK_PATTERNS = [
    re.compile(r"<redacted_reasoning>.*?</redacted_reasoning>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE),
    re.compile(r"【思考】.*?【/思考】", re.DOTALL),
    re.compile(r"^Thought:\s*.*?(?=^Answer:|^Output:|```|\{|\[)", re.MULTILINE | re.DOTALL),
]

_FENCE_PATTERN = re.compile(
    r"```(?:json|JSON)?\s*\n?(.*?)```",
    re.DOTALL,
)

_JSON_CANDIDATE = re.compile(
    r"(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}|\[(?:[^\[\]]|\[(?:[^\[\]]|\[[^\[\]]*\])*\])*\])",
    re.DOTALL,
)


def strip_thinking_artifacts(text: str) -> str:
    """移除 Reasoner 思考過程殘留。"""
    result = text.strip()
    for pat in _THINK_PATTERNS:
        result = pat.sub("", result)
    result = re.sub(r"^(Answer|Output|正式輸出|結果)\s*[:：]\s*", "", result, flags=re.MULTILINE)
    return result.strip()


def extract_fenced_json(text: str) -> str | None:
    """優先從 Markdown json 圍欄提取。"""
    matches = _FENCE_PATTERN.findall(text)
    for block in reversed(matches):  # 取最後一個圍欄（常為最終答案）
        candidate = block.strip()
        if candidate:
            return candidate
    return None


def extract_json_by_brackets(text: str) -> str | None:
    """Regex 掃描最外層 { } 或 [ ] 區塊。"""
    matches = _JSON_CANDIDATE.findall(text)
    if not matches:
        return None
    # 偏好最長匹配（通常最完整）
    return max(matches, key=len)


def repair_truncated_json(raw: str) -> str:
    """
    修復 Token 耗盡導致的尾部截斷。
    策略：追蹤括號 / 引號狀態，補齊未閉合結構。
    """
    s = raw.strip()
    if not s:
        return s

    stack: list[str] = []
    in_string = False
    escape = False

    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()

    repaired = s
    if in_string:
        repaired += '"'
    while stack:
        repaired += stack.pop()

    # 移除尾部逗號後直接閉括號的非法情況：{ "a": 1, } → { "a": 1 }
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired


def parse_json_lenient(text: str) -> tuple[Any | None, str, list[str]]:
    """
    主解析流程。
    返回: (parsed_object, raw_json_string, warnings)
    """
    warnings: list[str] = []
    cleaned = strip_thinking_artifacts(text)

    raw = extract_fenced_json(cleaned)
    if raw:
        warnings.append("extracted_from_markdown_fence")
    else:
        raw = extract_json_by_brackets(cleaned)
        if raw:
            warnings.append("extracted_by_bracket_scan")
        else:
            raw = cleaned
            warnings.append("no_json_block_found_using_full_text")

    for attempt, payload in enumerate([raw, repair_truncated_json(raw)]):
        try:
            obj = json.loads(payload)
            if attempt == 1:
                warnings.append("repaired_truncated_json")
            return obj, payload, warnings
        except json.JSONDecodeError as e:
            last_error = str(e)
            continue

    return None, raw, warnings + [f"parse_failed: {last_error}"]


def sanitize_reasoner_output(text: str) -> dict[str, Any]:
    """
    Dify Code 節點統一出口。
    """
    if not text or not str(text).strip():
        return {
            "success": False,
            "data": None,
            "raw_json": "",
            "warnings": ["empty_input"],
            "error": "empty_input",
        }

    obj, raw, warnings = parse_json_lenient(str(text))

    if obj is not None:
        return {
            "success": True,
            "data": obj,
            "raw_json": raw,
            "warnings": warnings,
            "error": "",
        }

    return {
        "success": False,
        "data": None,
        "raw_json": raw,
        "warnings": warnings,
        "error": warnings[-1] if warnings else "unknown",
    }


# ── Dify Code 節點入口 ────────────────────────────────────

def main(reasoner_output: str) -> dict[str, Any]:
    """
    Dify 掛載參數:
      - reasoner_output: 節點 B (deepseek-reasoner) 的 text 輸出
    輸出變數:
      - success, data, raw_json, warnings, error
    """
    return sanitize_reasoner_output(reasoner_output)
