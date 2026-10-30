# Dashboard 資源優化：實作與驗收紀錄

日期：2026-10-30。對照 [原計畫](dashboard-resource-optimization-plan.md)。

本輪只部署隔離 preview；未新增 commit、未 push、未合併 main，未切換 production。
原有研究系統的門檻、選股、掃描頻率與模型版本沒有因 Dashboard 工作變更。

## 與進行中工作的分工

| 項目 | 原有工作 | 本次處理 |
| --- | --- | --- |
| 資料時間、READY freshness | 已完成，沿用規則與固定市場 fixtures | 獨立顯示市場資料時間／最近讀取檢查；時間經過時重新判斷 freshness，不偽造資料更新時間 |
| 手動掃描與刷新競態 | 已有授權、單一讀取流程及 token 清除測試 | 擴充同一控制器：一般刷新合併；掃描後等待舊請求完成，再檢查發布版本 |
| 研究 journal / CAS / outbox | 已完成本機與容器驗收 | 沿用同一 publisher；原子發布 state、公開清單及 manifest，不另建研究寫入器 |
| Neon preview | 條款接受後已建立免費 sin1 DB | 執行既有 migration，建立最小權限 runtime role；修正真實 pooler 不接受 startup timeout 的問題 |
| 持續掃描 / validation | 有既有 API 與 opt-in workflow | 沿用，不新增排程或啟用 worker；尚未成熟的 outcome 不宣稱驗收完成 |
| Dashboard 輪詢 | 本機原本每分鐘讀取 4 個 API | 改成 manifest；版本改變才讀公開清單，取消日常 health/research 查詢 |
| Dashboard 儲存與 cache | 尚未建立 | 共用既有 Redis/key namespace；不新增 DB、版本歷史或推播基礎設施 |

另外修正舊 `/api/state` 讀取固定 production key 的問題，讓它和 writer 一樣遵循 `CSR_STATE_KEY`。

## P0 決策與基準

- 正式基準：`main` 的 `5816d3a3806c2f68c2554f21b407bb620e278b2d`；本機已有四個研究 checkpoint commits，加上本輪未提交變更。不是把本輪變更視為正式版本。
- 正式站重新讀取：首頁 44,078 bytes、state 348,696 bytes，health 158/129 bytes，均 `no-store`、CDN `MISS`。此量測未要求壓縮，不能當帳單 bytes。
- 選擇 **latest 清單 + ETag** 過渡，不建立不可變 URL，也不提供歷史版本承諾。只有 latest 一份公開投影，因此沒有新歷史保留／清理成本。
- 公開投影 allowlist 見 `src/dashboard_view.py`。保留搜尋、篩選、排序、freshness、現有詳情所需欄位；省略重複 symbol、null 欄位及空理由。排除 features、ledger 身分、pending queue、通知及原始內部錯誤。
- 模型／schema 與公開清單內容雜湊產生 snapshot_id，與 DB revision 解耦。revision 重置本身不假裝資料改變；模型、schema 或公開內容不同會有新 ID。讀取時不重新序列化整份研究 state 計算版本。
- 一個完整日的 requests、CPU、memory、Redis commands/bytes、掃描成功率及費用對照**仍缺少**。CLI 實查：Hobby 無法查詢需 Observability Plus 的用量，回傳 `payment_required`。未升級方案，不以推算取代。

## P1 / P2：讀取與快取

- `/api/dashboard-meta`：小型 manifest，成功時 CDN fresh TTL 30 秒，無 stale-while-revalidate。
- `/api/dashboard-latest`：同樣短 TTL + ETag，**禁止 immutable**。manifest A／list B 只有限重試一次；仍不一致則保留舊畫面，退避後重新對齊。
- 只有成功配對或確認相同版本後才接受 manifest ETag，避免失敗後以新 ETag 搭配舊畫面而永久卡住。
- 成功回應：`Cache-Control: public, max-age=0, must-revalidate` 與 `Vercel-CDN-Cache-Control: public, max-age=30`。錯誤和掃描後 `?sync=1` 的有限同步檢查為 no-store。首頁只含公開 shell 和無密鑰的手動掃描 UI，採短 CDN cache。
- If-None-Match 支援強／弱 ETag 與 304；304 不帶 body。舊 state/health API 保持相容、不改成公開 cache；掃描／維護 API 維持授權與 no-store。
- 同一 Lua CAS 在拒絕舊 revision／不同 ledger 後，才一起寫入 state、清單和 manifest。發布失敗不推進公開版本。File adapter 在同一個原子檔案替換中保存相同公開投影。
- 新讀取路徑只 GET 對應公開 key，不探測 Binance，也不讀 research DB。健康訊息表示最後採集／發布觀察，過期為待確認，並非即時健康保證。

HTTP header 的設定依 [Vercel Cache-Control 文件](https://vercel.com/docs/caching/cache-control-headers) 核對；下列驗收另外確認實際 CDN 行為。

## P1 / P3：前端與多分頁

- 單次 setTimeout 鏈，正常 60 秒 ±10% jitter。錯誤 15/30/60/120/240/300 秒退避並尊重 Retry-After 下限；成功恢復正常間隔。
- 隱藏／離線停止週期請求，恢復立即檢查；未完成的既有請求不算新輪詢。接續下載清單前也再檢查可見性。
- 同時只有一個讀取流程；相同版本不下載清單，不重設搜尋、方向／狀態篩選、捲動、焦點或已開詳情。資料更新時詳情同步更新；標的消失則明確標示保留舊詳情。
- 讀取失敗保留最後成功資料；首次沒有資料另有空狀態。資料年齡由原資料時間計算，不以成功輪詢時間刷新。
- 手動掃描先記錄掃描前版本。已完成但未發布時顯示待同步，最多 45 秒、每 5 秒短期檢查，之後恢復一般週期；影子掃描不假裝正式發布。
- BroadcastChannel + Web Locks：同源可見分頁由單一更新者輪詢；版本改變才廣播完整快照，未變更只廣播小型 metadata。隱藏／關閉後接管。訊息具有 schema 與檢查時間，忽略較舊公告與不相容資料。不支援 API 時獨立低成本輪詢；不使用瀏覽器持久儲存。保留 bfcache 恢復時的 channel，pageshow 立即檢查。
- 已精簡公開欄位，現有詳情仍足夠小且可隨清單一起使用，因此不新增詳情 API。未導入差量、SSE、WebSocket：P4 的量測必要性尚未成立。

## 共用驗收與已取得證據

- 一般 Python：**81 passed, 3 skipped**。三個外部服務案例沿用 storage runner，另測真 PostgreSQL/Redis：**3 passed**，新增的公開三 key 原子性及獨立 legacy writer 檢查併入同一既有測試，未複製另一套測試環境。
- 沿用 `tests/browser_acceptance.cjs`：desktop 1440×900、mobile 390×844；原有 freshness、手動掃描競態、token 清除，增加 ETag、相同版本零清單下載、讀取失敗保留、兩次版本不一致後有限結束、保留搜尋與詳情、多分頁廣播／接管。
- 同一瀏覽器驗收另以模擬時鐘確認：背景 121 秒零週期請求、離線 121 秒零請求、恢復立即檢查、Retry-After 20 秒下限、缺少 Web Locks 時回退、bfcache pageshow 恢復。fixtures 及原先真 API→SQLite→file read view→Dashboard integration 沿用。
- Neon pooled runtime 連線已驗證 ledger 可讀、preview 綁定、不可改 ledger/environment、不可更新／刪除 immutable records、無 schema CREATE、immutability trigger 與 lease 欄位均正常。管理者連線不注入 runtime；本機私有連線檔保留在忽略的 `.vercel/private/`。
- Vercel sin1 全市場：522 observations、25 deep、575 upstream requests、0 failures、497 pending、HTTP 約 9.984 秒，成功發布 revision 1。
- 同一 DB 續跑：100 deep、302 upstream requests、0 failures、397 pending、HTTP 約 7.297 秒，成功發布 revision 2。validation 無到期工作、0 upstream requests；outbox 重播成功。這些不能代替成熟 horizon 的結果驗證。
- 第一次 preview 公開讀取：manifest **402 bytes**；清單 311,669 bytes 未壓縮／8,917 bytes gzip，舊相容 state 同時為 406,279／12,862 bytes。
- 續跑 100 個標的並省略空欄位後，同一份資料的公開清單 **144,054 bytes／15,251 bytes gzip**；相容 state **418,742／22,045 bytes**。此對照約減少 65.6% JSON body、30.8% gzip body；與首次 preview 是不同資料內容，不能交叉比較或當作帳單節省比例。
- 相同 URL 第二次條件讀取：manifest 與清單均 **CDN HIT + HTTP 304 + body 0 bytes**；第一次 MISS。這證明該次 cache 命中省掉 origin invocation，不能推論跨 POP 的每次 miss 都合併。
- 原始量測：`artifacts/verification/dashboard-cdn.json`、`vercel-preview-scan.json`、`vercel-preview-worker.json`、`browser-result.json`、`integration-result.json`、`storage-services.json`。這些是忽略的本機報告，不含連線密鑰。

## 最終 preview 與尚未關閉的關卡

最終 preview：[隔離驗證站](https://crypto-state-radar-m5qren1go-masons-projects-1db534c5.vercel.app)，deployment `dpl_2dpZrZe1We1QfWXMWtXS4sVkwMqP`，sin1，Ready。來源 SHA-256 `bc353c0562f745bfb90af821f39a853826876bec72e854121d36dc00bf20a8d9`。

來源指紋、未提交狀態與部署 ID 保存在 `artifacts/verification/deployment-source.json`、`final-deploy.json`。Vercel 部署也標記 `sourceTreeHash` 和 `workingTree=uncommitted`，避免把 Git HEAD 誤當成整份部署來源。

最終 preview 已通過真實 HTTP 與瀏覽器驗收，報告存於 `dashboard-cdn-final.json` 與 `cloud-dashboard-browser.json`；瀏覽器檢查包括真實路由、522 個標的、相同版本零清單下載、無 health/research 輪詢、鍵盤開啟與保留詳情、desktop/mobile 和無頁面錯誤。

尚須取得代表性歷史與成熟 forward evaluation，以及將來正式發布後完整一天的觀察；不宣稱 production Definition of Done 已完成。

回退方式：保留舊 state 端點及既有權威 ledger，切回舊讀取前端即可；不刪資料庫、不更改模型、不倒灌舊 snapshot。latest 僅短 TTL，不存在不可變 URL 被覆寫問題。

後續授權更新：使用者已要求完成後 commit、push 並合併 main；部署準備與回退步驟另記於 [正式發布清單](production-release.md)。上述 preview 驗收保留作為當時證據。
