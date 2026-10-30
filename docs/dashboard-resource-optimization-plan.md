# Dashboard 資源與使用體驗優化實作計畫

日期：2026-10-30
狀態：核心 P0–P3 已正式上線；完整日用量與長期驗收進行中，見 [目前狀態](current-status.md)
用途：設計與驗收依據；目前成果、重用項目與剩餘關卡見 [實作與驗收紀錄](dashboard-resource-optimization-implementation.md)

## 1. 目標與範圍

在維持市場資料正確性、更新可見性與手動操作體驗的前提下，減少重複下載、Vercel 函式執行、Redis 讀取與外部健康檢查。

本計畫處理 Dashboard 的讀取與發布介面，不調整訊號模型、掃描選股、掃描頻率、研究驗證或交易判定。執行本計畫不代表授權啟用研究 worker、建立資料庫、執行 migration、推送 GitHub 或部署 production。

優先順序：

1. 有變才下載、背景暫停、共用快取。
2. 健康狀態彙整與清楚的新鮮度提示。
3. 精簡清單與多分頁協調。
4. 根據量測決定是否需要差量更新或推播。

## 2. 已知基準與實作前核對

本次討論曾直接讀取正式網站，得到下列基準；這些是當時快照，實作前必須重新核對。

| 項目 | 當時觀察 |
| --- | --- |
| 正式網址 | https://crypto-state-radar.vercel.app |
| 網頁自動更新 | 每 60 秒呼叫 binance-health、state-health、state，共 3 支 API |
| 完整 state | 522 個標的、348,696 bytes 未壓縮 JSON |
| 首頁 | 約 44 KB，經 Python Dashboard handler 輸出 |
| 全市場排程 | GitHub workflow 每小時第 7 分鐘觸發一次 |
| 回應快取 | 既有 Dashboard/API 使用 no-store |
| 計費實測 | 未取得；Vercel observability 查詢當時回傳 404 |

24 小時持續運作的單分頁，理論上產生約 4,320 次 API 呼叫與 502 MB 未壓縮快照內容。不包含額外手動更新，瀏覽器背景節流、CDN 壓縮及回應大小變化均會影響實際結果。不可將此數值當作 Vercel 帳單流量。

本機已有較新的研究 ledger、revision、shadow/live 與 publication 設計；正式站當時仍呈現舊版三支 API 的行為。**不能把本機新版直接整批部署，當作完成本計畫。**

實作起點檢查清單：

- 確認正式部署 SHA、GitHub workflow、目前前端輪詢與實際服務方案。
- 檢查 AGENTS.md、工作目錄差異及既有未提交內容。
- 確認正式 state writer、Redis key、是否存在 ledger/outbox，以及實際發布成功的定義。
- 確認哪些 snapshot 欄位可公開快取；個人資訊、密鑰、內部錯誤與研究內容不可混入。
- 蒐集至少一個完整日的請求數、CPU、記憶體、壓縮後傳輸量、Redis commands/bytes 與掃描成功率；無權限時標記缺項，不以推估代替。

## 3. 目標架構

```text
掃描 / 既有發布程序
  → 產生公開 Dashboard read view 與 snapshot 身分
  → 完整資料成功發布後，原子更新 latest manifest

使用者首次進入
  → 讀取可共用快取的 manifest
  → 下載對應版本的市場清單

可見分頁定期檢查 manifest
  → snapshot 身分相同：不下載市場清單
  → snapshot 身分改變：載入對應版本並原子替換畫面

分頁隱藏：停止週期網路請求
重新可見：立即檢查 manifest
```

權威研究資料仍由既有架構管理。Dashboard read view 是供讀取的投影；瀏覽器與 CDN 都不成為研究資料的權威來源。

## 4. API 與發布契約

以下路徑為提案，實作時依既有路由決定，並保留舊介面的相容期。

### 4.1 小型 manifest

`GET /api/dashboard-meta`

```json
{
  "schema_version": 1,
  "snapshot_id": "opaque-public-view-id",
  "published_at": "2026-10-30T08:07:20Z",
  "last_successful_scan_at": "2026-10-30T08:07:18Z",
  "expected_scan_interval_seconds": 3600,
  "scan_status": "ok",
  "health_checked_at": "2026-10-30T08:07:20Z",
  "services": { "market": "ok", "state": "ok" },
  "summary_url": "/api/dashboard-snapshots/opaque-public-view-id"
}
```

- 目標 body 不超過 2 KB；只公開畫面必要資訊。
- `snapshot_id` 表示「已成功發布的公開清單版本」，不可直接等同任何 DB transaction revision。validation、shadow 更新或失敗發布不一定改變公開市場清單。
- 可採用公開 read view 的穩定內容雜湊；舊 runtime 沒有 revision 時，在既有發布位置計算，避免讀取端每次序列化整份 state 才算版本。
- 版本識別需包含 schema / model / publication generation 的必要差異，避免資料庫重建或 rollback 後 revision 重置導致誤判。識別值不洩漏內部環境資訊。
- 健康狀態改變可以更新 manifest/ETag，但不必改變 snapshot_id，避免因此重抓完整清單。
- 時間為 UTC ISO 8601，前端按使用者時區顯示。
- 未知、過期、失敗、未設定均有明確狀態，不用「ok」替代未知。

### 4.2 指定版本的清單

`GET /api/dashboard-snapshots/{snapshot_id}`

- 回傳 schema_version、snapshot_id、published_at、清單必要欄位與摘要。
- URL 的版本與 body 必須一致；不存在回傳 404/410，不可用最新資料冒充指定版本。
- 若採不可變 URL，必須有可跨函式實例與重新部署存取的版本儲存；不可只放程序記憶體或 /tmp。
- 先完成 snapshot 持久化，再切換 manifest；兩者無法原子交易時，用不可變 snapshot＋最後更新 pointer 的方式實現一致性。
- 保留上一個可用版本以支援讀取中的使用者與回退；實作時定義數量/期限、刪除時機與儲存成本。
- 新版讀取失敗時保留舊畫面，重新取 manifest 後有限次重試，不進入無限循環。

若第一階段不導入歷史版本儲存，可先採「latest 清單＋ETag」過渡：前端必須驗證清單 snapshot_id，版本競態時重新對齊；此 URL 不得設定 immutable 或長效快取。第一階段開始前選定其中一種方式並記錄理由，不同時維護兩套新機制。

### 4.3 詳情

第二階段視實測決定是否新增 `GET /api/dashboard-snapshots/{snapshot_id}/symbols/{symbol}`。

清單保留搜尋、篩選、排序、摘要與 freshness 所需欄位；長理由、完整 features 與研究資訊按需讀取。詳情必須與清單版本一致。若清單已足夠小，沿用既有快照中的詳情，避免為拆分而增加請求與維護成本。

### 4.4 健康資訊

- Dashboard 不再由每個分頁直接呼叫兩支健康 API。
- 優先重用掃描/發布程序已觀察到的結果；發布成功不等同 Binance 每秒都正常。
- 如需掃描間隔中的健康探測，使用單一共用、可限頻的探測流程；先評估是否真的需要新增排程。
- 記錄 checked_at；過期即顯示「尚待確認」，服務故障不能因舊快取長期顯示正常。
- 讀取端不可因 cache miss 自動無上限探測 Binance 或執行掃描。

## 5. 快取與 HTTP 設計

| 資源 | 初始策略 | 驗證重點 |
| --- | --- | --- |
| manifest | CDN fresh TTL 30 秒；初期不使用 stale-while-revalidate | 新版本可見延遲及健康資訊時間可解釋 |
| 不可變版本清單 | 驗證版本永久不變後才給長效 public / immutable | 版本保留、舊版本讀取與隔離 |
| latest 過渡清單 | 短 TTL＋ETag，禁止 immutable | body 與版本競態 |
| 首頁 | 視內容採靜態輸出或短 CDN 快取，先確認注入內容 | 不快取使用者 token、部署後前端更新 |
| 掃描與維護 API | 維持驗證與 no-store | GET/POST 副作用不被 CDN 重播或公開 |
| 個人化或授權資訊 | private / no-store | 不與公開市場 read view 共用 |

實作時依 Vercel 當時文件核對 `Cache-Control`、`CDN-Cache-Control` 或 `Vercel-CDN-Cache-Control` 的優先順序及 Python 路由支援，再決定 header，不能只看程式宣告就判定 CDN 已命中。

- manifest / 清單提供穩定 ETag；正確處理 If-None-Match / 304。
- 304 可節省 response body，但若仍進入 Python 或 Redis，就沒有省下對應後端操作，須分開驗收。
- 前端 fetch 策略需配合 HTTP 快取，不再對所有公開讀取一律 no-store。
- 不將 origin「請求快取命中」與瀏覽器「不發請求」混為一談。
- 檢查 Age、x-vercel-cache、Content-Encoding、Vary 與瀏覽器 transferred size；確認壓縮與快取 key 正確。
- cache miss / 過期可能跨 POP、多實例同時發生；不能宣稱所有使用者每 30 秒只查一次資料庫。熱門 miss 必須量測是否需 coalescing。
- 若使用 last-known-good/stale 策略，明訂最長可接受資料年齡及可見 stale 提示，不能無限顯示「即時」。

## 6. 前端更新與體驗

初始參數（後續依實測調整）：

- 可見分頁：每 60 秒查 manifest，加入約 ±10% jitter。
- 隱藏分頁：取消下一輪計時，不發出週期網路請求；回到前景立即檢查。
- 改用單次 setTimeout 鏈，在請求完成後排下次，避免 setInterval 堆疊。
- 同時只允許一個更新流程；使用 AbortController 或 generation guard，防止舊回應覆蓋新版本。
- 錯誤退避：例如 15、30、60、120、最多 300 秒，加入 jitter；成功後恢復。尊重 Retry-After，離線時暫停，恢復連線再檢查。
- 手動重新整理可合併已在進行中的請求，先檢查版本；顯示「已是最新版本」而非強制重下載。
- 掃描操作維持原有授權；成功後檢查已發布版本。若尚待發布，顯示待同步，採有期限的短期檢查，不假設 HTTP 200 就有新 snapshot。

畫面要求：

- 分別呈現「市場資料時間」「最近檢查時間」「掃描/同步狀態」。
- 背景更新保留清單、搜尋、篩選、排序、捲動、焦點與已開啟的詳情。
- 更新完成後以 snapshot 原子替換，避免新舊欄位混合；詳情版本改變時更新或明確保留版本標記。
- 失敗保留最後成功資料並標記過期；首次無資料與有舊資料時採不同的錯誤呈現。
- freshness 使用資料時間與預期間隔判斷；每分鐘成功讀取不能讓一小時前資料變成「剛更新」。
- 數字短暫變動不造成整頁跳動；狀態提示支援鍵盤與適當的輔助技術公告。
- 初期只保留記憶體快照；若後續加入本機持久快取，另定 schema、容量、有效期與清除規則。

## 7. 分階段工作與交付

### P0：確認基準與架構決策

交付：量測表、正式/本機差異、公開欄位清單、版本身分與儲存決策。

選擇最新清單＋ETag過渡或不可變版本；確認實作可獨立於未發布研究 runtime 交付。記錄可觀測性缺項及補測方式。

完成條件：可重現現行輪詢量、回應大小、Redis 路徑，並可辨識實測與推估。

### P1：版本檢查與前端生命週期

交付：小型 manifest、版本一致性、背景暫停、單一更新流程、退避、資料時間提示，以及對應測試。

先在隔離資料與 preview 驗證發布/讀取契約；舊 state API 暫時保留。健康資訊先呈現已知值與時間，尚未完成彙整時不得假設正常。

完成條件：未變更版本不下載完整快照；隱藏後無新週期請求；回前景立即檢查；失敗不清空舊資料。

### P2：共用快取與健康彙整

交付：正式 HTTP 快取策略、快取命中驗證、共用健康資訊，前端移除逐分頁健康探測。

P1/P2 依賴同一 API 契約，可同批驗收；前端移除舊健康路徑前必須具備新狀態來源。若需新增探測排程，獨立記錄預算與啟用步驟。

完成條件：preview 真實 CDN 命中可證實；增加使用者不再按每人每分鐘增加 Binance 健康請求；後端讀取次數有量測改善。

### P3：清單精簡與多分頁共用

交付：清單欄位契約、必要時的版本化詳情；以 BroadcastChannel 配合 Web Locks（可用時）協調。

- 同源可見分頁選一個更新者，廣播版本/必要快照；隱藏或關閉後接管。
- 處理 leader crash、lease 到期、多 leader 競態、schema 不相容與不同新舊前端同時開啟。
- 不支援協調 API 時退回各分頁獨立的低成本版本輪詢，正確性不能依賴 leader 存活。
- 跨裝置不共用瀏覽器協調，依靠 CDN；支援時可用 session/channel 命名避免環境混用。

完成條件：搜尋/篩選/排序結果不變；同源多分頁正常協調時主要由一個可見分頁更新；故障後可接管且不漏更新。

### P4：量測後的選配

只有資料更新頻率或使用規模證明有必要，才評估差量 API、SSE/WebSocket、長連線或額外基礎設施。評估必須包含記憶體占用、連線生命週期、重連、漏事件補抓、平台限制與維護成本。不得僅為降低短輪詢請求數就導入長連線。

## 8. 預期效益與驗收指標

以每日 24 次公開 snapshot 更新、單分頁全天可見、快照大小維持 348,696 bytes 計算：

| 指標 | 現行理論值 | P1/P2 目標 |
| --- | --- | --- |
| 完整快照下載 | 約 1,440 次/日 | 首次＋每個已見新版本，約 24～25 次/日 |
| 未壓縮完整快照內容 | 約 502 MB/日 | 約 8.4～8.7 MB/日，另加 manifest |
| 健康探測 | 每分頁約 1,440 次 Binance/日 | 不隨分頁數線性增加 |
| 前端 API 次數 | 約 4,320 次/日 | 約 1,440 次 manifest＋24～25 次清單，另加手動/恢復 |
| 隱藏時週期請求 | 依瀏覽器節流仍可能存在 | 0；不含隱藏前已發出的請求 |

完整快照內容可望下降約 98%；**不是總費用下降 98% 的保證**。manifest body 目標 ≤2 KB，即使每天 1,440 次，其 body 也約 ≤2.9 MB，仍需另外計 HTTP headers 與實際壓縮。

首次載入、手動掃描、每日新版本數增加、詳情下載、多分頁接管與使用者實際時數都會改變結果。

體驗驗收初始目標：

- 正常網路下，已可見分頁於成功公開發布後 120 秒內看到新版本；以輪詢、CDN TTL 與網路延遲合併量測，不對網路故障保證此上限。
- 回前景立即發起檢查，不等待下一個 60 秒週期。
- 相同版本手動更新不重新下載完整 body；弱網/重試不重設使用者操作狀態。
- 多使用者壓測須分開統計 browser requests、CDN hits、origin invocations、Redis commands、Binance calls；不能只量其中一項推論全部成本。
- 記錄 p50/p95 回應時間、壓縮後 bytes、錯誤率、資料年齡；以相近負載比較優化前後，不預設缺乏基準的絕對效能數字。

## 9. 測試矩陣

| 層級 | 必要案例 |
| --- | --- |
| API / 發布 | 相同版本、成功新版本、失敗發布不前移 pointer、舊 writer 延遲回寫、schema 變更、revision 重置/rollback |
| HTTP | ETag/304、真實 CDN HIT/MISS、壓縮、查詢參數對 key 影響、未授權/錯誤回應不公開快取 |
| 一致性 | manifest A→清單 B 競態、版本不存在、舊快取、snapshot 已寫但 pointer 未更新、pointer 更新失敗 |
| 前端 | 初載、前景/背景切換、離線/恢復、慢請求、重複按更新、舊回應較晚到、保留排序/捲動/詳情 |
| 掃描狀態 | HTTP 成功但未發布、發布失敗、部分標的失敗、last-success 過期、不顯示虛假正常 |
| 多分頁 | 開關分頁、leader 隱藏/崩潰、重新接管、不支援 API、新舊 schema 共存 |
| 裝置 | 桌面與手機、鍵盤操作、慢網路下可讀取最後成功資料 |
| 回歸 | 舊讀取 API、手動掃描授權、snapshot freshness、研究 shadow/live 隔離與既有 publication guard |

先使用固定 fixture 與模擬時鐘驗證計時、競態與下載次數；preview 再跑少量真實 CDN 測試。負載測試使用隔離資料，不用正式 Binance 或 production DB 當壓測對象。Python/瀏覽器既有相關測試應一起執行；文件階段不要求提前執行未實作測試。

## 10. 預計涉及檔案

| 位置 | 預計工作 |
| --- | --- |
| index.html | 更新控制器、版本比較、前景生命週期、提示與保留 UI 狀態 |
| api/index.py | 公開讀取介面相容、既有 health/state 行為與安全邊界 |
| api/dashboard.py | 首頁輸出與快取策略，檢查注入腳本是否覆蓋新邏輯 |
| 新增 Dashboard API 模組 | manifest、版本清單與條件式 HTTP 回應；名稱待 P0 決定 |
| src/state_store.py / 既有 publication adapter | read view 發布一致性與公開版本 metadata |
| src/run_cycle.py / src/research_worker.py | 僅在確認實際 writer 後接入發布結果，避免兩套 writer |
| vercel.json | 新路由、打包依賴與 header 支援 |
| tests/ | API、HTTP、發布一致性、瀏覽器生命週期與相關回歸測試 |
| README.md / docs/ | 更新對外行為、設定、量測與回退指引 |

此表為預計範圍，不能視作所有檔案都必須修改；P0 之後縮小為實際變更集合。

## 11. 部署與回退順序

1. 在窄範圍分支實作，保留無關本機變更；記錄選定正式 baseline。
2. 先隔離驗證新讀取介面與發布契約，舊介面繼續可用。
3. preview 驗證前端新流程、CDN、安全邊界與真實量測；preview 使用隔離 key/ledger。
4. 彙整可審查的 diff、測試結果、預算、環境需求及回退方式，再決定正式發布。
5. 正式先具備相容後端，再切前端；上線觀察至少完整一日並包含一次成功掃描與錯誤情境。
6. 確認舊版前端仍能讀取後，才考慮移除舊端點；需考量長時間未關閉的舊分頁。

回退優先切回舊讀取前端/相容路徑，保留新 snapshot 儲存與既有權威資料；不要為回退刪除 ledger 或還原研究模型。若版本識別或快取錯誤，修正/切換 manifest generation 並按已驗證方式失效快取，避免長效 immutable URL 被覆寫。部署回退不應使畫面把更舊資料標為最新。

## 12. 開始實作時的完成定義

- P0 選定方案與公開資料契約已記錄。
- 未變更資料不重下載完整快照；健康檢查不再逐分頁放大。
- 有效快取命中與資源改善有可重現的實測證據。
- 資料版本、時間、失敗與 stale 提示正確，操作體驗不退步。
- 正確處理發布競態、前端請求競態、背景恢復及回退。
- 既有掃描/研究保護與相容測試通過。
- 實作完成、本機驗證、preview 驗證與 production 部署分別記錄，不混稱已完成。

後續實作可從「P0＋P1/P2」作為第一個獨立交付範圍；P3、P4 依量測另行決定。
