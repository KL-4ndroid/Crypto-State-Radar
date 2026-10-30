# Checkpoint 4f50dd3 後續驗收 — 2026-10-30

> 歷史驗收紀錄；其中「尚未上線／未建立資料庫」是當時狀態。現況與剩餘工作見 [目前狀態](current-status.md)。

> 下列為建立雲端資源前的 checkpoint 紀錄。後續 Neon preview、pooler 修正、
> Dashboard 共用發布／快取及 sin1 實測進度，見
> [Dashboard 實作與驗收](dashboard-resource-optimization-implementation.md)。production 尚未切換。

本輪完成可在本機執行的剩餘實作與隔離驗收；未推送或部署，未建立雲端資料庫，未切換 production。
歷史 checkpoint 的狀態保留作為當時紀錄，以本文件補充目前進度。

## 已修正

- Scan backlog：失敗退避約 60 秒起、最多約 1 小時，加上可重播的固定 jitter；不丟棄 pinned 標的，不讓失敗項目佔住每個批次。
- 新鮮的 active setup 不因 idle drain 重複抓取；到期再入列。時間排序統一按 UTC 絕對時間，cooldown 中的 pending 數不會錯報為零。
- Validation：原子 claim、120 秒 lease、過期回收、拒絕舊 worker 回寫；成功結果 first-writer-wins，重複處理不再錯報為新 validation。
- 持久化：可重跑的 lease/環境 migration、SQLite 與 PostgreSQL immutable record 防護、環境身分綁定、ledger 身分與 Redis revision 原子發布。Runtime 只能更新 revision/payload，不能改 ledger/environment 身分。
- Shadow/preview：preview 不可使用正式預設 state key；runtime DB 身分必須符合配置，不能僅改環境變數就把 production DB 當 preview。Outbox 發布不再先依賴 Redis GET 成功。
- Lifecycle：WATCH 後重新出現的警示屬新 setup；legacy READY 缺 anchor/expiry 不會自動當成有效或成功評估。這些語意以 `V2.4.1-alpha` 區分，門檻未校準。
- Offline replay：UTC 排序、重複/未來 snapshot 拒絕、模型與實際被選取系統各自維護 lifecycle；後續才選中的標的不會被錯算成永遠漏失。
- Event detection：移除每個時間點複製整個後續資料的二次方處理，保留 close-to-close 事件定義與已知 crossing 的回歸案例；配對按 symbol/direction 分組。
- History capture：OI 以有限時間頁分頁，避免 API 回傳 broad range 的最後 500 筆而默默丟失早期 context。驗收要求全部 frame 可評估，不能讓全部 missing-data 也算成功。
- Worker：五分鐘 opt-in tick 分別呼叫 drain / validate / live publish，每個 HTTP operation 有獨立執行預算。既有每小時全市場 workflow 不變。GitHub enable flag 實查尚未設定，未啟用排程。

## 驗收範圍與數據

1. 最終一般 Python suite：**78 passed, 3 skipped**；另以 storage runner 實際執行該三項服務測試：**3 passed**。Desktop/mobile browser acceptance 與真 API→SQLite→快照→Dashboard integration 也通過。隔離容器已移除。
2. 真 PostgreSQL **17.11**、Redis **7.4.12**，driver psycopg **3.3.6**；使用固定 image digest、隨機隔離 schema、最小 runtime 權限，以及 localhost REST bridge 執行實際 Redis Lua。包含真 run_cycle → PostgreSQL transaction → Redis read view。這不是 Upstash 託管服務或雲端網路的驗收。
3. 限定 BTC/ETH/SOL 真實歷史 cohort：147 個 hourly frame 全部具備必要 context，18 次 API request、0 request failure。資料與重播保存在 `artifacts/verification/historical-cohort/`。無目標事件、無 warning，因此 precision/recall 為 null，不能宣稱 accuracy 有效；也不是完整 Binance universe 的歷史回測。
4. 本機真實全市場影子量測：522 個成功 observations、25 個深度評估、575 requests、約 12.796 秒；497 個留在待處理 queue。零 production 寫入。
5. 100 個標的續跑：302 requests（另一次 universe seed request）、100 個深度評估成功、約 7.172 秒。據此設本機待 preview 驗證的 continuation capacity=100，並保留上游 40 秒 deadline；不是 sin1 SLO 保證。
6. 86,400 筆合成行情、10 symbols 各 30 天：83,520 個成熟窗口，開啟 tracemalloc 約 9.699 秒、額外峰值 26.54 MiB。僅證明這個規模的事件計算可執行，不是完整六個月全市場 pipeline 的效能證據。
7. 真瀏覽器 desktop/mobile、refresh race、token clearing，及真本機 API→SQLite→快照→Dashboard 回歸驗收。

## 重跑方式

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe tools/storage_acceptance.py
.\.venv\Scripts\python.exe -m src.replay_model --dataset artifacts/verification/historical-cohort/cohort.json --output artifacts/verification/historical-cohort/replay-current.json
.\.venv\Scripts\python.exe -m src.release_readiness --stage preview
```

Storage runner 要求 Docker Desktop 已啟動；它只建立/移除帶本次識別碼的 localhost 容器。
未啟用 storage fixture 的一般 pytest 會 skip 三個外部服務測試；必須另外跑 runner，不能將 skip 當作通過。
`release_readiness` 不會 migration、發布或印出密鑰；本機沒有 preview 配置時，應以非零碼結束並明確列出缺項。

## 剩餘外部關卡：不得視為完成

1. 建立獨立雲端 preview DB 和 runtime role，設定 preview Vercel 環境與隔離 state key；由 migration role 執行 migration 並綁定 `CSR_RESEARCH_ENVIRONMENT=preview`。
2. Runtime role：`SELECT,INSERT` on csr_records；`SELECT` and `UPDATE(revision,payload)` on csr_runtime；`SELECT,INSERT,UPDATE` on csr_validation_jobs；schema USAGE，無 CREATE、record UPDATE/DELETE 或 ledger/environment UPDATE。用 `release_readiness --connections` 驗證。
3. Vercel sin1 實際完整 request、上游限制、資料庫延遲、overflow 與 worker cadence 驗收；不要以本機速度保證雲端時限。
4. 先隔離 live preview 驗證 UI，再在隔離 shadow ledger 比較輸出；不得共用 production DB/key，亦不得直接將 shadow ledger 發布到正式 key。
5. 完整且具代表性的歷史事件資料、holdout 與足夠的 forward horizons 成熟後，才評估/校準門檻。少量無事件 cohort 不能關閉此關卡。
6. 經具體發布審查後再切換 production；先保留 DB 與 read view 備份、停止舊 scanner 寫入，再演練 read-only rollback。尚未進行正式切換或回退演練。

本輪沒有宣告七階段的 production Definition of Done 全數完成。
