#!/usr/bin/env python3
"""GRAVITY 伺服器管家 — 唯一管理進入點。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def cmd_sync() -> None:
    """推送 Workflow Pipeline（Template 節點）並同步 Agent 與工具。"""
    from core.setup_dify_workflow import main as workflow_main
    from core.setup_dify_gravity import main as gravity_main

    workflow_main()
    print("\n" + "=" * 56 + "\n")
    gravity_main()


def cmd_install_autostart() -> None:
    from core.install_autostart import install

    raise SystemExit(install())


def cmd_start_all() -> None:
    from core.install_autostart import start_now

    raise SystemExit(start_now())


def cmd_start_mc() -> None:
    from core.install_autostart import start_mc_now

    raise SystemExit(start_mc_now())


def cmd_autostart_status() -> None:
    from core.install_autostart import status

    status()


def cmd_uninstall_autostart() -> None:
    from core.install_autostart import uninstall

    raise SystemExit(uninstall())


def cmd_verify_boot() -> None:
    from core.verify_boot import verify

    raise SystemExit(verify())


def main() -> None:
    parser = argparse.ArgumentParser(description="GRAVITY 伺服器管家管理工具")
    parser.add_argument(
        "command",
        choices=[
            "sync",
            "install-autostart",
            "uninstall-autostart",
            "start-all",
            "start-mc",
            "autostart-status",
            "verify-boot",
        ],
        help=(
            "sync：推送 Workflow + 同步 Agent；"
            "install-autostart：註冊開機自啟；"
            "start-all：立即啟動基建（Host Bridge + Docker）；"
            "start-mc：立即啟動三個 MC 伺服器（正常視窗）；"
            "autostart-status：查看排程狀態；"
            "verify-boot：健康檢查全部基建"
        ),
    )
    args = parser.parse_args()
    commands = {
        "sync": cmd_sync,
        "install-autostart": cmd_install_autostart,
        "uninstall-autostart": cmd_uninstall_autostart,
        "start-all": cmd_start_all,
        "start-mc": cmd_start_mc,
        "autostart-status": cmd_autostart_status,
        "verify-boot": cmd_verify_boot,
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
