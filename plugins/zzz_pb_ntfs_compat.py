from mcdreforged.api.all import *

_patched = False


def _patch(server: PluginServerInterface) -> bool:
    global _patched
    if _patched:
        return True

    try:
        from prime_backup.action.export_backup_action_directory import ExportBackupToDirectoryAction
    except ImportError:
        return False

    original = ExportBackupToDirectoryAction._ExportBackupToDirectoryAction__set_attrs.__func__

    @classmethod
    def _set_attrs_safe(cls, file, file_path):
        try:
            original(cls, file, file_path)
        except PermissionError:
            pass
        except OSError as error:
            if error.errno == 1:
                pass
            else:
                raise

    ExportBackupToDirectoryAction._ExportBackupToDirectoryAction__set_attrs = _set_attrs_safe
    _patched = True
    server.logger.info('PrimeBackup NTFS compat: ignoring chmod/chown errors during export/restore')
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
        server.logger.warning('PrimeBackup NTFS compat: prime_backup not found, restore may still fail on NTFS')

    server.run_task(retry_patch)
