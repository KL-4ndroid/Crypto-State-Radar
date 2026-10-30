# V2.4.1-alpha 與 Dashboard 正式發布清單

> 這是先前發布的準備紀錄，production 已完成切換。現況見 [目前狀態](current-status.md)；排程與回退請先看 [QStash 文件](independent-scheduler.md)，不要在 QStash 啟用時直接恢復下列歷史 GitHub cron 步驟。

2026-10-30；使用者已授權完成後 commit、push、合併 main。
此文件記錄合併前準備；最終合併 SHA、CI 與部署結果以 GitHub PR／Vercel deployment 記錄為準。

## 已驗證的來源

- 前一正式基準 `5816d3a3806c2f68c2554f21b407bb620e278b2d`。
- 應用來源 SHA-256 `bc353c0562f745bfb90af821f39a853826876bec72e854121d36dc00bf20a8d9`，與完成驗收的 preview 相同。
- 沿用本機 **81 passed**、另行真 PostgreSQL/Redis **3 passed**、desktop/mobile 與真 API integration 結果；沒有因提交而重跑未變更的市場掃描或歷史研究。
- Preview 證據見 [Dashboard 驗收](dashboard-resource-optimization-implementation.md)。模型仍為 alpha；少量無事件 cohort 不能證明預測準確率，成熟 forward outcomes 與完整日用量持續累積。

## 正式環境準備

- Neon `crypto-state-radar-production`，Free `free_v3`、sin1；資源 `store_pmuUWxJcRnpvnNr4`。和 preview 不共用 DB、runtime role 或 ledger。
- 使用既有 migration 綁定 `production`，建立 `csr_production_runtime`；真 pooled 連線已確認 immutable records、禁止 DDL／修改 ledger identity、retry schema 等七項權限檢查通過。
- 移除 provider 自動注入的管理者環境變數，正式 runtime 僅使用最小權限的 `CSR_RESEARCH_DATABASE_URL`。
- 保留既有 `CSR_SCAN_TOKEN`；新增獨立 `CSR_WORKER_TOKEN`。實際值僅在環境設定與忽略的私有檔案中。
- 正式 read view key 改為 `crypto-state-radar:production:research:v1`；舊 `crypto-state-radar:state:v1` 完整保留，避免回退時覆寫新 ledger。
- 舊 state 的 522 個 WATCH 已備份並匯入正式 ledger revision 1；匯入紀錄保存來源 SHA-256 與時間，不偽造歷史 signal 或 outcome。舊 snapshot SHA-256：`e0ee4053673d9d39910ed89efdabb45e16c0ba021479d24362af2b3412c86057`。
- 切換期間暫停原 Radar Scheduler，完成後必須恢復。既有每小時第 7 分鐘設定不變。
- Staged production deployment：`dpl_Ei3fzCWWszpTAWxzKQBF8evUgZBy`，使用正式環境、`--skip-domain`；通過實際 API／發布驗證後，才切換正式網域。

## 發布順序與必要確認

1. Staged deployment Ready；以受保護的 worker API 驗證 production ledger、drain、validation 與原子發布。讀取清單與 manifest 必須相同版本，且 state key 為正式新 key。
2. 明確 staging 已審查檔案，確認無密鑰、無 `.vercel/`、無 artifacts；commit／push 分支，建立 PR，等待 CI 與 Vercel 檢查。
3. 合併 main，檢查對應正式部署 Ready，確認公開首頁、API、522 個標的、cache、手動掃描入口可用。
4. 恢復 Radar Scheduler 並手動 dispatch 一次，驗證 repository 原有掃描 token 能呼叫正式新 scanner。
5. 設定 research worker 的正式 HTTPS URL、獨立 token，驗證一次有界 tick 後啟用 `CSR_RESEARCH_WORKER_ENABLED=true`。每個 tick 分別 drain／validate／live publish，不改既有全市場 hourly 頻率。
6. 確認本機 main 與 origin/main 一致，working tree 無遺漏的應用變更；保留本機私有驗收報告及回退快照。

## 回退

- 停用新的 research worker，暫停 hourly scanner，保留新 DB 與新 key。
- 將正式網域切回前一部署；舊部署仍使用原 key／既有 token，可讀取保留的 legacy snapshot。不要把新 ledger 倒灌至舊 key。
- 恢復 legacy hourly workflow；確認健康與資料時間。Vercel rollback 後會停止自動網域指派，後續再次發布需明確 promote。
- 原始快照、migration 與部署證據存於忽略的 `artifacts/verification/`；連線密鑰在 `.vercel/private/`，不可分享或提交。

未取得一日觀察與代表性模型評估時，不宣稱成本下降比例、Precision/Recall 或全部研究關卡完成。
