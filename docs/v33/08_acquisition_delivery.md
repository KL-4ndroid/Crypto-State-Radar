# V3.3 下一階段：有界資料採集與 QA

日期：2026-10-31。這是 PR #20 工程骨架之後的獨立變更；依賴該分支，與 PR #19 demand scheduler 分開。本輪交付可持久恢復的 **本機 SHADOW pilot**，沒有啟動正式五分鐘採集、切換模型或增加雲端服務。

## 已完成

| 項目 | 行為 |
|---|---|
| 九種來源 adapter | OI、Taker、Top Account／Position、Global accounts、Funding settlement、Mark/Index/funding quote、Spot／Perp aggTrades |
| 有界 worker | 明確指定最多 5 個標的，預設 12 次請求／40 秒，單頁最多 500；無自動 HTTP 重試，429／418／451 停止本輪 |
| 原始保存 | 內容 SHA-256、deterministic gzip、原子檔案替換、讀取 checksum；本機預設 4 MB/object、128 MB、10,000 objects，Vercel ephemeral filesystem 拒絕使用 |
| 持久游標 | source/symbol/run-scoped lease、revision、retry/backoff、next_due；manifest＋raw index＋cursor 同交易提交 |
| 資料品質 | wrong symbol、非數值、非有限值、缺間隔、重複／亂序、未封閉區間、trade ID gap、遺失 raw object 都不會靜默略過 |
| 資料修訂 | 同 item/hash 保留最早可得時間；原始 item hash 改變則 append 新 revision，不改舊記錄 |
| 基礎特徵 | OI 數量／名義價值及各自變化、Taker ratio、帳戶／部位比例、結算與報價 Funding、Mark/Index basis、完整五分鐘 CVD quote delta |
| 重複執行 | 未到期不發 HTTP；衍生特徵未變則重用 feature_id，不把重新讀取時間當新鮮度 |

## 資料流與交易邊界

```text
固定官方 endpoint + RequestBudget
 → bounded raw response + received_at
 → content-addressed compressed object
 → time/shape/gap/closed-window QA
 → 同交易 append raw observation/index/manifest + cursor revision
 → checksum-verified causal measurement features
```

外部 I/O 不持有 DB transaction。原始檔案已寫入、DB 尚未成功時保留可重用 object；cursor 不提前移動。失敗採有界 backoff／Retry-After；失效 lease 不得 finish。Source 認證只使用可選 `CSR_BINANCE_DATA_API_KEY`，不接受前端憑證、不傳 query、不記錄 header／provider error message，且禁止 HTTP redirect。

新 schema：`schema/v33_acquisition.sql`。

- `csr_v33_acquisition`：唯一可更新的各 stream cursor／revision／lease／due／failure metadata。
- `csr_v33_acquisition_items`：immutable item revision index，按有效時間與可得時間查詢。
- 沿用 `csr_records` 保存 namespaced raw observations、acquisition manifest 與精簡失敗紀錄；`csr_v33_features` 保存衍生特徵。

PostgreSQL migration 必須先有 `v33_contract.sql`，再顯式套用 acquisition schema；本輪僅在 disposable local PostgreSQL 驗證，沒有變更 Neon。runtime 只需 history SELECT/INSERT 及 stream metadata 必要 columns UPDATE，不需 DDL／DELETE。

## 實測帶來的修正

1. **Taker**：回應可省略 symbol；從明確的單標的 request 綁定，不把缺 symbol 當錯幣。實測還回傳比 startTime 早一格的 row，故允許有界的一格 overlap，再依 row timestamp＋period 封閉時間過濾；既不跳過真正 gap，也不把當前未結束 row 納入。完整原回應仍保留。
2. **快照時鐘**：來源 timestamp 可能略快於本機時鐘。最多等待一秒再允許使用，保留原 received_at；availability_time／ingested_at 記錄實際延後使用時間，絕不回填過去。超過界線維持 ClockSkew quarantine，沒有調整系統時間或假造 source time。
3. **OI lag**：實測最新統計可比預期 cutoff 落後一格；狀態保持 PARTIAL，cursor 只走到真的收到之處，source_time 不刷新。仍依官方文件的 period-end 定義，後續應量測發布延遲與跨來源對齊。
4. **Futures aggTrades**：官方目前限制最近 48 小時；單次 weight=20，Spot weight=4。fromId inclusive，resume 不與 start/end time 混用。公開 Futures market trade feed 不包括 insurance fund／ADL，RPI 包含於 q，不能宣稱是所有市場／所有清算的完整資金流。

來源：[Futures Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)、[Spot API](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md)。Taker overlap／快照微小 clock lead 是本機 pilot 的實測紀錄，不假稱官方已承諾此行為。

## CVD 完整性

每次只處理一個五分鐘窗口的一頁。滿頁且未看到邊界，保持 PARTIAL 並保存 inclusive-next-ID、Decimal 累積 quote delta 與 page hashes；所有頁面齊全、連續且 raw objects 可驗證後才寫入完整 window aggregate。

`m=true` 是買方 maker，計為主動賣出的負 quote delta；反之為正。空的冷啟動回應不是 CVD=0。只輸出 `spot_cvd_5m_quote`／`perp_cvd_5m_quote`，不冒充任意 horizon CVD、資金出入或 Family 分數。REST coverage 標記為 `REST_OBSERVED_CONTIGUOUS`，不是全市場資料完整性的保證；ID gap 是保守隔離條件，並非已證明 provider 損毀。

## 量測與驗收

- 新增 acquisition／CLI 測試 23 項，含 Hypothesis 買賣方向／Decimal 累積 property、分頁、gap、原始檔案遺失、lease、atomic retry、來源修訂、缺 key、時鐘、Taker 真實 shape 與不發 HTTP 的 plan。
- 本機完整 suite：**263 passed，5 opt-in storage skipped**；本 PR 最終 CI 另行核對，PostgreSQL acceptance 獨立執行。
- 真正 PostgreSQL 新增案例 **1 passed，4 deselected**：migration 可重跑、並行 claim、原始 index／manifest／cursor rollback、stale lease、immutable index；鎖住正式 runtime row 仍不阻塞採集。只啟動 PostgreSQL，沒有為本次測試啟動 Redis。
- 一標的實測：第一輪 7 次 HTTP＋修正後窄複驗 2 次，合計 **9 次**；原始物件 9 個、壓縮共 **15,665 bytes**（當時快照）。OIDB／global 比例已保存；Taker／Mark/Index 複驗完成；Funding 該窗口無結算，未填 0。Top Trader 缺 key 明列 UNAVAILABLE；Spot／Perp 各一頁尚未齊全，沒有發布 CVD feature。
- 模型 evaluation=0、signal=0、legacy revision=0；不是 performance backtest 或模型有效性證據。

本機忽略版控證據：

- `artifacts/verification/v33-acquisition-pilot.json`：保留第一輪包含資料 QA 錯誤的原始結果，未覆寫。
- `artifacts/verification/v33-acquisition-followup.json`：窄範圍複驗。
- `artifacts/verification/v33-acquisition-storage.json`：實際 PG 與測試容器清理紀錄。
- `data/v33/acquisition-pilot.sqlite3`、`data/v33/raw-pilot/`：可繼續重播與 checksum 驗證的隔離資料。

request count 不等於 weight 或費用；目前報告 known weight 下界、unknown-weight requests 與 provider 回傳的 IP cumulative used-weight。不是整個帳號成本或跨程序的全域 rate limiter。本機容量檢查亦非多進程的全域配額保證，未具備正式 archive backend。

## 操作方式

```powershell
# 只輸出計畫，不建立資料庫、不呼叫 API。
.venv/Scripts/python.exe tools/v33_acquire.py plan --run-id pilot --symbols BTCUSDT

# 明確執行一次少量採集，沒有常駐排程。
.venv/Scripts/python.exe tools/v33_acquire.py once --run-id pilot --symbols BTCUSDT --sources oi taker premium_mark_index --max-requests 3 --output artifacts/v33/pilot.json

# 只從已保存且可驗證的資料重建特徵，不向 Binance 查詢。
.venv/Scripts/python.exe tools/v33_acquire.py derive --run-id pilot --symbols BTCUSDT

# 只驗證本次新增的 PostgreSQL slice。
.venv/Scripts/python.exe tools/storage_acceptance.py --postgres-only --test-filter v33_acquisition --output artifacts/verification/v33-acquisition-storage.json
```

相同 run 保留原 cursor；--start 只用於新 stream 的 bootstrap。Auth failure 會冷卻一小時；資料保留窗口之外的 gap 需明確修復，不能藉由改 cursor 默默跳過。

## 尚未啟用與下一個階段

1. 選定遠端 raw object backend，完成容量／成本／存取權限／還原驗證；本機 `/tmp` 不可當 serverless durable storage。
2. 將 worker 接回既有 scheduler 與觀測／budget，重用既有 scanner batch payload；完成 24 小時基準之前不改正式 cadence。
3. 持續窗口完整性、晚到／修訂修復、Spot/Futures symbol／單位映射、API key 配置與 Funding 特殊結算處理。新上市 stream 需要已知 bootstrap 起點，不能將上市前缺口默默標為完整；Regular/Special 同時間的模糊 revision 目前拒絕任意挑選。
4. 根據品質與完整事件資料研究 OI deceleration、Efficiency、Spot/Perp CVD divergence；現有量測原語不會自動轉成 StrongFamily 或可執行訊號。

本輪沒有新增自動排程、沒有 production migration／模型切換、沒有 auto-promotion 或交易。依賴 PR #20；合併／部署需與尚在進行的正式基準協調。
