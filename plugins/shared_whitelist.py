from mcdreforged.api.all import *
import json
import os
import re
import shutil
import threading
from pathlib import Path

config: dict = {}
_server: PluginServerInterface | None = None
_timer: threading.Timer | None = None
_last_source_mtime: float = -1
_lock = threading.Lock()

DEFAULT_CONFIG = {
    'role': 'primary',
    'source_whitelist': './server/whitelist.json',
    'target_whitelist': './Mirror/server/whitelist.json',
    'check_interval': 2.0,
    'mirror_rcon_enable': True,
    'mirror_rcon_host': 'gravity-mirror',
    'mirror_rcon_port': 25576,
    'mirror_rcon_password': '',
}

_ADDED_RE = re.compile(
    r'(?:Added|已将|已將)\s+(.+?)\s+(?:to|加入|添加)',
    re.IGNORECASE,
)
_REMOVED_RE = re.compile(
    r'(?:Removed|已将|已將)\s+(.+?)\s+(?:from|移出|移除)',
    re.IGNORECASE,
)


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1


def _same_content(left: Path, right: Path) -> bool:
    try:
        return left.read_bytes() == right.read_bytes()
    except OSError:
        return False


def _read_names(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        with path.open(encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    names: list[str] = []
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                name = str(entry.get('name', '')).strip()
                if name:
                    names.append(name)
    return sorted(set(names), key=str.lower)


def _mirror_rcon(command: str) -> str | None:
    assert _server is not None
    if not config.get('mirror_rcon_enable', True):
        return None
    try:
        from mcdreforged.api.rcon import RconConnection
    except ImportError:
        _server.logger.error('[shared_whitelist] MCDR RCON module unavailable, mirror sync skipped')
        return None
    host = str(os.getenv('MIRROR_RCON_HOST') or config.get('mirror_rcon_host', 'gravity-mirror'))
    port = int(os.getenv('MIRROR_RCON_PORT') or config.get('mirror_rcon_port', 25576))
    password = os.getenv('MIRROR_RCON_PASSWORD') or str(config.get('mirror_rcon_password', ''))
    if not password or password in ('YOUR_RCON_PASSWORD', 'REDACTED'):
        _server.logger.error('[shared_whitelist] MIRROR_RCON_PASSWORD not set, mirror RCON sync skipped')
        return None
    conn = RconConnection(host, port, password)
    if not conn.connect():
        _server.logger.error(f'[shared_whitelist] Mirror RCON connection failed: {host}:{port}')
        return None
    try:
        return conn.send_command(command)
    finally:
        conn.disconnect()


def _parse_mirror_whitelist_names(response: str | None) -> set[str]:
    if not response:
        return set()
    text = response.strip()
    if ':' in text:
        text = text.split(':', 1)[1].strip()
    if not text or text.lower() in {'no players', 'none', '（无）', '（無）'}:
        return set()
    return {part.strip().strip('`') for part in text.split(',') if part.strip()}


def _sync_mirror_via_rcon(source: Path) -> bool:
    assert _server is not None
    desired = set(_read_names(source))
    current = _parse_mirror_whitelist_names(_mirror_rcon('whitelist list'))
    changed = False
    for name in sorted(desired - current, key=str.lower):
        _mirror_rcon(f'whitelist add {name}')
        changed = True
        _server.logger.info(f'[shared_whitelist] Mirror whitelist add: {name}')
    for name in sorted(current - desired, key=str.lower):
        _mirror_rcon(f'whitelist remove {name}')
        changed = True
        _server.logger.info(f'[shared_whitelist] Mirror whitelist remove: {name}')
    if changed:
        _mirror_rcon('whitelist reload')
        _server.logger.info('[shared_whitelist] Mirror whitelist synced and reloaded')
    return changed


def _copy_whitelist(source: Path, target: Path, *, reload: bool) -> bool:
    assert _server is not None
    if not source.is_file():
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as error:
        _server.logger.error(f'[shared_whitelist] Whitelist copy failed: {error}')
        return False
    _server.logger.info(f'[shared_whitelist] Whitelist copied: {source.name} -> {target}')
    if reload and _server.is_server_running():
        _server.execute('whitelist reload')
    return True


def _sync_once() -> None:
    global _last_source_mtime

    assert _server is not None
    source = _resolve(config['source_whitelist'])
    target = _resolve(config['target_whitelist'])
    role = config.get('role', 'primary')

    source_mtime = _mtime(source)
    if source_mtime < 0:
        return

    with _lock:
        if role == 'primary':
            if source_mtime == _last_source_mtime:
                return
            _last_source_mtime = source_mtime
            if config.get('mirror_rcon_enable', True):
                _sync_mirror_via_rcon(source)
            elif target.parent.exists() or target == _resolve('.'):
                _copy_whitelist(source, target, reload=False)
            return

        local = _resolve('./server/whitelist.json')
        if source == local:
            if source_mtime == _last_source_mtime:
                return
            _last_source_mtime = source_mtime
            if _server.is_server_running():
                _server.execute('whitelist reload')
            return

        if _same_content(source, local):
            _last_source_mtime = source_mtime
            return

        _last_source_mtime = source_mtime
        _copy_whitelist(source, local, reload=True)


def _schedule_next() -> None:
    global _timer
    interval = float(config.get('check_interval', 2.0))
    _timer = threading.Timer(interval, _run_check)
    _timer.daemon = True
    _timer.start()


def _run_check() -> None:
    try:
        _sync_once()
    finally:
        _schedule_next()


def _on_whitelist_changed() -> None:
    global _last_source_mtime
    with _lock:
        _last_source_mtime = -1
    _sync_once()


def _quick_mirror_command_from_log(content: str) -> str | None:
    lowered = content.lower()
    if 'whitelist' not in lowered and '白名单' not in content and '白名單' not in content:
        return None
    for pattern in (_ADDED_RE, _REMOVED_RE):
        match = pattern.search(content)
        if match:
            name = match.group(1).strip().strip('<>')
            if name:
                if pattern is _ADDED_RE:
                    return f'whitelist add {name}'
                return f'whitelist remove {name}'
    return None


def on_info(server: PluginServerInterface, info: Info):
    if not info.content:
        return
    if config.get('role', 'primary') != 'primary':
        return
    if not config.get('mirror_rcon_enable', True):
        return

    quick = _quick_mirror_command_from_log(info.content)
    if quick:
        _mirror_rcon(quick)
        if quick.startswith('whitelist add') or quick.startswith('whitelist remove'):
            _mirror_rcon('whitelist reload')

    if 'whitelist' in info.content.lower() or '白名单' in info.content or '白名單' in info.content:
        if any(token in info.content.lower() for token in ('added', 'removed', 'reloaded')) or \
                any(token in info.content for token in ('加入', '移出', '白名单', '白名單')):
            _on_whitelist_changed()


def on_load(server: PluginServerInterface, old_module):
    global config, _server, _timer, _last_source_mtime
    _server = server
    config = server.load_config_simple('config.json', DEFAULT_CONFIG.copy())
    _last_source_mtime = -1

    role = config.get('role', 'primary')
    source = _resolve(config['source_whitelist'])

    if role == 'primary':
        if config.get('mirror_rcon_enable', True):
            server.logger.info(
                f'[shared_whitelist] Primary mode: {source.name} -> mirror RCON '
                f'{config.get("mirror_rcon_host")}:{config.get("mirror_rcon_port")}'
            )
        else:
            target = _resolve(config['target_whitelist'])
            server.logger.info(f'[shared_whitelist] Primary mode: {source.name} -> {target}')
    else:
        server.logger.info(f'[shared_whitelist] Mirror mode: following {source}')

    _sync_once()
    _schedule_next()


def on_unload(server: PluginServerInterface):
    global _timer
    if _timer is not None:
        _timer.cancel()
        _timer = None
