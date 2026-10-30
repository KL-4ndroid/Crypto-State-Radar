# 依待辦喚醒：實作與切換界線

本階段沿用 QStash、research journal、validation lease 和 Redis read view，沒有新增服務、付費方案或模型。`CSR_SCHEDULER_MODE` 預設 `periodic`，正式排程與目前的 24 小時基準保持原狀。

## 行為

| 工作 | demand 模式 |
|---|---|
| 全市場掃描 | 每小時 07 分，沿用既有掃描 |
| 續跑 | backlog 或 active lifecycle 有下一次評估時間時才安排；保守保留至少 300 秒批次間隔 |
| 驗證 | 全部 signal / horizon 共用一個最早到期通知，單次仍限 20 個工作；未完成時最早 60 秒後繼續 |
| 補救檢查 | 每小時 02、17、32、47 分，重播發布及修復喚醒，不直接查幣安 |
| 畫面發布 | 相同 revision、相同公開內容且三個 Redis key 完整時不再 SET；缺 key／無法解析的公開內容會重建 |

固定排程由每日 600 次變為 120 次，**另加實際需要的續跑與驗證通知及重試**。這不是總訊息量下降 80% 的承諾；大量驗證工作仍可能達到免費訊息額度，失敗會保留待辦，不會自動升級。驗證密集時也不保證 Neon 可休眠。Binance 的每批歷史資料讀取量本階段沒有改變，不能宣稱已降低其 request weight。

`csr_scheduler_wakes` 每種操作只有一個現行 intent，獨立於模型 revision。先交易寫入 intent，再以穩定 token 發送延遲通知，成功後標記 sent。provider 結果不明時重用 token；通知遺失超過 180 秒寬限、處理 lease 超過 120 秒時由補救檢查重建。舊 token 不會再執行市場工作。同一 token 執行中的重複投遞要求重試，完成後則略過；續跑也沿用原子 operation receipt，validation 沿用工作 lease。

更新 intent 與 scan commit 以相同 runtime row lock 序列化，不在資料庫交易中等待網路。新 signal 讓驗證提前時會取代較晚的 intent。手動掃描仍沿用原有入口；其新增待辦最晚由下一次補救檢查發現，可能等待約 15 分鐘。正式切換前應比較這項延遲是否可接受。

## 部署與切換

1. 本機以 SQLite 重播與真正 PostgreSQL／Redis 驗證；preview 使用現有隔離資料庫與 key。
2. 明確套用 `schema/demand_scheduler.sql`，只授予既有 runtime role 對新表的 SELECT、INSERT、UPDATE，禁止 DELETE／DDL；不更動研究歷史或舊 state。
3. preview 設 `CSR_SCHEDULER_MODE=demand` 並確認 QSTASH_URL／TOKEN、簽章 key、HTTPS origin。受保護 preview 的延遲通知需要額外短效 `CSR_SCHEDULER_PREVIEW_AUTH`；過期會阻擋投遞，不能誤報程式故障或解除保護。
4. `tools/manage_scheduler.py --mode demand` 沿用既有 plan / prepare / activate / pause。新排程使用獨立 `csr-<environment>-demand-v1-*` ID，仍共用原有 parallelism=1 flow key。prepare 後暫停；activate 拒絕與舊模式同時啟用。對正式環境切換前先完成目前基準。
5. 停用舊 drain／validate 及原 hourly 排程，部署 demand 設定後再啟用新 hourly／repair，檢查 token、實際投遞、工作覆蓋及免費訊息配額。不可直接拿原固定 600 時槽的驗收器判定 demand 模式；新模式需依 intent 到期、實際處理與補救延遲驗收。

回退：先暫停 demand 排程，部署 `CSR_SCHEDULER_MODE=periodic` 並恢復原三個排程。已發出的 demand 通知在 periodic 模式被拒絕，避免偷偷執行；可能短暫產生 provider 重試，保留記錄供核對。新表保留，不刪除歷史或回寫較舊快照。

下一階段才處理有上限的 K 線／funding 歷史快取與增量抓取，需先確保封閉 K 線、as_of、一致視窗、資料修訂及缺口補抓；不在本階段用陳舊資料替代 API 請求。

## 本階段驗證紀錄

2026-10-30 已完成可重播測試及四項真正 PostgreSQL／Redis 隔離測試，包含並行 claim、lease 恢復、相同版本不重寫與公開 key 重建。另於獨立臨時 preview schema 驗證實際簽章入口 → 延遲 QStash 投遞 → validation claim／重試 → 待辦清除後停止喚醒；刻意使用不存在的 signal 測試缺資料重試路徑，全程 Binance request=0、model revision=0。測試 schema 已清除，沒有啟用新的正式或 preview 常駐排程。

證據保存在忽略版控的 `artifacts/verification/demand-preview.json` 與 `storage-services.json`。這些證據不代表 demand 已正式切換，也不代表驗證了模型預測力。舊 periodic 的在途 drain／validate 在 demand 模式只確認收件及重整 intent，不會直接執行採集。
