# 排程與前端請求修正紀錄 — 2026-10-30

## 已確認

- Actions 權限、default branch main、workflow active 與 worker enable flag 正常。
- 原有 hourly 及 worker 的手動執行成功，卻未持續出現 schedule 事件。
- 重新停用／啟用兩個 workflow，以重新註冊既有排程；不是排程成功證據。
- 原生 GitHub schedule 是 best effort，可能延遲／丟棄；沒有 schedule run 不能歸因為 Binance 或 API 內部錯誤。
- Vercel Hobby cron 只支援每日頻率，不能直接取代本專案 hourly／五分鐘需求。
- 單頁快速恢復前景四次，可以重現額外四次 manifest 請求。這證明一個成因，但不能倒推所有歷史 log 都由此產生。

## 修正

- 最近五秒有成功檢查時，快速切回前景或 bfcache 恢復會重用該結果；較久未檢查、失敗或離線後需重試時仍重新檢查。手動刷新與掃描後同步不受此短期合併限制。
- `?radarDebug=1` 開啟純記憶體診斷，開發者可在 console 執行 `getRadarDiagnostics()` 查看 initial／poll／retry／foreground／after-scan 等來源及合併事件。最多 80 筆，不讀 token，不記錄回應資料，不寫 browser storage，不增加 API。
- Full scan 成功完成流程後單獨保存 `last_full_scan`；drain 保留它，不再以續跑時間取代全市場掃描時間。
- `/api/research` 額外回傳 `last_full_scan_at` 和 `full_scan_status`。舊 ledger 無記錄時為 unknown，不虛構歷史；超過 75 分鐘為 overdue，零觀察或部分採集失敗另標 failed／partial。
- 保護 API 每次操作輸出一筆 `radar_operation` JSON：operation、完成時間、耗時、HTTP 狀態、revision、覆蓋／待處理／驗證數、發布結果、觸發來源及 GitHub run ID。不輸出 headers、token、DSN、完整行情或例外訊息。
- Workflow 傳送固定類別的觸發來源及 run ID。Worker 排程改在每小時 03、08、13…58 分，避開整點與 full scan 的 07 分；頻率仍為五分鐘。這是降低碰撞及重新註冊的措施，不是平台可靠性保證。

## 驗收與界線

- Python 回歸包含：drain 不覆蓋 full scan、fresh drain 不掩蓋 overdue full scan、未知／失敗狀態、結構化 log 不洩漏敏感值。
- 沿用瀏覽器驗收：四次快速前景切換零額外請求、長時間背景／離線恢復立即檢查、手動掃描刷新、重試、分頁接管與記憶體診斷。
- 手動 dispatch 只驗證 API 鏈路；必須分別看到 `event=schedule`、成功回應、資料時間／revision 前進及 pending 處理，才可關閉自動排程關卡。
- 若重新註冊仍未產生自動事件，應使用獨立排程來源或向 GitHub 查詢，不以無限制重試或瀏覽器輪詢維持系統。新增外部帳號／法律條款或付費方案需另確認。
- 獨立排程可直接沿用現有授權 API：hourly `GET /api/scan`；五分鐘 `POST /api/research?operation=drain`、`validate`，並分開呼叫 publish 重試。需分開執行，不把三個上游工作塞入同一個 60 秒函式。權杖放服務的私密 header 設定，不能放 URL。

未修改模型、門檻、研究契約或擴充付費資源；快取 TTL／依賴套件縮減另行處理。
