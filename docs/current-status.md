# 目前狀態與剩餘工作

更新日期：2026-11-01。這是目前狀態入口；其他 checkpoint 與發布清單保留作為歷史證據。正式來源與完整測試數以各 PR / main CI / Vercel 部署紀錄為準。

## 演算法模組化

已將正式 V2 掃描／重播與 V3 研究評估接到 `src/algorithms`，共用呼叫契約並綁定模型版本、規則、程式與狀態。更新及回退方式見 [演算法操作指南](algorithm-updates.md)。這項工程完成不代表 V3.3 真實資料驗證或正式訊號切換已完成；未驗證 V3 live selection 會明確拒絕。

## 24 小時運行驗收已完成

固定窗口：台灣時間 2026-10-30 22:07～2026-10-31 22:07。全市場掃描 24/24、續跑 288/288、驗證 288/288，600 次投遞及收據完整，無漏排、重複、額外重試或記錄到的上游／應用錯誤。共 50,962 次上游 request，每輪 pending 約 20.90～20.96 分鐘清空。驗收自動化已刪除，正式 QStash 排程保留。

本機完整報告為忽略版控的 `artifacts/operations/acceptance-24h.json`。窗口中曾發布 v3.3.0 研究軟體，屬跨版本運行驗收，不是單一版本成本比較。Binance weight、Vercel／Redis 帳單與 Neon 增長未量測；運行通過不代表模型準確率通過。

## v3.3.0 研究成果發布

後續「完成整套 V3.3」開發進度及尚未關閉的驗收見 [完整版進度](v33/10_completion_progress.md)。成交研究工具的新增不代表完整 V3 模型或正式切換已完成。

使用者已選定「將現有研究成果合併發布，保留實驗狀態」。PR #20（研究契約）與 #21（有界採集 pilot）已依序合併 main；發布內容與驗收見 [v3.3.0 發布說明](v33/09_research_release.md)。V3.3 規則仍為 EXPERIMENTAL / PRE_VALIDATION，正式訊號模型仍為 V2.4.1-alpha。以下「已發布」為既有正式服務功能，並非 V3.3 模型已啟用。

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
| 已完成 | 至少 24 小時正式運行驗收 | 完整固定窗口通過；範圍與限制見本頁上方，不代表 V3 新模型驗收 |
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

## V3.3 隔離研究骨架

V3.3 研究轉型的本輪 P0–P4 工程交付、已知語意缺口與停止點見 [V3.3 交付報告](v33/07_delivery_report.md)。此為隔離研究骨架，未取代目前正式模型；所有附帶 V3 規則仍 EXPERIMENTAL。

2026-10-31 後續：已建立 [有界資料採集與 QA pilot](v33/08_acquisition_delivery.md)，保存少量隔離資料與基礎特徵；尚未啟用正式持續採集、遠端 raw backend 或新模型。
