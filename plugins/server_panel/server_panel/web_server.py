from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import secrets
import shutil
import tempfile
import threading
import time
import urllib.parse
import zipfile
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from server_panel.features import get_resource_stats, parse_properties, search_files, serialize_properties
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from server_panel.features import ActivityLog, ScheduleManager


ZIP_SKIP_DIRS = {'logs', 'debug', 'crash-reports', 'node_modules', '.git', '__pycache__', '.cursor'}
ZIP_SKIP_SUFFIXES = {'.log', '.tmp', '.lock'}


def should_skip_zip_path(relative_parts: tuple[str, ...]) -> bool:
    if not relative_parts:
        return False
    if relative_parts[0] in ZIP_SKIP_DIRS:
        return True
    if any(part in ZIP_SKIP_DIRS for part in relative_parts):
        return True
    if relative_parts[-1].endswith(tuple(ZIP_SKIP_SUFFIXES)):
        return True
    return False


def build_zip_archive(file_root: Path, paths: list[str], *, skip_heavy: bool = False) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    tmp.close()
    archive_path = Path(tmp.name)
    root = file_root.resolve()

    def add_path(target: Path) -> None:
        if not target.exists():
            return
        if target.is_file():
            rel = target.relative_to(root)
            if skip_heavy and should_skip_zip_path(rel.parts):
                return
            zf.write(target, rel.as_posix())
            return
        for item in sorted(target.rglob('*')):
            if not item.is_file():
                continue
            rel = item.relative_to(root)
            if skip_heavy and should_skip_zip_path(rel.parts):
                continue
            zf.write(item, rel.as_posix())

    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel_path in paths:
            rel_path = rel_path.replace('\\', '/').strip('/')
            target = (root / rel_path).resolve() if rel_path else root
            try:
                target.relative_to(root)
            except ValueError:
                continue
            add_path(target)
    return archive_path


BACKUP_PRESETS: dict[str, dict[str, Any]] = {
    'world': {
        'label': '世界存档',
        'paths': ['world'],
        'skip_heavy': False,
    },
    'config': {
        'label': '配置文件',
        'paths': ['config', 'server.properties', 'permission.yml', 'ops.json', 'whitelist.json', 'banned-ips.json', 'banned-players.json'],
        'skip_heavy': False,
    },
    'full': {
        'label': '完整备份',
        'paths': ['.'],
        'skip_heavy': True,
    },
}


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def parse_multipart_form(body: bytes, content_type: str) -> dict[str, tuple[str | None, bytes]]:
    boundary = None
    for part in content_type.split(';'):
        part = part.strip()
        if part.startswith('boundary='):
            boundary = part[len('boundary='):].strip('"')
            break
    if not boundary:
        return {}

    delimiter = f'--{boundary}'.encode('latin-1')
    fields: dict[str, tuple[str | None, bytes]] = {}
    for chunk in body.split(delimiter):
        if not chunk or chunk in (b'--', b'--\r\n'):
            continue
        if chunk.startswith(b'\r\n'):
            chunk = chunk[2:]
        header_end = chunk.find(b'\r\n\r\n')
        if header_end == -1:
            continue
        headers = chunk[:header_end].decode('latin-1', errors='replace')
        content = chunk[header_end + 4:]
        if content.endswith(b'\r\n'):
            content = content[:-2]

        name = None
        filename = None
        for line in headers.split('\r\n'):
            if not line.lower().startswith('content-disposition:'):
                continue
            for token in line.split(';')[1:]:
                token = token.strip()
                if token.startswith('name='):
                    name = token[5:].strip('"')
                elif token.startswith('filename='):
                    filename = token[9:].strip('"')
        if name:
            fields[name] = (filename, content)
    return fields


class LogBroadcaster:
    def __init__(self, max_history: int = 500):
        self._lock = threading.Lock()
        self._history: list[str] = []
        self._subscribers: list[threading.Event] = []
        self._max_history = max_history

    def append(self, line: str) -> None:
        with self._lock:
            self._history.append(line)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            for event in self._subscribers:
                event.set()

    def history(self) -> list[str]:
        with self._lock:
            return list(self._history)

    def wait_event(self) -> tuple[threading.Event, int]:
        event = threading.Event()
        with self._lock:
            index = len(self._history)
            self._subscribers.append(event)
        return event, index

    def release_event(self, event: threading.Event) -> None:
        with self._lock:
            if event in self._subscribers:
                self._subscribers.remove(event)


class PanelWebServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        password_hash: str,
        file_root: Path,
        allow_upload: bool,
        max_upload_mb: int,
        get_status: Callable[[], dict[str, Any]],
        server_start: Callable[[], bool],
        server_stop: Callable[[], bool],
        server_restart: Callable[[], bool],
        server_stop_exit: Callable[[], bool],
        execute_command: Callable[[str], None],
        logger: Callable[[str], None],
        plugin_root: Path,
        server_name: str = 'GRAVITY Server Panel',
        server_role: str = 'main',
        server_label: str = '主服',
        activity_log: ActivityLog | None = None,
        schedule_manager: ScheduleManager | None = None,
        hot_reload_check: Callable[[], None] | None = None,
    ):
        self.host = host
        self.port = port
        self.password_hash = password_hash
        self.file_root = file_root.resolve()
        self.allow_upload = allow_upload
        self.max_upload_bytes = max_upload_mb * 1024 * 1024
        self.get_status = get_status
        self.server_start = server_start
        self.server_stop = server_stop
        self.server_restart = server_restart
        self.server_stop_exit = server_stop_exit
        self.execute_command = execute_command
        self.logger = logger
        self.plugin_root = plugin_root
        self.server_name = server_name
        self.server_role = server_role
        self.server_label = server_label
        self.activity_log = activity_log
        self.schedule_manager = schedule_manager
        self.hot_reload_check = hot_reload_check
        self.logs = LogBroadcaster()
        self._sessions: dict[str, float] = {}
        self._session_lock = threading.Lock()
        self._session_ttl = 24 * 3600
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        handler_cls = self._build_handler()
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler_cls)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name='ServerPanelWeb', daemon=True)
        self._thread.start()
        self.logger(f'Web 面板已啟動：http://{self.host}:{self.port}')

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def _build_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, fmt: str, *args) -> None:
                return

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                if not raw:
                    return {}
                return json.loads(raw.decode('utf-8'))

            def _get_token(self) -> str | None:
                auth = self.headers.get('Authorization', '')
                if auth.startswith('Bearer '):
                    return auth[7:].strip()
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if 'token' in query and query['token']:
                    return query['token'][0]
                cookie = self.headers.get('Cookie', '')
                for part in cookie.split(';'):
                    part = part.strip()
                    if part.startswith('panel_token='):
                        return part.split('=', 1)[1]
                return None

            def _is_authenticated(self) -> bool:
                token = self._get_token()
                if not token:
                    return False
                now = time.time()
                with server._session_lock:
                    expired = [k for k, v in server._sessions.items() if now - v > server._session_ttl]
                    for key in expired:
                        del server._sessions[key]
                    if token in server._sessions:
                        server._sessions[token] = now
                        return True
                return False

            def _require_auth(self) -> bool:
                if self._is_authenticated():
                    return True
                self._send_json(HTTPStatus.UNAUTHORIZED, {'ok': False, 'error': '未登入'})
                return False

            def _resolve_path(self, rel_path: str) -> Path | None:
                rel_path = rel_path.replace('\\', '/').strip('/')
                target = (server.file_root / rel_path).resolve()
                try:
                    target.relative_to(server.file_root)
                except ValueError:
                    return None
                return target

            def _serve_static(self, rel_path: str) -> None:
                safe = rel_path.lstrip('/').replace('..', '')
                file_path = (server.plugin_root / 'web' / safe).resolve()
                web_root = (server.plugin_root / 'web').resolve()
                if not str(file_path).startswith(str(web_root)) or not file_path.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                content = file_path.read_bytes()
                mime, _ = mimetypes.guess_type(str(file_path))
                self.send_response(HTTPStatus.OK)
                self.send_header('Content-Type', mime or 'application/octet-stream')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def _serve_download_file(self, file_path: Path, download_name: str, mime: str = 'application/octet-stream') -> None:
                size = file_path.stat().st_size
                self.send_response(HTTPStatus.OK)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Disposition', f'attachment; filename="{download_name}"')
                self.send_header('Content-Length', str(size))
                self.end_headers()
                with file_path.open('rb') as fp:
                    shutil.copyfileobj(fp, self.wfile)

            def _serve_zip_download(self, paths: list[str], download_name: str, skip_heavy: bool = False) -> None:
                archive = build_zip_archive(server.file_root, paths, skip_heavy=skip_heavy)
                try:
                    if archive.stat().st_size == 0:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '沒有可打包的檔案'})
                    self._serve_download_file(archive, download_name, 'application/zip')
                finally:
                    archive.unlink(missing_ok=True)

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path

                if server.hot_reload_check is not None and path in ('/', '/index.html', '/api/panel-info'):
                    try:
                        server.hot_reload_check()
                    except Exception:
                        pass

                if path in ('/', '/index.html'):
                    return self._serve_static('index.html')
                if path.startswith('/static/'):
                    return self._serve_static(path[len('/static/'):])

                if path == '/api/panel-info':
                    return self._send_json(HTTPStatus.OK, {
                        'ok': True,
                        'data': {
                            'server_name': server.server_name,
                            'server_role': server.server_role,
                            'server_label': server.server_label,
                            'port': server.port,
                        },
                    })

                if path == '/api/status':
                    if not self._require_auth():
                        return
                    payload = server.get_status()
                    payload['stats'] = get_resource_stats(server.file_root)
                    return self._send_json(HTTPStatus.OK, {'ok': True, 'data': payload})

                if path == '/api/stats':
                    if not self._require_auth():
                        return
                    return self._send_json(HTTPStatus.OK, {
                        'ok': True,
                        'data': get_resource_stats(server.file_root),
                    })

                if path == '/api/activity':
                    if not self._require_auth():
                        return
                    if server.activity_log is None:
                        return self._send_json(HTTPStatus.OK, {'ok': True, 'data': []})
                    return self._send_json(HTTPStatus.OK, {'ok': True, 'data': server.activity_log.list()})

                if path == '/api/schedules':
                    if not self._require_auth():
                        return
                    if server.schedule_manager is None:
                        return self._send_json(HTTPStatus.OK, {'ok': True, 'data': []})
                    return self._send_json(HTTPStatus.OK, {'ok': True, 'data': server.schedule_manager.list()})

                if path == '/api/settings/server-properties':
                    if not self._require_auth():
                        return
                    target = server.file_root / 'server.properties'
                    if not target.is_file():
                        return self._send_json(HTTPStatus.NOT_FOUND, {'ok': False, 'error': '找不到 server.properties'})
                    content = target.read_text(encoding='utf-8')
                    return self._send_json(HTTPStatus.OK, {
                        'ok': True,
                        'data': {'content': content, 'parsed': parse_properties(content)},
                    })

                if path == '/api/settings/mcdr-config':
                    if not self._require_auth():
                        return
                    target = server.file_root / 'config.yml'
                    if not target.is_file():
                        return self._send_json(HTTPStatus.NOT_FOUND, {'ok': False, 'error': '找不到 config.yml'})
                    return self._send_json(HTTPStatus.OK, {
                        'ok': True,
                        'data': {'content': target.read_text(encoding='utf-8')},
                    })

                if path == '/api/files/search':
                    if not self._require_auth():
                        return
                    query = urllib.parse.parse_qs(parsed.query)
                    q = query.get('q', [''])[0]
                    return self._send_json(HTTPStatus.OK, {
                        'ok': True,
                        'data': search_files(server.file_root, q),
                    })

                if path == '/api/logs/history':
                    if not self._require_auth():
                        return
                    return self._send_json(HTTPStatus.OK, {'ok': True, 'data': server.logs.history()})

                if path == '/api/logs/stream':
                    if not self._require_auth():
                        return
                    self.send_response(HTTPStatus.OK)
                    self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Connection', 'keep-alive')
                    self.send_header('X-Accel-Buffering', 'no')
                    self.end_headers()
                    event, start_index = server.logs.wait_event()
                    try:
                        for line in server.logs.history():
                            payload = f'data: {json.dumps(line, ensure_ascii=False)}\n\n'
                            self.wfile.write(payload.encode('utf-8'))
                            self.wfile.flush()
                        while True:
                            if not event.wait(timeout=15):
                                try:
                                    self.wfile.write(b': ping\n\n')
                                    self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError):
                                    break
                                continue
                            try:
                                history = server.logs.history()
                                for line in history[start_index:]:
                                    payload = f'data: {json.dumps(line, ensure_ascii=False)}\n\n'
                                    self.wfile.write(payload.encode('utf-8'))
                                    self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                break
                            finally:
                                event.clear()
                                server.logs.release_event(event)
                                event, start_index = server.logs.wait_event()
                    finally:
                        server.logs.release_event(event)
                    return

                if path == '/api/files/list':
                    if not self._require_auth():
                        return
                    query = urllib.parse.parse_qs(parsed.query)
                    rel = query.get('path', [''])[0]
                    target = self._resolve_path(rel)
                    if target is None:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '非法路徑'})
                    if not target.exists():
                        return self._send_json(HTTPStatus.NOT_FOUND, {'ok': False, 'error': '路徑不存在'})
                    if target.is_file():
                        stat = target.stat()
                        return self._send_json(HTTPStatus.OK, {
                            'ok': True,
                            'data': {
                                'type': 'file',
                                'name': target.name,
                                'path': rel.replace('\\', '/'),
                                'size': stat.st_size,
                                'modified': stat.st_mtime,
                            }
                        })
                    entries = []
                    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                        try:
                            stat = child.stat()
                        except OSError:
                            continue
                        rel_child = str(child.relative_to(server.file_root)).replace('\\', '/')
                        entries.append({
                            'name': child.name,
                            'path': rel_child,
                            'type': 'dir' if child.is_dir() else 'file',
                            'size': stat.st_size if child.is_file() else None,
                            'modified': stat.st_mtime,
                        })
                    return self._send_json(HTTPStatus.OK, {
                        'ok': True,
                        'data': {
                            'type': 'dir',
                            'path': rel.replace('\\', '/'),
                            'entries': entries,
                        }
                    })

                if path == '/api/files/read':
                    if not self._require_auth():
                        return
                    query = urllib.parse.parse_qs(parsed.query)
                    rel = query.get('path', [''])[0]
                    target = self._resolve_path(rel)
                    if target is None or not target.is_file():
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '無法讀取檔案'})
                    if target.stat().st_size > 2 * 1024 * 1024:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '檔案過大，請使用下載'})
                    try:
                        content = target.read_text(encoding='utf-8')
                    except UnicodeDecodeError:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '非文字檔案，請使用下載'})
                    return self._send_json(HTTPStatus.OK, {'ok': True, 'data': {'content': content}})

                if path == '/api/backup/presets':
                    if not self._require_auth():
                        return
                    presets = [
                        {'id': key, 'label': value['label']}
                        for key, value in BACKUP_PRESETS.items()
                    ]
                    return self._send_json(HTTPStatus.OK, {'ok': True, 'data': presets})

                if path == '/api/backup/download':
                    if not self._require_auth():
                        return
                    query = urllib.parse.parse_qs(parsed.query)
                    preset_id = query.get('preset', [''])[0]
                    preset = BACKUP_PRESETS.get(preset_id)
                    if preset is None:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '未知的備份項目'})
                    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                    role = server.server_role or 'server'
                    filename = f'{role}-{preset_id}-backup-{stamp}.zip'
                    return self._serve_zip_download(
                        preset['paths'],
                        filename,
                        skip_heavy=bool(preset.get('skip_heavy')),
                    )

                if path == '/api/files/zip':
                    if not self._require_auth():
                        return
                    query = urllib.parse.parse_qs(parsed.query)
                    rel = query.get('path', [''])[0]
                    target = self._resolve_path(rel)
                    if target is None or not target.exists():
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '無法打包此路徑'})
                    name = target.name if rel else 'root'
                    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                    filename = f'{server.server_role}-{name}-{stamp}.zip'
                    zip_path = '.' if not rel else rel.replace('\\', '/')
                    skip_heavy = query.get('skip_heavy', ['0'])[0] == '1'
                    return self._serve_zip_download([zip_path], filename, skip_heavy=skip_heavy)

                if path == '/api/files/download':
                    if not self._require_auth():
                        return
                    query = urllib.parse.parse_qs(parsed.query)
                    rel = query.get('path', [''])[0]
                    target = self._resolve_path(rel)
                    if target is None or not target.is_file():
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    content = target.read_bytes()
                    mime, _ = mimetypes.guess_type(target.name)
                    self.send_response(HTTPStatus.OK)
                    self.send_header('Content-Type', mime or 'application/octet-stream')
                    self.send_header('Content-Disposition', f'attachment; filename="{target.name}"')
                    self.send_header('Content-Length', str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return

                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path

                if path == '/api/login':
                    body = self._read_json()
                    password = body.get('password', '')
                    if hash_password(password) != server.password_hash:
                        return self._send_json(HTTPStatus.UNAUTHORIZED, {'ok': False, 'error': '密碼錯誤'})
                    token = secrets.token_urlsafe(32)
                    with server._session_lock:
                        server._sessions[token] = time.time()
                    self._send_json(HTTPStatus.OK, {'ok': True, 'data': {'token': token}})
                    return

                if not self._require_auth():
                    return

                if path == '/api/server/start':
                    ok = server.server_start()
                    if server.activity_log:
                        server.activity_log.append('啟動伺服器', 'Web 面板')
                    return self._send_json(HTTPStatus.OK, {
                        'ok': True,
                        'data': {
                            'success': ok,
                            'message': '已送出啟動指令' if ok else '伺服器已在運行或無法啟動',
                            'status': server.get_status(),
                        },
                    })

                if path == '/api/server/stop':
                    ok = server.server_stop()
                    if server.activity_log:
                        server.activity_log.append('停止伺服器', 'Web 面板')
                    return self._send_json(HTTPStatus.OK, {
                        'ok': True,
                        'data': {
                            'success': ok,
                            'message': '已送出停止指令' if ok else '伺服器目前未運行',
                            'status': server.get_status(),
                        },
                    })

                if path == '/api/server/restart':
                    ok = server.server_restart()
                    if server.activity_log:
                        server.activity_log.append('重啟伺服器', 'Web 面板')
                    return self._send_json(HTTPStatus.OK, {
                        'ok': True,
                        'data': {
                            'success': ok,
                            'message': '已送出重啟指令' if ok else '伺服器目前未運行，無法重啟',
                            'status': server.get_status(),
                        },
                    })

                if path == '/api/server/stop_exit':
                    ok = server.server_stop_exit()
                    return self._send_json(HTTPStatus.OK, {
                        'ok': True,
                        'data': {
                            'success': ok,
                            'message': 'MCDR 關閉指令已送出' if ok else '無法關閉 MCDR',
                            'status': server.get_status(),
                        },
                    })

                if path == '/api/console':
                    body = self._read_json()
                    command = str(body.get('command', '')).strip()
                    if not command:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '指令不可為空'})
                    server.execute_command(command)
                    if server.activity_log:
                        server.activity_log.append('主控台指令', command)
                    return self._send_json(HTTPStatus.OK, {'ok': True})

                if path == '/api/files/write':
                    body = self._read_json()
                    rel = body.get('path', '')
                    content = body.get('content', '')
                    target = self._resolve_path(rel)
                    if target is None:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '非法路徑'})
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding='utf-8')
                    return self._send_json(HTTPStatus.OK, {'ok': True})

                if path == '/api/files/mkdir':
                    body = self._read_json()
                    rel = body.get('path', '')
                    target = self._resolve_path(rel)
                    if target is None:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '非法路徑'})
                    target.mkdir(parents=True, exist_ok=True)
                    return self._send_json(HTTPStatus.OK, {'ok': True})

                if path == '/api/files/rename':
                    body = self._read_json()
                    src = self._resolve_path(body.get('from', ''))
                    dst = self._resolve_path(body.get('to', ''))
                    if src is None or dst is None or not src.exists():
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '重新命名失敗'})
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    src.rename(dst)
                    return self._send_json(HTTPStatus.OK, {'ok': True})

                if path == '/api/files/delete':
                    body = self._read_json()
                    target = self._resolve_path(body.get('path', ''))
                    if target is None or not target.exists():
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '刪除失敗'})
                    if target.is_dir():
                        import shutil
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                    return self._send_json(HTTPStatus.OK, {'ok': True})

                if path == '/api/files/upload':
                    if not server.allow_upload:
                        return self._send_json(HTTPStatus.FORBIDDEN, {'ok': False, 'error': '已停用上傳'})
                    content_type = self.headers.get('Content-Type', '')
                    if 'multipart/form-data' not in content_type:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '需要 multipart 上傳'})
                    length = int(self.headers.get('Content-Length', '0'))
                    if length > server.max_upload_bytes:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '檔案過大'})
                    body = self.rfile.read(length)
                    form = parse_multipart_form(body, content_type)
                    path_field = form.get('path', (None, b''))
                    file_field = form.get('file')
                    if file_field is None:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '缺少檔案'})
                    rel_dir = path_field[1].decode('utf-8', errors='replace')
                    filename = os.path.basename(file_field[0] or 'upload.bin')
                    target_dir = self._resolve_path(rel_dir)
                    if target_dir is None:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '非法路徑'})
                    if not target_dir.exists():
                        target_dir.mkdir(parents=True, exist_ok=True)
                    if target_dir.is_file():
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '目標不是資料夾'})
                    target_file = (target_dir / filename).resolve()
                    try:
                        target_file.relative_to(server.file_root)
                    except ValueError:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '非法路徑'})
                    data = file_field[1]
                    if len(data) > server.max_upload_bytes:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '檔案過大'})
                    target_file.write_bytes(data)
                    if server.activity_log:
                        server.activity_log.append('上傳檔案', str(target_file.relative_to(server.file_root)).replace('\\', '/'))
                    return self._send_json(HTTPStatus.OK, {'ok': True, 'data': {'path': str(target_file.relative_to(server.file_root)).replace('\\', '/')}})

                if path == '/api/settings/server-properties':
                    body = self._read_json()
                    content = body.get('content')
                    if content is None:
                        parsed = body.get('parsed')
                        if isinstance(parsed, dict):
                            original = (server.file_root / 'server.properties').read_text(encoding='utf-8') if (server.file_root / 'server.properties').is_file() else ''
                            content = serialize_properties({str(k): str(v) for k, v in parsed.items()}, original)
                        else:
                            return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '缺少內容'})
                    target = server.file_root / 'server.properties'
                    target.write_text(str(content), encoding='utf-8')
                    if server.activity_log:
                        server.activity_log.append('儲存設定', 'server.properties')
                    return self._send_json(HTTPStatus.OK, {'ok': True})

                if path == '/api/settings/mcdr-config':
                    body = self._read_json()
                    content = body.get('content')
                    if content is None:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '缺少內容'})
                    target = server.file_root / 'config.yml'
                    target.write_text(str(content), encoding='utf-8')
                    if server.activity_log:
                        server.activity_log.append('儲存設定', 'config.yml')
                    return self._send_json(HTTPStatus.OK, {'ok': True})

                if path == '/api/schedules/save':
                    if server.schedule_manager is None:
                        return self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {'ok': False, 'error': '排程未啟用'})
                    body = self._read_json()
                    item = server.schedule_manager.upsert(body)
                    if server.activity_log:
                        server.activity_log.append('儲存排程', item.get('name', item.get('id', '')))
                    return self._send_json(HTTPStatus.OK, {'ok': True, 'data': item})

                if path == '/api/schedules/delete':
                    if server.schedule_manager is None:
                        return self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {'ok': False, 'error': '排程未啟用'})
                    body = self._read_json()
                    item_id = str(body.get('id', '')).strip()
                    if not item_id:
                        return self._send_json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': '缺少 id'})
                    ok = server.schedule_manager.delete(item_id)
                    if server.activity_log and ok:
                        server.activity_log.append('刪除排程', item_id)
                    return self._send_json(HTTPStatus.OK, {'ok': ok})

                if path == '/api/schedules/run':
                    if server.schedule_manager is None:
                        return self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {'ok': False, 'error': '排程未啟用'})
                    body = self._read_json()
                    item_id = str(body.get('id', '')).strip()
                    ok = server.schedule_manager.run_now(item_id)
                    return self._send_json(HTTPStatus.OK, {'ok': ok})

                self.send_error(HTTPStatus.NOT_FOUND)

        return Handler
