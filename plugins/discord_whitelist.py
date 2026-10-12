"""
配合 Discord /whitelistlist：讀取 whitelist.json 並用 discord rawmsg 發送格式化列表。
"""

from mcdreforged.api.all import *
import json
from pathlib import Path

_server: PluginServerInterface | None = None
config: dict = {}
TRIGGER = 'WLIST_REQ'


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _load_names() -> list[str]:
    path = _resolve(config.get('whitelist_file', './server/whitelist.json'))
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
    names.sort(key=str.lower)
    return names


def _build_message(names: list[str]) -> str:
    title = config.get('title', 'GRAVITY 白名單')
    if not names:
        return f'**{title}**\n目前沒有任何玩家。'
    body = '、'.join(f'`{name}`' for name in names)
    return f'**{title}**（共 {len(names)} 人）\n{body}'


def _send_to_discord(server: PluginServerInterface, text: str) -> None:
    payload = json.dumps({'content': text}, ensure_ascii=False)
    server.execute(f'discord rawmsg {payload}')


def on_info(server: PluginServerInterface, info: Info) -> None:
    if not info.content or TRIGGER not in info.content:
        return
    if getattr(info, 'is_user', False) or getattr(info, 'is_player', False):
        return
    names = _load_names()
    message = _build_message(names)
    try:
        _send_to_discord(server, message)
        server.logger.info(f'[discord_whitelist] Whitelist list sent ({len(names)} players)')
    except Exception as error:
        server.logger.error(f'[discord_whitelist] Failed to send whitelist list: {error}')


def on_load(server: PluginServerInterface, prev_module) -> None:
    global _server, config
    _server = server
    config = server.load_config_simple('config.json', {
        'whitelist_file': './server/whitelist.json',
        'title': 'GRAVITY 白名單',
    })
    server.logger.info('[discord_whitelist] Whitelist list helper enabled (/whitelistlist)')
