"""Mirror server join/leave/death/start/stop notifications via Discord webhook (no bot gateway)."""

from mcdreforged.api.all import *
import json
import os
import re
import threading
import urllib.error
import urllib.request
import uuid

config: dict = {}
_server: PluginServerInterface | None = None

JOIN_RE = re.compile(r'^(.+?) joined the game$')
LEAVE_RE = re.compile(r'^(.+?) left the game$')
DEATH_HINTS = (
    ' was killed',
    ' was slain',
    ' drowned',
    ' burned',
    ' fell',
    ' hit the ground',
    ' blew up',
    ' went off',
    ' starved',
    ' suffocated',
    ' withered',
    ' experienced kinetic energy',
    ' was impaled',
    ' was shot',
    ' was fireballed',
    ' was pricked',
    ' was squashed',
    ' was frozen',
    ' was skewered',
)

DEFAULT_CONFIG = {
    'enabled': True,
    'webhook_url': '',
    'webhook_name': 'GRVY鏡像聊天',
    'webhook_avatar': 'https://cdn.discordapp.com/attachments/1488865939905839230/1495361415685476492/IMG_4740.jpg',
    'player_avatar_url': 'https://starlightskins.lunareclipse.studio/render/pixel/{name}/face?randomuuid={rand}',
    'embed_join_color': 5763719,
    'embed_leave_color': 15548997,
    'embed_start_color': 5763719,
    'embed_stop_color': 15548997,
    'message_start': '**✅ 鏡像伺服器已開啟！**',
    'message_stop': '**❌ 鏡像伺服器已關閉！**',
    'message_join': '📥 **{name}** 加入了鏡像伺服器',
    'message_leave': '📤 **{name}** 離開了鏡像伺服器',
}


def _player_avatar(name: str) -> str:
    template = config.get('player_avatar_url', DEFAULT_CONFIG['player_avatar_url'])
    return template.format(name=name, rand=uuid.uuid4().hex)


def _post(payload: dict) -> None:
    if not config.get('enabled', True):
        return
    url = (os.getenv('DISCORD_WEBHOOK_URL') or str(config.get('webhook_url', ''))).strip()
    if url in ('YOUR_DISCORD_WEBHOOK_URL', 'REDACTED'):
        return
    if not url:
        return
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        f'{url}?wait=false',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'GRAVITY-Mirror-Notify/1.0',
        },
        method='POST',
    )

    def _send() -> None:
        try:
            with urllib.request.urlopen(request, timeout=10):
                pass
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if _server is not None:
                _server.logger.warning(f'[mirror_discord_notify] webhook failed: {exc}')

    threading.Thread(target=_send, daemon=True).start()


def _embed(description: str, color: int, name: str | None = None) -> None:
    embed = {'description': description, 'color': color}
    if name:
        embed['author'] = {'name': name, 'icon_url': _player_avatar(name)}
    _post({
        'username': config.get('webhook_name', DEFAULT_CONFIG['webhook_name']),
        'avatar_url': config.get('webhook_avatar', DEFAULT_CONFIG['webhook_avatar']),
        'embeds': [embed],
    })


def _text(content: str) -> None:
    _post({
        'username': config.get('webhook_name', DEFAULT_CONFIG['webhook_name']),
        'avatar_url': config.get('webhook_avatar', DEFAULT_CONFIG['webhook_avatar']),
        'content': content,
    })


def _notify_start() -> None:
    _embed(
        config.get('message_start', DEFAULT_CONFIG['message_start']),
        int(config.get('embed_start_color', DEFAULT_CONFIG['embed_start_color'])),
    )


def _notify_stop() -> None:
    _embed(
        config.get('message_stop', DEFAULT_CONFIG['message_stop']),
        int(config.get('embed_stop_color', DEFAULT_CONFIG['embed_stop_color'])),
    )


def _notify_join(name: str) -> None:
    text = config.get('message_join', DEFAULT_CONFIG['message_join']).format(name=name)
    _embed(text, int(config.get('embed_join_color', DEFAULT_CONFIG['embed_join_color'])), name)


def _notify_leave(name: str) -> None:
    text = config.get('message_leave', DEFAULT_CONFIG['message_leave']).format(name=name)
    _embed(text, int(config.get('embed_leave_color', DEFAULT_CONFIG['embed_leave_color'])), name)


def _maybe_death(content: str) -> None:
    if not any(hint in content for hint in DEATH_HINTS):
        return
    if JOIN_RE.match(content) or LEAVE_RE.match(content):
        return
    _text(content)


def on_server_startup(server: PluginServerInterface) -> None:
    _notify_start()


def on_server_stop(server: PluginServerInterface, server_return_code: int) -> None:
    _notify_stop()


def on_info(server: PluginServerInterface, info: Info) -> None:
    if not info.content or getattr(info, 'is_user', False) or getattr(info, 'is_player', False):
        return
    content = info.content.strip()
    join = JOIN_RE.match(content)
    if join is not None:
        _notify_join(join.group(1).strip())
        return
    leave = LEAVE_RE.match(content)
    if leave is not None:
        _notify_leave(leave.group(1).strip())
        return
    _maybe_death(content)


def on_load(server: PluginServerInterface, old_module) -> None:
    global config, _server
    _server = server
    config = server.load_config_simple('config.json', DEFAULT_CONFIG.copy())
    if config.get('enabled', True) and str(config.get('webhook_url', '')).strip():
        server.logger.info('[mirror_discord_notify] Discord webhook notifications enabled')
    else:
        server.logger.warning('[mirror_discord_notify] webhook_url not configured, notifications disabled')
