import shutil
import os
import json
import sys
import time
import datetime
import subprocess
# MCDR Command & Class
from mcdreforged.api.decorator import new_thread
from mcdreforged.api.command import Literal, Text, SimpleCommandBuilder, Requirements
from mcdreforged.api.rcon import RconConnection
from mcdreforged.mcdr_server import ServerInterface
# Initalize Start
platform = sys.platform
Interface = None

if sys.platform == 'win32':
    MCDR_Command = 'python -m mcdreforged'
else:
    MCDR_Command = 'python3 -m mcdreforged'

PLUGIN_METADATA = {
    'id': 'mirror_server_reforged',
    'version': '1.0.8-alpha-gravity1',
    'name': 'MirrorServerReforged',
    'description': 'A reforged version of [MCDR-Mirror-Server](https://github.com/GamerNoTitle/MCDR-Mirror-Server), which is a plugin for MCDR-Reforged 2.6.0+.',
    'author': 'GamerNoTitle',
    'link': 'https://github.com/EMUnion/MirrorServerReforged',
    'dependencies': {
        'mcdreforged': '>=2.6.0'
    }
}

config = {
    'world': ['world'],
    'command': MCDR_Command,
    'rcon': {
        'enable': False,
        'host': 'localhost',
        'port': 25575,
        'password': ''
    },
    'source': './server',
    'target': './Mirror/server',
    'permission_level': 0
}

help_msg = '''{:=^50}
§b!!msr help §r- §6显示帮助信息
§b!!msr sync §r- §6同步服务器地图至镜像
§b!!msr reload §r- §6重载配置文件
§b!!msr start §r- §6启动镜像服务器
§b!!msr stop §r- §6关闭镜像服务器（需要开启Rcon）
§b!!msr init §r- §6初始化镜像服务器（仅MCDR类服务器可用）
{:=^50}'''.format(' §b[MirrorServerReforged] 帮助信息 §r', ' §b[MirrorServerReforged] Version: {} §r'.format(PLUGIN_METADATA['version']))
# §b!!msr status §r- §6查看镜像服务器状态


# Started = False  # Mirror server status
MCDR = False    # MCDR mode controller
path = os.getcwd()
syncFlag = False
# Initalize End


def InitalizeOnFirstRun():
    if os.path.exists('./Mirror/MCDReforged.py') or 'mcdreforged' in config['command']:
        global MCDR
        MCDR = True     # Turn on MCDR mode
    if not os.path.exists('./Mirror'):
        print('[INFO] [MirrorServerReforged] First run detected, initializing')
        print('[INFO] [MirrorServerReforged] Creating mirror directory')
        if MCDR:    # MCDR mode on, create Mirror folder and a server folder in Mirror folder
            print('[INFO] [MirrorServerReforged] MCDR detected, using MCDR directory layout')
            try:
                os.makedirs('./Mirror')
            except:
                print('[WARN] [MirrorServerReforged] Mirror directory already exists')
            os.makedirs('./Mirror/server')
            os.chdir('Mirror')
            # Create MCDR dictionary structure
            os.system('python3 -m mcdreforged init')
            os.makedirs('./server/world')
            os.chdir(path)
        else:   # MCDR mode off, turn into legacy mode. Like Vanilla, Bukkit, Waterfalls and so on.
            print('[INFO] [MirrorServerReforged] MCDR not detected, using vanilla directory layout')
            try:
                os.makedirs('./Mirror')
            except:
                print('[WARN] [MirrorServerReforged] Mirror directory already exists')
            for world in config['world']:
                os.makedirs('./Mirror/{}'.format(world))
        print('[INFO] [MirrorServerReforged] Initialization complete')


def CreateConfig():
    print('[INFO] [MirrorServerReforged] Creating config file')
    global config
    with open('./config/MirrorServerReforged.json', 'w', encoding="utf-8") as f:
        f.write(json.dumps(config, indent=2, separators=(
            ',', ':'), ensure_ascii=False))
        f.close()


def RconInit(host, port, password):
    Rcon = RconConnection(host, port, password)
    return Rcon


def LoadConfig():
    print('[INFO] [MirrorServerReforged] Loading config file')
    global config
    with open('./config/MirrorServerReforged.json', 'r', encoding="utf-8") as f:
        config = json.load(f)
    _env_pw = os.getenv('RCON_PASSWORD_MIRROR')
    if _env_pw:
        config.setdefault('rcon', {})['password'] = _env_pw
    if 'source' not in config:
        config['source'] = './server'
    if 'target' not in config:
        config['target'] = './Mirror/server'
    if 'permission_level' not in config:
        config['permission_level'] = 0
    CreateConfig()


@new_thread('MSR-Sync')
def ServerSync(InterFace):
    global syncFlag
    syncFlag = True
    start_time = datetime.datetime.now()
    ignore = shutil.ignore_patterns('session.lock')
    for world in config['world']:
        if os.path.exists(f'{config["target"]}/{world}'):
            shutil.rmtree(f'{config["target"]}/{world}/')
        if sys.platform == 'win32':
            shutil.copytree(f'{config["source"]}/{world}', f'{config["target"]}/{world}', ignore=ignore)
            # os.mkdir(f'{config["target"]}/{world}')
            # with open('./exclude.txt', 'wt') as f:
            #     f.write('session.lock')
            # os.system(f'xcopy "{config["source"]}/{world}" "{config["target"]}/{world}" /e /h /k /y /exclude:{config["source"]}/*/session.lock /exclude:{config["source"]}/*/level.*')
        else:
            os.system(f'cp -r {config["source"]}/{world} {config["target"]}/{world}')
    end_time = datetime.datetime.now()
    InterFace.execute(
        f'say §b[MirrorServerReforged] §6同步完成！用时{end_time-start_time}')
    syncFlag = False


def Sync():
    InterFace = GetInterFace()
    if syncFlag:
        InterFace.execute(
            'say §b[MirrorServerReforged] §d服务器正在进行同步，请不要重复提交同步任务！')
    else:
        InterFace.execute('say §b[MirrorServerReforged] §6正在同步服务器地图……')
        InterFace.execute('save-off')
        InterFace.execute('save-all')
        ServerSync(InterFace)
        InterFace.execute('save-on')


@new_thread('MSR-Start')
def CommandExecute(InterFace):
    try:
        global MirrorProcess
        if platform == 'win32':
            MirrorProcess = subprocess.Popen(config['command'], creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            MirrorProcess = subprocess.Popen(config['command'], shell=True)
    except Exception as e:
        InterFace.execute(f'say §b[MirrorServerReforged] §6启动失败！原因为：{e}')
    os.chdir(path)

@new_thread('MSR-Main')
def ServerStart(InterFace):
    # global Started
    try:
        os.chdir('Mirror')
        CommandExecute(InterFace)
        time.sleep(5)
        os.chdir(path)
    except Exception as e:
        InterFace.execute(
            'say §b[MirrorServerReforged] §6启动失败！原因为：{}'.format(e))


def Start(server):
    # global Started
    InterFace = GetInterFace()
    # if Started:
    #     server.reply('§b[MirrorServerReforged] §6镜像服正在运行……')
    # else:
    if syncFlag:
        InterFace.execute('say §b[MirrorServerReforged] §d§l镜像服正在进行同步，请在同步完成后再启动镜像服！')
    else:
        InterFace.execute('say §b[MirrorServerReforged] §6正在启动镜像服，这可能需要一定的时间……')
        InterFace.execute(
            'say §b[MirrorServerReforged] §6启动完成后，请自行利用BungeeCord的转服或者直连进行转服！')
        # Started = True
        ServerStart(InterFace)


def GetInterFace(*args):
    global Interface
    InterFace = ServerInterface.get_instance().as_plugin_server_interface()
    return InterFace

# def Status(server):
#     if Started:
#         server.reply('§b[MirrorServerReforged] §6镜像服正在运行……')
#     else:
#         server.reply('§b[MirrorServerReforged] §6镜像服未运行……')


def Stop(server):
    # global Started
    # if Started:
    if config['rcon']['enable']:
        conn = RconInit(config['rcon']['host'], config['rcon']
                        ['port'], config['rcon']['password'])
        try:
            connected = conn.connect()
            if connected:
                conn.send_command('stop', max_retry_time=3)
                conn.disconnect()
        except Exception as e:
            server.reply('§b[MirrorServerReforged] §6无法停止镜像服！原因为：{}'.format(e))
    else:
        server.reply('§b[MirrorServerReforged] §6无法通过Rcon停止镜像服，因为Rcon未开启！')
    # else:
    #     server.reply('§b[MirrorServerReforged] §6镜像服未运行！')


def Reload(server):
    server.reply('§b[MirrorServerReforged] §6正在重载配置文件……')
    ConfigToDo()
    server.reply('§b[MirrorServerReforged] §6重载完成！')


def DisplayHelp(server):
    for line in help_msg.splitlines():
        server.reply(line)


@new_thread('MSR-Init')
def MCDRInitalize(server):
    if MCDR:
        try:
            os.chdir('Mirror')
            system = sys.platform
            if system == 'win32':
                # Windows NT Platform
                os.system('python -m mcdreforged init')
            else:
                # Linux/Unix Platform
                os.system('python3 -m mcdreforged init')
            server.reply('§b[MirrorServerReforged] §6初始化已完成！')
        except Exception as e:
            server.reply('§b[MirrorServerReforged] §6初始化失败！原因为：{}'.format(e))
        os.chdir(path)
    else:
        server.reply('§b[MirrorServerReforged] §6非MCDR类服务器，无需初始化！')


def Initalize(server):
    server.reply('§b[MirrorServerReforged] §6已启动初始化进程！')
    MCDRInitalize(server)


def ConfigToDo():
    if os.path.exists('./config/MirrorServerReforged.json'):
        LoadConfig()
    else:
        CreateConfig()


def _command_permission_level() -> int:
    try:
        return max(0, min(4, int(config.get('permission_level', 0))))
    except (TypeError, ValueError):
        return 0


def on_load(server, prev):
    ConfigToDo()    # Load Config
    InitalizeOnFirstRun()   # Initalize if this is the first run
    permission_level = _command_permission_level()
    msr_root = Literal('!!msr').requires(
        Requirements.has_permission(permission_level)
    ).runs(DisplayHelp)
    server.register_help_message('!!msr', 'MirrorServerReforged 帮助', permission=permission_level)
    server.register_command(msr_root
                            .then(Literal('help').runs(DisplayHelp))
                            .then(Literal('sync').runs(Sync))
                            .then(Literal('reload').runs(Reload))
                            .then(Literal('start').runs(Start))
                            .then(Literal('stop').runs(Stop))
                            .then(Literal('init').runs(Initalize))
                            # .then(Literal('status').runs(Status))
                            )
    # register stop confirm command (TOO LAZY TO REBUILD THE PREVIOUS COMMAND)
    # builder.command('!!msr help', DisplayHelp)
    # builder.command('!!msr sync', Sync)
    # builder.command('!!msr reload', Reload)
    # builder.command('!!msr start', Start)
    # builder.command('!!msr stop', Stop)
    # builder.command('!!msr init', Initalize)
    # builder.register(server)
