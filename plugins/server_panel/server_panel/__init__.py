import os
import secrets
import time
from mcdreforged.api.all import *
from mcdreforged.command.builder.nodes.basic import Literal
from mcdreforged.minecraft.rtext.click_event import RClickAction
from pathlib import Path

from server_panel.features import ActivityLog, ScheduleManager
from server_panel.web_server import PanelWebServer, hash_password

PLUGIN_ID = 'server_panel'
DEFAULT_CONFIG = {
    'host': '0.0.0.0',
    'port': 8080,
    'password': '',
    'file_root': '.',
    'allow_upload': True,
    'max_upload_mb': 200,
    'server_name': 'GRAVITY 主伺服器',
    'server_role': 'main',
    'server_label': '主服',
}

_server: PluginServerInterface | None = None
_config: dict = {}
_web: PanelWebServer | None = None
_plugin_root: Path | None = None
_activity: ActivityLog | None = None
_schedules: ScheduleManager | None = None
_code_loaded_at: float = 0.0


def _log(message: str) -> None:
    if _server is not None:
        _server.logger.info(f'[{PLUGIN_ID}] {message}')


def _get_status() -> dict:
    assert _server is not None
    return {
        'mcdr_running': True,
        'server_running': _server.is_server_running(),
        'server_startup': _server.is_server_startup(),
        'file_root': str(_web.file_root) if _web else '',
        'port': _config.get('port', 8080),
        'server_name': _config.get('server_name', 'GRAVITY Server Panel'),
        'server_role': _config.get('server_role', 'main'),
        'server_label': _config.get('server_label', '主服'),
    }


def _start_server() -> bool:
    assert _server is not None
    result = _server.start()
    _log('Start server request received')
    return result


def _stop_server() -> bool:
    assert _server is not None
    result = _server.stop()
    _log('Stop server request received')
    return result


def _restart_server() -> bool:
    assert _server is not None
    result = _server.restart()
    _log('Restart server request received')
    return result


def _stop_exit() -> bool:
    assert _server is not None
    _log('Shutdown MCDR request received')
    return _server.stop_exit()


def _execute_command(command: str) -> None:
    assert _server is not None
    _server.execute(command)


def _append_log(line: str) -> None:
    if _web is not None:
        _web.logs.append(line)


def on_info(server: PluginServerInterface, info: Info):
    if _web is None:
        return
    if info.is_user:
        prefix = 'Player'
        name = info.player or 'Unknown'
    else:
        prefix = 'Server'
        name = 'Console'
    _append_log(f'[{prefix}] <{name}> {info.content}')


def _plugin_code_mtime() -> float:
    if _plugin_root is None:
        return 0.0
    mtimes = []
    for pattern in ('server_panel/*.py', 'web/*'):
        for path in _plugin_root.glob(pattern):
            if path.is_file():
                try:
                    mtimes.append(path.stat().st_mtime)
                except OSError:
                    pass
    return max(mtimes) if mtimes else 0.0


def _check_hot_reload() -> None:
    global _code_loaded_at
    assert _server is not None
    latest = _plugin_code_mtime()
    if _code_loaded_at and latest > _code_loaded_at + 0.5:
        _log('Panel code change detected, reloading plugin')
        _server.reload_plugin(PLUGIN_ID)


def on_load(server: PluginServerInterface, old_module):
    global _server, _config, _web, _plugin_root, _activity, _schedules, _code_loaded_at
    _server = server
    _config = server.load_config_simple('config.json', DEFAULT_CONFIG.copy())

    plugin_path = server.get_plugin_file_path(PLUGIN_ID)
    if plugin_path is None:
        server.logger.error(f'[{PLUGIN_ID}] Unable to resolve plugin path')
        return
    _plugin_root = Path(plugin_path)

    password = os.getenv('PANEL_PASSWORD') or _config.get('password') or ''
    if password in ('YOUR_PANEL_PASSWORD', 'REDACTED'):
        password = ''
    if not password:
        # 不再使用預設弱密碼：未設定時產生一次性隨機密碼（僅輸出到 MCDR 日誌，不寫入設定檔）
        password = secrets.token_urlsafe(12)
        server.logger.warning(f'[{PLUGIN_ID}] PANEL_PASSWORD not set, generated temporary password: {password} (regenerated on restart)')

    file_root = Path(_config.get('file_root', '.'))
    if not file_root.is_absolute():
        file_root = Path.cwd() / file_root

    config_dir = Path('config') / PLUGIN_ID
    _activity = ActivityLog(config_dir / 'activity.json')
    _schedules = ScheduleManager(
        config_dir / 'schedules.json',
        execute_command=_execute_command,
        server_start=_start_server,
        server_stop=_stop_server,
        server_restart=_restart_server,
        logger=_log,
        activity=_activity,
        is_server_running=lambda: _server.is_server_running() if _server else False,
    )
    _schedules.start()

    _web = PanelWebServer(
        host=_config.get('host', '0.0.0.0'),
        port=int(_config.get('port', 8080)),
        password_hash=hash_password(password),
        file_root=file_root,
        allow_upload=bool(_config.get('allow_upload', True)),
        max_upload_mb=int(_config.get('max_upload_mb', 200)),
        get_status=_get_status,
        server_start=_start_server,
        server_stop=_stop_server,
        server_restart=_restart_server,
        server_stop_exit=_stop_exit,
        execute_command=_execute_command,
        logger=_log,
        plugin_root=_plugin_root,
        server_name=_config.get('server_name', 'GRAVITY Server Panel'),
        server_role=_config.get('server_role', 'main'),
        server_label=_config.get('server_label', '主服'),
        activity_log=_activity,
        schedule_manager=_schedules,
        hot_reload_check=_check_hot_reload,
    )
    _web.start()
    _code_loaded_at = _plugin_code_mtime()
    _activity.append('面板啟動', f'埠 {_config.get("port", 8080)}')

    def show_panel_url(source: CommandSource):
        port = _config.get('port', 8080)
        host = _config.get('host', '0.0.0.0')
        name = _config.get('server_name', 'Web 管理面板')
        display_host = '127.0.0.1' if host in ('0.0.0.0', '::') else host
        url = f'http://{display_host}:{port}'
        source.reply(
            RText(f'{name}：{url}', color=RColor.green)
            .set_styles(RStyle.underlined)
            .c(RClickAction.open_url, url)
        )

    server.register_command(Literal('panel').runs(show_panel_url))
    server.register_help_message('!!panel', '顯示 Web 管理面板網址')


def on_unload(server: PluginServerInterface):
    global _web, _schedules
    if _schedules is not None:
        _schedules.stop()
        _schedules = None
    if _web is not None:
        _web.stop()
        _web = None
    _log('Web panel stopped')
