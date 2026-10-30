# Upstash 獨立排程

沿用既有 Vercel / Upstash 安裝，Redis 繼續儲存 current snapshot；新增同帳號 Free QStash，負責傳送簽章請求。未改模型、人工掃描密鑰或資料庫 schema。

| 工作 | UTC 排程 | 每日基本訊息 |
|---|---|---:|
| 全市場觀察與 25 個深入分析 | `7 * * * *` | 24 |
| 最多 100 個待處理標的 | `3-58/5 * * * *` | 288 |
| 最多 20 個到期驗證工作 | `4-59/5 * * * *` | 288 |

合計 600 次／日，不含失敗重試。2026-10-30 Upstash 控制台顯示 Free 為 1,000 則／日（Marketplace 方案 metadata 仍顯示 500，兩者有落差）。本次依實際資源控制台確認的 1,000 上限安排；如服務回傳額度錯誤，先暫停與查核，不自動升級付費方案。Free 沒有 uptime SLA，仍需留意 overdue 與 DLQ。

## 安全與重試

- `/api/scheduler` 只接受 POST，由官方 Python SDK 檢查 JWT 簽章、issuer、期限、原始 body 雜湊與目標 URL，支援 current / next key 輪替。
- 目的 URL 由伺服器設定決定，不信任 Host header。只允許 full_scan / drain / validate，不接受任意 URL 或程式命令。
- `CSR_SCHEDULER_ENABLED=true` 才執行資料操作；必須有 durable ledger，且 Vercel 環境與 ledger label 相符。
- 一個環境使用獨立 FIFO queue，parallelism=1，避免三種排程互相碰撞。人工觸發仍沿用 revision CAS 保護。
- 每則訊息最多重試 2 次，HTTP timeout 55 秒；既有採集預算 40 秒、函式上限 60 秒保留。
- QStash message ID 與 operation 組成穩定操作 ID。掃描 receipt 與 lifecycle/signal 在同一筆交易提交；回應遺失後重送不重新採集、不新增 revision 或重複 signal。
- 掃描提交但發布失敗時，重試只重播 current snapshot outbox；idle drain 也會恢復未發布快照。不將多個 40 秒工作塞在同一個請求中。
- Provider 與操作 log 只收到數量、revision、時間及錯誤類別，不傳完整行情、signal 或密鑰。

## 管理與切換

`tools/manage_scheduler.py` 提供 plan / prepare / activate / pause / status / probe。憑證檔僅放忽略版控的 `.vercel/private/`；preview 可使用短效 Vercel OIDC header，不關閉 Deployment Protection。

1. plan 檢視三個目的與 cron；prepare 建立獨立暫停 queue 與固定 ID schedules。
2. 在隔離 preview 驗證有效／無效簽章、實際送達、重送去重與資料發布。
3. 正式部署 endpoint，連接正式 QStash 金鑰與 origin，先以 probe 驗證。
4. activate 後取得真正 schedule ID 與 message ID 的成功送達、revision 及 coverage 證據。
5. 確認新來源正常後，移除 GitHub 的 schedule trigger，保留 workflow_dispatch 作為人工備援；停止 preview schedules / queue。
6. 回退：pause QStash，確認沒有在途工作，再恢復 GitHub cron 或人工掃描；不要讓兩套正式排程並行。

## GitHub 查核

已提交案件 [#4928453](https://help.github.com/ticket/personal/0/4928453)。客服因帳號只包含自助支援而結案，未提供 scheduler backend 調查結果。因此不將「平台延遲／丟棄」列為已由 GitHub 證實的根因。

## 驗收紀錄

切換前補入 preview / production 的實際 schedule 訊息、操作 revision 與完整掃描時間。手動 probe 只能證明傳送鏈路，不能代替真正 cron 觸發的證據。
