# V3.3 完整版進度與驗收

2026-10-31：使用者要求完成整套 V3.3；本文件記錄後續實作，不能將研究成果版 v3.3.0 當作完整交易模型。

## 成交研究引擎（已實作，尚無真實資料成效證據）

`src/v33/backtest.py` 與 `tools/v33_backtest.py` 提供離線 short 成交研究：

- 使用 evaluation 之後的下一根 bar open，ATR 必須在 evaluation 時已可得。
- Bootstrap stop / target / horizon、雙邊 fee 與 adverse slippage 由明確 policy 提供；預設仍是 NOT_VALIDATED。
- 同根 stop / target 雙穿越採 stop-first；不利跳空按更差 open 成交，有利跳空不額外給予收益。
- 連續完整 bar path、明確 funding coverage、逐 settlement rate × mark / entry，正 funding 支付給 short。退出 bar 內 funding 先後不明時回傳 FUNDING_AMBIGUOUS，不假設有利時序。
- PENDING、DATA_GAP、FUNDING_GAP、FUNDING_AMBIGUOUS、CAPACITY_BLOCKED、BLOCKED、NO_ENTRY 不當作已成交虧損樣本。報告完整列出 status counts；不能將已成交子群 Precision 當成全市場 Recall。
- Capacity 使用事後 bar quote volume × max participation，明確標示 EX_POST_BAR_VOLUME，並非 order-book fill 或即時 liquidity 驗證。
- MAE/MFE 為完整退出 bar 的界限估計，不宣稱成交前的精確 intrabar excursion。
- 時序 train / validation / test 分割 purge 跨界 label，支援 embargo；尚未包含 nested calibration、特徵選擇或 ablation runner。
- Day/regime cluster bootstrap 輸出 EV / profitable-trade precision 與 95% CI。effective_n 為提供的 cluster 數，並非估算出的真正獨立樣本數；不足兩群時不產生 CI。
- CLI 輸入相同則輸出 bytes 相同；input hash 包含完整 replay document；輸出不得覆寫既有檔案，沒有網路或 production writes。

輸入 JSON 包含 `policy`、`observed_until`、`cases`。每個 case 包含 `signal`、`bars`、`funding`、`cluster`。

Signal 必填：`symbol`、`signal_id`、`evaluation_time`、`atr`、`atr_available_at`、`notional`、`executable`、`funding_covered_from`、`funding_covered_until`。Funding coverage 必須由外部資料 QA 證明，不可用空陣列推定完整。

Bar 必填：`symbol`、`open_time`、`close_time`、`open`、`high`、`low`、`close`、`quote_volume`。Funding row 必填：`symbol`、`time`、`rate`、`mark_price`。Cluster 應由研究資料集提供，例如 `2026-10-30/BTC_DOWN`。

```powershell
.venv/Scripts/python.exe tools/v33_backtest.py --input <cases.json> --output <new-report.json>
.venv/Scripts/python.exe -m pytest tests/test_v33_backtest.py tests/test_v33_backtest_cli.py -q
```

## 尚待完成的完整範圍

1. 遠端 raw backend、容量與權限、checksum 還原，以及既有 scheduler 的有界持續採集整合。使用者已選定沿用既有服務與免費額度；小型 journal archive adapter 已完成（下節），尚未在雲端配置或啟用持續採集。
2. 具有版本／lineage 的研究 feature producers 與 explicit experimental formulas、跨來源時間對齊、新上市／下市 universe 與完整 event dataset。
3. 基於真實完整資料的 nested walk-forward、ablation、Champion vs Challenger 比較與不確定性報告。
4. V3 Dashboard 與 SHADOW 路徑端到端驗收。
5. 主規格 P8–P10 的持續 shadow、execution validation、guarded release。時間窗口尚未累積、模型未獲驗證時保持未通過。

目前本機 data 只有 pilot 的 9 個 gzip raw objects 與一個 SQLite journal，不足以證明代表性歷史、持續完整性或 OOS 優勢。合成測試驗證的是工程行為，不是研究成效。此開發分支沒有修改或部署正式 runtime。

## 免費額度方向：有界 journal archive

`src/v33/journal_archive.py` 沿用既有 ResearchJournal / `csr_records`，不新增服務或 schema；SQLite 用於本機，PostgreSQL 可用於 durable cloud storage。Vercel 上拒絕 SQLite。此 adapter 僅供小範圍研究，不能把大量逐筆 archive 長期存入免費 Neon。

- 固定全域 archive kind，跨 run 共用預算。預設至多 **16 MiB logical encoded payload、10,000 objects、256 KiB uncompressed/object**；呼叫端可以調低，不能調高硬上限。
- gzip + base64、SHA-256 content identity、去重、bounded decompression、checksum restore；傳回 metadata 不含 raw body。
- SQLite transaction / PostgreSQL advisory transaction lock 序列化 check + insert，不鎖正式 runtime row。不刪除資料、不自動增加 quota、不修改模型或 Redis publication。
- quota 為 payload 字元數（ASCII encoded payload），不是實體 DB size、索引、WAL、運算或供應商帳單。上線前仍須量測現有資料庫實際用量與可用免費額度；此上限不是免費保證。
- acquisition worker 偵測已滿時不呼叫來源；upload 發生 OSError 時保留失敗證據與 cursor，停止本輪後續來源，避免容量耗盡後繼續抓取。
- `tools/v33_acquire.py --archive-backend journal` 可在明確本機 journal 測試此 backend；預設 file 行為保留。這個 CLI 尚未連到雲端 credentials，且不啟動排程。

真正 disposable PostgreSQL 驗收：**1 passed、5 deselected**。最小權限 runtime role 能寫入、跨 connection restore、並行最多存一個 object、滿額去重仍可重用、immutable history；鎖定正式 runtime row 不阻塞 archive。驗收報告：忽略版控的 `artifacts/verification/v33-journal-archive-storage.json`。這是本機真 PG，不是假稱已驗證 Neon 或正式部署。
