from mcdreforged.api.all import *

WELCOME_BACKUP_COUNT = 10
_patched = False


def _patch(server: PluginServerInterface) -> bool:
    global _patched
    if _patched:
        return True

    try:
        from prime_backup.mcdr.task.general.show_welcome_task import ShowWelcomeTask
    except ImportError:
        return False

    ShowWelcomeTask.BACKUP_NUMBER_TO_SHOW = WELCOME_BACKUP_COUNT
    _patched = True
    server.logger.info(f'PrimeBackup welcome: showing latest {WELCOME_BACKUP_COUNT} backups on !!pb')
    return True


def on_load(server: PluginServerInterface, old_module):
    if _patch(server):
        return

    def retry_patch():
        import time
        for _ in range(20):
            time.sleep(0.5)
            if _patch(server):
                return
        server.logger.warning('PrimeBackup welcome: prime_backup not found, welcome count not patched')

    server.run_task(retry_patch)
