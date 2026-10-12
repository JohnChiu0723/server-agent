# GRAVITY 遷移檢查點

## 遷移原則
- 每分服僅一隻 .bat：`Start-Main-Server.bat` / `Start-Mirror-Server.bat`
- 無暴力殺進程：關服統一透過 `!!stop` MCDR 指令
- Crash 自動重啟：.bat 內建 `goto` 循環
- 開機自啟：Windows 工作排程器觸發 .bat
- 零冗餘：目錄內不殘留 Streamlit 面板殘骸
- DuckDNS：`server-ip=` 留空

## 完成狀態

| 步驟 | 內容 | 狀態 |
|------|------|------|
| **0** | 最終存檔備份 | ✅ 主服已優雅關閉、世界已存檔 |
| **1** | `docker stop/rm mc-server` | ✅ 已完成 |
| **2** | `docker stop/rm gravity-mirror` | ✅ 已完成 |
| **3** | `Start-Main-Server.bat` | ✅ 已建立於 `minecraft-server/` |
| **4** | `Start-Mirror-Server.bat` | ✅ 已建立於 `minecraft-mirror/` |
| **5** | agent_proxy RCON → 127.0.0.1 | ✅ docker-compose.yml + agent_tools.py 已更新 |
| **6** | 手動啟動確認 MCDR | 🔶 **待使用者操作** |
| **7** | Windows 工作排程器 | 🔶 **待使用者操作** |
| **8** | 清理 Docker/WSL2 殘留 | ✅ 已完成（docker system prune + 歸檔舊腳本） |

## 關鍵設定

### 主服 MCDR (`minecraft-server/config.yml`)
- JDK: `C:/Program Files/Eclipse Adoptium/jdk-21.0.10.7-hotspot/bin/java.exe`
- RAM: `-Xms20G -Xmx20G` (ZGC)
- RCON: `127.0.0.1:25575` / 密碼: `<REDACTED>`
- 工作目錄: `.` (minecraft-server/)

### 鏡像服 MCDR (`minecraft-mirror/config.yml`)
- Java: `java` (需在 PATH 中)
- RAM: `-Xms6G -Xmx6G` (ZGC)
- RCON: `127.0.0.1:25575` / 密碼: `<REDACTED>`
- 工作目錄: `.` (minecraft-mirror/)

### RCON 代理
- `docker-compose.yml` → `AGENT_PROXY_RCON_HOST: 127.0.0.1`
- `agent_tools.py` → fallback 預設 `127.0.0.1`

### DuckDNS
- 兩服 `server.properties` → `server-ip=` (留空)
