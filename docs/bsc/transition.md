# BSC Agent ID 轉型與只讀 MVP

2026-11-01。使用者決定停止 Futures 多空壓力作為主要功能，保留工程基礎，改做 BSC Agent ID 新項目雷達。這一階段沒有錢包、私鑰、交易簽署、授權或下單功能。

## 已交付與沿用

- `src/record_stream.py` 沿用現有 ResearchJournal / csr_records，為 BSC 保存獨立 append-only heads 與 evidence，不推進舊 csr_runtime revision、不自動建立 Futures validation jobs。使用既有 runtime 權限，不新增 schema 或雲端服務。
- 短 commit 沿用 DB transaction、revision CAS 及原本 runtime row lock；RPC 在交易外執行。這是跨程序資料一致性，並非交易執行 exactly-once 保證。
- `src/algorithms/bsc.py` 沿用可替換純運算契約及版本綁定，只輸出 OBSERVED、DATA_PENDING、REVIEW_REQUIRED，不輸出買賣分數。bytecode 登記地址引用只是線索，不證明 Agent ID 專屬、退款能力或安全。
- `src/bsc/scanner.py` 支援小範圍完整 block + receipts，核對筆數、hash、重複、removed logs，保存直接部署與 Mint Transfer 證據。
- `src/bsc/log_scanner.py` 對明確 watchlist 追蹤連續 Mint 區間，預設每輪最多 128 塊、32 個地址；跨輪保持游標，不因落後而跳過。區間完整性是 RPC 宣告，不是 receipt/Merkle 全量驗證。
- 登記引用值來自稽核版本 Sniper 的設定，尚未建立獨立 registry 真實性驗證。沒有將其當成安全 allowlist。
- `tools/bsc_replay.py` 從已保存 evidence 回放，與採集共用 pure projection，不重新抓資料；保留原本 availability 時間，新輸出不得覆寫舊證據。
- Redis 的原子投影、manifest、ETag 與公開白名單欄位沿用；`bsc.html` 提供大字體中文畫面、篩選、細節收納、背景暫停、single-flight 及頁籤廣播。刷新只讀 Redis，不會採集或下單。

## 實際來源限制與範圍

實測 PublicNode 無地址 eth_getLogs 被拒絕，dRPC 回 429，官方公共節點回 limit exceeded。因此沒有偷偷升級或聲稱免費全市場持續掃描。MVP 採「有界區塊樣本發現＋已發現候選持續追蹤」。目前 watchlist 由真實 pilot 的兩個合約建立；非完整市場分母，不以沒有新事件推論沒有新項目。

完整區塊 pilot：1 塊、2 個合約、9 次 RPC、0 次失敗，保存 8 筆 evidence/head，約 49,215 logical JSON characters。從已存證據重播得到相同 project states。區間 preview 試跑：6 次 RPC、0 次失敗、128 塊 watchlist 區間；這不是全市場 128 塊所有活動。實際報告在忽略版控的 artifacts/verification/bsc-*。

確認延遲 12 塊是可調整的工程假設，不是不可逆 finality。錨點 hash 改變時停止該 stream；尚未自動重組修復，應在保留原證據後另開 recovery stream 並核對 lineage。一般 RPC 失敗不當成空資料、不推進失敗區間；已成功提交的前段仍保留。

## 資料與資源

- entity ID 使用 `eip155:56:<contract address>`，不以幣名為 key。
- stream 為 `bsc:<environment>:mint-v1`；Redis key 為 `crypto-state-radar:bsc:<environment>:v1`。
- 每個 stream 硬上限 16 MiB logical JSON characters、10,000 records、500 個 active project entries。bytecode 與 compact evidence 都會計入。這不是實體 DB 大小或免費額度承諾，跨 stream 的 DB 總量尚無全域 quota。
- 快照與原始證據不可變，不自動清除研究資料；容量不足即停止。長期連續運行前仍須實測增長及封存／保留策略，不能靠無限新建 stream 繞過上限。
- ReadRPC 只允許六種讀取方法，禁止 sign/send；每輪預設 40 秒／12 次請求、單回應 4 MiB，沒有隱藏的無限 fallback。
- 本機 worker 由使用者指定此電腦執行；採有限輪數驗收，不設開機啟動、不建立新的自動化。caught-up 時預設 30 秒，落後超過 128 塊時 5 秒；連續三次暫時失敗停止，重組／容量直接停止。

## 操作

在專案根目錄，以既有 venv 執行。正式／preview profile 位於忽略版控的 `.vercel/private/bsc-*-runtime.json`；不要印出或提交。CLI 使用最小 DB 權限寫入 BSC stream，再以受保護的 publication gateway 重播 Redis，不需要將 Redis token 取回本機。

```powershell
.venv/Scripts/python.exe tools/bsc_radar.py status --runtime .vercel/private/bsc-production-runtime.json
.venv/Scripts/python.exe tools/bsc_radar.py watch --runtime .vercel/private/bsc-production-runtime.json --root data/bsc/production --cycles 20 --interval 30 --publish
.venv/Scripts/python.exe tools/bsc_radar.py publish --runtime .vercel/private/bsc-production-runtime.json
.venv/Scripts/python.exe tools/bsc_replay.py --runtime .vercel/private/bsc-production-runtime.json --output artifacts/research/bsc-new-replay.json
```

同 root 的 watch 使用 SQLite exclusive lock 避免同機重複程序；資料庫另有 CAS。建立 `data/bsc/production/WATCH_STOP` 可在下一輪開始前停止；重新啟動前自行移除該停止檔。換 watchlist 必須使用新 stream／重播，不能宣稱新增地址已具備舊期間的完整覆蓋。

## 正式切換與回退

Preview 先使用隔離 stream/key；確認資料庫、投影、畫面、重播及 CI 後，正式設定 `CSR_PRODUCT_MODE=bsc`、`CSR_ALGORITHM_ID=bsc-discovery` 及新 key/stream/watchlist。

只暫停 `csr-production-v1-full-scan`、`csr-production-v1-drain`、`csr-production-v1-validate`。既有 GitHub workflow 沒有 cron。舊已入列 QStash 投遞由 BSC mode 回 retired，不再呼叫 Futures；手動 /api/scan 與 Binance probe 回 410。舊狀態、規則、研究及 pending validation 全部保留，但不再繼續餵新結果。

回退需恢復原部署、原 product、原 Redis key 和排程；不要將 BSC head 寫入 Futures runtime。既有 `.vercel/private/production-runtime.json` 保留原設定，新的 BSC profile 另存。未驗收的訂單執行不得隨切換啟用。

## 下一階段

1. 長期容量量測、可恢復的 retention／封存，以及 worker 掉線／停機觀測。
2. 能負擔且完整的全市場新項目來源；新增來源前先核對條件與成本。
3. 正確 proxy/beacon 解析、Agent ID 資格證據、參與人數、流動性與退款語意。
4. 新項目／訂單／持倉三種獨立狀態機，shadow、gas／失敗／賣不出評估。
5. 完成 durable intent、預算 reservation、nonce／receipt／fill 對帳後，才另行驗收交易層。

不得將這次只讀工程交付稱為完整自動交易或已驗證獲利策略。
