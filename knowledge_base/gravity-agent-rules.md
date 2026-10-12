# GRAVITY Agent 操作規則（知識庫核心文件）
# ==========================================
# 上傳到 Dify 知識庫時，使用「高品質索引」模式
# 這是 Agent 的「憲法」— 每一條都是強制性規則

## 絕對禁止的操作

1. **禁止修改受保護檔案**：eula.txt、banned-players.json、banned-ips.json、ops.json、whitelist.json 不可被修改
2. **禁止修改 MOTD 或聊天格式**：server.properties 中的 motd= 行、StyledChat/StyledNicknames 配置不可動
3. **禁止關閉 sync-chunk-writes**：必須保持 true，否則地圖可能損毀
4. **禁止修改 randomTickSpeed**：必須保持原版值 3，否則破壞所有紅石機械時序
5. **禁止變更難度或遊戲規則**：除非用戶明確要求
6. **禁止修改 RCON 密碼或 JMX 設定**
7. **禁止停用任何 Carpet 規則**：除非用戶明確要求
8. **禁止增加或減少模擬距離到超出 6-10 範圍**：低於 6 會讓生電農場失效，高於 10 會嚴重影響效能
9. **禁止修改 max-tick-time**：目前是 -1（生電需求），不可變更

## 修改檔案的正確流程

每次修改檔案必須遵循以下流程：

Step 1: 讀取現狀
- 用 readFile 讀取目標檔案的目前內容
- 理解每一行的作用
- 確認要修改的參數目前的值

Step 2: 檢查相依性
- 用 searchFiles 搜尋相關參數在其他設定檔中的引用
- 例如：改 view-distance 前，檢查 Carpet 設定中是否有相關限制
- 改 Lithium 設定前，檢查是否與其他優化模組衝突

Step 3: 提案並確認（關鍵步驟！）
- 向用戶說明：要改什麼、為什麼改、改了會有什麼影響
- 如果修改涉及 TPS/效能，引用知識庫中的最佳實踐作為依據
- 等待用戶確認後才執行

Step 4: 精確修改
- 使用 patchFile（而非 writeFile）進行最小化修改
- 只改目標行，不改任何其他內容
- 保留原有的註釋和格式

Step 5: 驗證結果
- 用 readFile 重新讀取，確認只有目標行被修改
- 用 diffFile 查看 diff
- 告知用戶備份路徑，以便必要時恢復

## TMC 生電伺服器最佳實踐

### 效能模組配置參考
- Lithium (lithium.properties)：所有優化預設開啟，無需手動調整
- FerriteCore (ferritecore.mixin.properties)：記憶體優化，預設全部啟用
- Krypton：網路層優化，自動生效無需配置
- C2ME (c2me.toml)：並發區塊管理，使用預設配置即可
- ModernFix (modernfix-mixins.properties)：啟動與記憶體優化，預設配置

### 效能瓶頸診斷流程
1. 檢查 Spark profiler 報告（/spark profiler --timeout 300）
2. 常見瓶頸排序：
   a. 過多實體（entity tick 佔 >30%）→ 檢查生電農場是否堆積過多生物
   b. 漏斗運算（hopper 佔 >20%）→ 檢查是否有未鎖定的漏斗陣列
   c. 紅石更新（redstone 佔 >15%）→ 檢查是否有高頻紅石時鐘
   d. 區塊生成（chunk generation 佔 >25%）→ 降低 view-distance 或暫停玩家探索
3. 看 MSPT 趨勢：>40ms 需要注意，>50ms 有 TPS 下降風險

### view-distance / simulation-distance 調整指引
- 目前值：view-distance=10, simulation-distance=8
- 安全範圍：view-distance 8-12, simulation-distance 6-10
- 降低 view-distance 可減少區塊發送量，對網路頻寬有幫助
- 降低 simulation-distance 直接減少實體處理範圍，對 CPU 最有幫助
- 修改後需要重啟伺服器才生效

### 記憶體相關
- 目前配置：-Xms20G -Xmx20G, ZGC + ZGenerational
- ZGC 優點：超低停頓（<1ms），適合遊戲伺服器
- 正常記憶體使用：15-18GB（剩餘 2-5GB 給 OS 緩衝）
- 如果頻繁 GC 或記憶體超過 19GB：
  1. 檢查是否有 memory leak（mod 衝突）
  2. 考慮增加 -Xmx 到 24G（如果硬體允許）
  3. 用 /spark health 檢查 heap 使用情況

### 模組相容性注意事項
- Carpet + Lithium + C2ME 組合已知穩定
- ViaVersion + ViaBackwards 組合用於跨版本支援
- Floodgate + Geyser 組合用於 Bedrock 支援
- 不要同時安裝多個功能重疊的效能模組（例如不要同時裝 Lithium 和 Phosphor）

## 玩家管理

### 白名單操作
- 加入：whitelist add <玩家名>
- 移除：whitelist remove <玩家名>
- 查詢：whitelist list
- 重載：whitelist reload
- 注意：玩家名區分大小寫！必須使用精確的 Minecraft 玩家名
- 白名單會自動同步到鏡像服（shared_whitelist 插件）

### 管理指令
- 查看線上玩家：list
- 廣播訊息：say <訊息>
- 踢出玩家：目前不在白名單中，需管理員手動操作
- 備份觸發：!!pb make
- 難度設定：difficulty hard/normal/easy/peaceful

## 備份與恢復

### Agent 備份機制
- 每次修改檔案前，bridge 服務會自動建立 .bak-<時間戳> 備份
- 備份位置與原檔案相同目錄
- 可用 /api/backup/list 查看所有備份
- 恢復：手動將 .bak-* 檔案重新命名覆蓋原檔案

### PrimeBackup 遊戲備份
- !!pb make：立即建立備份
- !!pb list：列出備份
- 備份包含整個世界存檔和設定檔

## Pterodactyl 相關

- Panel 網址：http://127.0.0.1:8090
- 不要修改 Panel 的 nginx 配置
- 不要修改 Wings 配置
- Panel 狀態可透過 /api/status 檢查 port 8090

## superwy Bot 相關

- Web 界面：http://localhost:3000
- config.js 中的坐标設定請勿修改（與實際倉庫對應）
- !!kd 指令的語義解析依賴 LLM API（dashscope），如需更換需同步修改 .env
- Bot 斷線會自動重連（10 秒後）

## 安全提醒

- 每次修改後，告知用戶備份路徑
- 如果修改後出現問題，優先建議恢復備份
- 同一時間只改一個檔案，避免連鎖問題難以排查
- 修改 server.properties 或 config.yml 後，通常需要重啟 MCDR 才能生效
- 重大變更前，建議先用 !!pb make 建立完整備份
