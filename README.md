# Minecraft 生電伺服器自動化維運工具

**Minecraft Server Operation & Diagnostics Tool with LLM Agent**

這是我為自己和朋友的 Minecraft 生電伺服器寫的維運工具。伺服器當機時，讓 LLM Agent 協助讀 Log、判斷原因，並透過 API 查詢狀態或下控制台指令。

**使用技術**：Python、FastAPI、Minecraft RCON、Docker、PowerShell、Dify、DeepSeek API

---

## 為什麼做這個

我平常和朋友玩生電，伺服器上有很多紅石機關，常常因為超載而崩潰或卡死。實際維護時遇到這幾個問題：

1. **當機後要手動翻 Log。** 每次都要在幾千行英文 Log 裡找報錯，很花時間。直接強制重啟又容易讓地圖存檔損壞。
2. **通用 AI 不懂這些冷門模組。** 像 Carpet、MCDR 這類工具，直接問 AI 常常會憑空編出不存在的指令。
3. **程式會卡在原地等結果。** 我一開始寫的程式呼叫系統指令時會停下來等回應，結果整個 API 都卡死，沒辦法接收其他指令。

---

## 我怎麼解決

### 1. 生電知識庫檢索（Parent-Child Chunking RAG）

我把 Fabric Carpet 和 MCDReforged 的官方手冊整理成知識庫，讓 AI 在下判斷之前，先查官方的設定規範（例如 `optimizedTNT` 這類參數）。

這樣做的目的是減少 AI 亂猜指令。在熱修改伺服器設定時，用錯指令可能影響存檔，所以先查規範再動手比較安全。

知識庫的素材放在 [`knowledge_base/`](knowledge_base/)，由 Dify 負責檢索。

### 2. 兩階段日誌分析（Dual-Stage Reasoning）

把幾千行崩潰日誌整份丟給大模型，又貴又慢。所以我拆成兩步：

1. 先用便宜、快的輕量模型 `deepseek-chat`，從日誌裡篩出報錯的關鍵片段。
2. 再把篩選後的內容交給推理模型 `deepseek-reasoner`，分析當機原因。

這樣大約節省 70% 的 Token 費用與等待時間。

這條流程是一個 Dify Workflow，定義檔在 [`dify-workflow/workflow/gravity_dual_model_pipeline.yml`](dify-workflow/workflow/gravity_dual_model_pipeline.yml)，提示詞放在 [`dify-workflow/prompts/`](dify-workflow/prompts/)。

### 3. 指令封裝與連線防護

我用 FastAPI 把伺服器狀態讀取、日誌提取、控制台指令（RCON / PowerShell）包成標準 API，讓 Dify 的 Agent 可以呼叫。

針對「程式死等結果」的問題，我加了連線中斷防護：遇到斷線就立即停止重試並回報錯誤，不再無限等待。阻塞式的工具呼叫也放進獨立的執行緒池執行，不佔住 API 的事件迴圈。

---

## 專案結構

```
.
├── agent_proxy.py          # FastAPI 入口，提供 Dify Agent 呼叫的工具 API
├── agent_tools.py          # 工具實作（檔案、RCON、PowerShell 等）
├── agent_infra.py          # 基礎設施故障的例外型別（供熔斷使用）
├── server_control.py       # RCON 連線、日誌讀取、server.properties 讀寫
├── manage.py               # 管理指令入口（sync / install-autostart / verify-boot ...）
├── core/                   # Dify 設定、Workflow DSL 產生、開機自啟
├── dify-workflow/          # 兩階段分析 Workflow、提示詞、JSON 清洗節點
├── knowledge_base/         # RAG 知識庫素材
├── plugins/                # MCDReforged 插件（網頁面板、白名單同步、Discord 通知等）
├── docker/ , docker-compose.yml
├── scripts/                # PowerShell 啟動與排程腳本
└── .env.example            # 環境變數範本
```

---

## 如何執行

需求：Windows、Docker Desktop、Python 3、PowerShell。

```powershell
# 1. 複製環境變數範本，填入自己的密碼與金鑰
Copy-Item .env.example .env
Copy-Item .env.dify.example .env.dify

# 2. 啟動 Dify 與 agent_proxy
docker compose up -d

# 3. 同步 Dify 的 Agent、工具與 Workflow 設定
python manage.py sync
```

DeepSeek 的 API Key 是在 Dify 的模型供應商設定裡填寫，不放在這個專案的檔案中。

RCON 的主機、連接埠與密碼透過 `.env` 設定（見 `.env.example` 內的 `RCON_*` 變數）。

---

## 注意事項

- 專案是針對我自己的環境（Windows 主機 + Docker + MCDReforged）寫的，其他環境可能需要自己調整路徑與設定。
- `minecraft-*/`（伺服器本體與世界檔）、`dify-data/` 和所有 `.env` 都不在版本控制內。
- 設定檔裡的 `YOUR_RCON_PASSWORD`、`YOUR_PANEL_PASSWORD`、`YOUR_DISCORD_WEBHOOK_URL` 都是佔位符，要換成自己的值。
