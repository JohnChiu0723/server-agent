# GRAVITY 聊天紀錄

## 2026-07-21 15:39 — 主服 EssentialAddons JSON 損毀修復
- **需求**: 主服啟動 bat 打不開伺服器
- **診斷**: 主服 crash，根因 `EssentialAddons-1.4.5` 解析設定檔失敗：
  `JsonSyntaxException: Expected JsonArray but was JsonPrimitive; at path $`
- **發現**: `config/EssentialAddons/subscribedata.json` 和 `teamteleportblacklist.json` 內容為 null bytes (`\u0000\u0000`)，非有效 JSON
- **動作**: 將兩個檔案內容修復為 `[]`（空陣列）
- **結果**: 檔案已修復，管理員可重新執行啟動 bat
- **待辦**: 無
