# 階段 checkpoint — 2026-10-30

使用者因額度接近上限，要求本階段收尾。停止新增功能；未部署、未推送、未建立正式資料庫，也未改原有每小時排程。

## 版本與範圍

- 工作分支：`fix/production-feature-enrichment`。
- Production 已透過 Vercel 查證為 `5816d3a3806c2f68c2554f21b407bb620e278b2d`，詳見 baseline 文件。
- 本機新模型版本：`V2.4.0-alpha`，尚未證明預測有效。
- 本 checkpoint 合併了前幾輪尚未提交的實作。它是續接與審查基準，不是七階段全數正式驗收完成的宣告。

## 各階段狀態

| 階段 | 本機已實作及驗證 | 尚未完成的驗收 |
|---|---|---|
| 1 基準 | Production 身分、修改分組、固定七類案例、固定 main 程式與新模型重播 | 真實歷史資料集的完整比較 |
| 2 契約 | 共用純函式、單一模型版本、壓縮方向、缺值規則、funding 樣本與時間條件 | 門檻歷史校準及模型有效性 |
| 3 Lifecycle | 固定 READY anchor/TTL、到期優先、terminal/new cycle、pending metadata、舊 snapshot 相容 | 真實長序列與舊 production 快照的隔離重播 |
| 4 覆蓋 | 多類候選、全體 active pinning、持久 backlog、bounded drain、request budget/metrics、逾時可見 | sin1 全市場容量、延遲 SLO；失敗重試公平性仍應審查 |
| 5 儲存 | SQLite 真交易、revision conflict、immutable record 檢查、signal ID、原子 Redis 發布程式及 outbox 重播 | PostgreSQL 服務/driver 實連、migration/權限、真 Redis Lua/並行測試 |
| 6 研究驗證 | due-job ledger、分批 validation、coverage/quality、現行模型 raw-frame replay、事件一對一配對、selection misses | 自動 worker 正式排程尚未啟用；大資料效能、事件定義與重試策略須審查 |
| 7 Dashboard | 資料新鮮度、expired/pending/partial READY 排除、掃描覆蓋、影子/待同步提示、manual refresh race 修正 | 隔離 Vercel preview、shadow 比較、正式切換與回退演練 |

## 本輪證據

- `python -m pytest -q -p no:cacheprovider`：**65 passed**。
- `tests/browser_acceptance.cjs`：1440x900、390x844；freshness、expired READY、manual refresh race、token 清除及不落地儲存通過。
- `tests/integration_acceptance.cjs`：真瀏覽器 → 真 scan handler → 分類/lifecycle → SQLite transaction → file read view → 真 state API → Dashboard 通過；市場資料為固定 fixture，正式環境零寫入。
- 小型唯讀 Binance 量測：BTC/ETH/SOL 共 11 次 request，0 request failure，本機約 **1.719 秒**。各標的價格變化、volume Z、funding Z、compression 均可取得。這不是 Vercel sin1 的全市場吞吐量驗收。
- `git diff --check` 通過。CRLF 提示不是測試失敗。
- 截圖和較大的執行證據在 `artifacts/verification/`（git ignored，本機保留）。

## 部署前必讀

新 scanner 在 serverless 缺 PostgreSQL 設定時會 fail closed；現在直接部署會影響現有手動掃描。
先建立隔離的 preview DB，設定 `CSR_RESEARCH_DATABASE_URL`、`CSR_RESEARCH_ENVIRONMENT=preview`，以 migration role 明確執行：

```powershell
python -m src.research_worker migrate
```

`schema/runtime_ledger.sql` 為新 ledger 的 migration。既有 `research_schema.sql` 與 SQLite/JSONL adapter 保留給舊研究流程；V2.4 runtime 使用 `research_journal.py`。

Preview 用獨立 state key、獨立 DB、`CSR_SCAN_MODE=shadow`；不得讓 preview 和 production 共用 ledger。
新 `/api/research` 的 POST 需要獨立 `CSR_WORKER_TOKEN`。operations：`drain`、`validate`、`publish`。
`publish` 只接受 live ledger，不能將 shadow ledger 直接發布到正式 key。GET 只提供狀態摘要。
`api/state PUT` 在設定 ledger DB 後停用，避免繞過 revision 管理。

本機 worker：

```powershell
python -m src.research_worker status --root <isolated-journal-directory>
python -m src.research_worker drain --root <isolated-journal-directory>
python -m src.research_worker validate --root <isolated-journal-directory>
python -m src.research_worker publish --root <isolated-journal-directory>
```

`publish` 失敗時以已提交 ledger 重播，不能重算或改寫歷史 signal。
Rollback 必須先阻止舊 scanner 寫入；不能直接讓舊版 OI-only scanner 覆蓋新模型的 READY 狀態。保留 DB 與 Upstash 快照，不 wipe。

## 下次從這裡繼續

1. 先審查本 checkpoint 的整體 diff，尤其 DB/outbox、retry fairness、時間語意、模型版本與 coverage。
2. 在隔離 PostgreSQL/Redis 測試交易、immutable trigger、schema migration、權限及並行發布；SQLite 通過不能替代這項。
3. 補 worker 重試退避/公平性、raw replay 大資料效能與正式自動化啟用條件，再量測 sin1 全市場與 overflow。
4. 用真實完整市場歷史比較事件級結果，包含未被選中標的與不成熟窗口；不要把 synthetic replay 當 accuracy 證據。
5. 完成 preview → shadow → production 的逐關卡驗收後，才提出正式切換。

不要因本機測試已通過，就省略外部驗收；目前沒有正式發布。
