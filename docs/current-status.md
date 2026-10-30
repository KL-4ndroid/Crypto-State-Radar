# 目前狀態與剩餘工作

更新日期：2026-10-30。這是目前狀態入口；其他 checkpoint 與發布清單保留作為歷史證據。正式來源與完整測試數以各 PR / main CI / Vercel 部署紀錄為準。

## 已發布

- V2.4.1-alpha 的 production 特徵接線、共用特徵契約、缺值語意、模型版本與 lifecycle 修正。
- 全市場觀察、不同類型候選、active lifecycle 優先、持久 backlog 與有界續跑。
- 隔離 Neon preview / production、最小 runtime 權限、immutable research records、revision CAS、原子 Redis read view 與發布重播。
- 到期 forward validation 的分批、lease、重試與 append-only outcomes。
- Dashboard 中文閱讀改善、重要資訊優先、細節收納、manifest / ETag、背景暫停、多分頁協調與手動掃描同步。
- 同一 Upstash 安裝下的 Free QStash 已接管自動排程。GitHub 僅保留 workflow_dispatch，preview 排程暫停，未使用的 Inngest 已移除。
- 18:07、19:07、20:07 三次真正的 hourly scan 已成功；每輪約 21 分鐘清空 backlog。這是短期實測，不能代替完整一天的驗收。

排程與幣安請求的實際關係見 [資料採集頻率](data-collection-cadence.md)；切換與回退見 [Upstash 排程](independent-scheduler.md)，正式切換證據見 [PR #17](https://github.com/KL-4ndroid/Crypto-State-Radar/pull/17)。

## 本次驗收工具

- 排程設定由 `src/scheduler_contract.py` 共用；管理工具與驗收工具不各自維護另一份頻率。
- 每次排程操作新增精簡的 `scheduler_receipts`：request count、failed request count、閒置／驗證數與結果。沿用 research journal 的 immutable records，沒有新 schema、沒有推進模型 revision。
- 收據寫入是額外證據，不會因為寫入失敗而重新掃描。運算超過 30 秒時跳過額外寫入，保留時間預算；缺少收據或 request metrics 會使驗收的 accounting 不完整，不能以零請求代替。
- `tools/operational_acceptance.py` 只讀 PostgreSQL 與 QStash GET API，不呼叫幣安，不觸發掃描、不發布資料、不修改排程。分頁不完整、漏觸發、重複時槽、未送達、缺計量或超時未清空的輪次都會列出。
- `src.release_readiness` 只評估所提供設定與 DB 權限，已支援 QStash；不再用固定的 `production_ready=false` 代表已上線服務失敗。它不會認證模型有效性或自動關閉運行關卡。

## 尚未完成

| 優先 | 項目 | 關閉條件 |
|---|---|---|
| 1 | 至少 24 小時正式運行驗收 | 新計量上線後的完整窗口、預期排程時槽、實際投遞與收據、覆蓋延遲和請求量報告；未到期保持 in_progress |
| 2 | 代表性歷史與成熟 forward evaluation | 足夠事件、完整市場分母、holdout、無洩漏的現行模型重播；模型門檻仍未校準 |
| 3 | 資源與保留策略 | 分開量測 Functions bundle、runtime、Redis、Neon 增長，再設計套件縮減與封存；目前未清除舊部署或研究資料 |
| 4 | 維運保障 | 通知管道與故障處理、隔離備份還原／回退演練及記錄；本次不新增通知服務或付費方案 |
| 5 | 成效呈現 | 沿用既有研究評估／匯出結果建立可讀報表及歷史趨勢 |

24 小時報告中的 request count 不等於 Binance request weight、CPU、Redis 命令或帳單費用。Hobby 無法取得的付費觀測指標須另列未量測，不自動升級。

## 驗收執行方式

使用忽略版控的 `.vercel/private/production-runtime.json` 與 `qstash-runtime.json`。新計量正式部署後記錄 UTC 起點（必須含時區），執行：

```powershell
.venv/Scripts/python.exe tools/operational_acceptance.py --start <UTC起點> --output artifacts/operations/acceptance-24h.json
```

完整 24 小時加 5 分鐘在途寬限前，結果只會是 in_progress。短樣本即使全部正常也不是 24 小時通過。完整窗口結束後重讀既有證據即可，不必為驗收再跑一輪市場採集。
