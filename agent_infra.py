"""Shared infrastructure failure types for agent_proxy / agent_tools."""

from __future__ import annotations


class InfrastructureFailure(Exception):
    """Host Bridge 斷線、Sandbox 401/403 等不可由 Agent 修復的底層故障。"""


CIRCUIT_BREAKER_HTTP_STATUSES = frozenset({503, 424})
