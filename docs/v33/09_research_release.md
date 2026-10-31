# v3.3.0 研究成果發布

日期：2026-10-31。發布現有 V3.3 研究成果，保留 **EXPERIMENTAL / PRE_VALIDATION**。此版本號代表研究軟體交付，不代表完整交易模型已完成或通過有效性驗證。

## 已合併內容

- [PR #20](https://github.com/KL-4ndroid/Crypto-State-Radar/pull/20)：不可變規則 registry、因果 feature store、Family coverage / breadth、雙 FSM、Gate、原子 journal 與離線 replay。合併 commit：`c654e69f406c455d1737108c40ee7e2e03d4a4ab`。
- [PR #21](https://github.com/KL-4ndroid/Crypto-State-Radar/pull/21)：九種來源的有界本機 SHADOW 採集、raw checksum archive、持久 cursor / lease、來源修訂、QA 與完整窗口 CVD 量測。合併 commit：`315dcde16ab18f3a11bc835b4476f16d76f0ef2b`。
- 原始規格與詳細限制見 [工程交付](07_delivery_report.md)、[採集交付](08_acquisition_delivery.md) 及 [升級政策](05_promotion_policy.md)。

## 驗收

- 2026-10-31 本機完整 suite 複驗：**263 passed，5 skipped**，12.87 秒。略過項目為 opt-in storage tests，不將略過當作通過。
- 兩個來源 PR 的 GitHub CI 與 Vercel preview checks 均成功，沒有未解決的 review threads。
- 先前真正 PostgreSQL / Redis 的隔離驗收與少量真實採集證據沿用兩份交付報告；本次發布沒有重新採集市場資料或重跑儲存服務測試。
- 最終 release commit、main CI 與正式部署狀態以 GitHub Release / Actions 及 Vercel 紀錄為準。

## 正式服務與研究邊界

正式訊號模型維持 **V2.4.1-alpha**。V3 研究模組沒有掛入正式 scanner / Dashboard，不執行 production migration、不啟動持續 V3 採集、不建立新雲端服務，也沒有 Champion promotion 或交易執行。PR #19 的 demand scheduler 不包含在本次發布。

尚待完成：遠端 durable raw backend、容量與跨程序 rate budget、正式持續採集、完整研究 feature producers、事件資料集、含成本的 executable backtest、OOS / walk-forward / ablation 與 Champion / Challenger 驗證，以及後續 V3 Dashboard 整合。這些工作沒有因合併或版本標籤而被視為完成。

## 本機研究入口

```powershell
.venv/Scripts/python.exe tools/v33_research.py sources
.venv/Scripts/python.exe tools/v33_acquire.py plan --run-id pilot --symbols BTCUSDT
```

這兩個命令只列出來源或採集計畫；初始化、實際採集與 replay 的操作方式見各交付文件。V3 SQL migration 檔案隨原始碼交付，正式服務不自動套用。

## 發布與回退

合併 main 後沿用既有 Vercel Git production deployment，驗證 commit、Ready 狀態、正式網域、首頁及公開 manifest / snapshot 配對。發布版本以 `v3.3.0` tag 對應最終文件與程式碼，GitHub Release 明列實驗狀態。

需要回退時使用 Vercel 已知可用的前一 production deployment；不刪除 ledger / cache、不倒灌研究資料、不變更排程。V3 本機研究資料與 migrations 保持獨立。
