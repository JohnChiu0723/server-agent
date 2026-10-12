"""Windows 開機自啟：註冊 GRAVITY 排程任務。

- GRAVITY-AutoStart：登入後啟動基建（Docker/Dify/agent_proxy），零視窗。
- GRAVITY-Host-Bridge：獨立監督常駐（掛掉自動重啟 + 每 5 分鐘安全網）。
- GRAVITY-MC-Main / Creative：MC 伺服器，排程器原生失敗自動重啟，正常視窗。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# 讓中文/特殊字元輸出不會在 cp950 主控台崩潰
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
START_PS1 = PROJECT_ROOT / "scripts" / "Start-GRAVITY-All.ps1"
LAUNCHER_VBS = PROJECT_ROOT / "scripts" / "Start-GRAVITY-Launcher.vbs"
REGISTER_MC_PS1 = PROJECT_ROOT / "scripts" / "Register-MC-Task.ps1"
REGISTER_HOST_BRIDGE_PS1 = PROJECT_ROOT / "scripts" / "Register-Host-Bridge-Task.ps1"
PYTHON_EXE = os.environ.get("GRAVITY_PYTHON") or sys.executable

TASK_NAME = "GRAVITY-AutoStart"
HOST_BRIDGE_TASK = "GRAVITY-Host-Bridge"
LEGACY_TASKS = ("GRAVITY-Start",)

# (任務名, 子目錄, 視窗標題, 登入後延遲秒數)
# 鏡像服（minecraft-mirror）不列入開機自啟，需要時手動啟動 Start-Mirror-Server.bat
MC_TASKS = (
    ("GRAVITY-MC-Main", "minecraft-server", "GRAVITY Main", 120),
    ("GRAVITY-MC-Creative", "minecraft-creative", "GRAVITY Creative", 150),
)
MC_TASK_NAMES = tuple(t[0] for t in MC_TASKS)

# 曾經註冊、現在要確保移除的 MC 任務（例如鏡像服改為手動啟動）
RETIRED_MC_TASKS = ("GRAVITY-MC-Mirror",)


def _gravity_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    return Path(local) / "GRAVITY"


def _write_launcher_files() -> Path:
    gravity = _gravity_dir()
    gravity.mkdir(parents=True, exist_ok=True)
    # UTF-16 (含 BOM)：VBS 以 Unicode 讀取，避免中文路徑被 Big5 誤讀成亂碼
    (gravity / "project-root.txt").write_text(str(PROJECT_ROOT), encoding="utf-16")
    shutil.copy2(LAUNCHER_VBS, gravity / "Start-GRAVITY-Launcher.vbs")
    return gravity / "Start-GRAVITY-Launcher.vbs"


def _remove_tasks(names: tuple[str, ...]) -> None:
    for name in names:
        proc = subprocess.run(
            ["schtasks", "/Delete", "/TN", name, "/F"],
            capture_output=True,
            text=True,
        encoding="utf-8",
        errors="replace",
            check=False,
        )
        if proc.returncode == 0:
            print(f"  [INFO] Removed legacy task: {name}")


def _register_main_task(script: Path) -> None:
    user = os.environ.get("USERNAME", "")
    if not user:
        raise RuntimeError("USERNAME not set")
    args = f'//B "{script}"'
    ps = (
        f"$a = New-ScheduledTaskAction -Execute 'wscript.exe' "
        f"-Argument '{args}' -WorkingDirectory '{script.parent}'; "
        f"$t = New-ScheduledTaskTrigger -AtLogOn -User '{user}'; "
        f"$t.Delay = 'PT90S'; "
        f"$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        f"-DontStopIfGoingOnBatteries -StartWhenAvailable "
        f"-ExecutionTimeLimit ([TimeSpan]::Zero) "
        f"-MultipleInstances IgnoreNew; "
        f"$p = New-ScheduledTaskPrincipal -UserId '{user}' -LogonType Interactive -RunLevel Limited; "
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t "
        f"-Settings $s -Principal $p "
        f"-Description 'GRAVITY autostart (silent): Host Bridge + Docker/Dify/agent_proxy' -Force"
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "main task failed")


def _register_mc_task(name: str, sub_dir: str, title: str, delay: int) -> None:
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(REGISTER_MC_PS1),
        "-TaskName",
        name,
        "-SubDir",
        sub_dir,
        "-Title",
        title,
        "-DelaySeconds",
        str(delay),
        "-Affinity",
        "ffff",
        "-Python",
        PYTHON_EXE,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or out or f"{name} failed")
    if out:
        print(f"  {out}")


def _register_host_bridge_task() -> None:
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(REGISTER_HOST_BRIDGE_PS1),
        "-TaskName",
        HOST_BRIDGE_TASK,
        "-DelaySeconds",
        "20",
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or out or f"{HOST_BRIDGE_TASK} failed")
    if out:
        print(f"  {out}")


def install() -> int:
    if sys.platform != "win32":
        print("[WARN] Autostart is supported on Windows only")
        return 1
    required = (START_PS1, LAUNCHER_VBS, REGISTER_MC_PS1, REGISTER_HOST_BRIDGE_PS1)
    if not all(p.is_file() for p in required):
        print("[ERROR] Required scripts missing (Start-GRAVITY-All / Launcher / Register-MC / Register-Host-Bridge)")
        return 1
    try:
        launcher = _write_launcher_files()
        _remove_tasks(LEGACY_TASKS + RETIRED_MC_TASKS)
        _register_main_task(launcher)
        _register_host_bridge_task()
        for name, sub_dir, title, delay in MC_TASKS:
            _register_mc_task(name, sub_dir, title, delay)
    except Exception as exc:
        print(f"[ERROR] Task registration failed: {exc}")
        return 1

    print("[INFO] All autostart tasks registered")
    print(f"     Infrastructure (headless): {TASK_NAME} (logon + 90s)")
    print(f"     Host Bridge (supervised): {HOST_BRIDGE_TASK} (logon + 20s, restart on failure)")
    print("     MC servers (windowed, scheduler-managed restart):")
    for name, sub_dir, _title, delay in MC_TASKS:
        print(f"       - {name} ({sub_dir}, logon + {delay}s)")
    print(f"     Project: {PROJECT_ROOT}")
    print(f"     Log: {PROJECT_ROOT / 'agent_memory' / 'boot.log'}")
    print(f"     Host Bridge log: {PROJECT_ROOT / 'agent_memory' / 'host_bridge.log'}")
    print("     MC / Host Bridge crashes are retried by the scheduler every 1 min; Host Bridge has an additional 5 min safety net")
    return 0


def uninstall() -> int:
    if sys.platform != "win32":
        return 1
    _remove_tasks((TASK_NAME, HOST_BRIDGE_TASK, *MC_TASK_NAMES, *LEGACY_TASKS, *RETIRED_MC_TASKS))
    return 0


def start_now() -> int:
    """立即啟動基建（不含 MC；MC 請用排程或各目錄的 Start-*-Server.bat）。"""
    gravity = _gravity_dir()
    launcher = gravity / "Start-GRAVITY-Launcher.vbs"
    if not launcher.is_file():
        print(f"[ERROR] {launcher} missing; run install-autostart first")
        return 1
    return subprocess.run(
        ["wscript.exe", "//B", str(launcher)],
        cwd=str(PROJECT_ROOT),
        check=False,
    ).returncode


def start_mc_now() -> int:
    """立即以排程任務啟動三個 MC 伺服器（正常視窗）。"""
    if sys.platform != "win32":
        return 1
    rc = 0
    for name in MC_TASK_NAMES:
        proc = subprocess.run(
            ["schtasks", "/Run", "/TN", name],
            capture_output=True,
            text=True,
        encoding="utf-8",
        errors="replace",
            check=False,
        )
        if proc.returncode == 0:
            print(f"  [INFO] Started: {name}")
        else:
            print(f"  [ERROR] Failed to start {name}: {(proc.stderr or proc.stdout).strip()}")
            rc = 1
    return rc


def status() -> None:
    if sys.platform != "win32":
        return
    for name in (TASK_NAME, HOST_BRIDGE_TASK, *MC_TASK_NAMES):
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", name, "/FO", "LIST"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode == 0:
            print(f"=== {name} ===")
            print(proc.stdout.strip())
        else:
            print(f"[WARN] Task {name} is not installed.")
    marker = _gravity_dir() / "project-root.txt"
    if marker.is_file():
        print(f"\nproject-root marker: {marker.read_text(encoding='utf-16').strip()}")
