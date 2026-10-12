# GRAVITY 目錄整理完成狀態

## 整理原則
- 每分服僅一隻 .bat：`Start-Main-Server.bat` / `Start-Mirror-Server.bat`
- 所有舊腳本歸檔至 `_archive/`（不刪除，可隨時取回）
- 核心 Minecraft 伺服器檔案（world、mods、plugins、config 等）完全保留

## minecraft-server/ 目錄結構
```
minecraft-server/
├── Start-Main-Server.bat        ← 唯一啟動腳本
├── config.yml                   ← MCDR 設定
├── server.properties            ← MC 設定（server-ip= 留空）
├── world/                       ← 主世界資料
├── mods/                        ← Fabric mods
├── plugins/                     ← MCDR plugins
├── config/                      ← Mod 設定
├── server.jar / fabric-*.jar    ← 伺服器核心
├── libraries/ / versions/       ← Minecraft 依賴庫
├── logs/ / crash-reports/       ← 日誌
├── DiscordIntegration-Data/     ← Discord 整合
├── Geyser_Proxy/                ← 跨平台代理
├── QuickBackupMulti/            ← 快速備份
├── pb_files/                    ← PrimeBackup 資料
├── schematics/ / syncmatics/    ← 結構藍圖
├── mods-disabled/               ← 停用 mods
├── banned-*.json / whitelist.json / ops.json
├── permission.yml / eula.txt
└── _archive/                    ← 所有舊腳本/備份（安全保留）
```

## minecraft-mirror/ 目錄結構
```
minecraft-mirror/
├── Start-Mirror-Server.bat      ← 唯一啟動腳本
├── config.yml                   ← MCDR 設定
├── server.properties            ← MC 設定
├── world/                       ← 鏡像世界資料
├── mods/ / plugins/ / config/   ← mods & 插件
├── server.jar / fabric-*.jar
└── ...同主服結構
```

## 根目錄（Dify/Server-Steward）保留清單
- agent_proxy.py / agent_tools.py（核心服務）
- docker-compose.yml / .env（Dify 基礎）
- 其餘 Dify 工具腳本 → _archive/root-dify-utils/

## 已歸檔項目（均可還原）
| 位置 | 內容 |
|------|------|
| minecraft-server/_archive/old-scripts/ | 33 個舊 .bat + 12 .vbs + 5 .ps1 |
| minecraft-server/_archive/old-docs/ | 3 個 .html + 2 個 .txt |
| minecraft-server/_archive/old-scripts-subdirs/ | scripts/ 目錄（213 個舊管理腳本） |
| minecraft-server/_archive/level2-export/ | 82GB 舊備份（95010 檔案） |
| _archive/root-dify-utils/ | 15 個 Dify 設置工具 |
| _archive_old_scripts/ | 上一輪已歸檔的 server_control.py 等 |
