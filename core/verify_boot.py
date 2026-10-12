"""開機後健康檢查：驗證 GRAVITY 基建是否就緒。"""

from __future__ import annotations

import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _http_ok(url: str, timeout: float = 8.0) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)


def verify() -> int:
    checks: list[tuple[str, str, bool]] = []
    endpoints = [
        ("Host Bridge", "http://127.0.0.1:9879/health"),
        ("agent_proxy", "http://127.0.0.1:8788/health"),
        ("Dify", "http://127.0.0.1:8080"),
    ]
    for name, url in endpoints:
        ok, detail = _http_ok(url)
        checks.append((name, detail, ok))
        mark = "INFO" if ok else "ERROR"
        print(f"  [{mark}] {name}: {detail}")

    proc = subprocess.run(
        ["docker", "compose", "ps", "ssrf_proxy", "--format", "{{.Status}}"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    ssrf = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    ssrf_ok = bool(ssrf) and "Restarting" not in ssrf and "Exited" not in ssrf
    checks.append(("ssrf_proxy", ssrf or "unknown", ssrf_ok))
    print(f"  [{'INFO' if ssrf_ok else 'ERROR'}] ssrf_proxy: {ssrf or 'unknown'}")

    marker = Path.home() / "AppData" / "Local" / "GRAVITY" / "project-root.txt"
    if marker.is_file():
        print(f"  [INFO] autostart marker: {marker.read_text(encoding='utf-8').strip()}")
    else:
        print(f"  [WARN] autostart marker missing; run: python manage.py install-autostart")

    boot_log = PROJECT_ROOT / "agent_memory" / "boot.log"
    if boot_log.is_file():
        lines = boot_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        print(f"\n  boot.log (last 5 lines):")
        for line in lines[-5:]:
            print(f"    {line}")

    failed = [n for n, _, ok in checks if not ok]
    if failed:
        print(f"\n[ERROR] Failed checks: {', '.join(failed)}")
        return 1
    print("\n[INFO] All health checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(verify())
