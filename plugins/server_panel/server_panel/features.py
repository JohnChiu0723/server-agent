from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


SCHEDULES_FILE = 'schedules.json'
ACTIVITY_FILE = 'activity.json'
MAX_ACTIVITY = 200


def _try_import_psutil():
    try:
        import psutil  # type: ignore
        return psutil
    except ImportError:
        return None


def find_java_pid() -> int | None:
    psutil = _try_import_psutil()
    if psutil is None:
        return None
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            name = (proc.info.get('name') or '').lower()
            if name != 'java.exe' and name != 'java':
                continue
            cmdline = ' '.join(proc.info.get('cmdline') or [])
            if 'server.jar' in cmdline or 'nogui' in cmdline:
                return int(proc.info['pid'])
        except (psutil.Error, TypeError, ValueError):
            continue
    return None


def get_resource_stats(file_root: Path) -> dict[str, Any]:
    psutil = _try_import_psutil()
    stats: dict[str, Any] = {
        'cpu_percent': None,
        'memory_mb': None,
        'memory_percent': None,
        'disk_total_gb': None,
        'disk_used_gb': None,
        'disk_percent': None,
        'java_pid': None,
    }
    if psutil is not None:
        pid = find_java_pid()
        stats['java_pid'] = pid
        if pid is not None:
            try:
                proc = psutil.Process(pid)
                stats['cpu_percent'] = round(proc.cpu_percent(interval=0.1), 1)
                mem = proc.memory_info()
                stats['memory_mb'] = round(mem.rss / 1024 / 1024, 1)
                stats['memory_percent'] = round(proc.memory_percent(), 1)
            except (psutil.Error, AttributeError):
                pass
        try:
            usage = psutil.disk_usage(str(file_root))
            stats['disk_total_gb'] = round(usage.total / 1024 / 1024 / 1024, 1)
            stats['disk_used_gb'] = round(usage.used / 1024 / 1024 / 1024, 1)
            stats['disk_percent'] = round(usage.percent, 1)
        except OSError:
            pass
    else:
        try:
            usage = shutil.disk_usage(file_root)
            stats['disk_total_gb'] = round(usage.total / 1024 / 1024 / 1024, 1)
            stats['disk_used_gb'] = round(usage.used / 1024 / 1024 / 1024, 1)
            stats['disk_percent'] = round(usage.used / usage.total * 100, 1)
        except OSError:
            pass
    return stats


def parse_properties(content: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if '=' not in stripped:
            continue
        key, value = stripped.split('=', 1)
        result[key.strip()] = value.strip()
    return result


def serialize_properties(data: dict[str, str], original: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for line in original.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            lines.append(line)
            continue
        key = stripped.split('=', 1)[0].strip()
        if key in data:
            lines.append(f'{key}={data[key]}')
            seen.add(key)
        else:
            lines.append(line)
    for key, value in data.items():
        if key not in seen:
            lines.append(f'{key}={value}')
    return '\n'.join(lines) + ('\n' if lines else '')


def search_files(file_root: Path, query: str, *, limit: int = 100) -> list[dict[str, Any]]:
    query = query.strip().lower()
    if not query:
        return []
    results: list[dict[str, Any]] = []
    skip_dirs = {'logs', 'libraries', 'versions', '.git', 'node_modules', '__pycache__', '.fabric'}
    for root, dirs, files in os.walk(file_root):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]
        rel_root = Path(root).relative_to(file_root)
        for name in files:
            if query in name.lower():
                rel = (rel_root / name).as_posix()
                if rel.startswith('./'):
                    rel = rel[2:]
                try:
                    stat = (file_root / rel).stat()
                except OSError:
                    continue
                results.append({
                    'name': name,
                    'path': rel,
                    'type': 'file',
                    'size': stat.st_size,
                    'modified': stat.st_mtime,
                })
                if len(results) >= limit:
                    return results
    return results


def load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_json_list(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


class ActivityLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def append(self, action: str, detail: str = '') -> None:
        entry = {
            'time': datetime.now().isoformat(timespec='seconds'),
            'action': action,
            'detail': detail,
        }
        with self._lock:
            items = load_json_list(self.path)
            items.insert(0, entry)
            save_json_list(self.path, items[:MAX_ACTIVITY])

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return load_json_list(self.path)[:limit]


class ScheduleManager:
    CRON_RE = re.compile(r'^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)$')

    def __init__(
        self,
        path: Path,
        *,
        execute_command: Callable[[str], None],
        server_start: Callable[[], bool],
        server_stop: Callable[[], bool],
        server_restart: Callable[[], bool],
        logger: Callable[[str], None],
        activity: ActivityLog,
        is_server_running: Callable[[], bool],
    ):
        self.path = path
        self.execute_command = execute_command
        self.server_start = server_start
        self.server_stop = server_stop
        self.server_restart = server_restart
        self.logger = logger
        self.activity = activity
        self.is_server_running = is_server_running
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run: dict[str, str] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='PanelSchedules', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return load_json_list(self.path)

    def save_all(self, items: list[dict[str, Any]]) -> None:
        with self._lock:
            save_json_list(self.path, items)

    def upsert(self, item: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            items = load_json_list(self.path)
            item_id = item.get('id') or f'sch-{int(time.time() * 1000)}'
            item['id'] = item_id
            replaced = False
            for idx, existing in enumerate(items):
                if existing.get('id') == item_id:
                    items[idx] = item
                    replaced = True
                    break
            if not replaced:
                items.append(item)
            save_json_list(self.path, items)
            return item

    def delete(self, item_id: str) -> bool:
        with self._lock:
            items = load_json_list(self.path)
            new_items = [i for i in items if i.get('id') != item_id]
            if len(new_items) == len(items):
                return False
            save_json_list(self.path, new_items)
            return True

    def run_now(self, item_id: str) -> bool:
        for item in self.list():
            if item.get('id') == item_id:
                self._execute_item(item, manual=True)
                return True
        return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now()
            key = now.strftime('%Y-%m-%d %H:%M')
            for item in self.list():
                if not item.get('enabled', True):
                    continue
                cron = item.get('cron', '').strip()
                if not self._match_cron(cron, now):
                    continue
                run_key = f"{item.get('id')}:{key}"
                if run_key in self._last_run:
                    continue
                self._last_run[run_key] = key
                self._execute_item(item)
            self._stop.wait(30)

    def _execute_item(self, item: dict[str, Any], *, manual: bool = False) -> None:
        action = item.get('action', 'command')
        name = item.get('name', item.get('id', 'schedule'))
        if action == 'command':
            command = str(item.get('command', '')).strip()
            if not command:
                return
            if not self.is_server_running():
                self.logger(f'排程「{name}」略過：伺服器未運行')
                return
            self.execute_command(command)
            self.activity.append('排程執行' if not manual else '手動排程', f'{name}: {command}')
            self.logger(f'排程執行：{name} -> {command}')
        elif action == 'start':
            self.server_start()
            self.activity.append('排程執行' if not manual else '手動排程', f'{name}: start')
            self.logger(f'排程觸發 start：{name}')
        elif action == 'stop':
            self.server_stop()
            self.activity.append('排程執行' if not manual else '手動排程', f'{name}: stop')
            self.logger(f'排程觸發 stop：{name}')
        elif action == 'restart':
            self.server_restart()
            self.activity.append('排程執行' if not manual else '手動排程', f'{name}: restart')
            self.logger(f'排程觸發 restart：{name}')

    @classmethod
    def _match_cron(cls, cron: str, now: datetime) -> bool:
        match = cls.CRON_RE.match(cron)
        if not match:
            return False
        minute, hour, dom, month, dow = match.groups()
        checks = [
            (minute, str(now.minute)),
            (hour, str(now.hour)),
            (dom, str(now.day)),
            (month, str(now.month)),
            (dow, str((now.weekday() + 1) % 7)),
        ]
        for field, value in checks:
            if not cls._cron_field_match(field, value):
                return False
        return True

    @staticmethod
    def _cron_field_match(field: str, value: str) -> bool:
        if field == '*':
            return True
        for part in field.split(','):
            part = part.strip()
            if part == value:
                return True
            if '/' in part:
                base, step = part.split('/', 1)
                try:
                    step_n = int(step)
                    val_n = int(value)
                    if base == '*':
                        return val_n % step_n == 0
                except ValueError:
                    continue
            if '-' in part:
                try:
                    start, end = part.split('-', 1)
                    if int(start) <= int(value) <= int(end):
                        return True
                except ValueError:
                    continue
        return False
