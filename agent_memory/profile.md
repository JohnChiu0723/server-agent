# Agent 思考品質約定（Cursor 同款）

- 每次行動前寫完整 Thought：意圖 → 現況 → 計畫 → 風險 → 執行
- 禁止沒查就猜；禁止叫使用者手動操作可自動化的步驟
- 長任務必須寫入 chat_log.md 續跑

# 伺服器架構決策與偏好

## 創造服配置（2026-07-03 建立，2026-07-03 修復）
- **位置**: `minecraft-creative/`（Windows 原生，非 Docker）
- **核心**: Fabric 1.21.4 (Loader 0.18.5)
- **記憶體**: 6G (Xms6G - Xmx6G), ZGC, SoftMaxHeapSize=4G
- **連接埠**: 25567 (遊戲), 25577 (RCON)
- **世界**: 超平坦純虛空 (minecraft:flat + the_void)
- **模式**: 創造模式 (gamemode=creative), PVP 關閉, 和平難度
- **MCDR**: watchdog_threshold=60, cron 每日4am重啟
- **已安裝 MOD**: 39 個（WorldEdit, fabric-carpet, Syncmatica, C2ME, Lithium, FerriteCore, Krypton, alternate-current, spark 等）
- **已安裝插件**: PrimeBackup（已啟用自動排程備份）、creative_auto_restart（排程重啟+崩潰恢復）、auto_op、DayCountNBT、joinMOTDnext、shared_whitelist（standalone 模式）
- **PrimeBackup**: 已啟用，備份目標 `creative_world`，排程每 12h
- **啟動方式**: `Start-Creative-Server.bat` → 使用 `python -m mcdreforged start`

## 主伺服器配置（2025-07-11 更新）
- **位置**: `./minecraft-server`（Windows 原生 MCDR）
- **核心**: Fabric 1.21.4
- **記憶體**: 20G ZGC, CPU P0-P3 獨佔
- **連接埠**: 25565 (遊戲), 25575 (RCON)
- **ServerReplay**: 已安裝 v2.1.0+mc1.21.4，已啟用，設定檔位於 `config/ServerReplay/config.json`
- **ServerReplay 配置重點**: fix_carpet_bot_view_distance=true（因有假人）, ignore_sound/chat=true, optimize_entity=true, max_duration=2h
- **2026-07-21**: EssentialAddons JSON 損毀事件 — subscribedata.json / teamteleportblacklist.json 變 null bytes，手動修復為 `[]`
