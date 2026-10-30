# 資料契約：canonical ownership

機器定義：`src/v33/sources.py`；不可變觀察：`RawObservation`；時間單位轉換：`causal.epoch_time`。來源 metadata 提供 endpoint、auth、retention、預期更新、timestamp semantics、缺值處理與 contract version，**不代表 collector 已上線**。

每筆 raw 保存 source、symbol、原始 timestamp、effective_time、availability_time、ingested_at、source_revision、payload、data_version、source_integrity、availability_basis。未知 effective_time 的原始資料可以留作 QA，但不能產生可用 feature。bad data 不能生成非空 feature。

已核對的差異：

- OI 統計、Top Trader Account／Position timestamp 為區間結束；Taker timestamp 為區間開始，需加上查詢 period，且收到未結束區間不得視為已封閉。
- Top Trader **Account 與 Position** 官方文件均要求 X-MBX-APIKEY；不保存 API key 至 observation／journal，後續使用僅讀設定，不申請交易權限。
- OI 最近一個月，Taker／Top Trader 約 30 天；Funding settlement 不假設同樣 30 天上限。預期／最近 funding quote 不等於結算值，資金費週期依合約定義。
- premiumIndex 一次回應可以保存 Mark、Index 及 funding quote；premium 歷史另有 K 線。不得把同一筆 response 分成三次相同上游查詢。
- Spot public archive 自 2025-01-01 使用微秒，Futures archive 使用毫秒；必須顯式指定來源／單位，禁止猜測數值長度。
- Smart Signal 仍為 optional unavailable；Top Trader 的比率不能冒充 Smart Money 名義部位。

availability_time 是本系統確知已可取得的時間。歷史下載的 event time、檔名日期和 candle close **不能自動當成原始 publication time**。OBSERVED 以實際收集時間記錄；ARCHIVE_PUBLISHED 需要可追溯發布證據；ASSUMED 必須保留標記、不可進 production，並會阻擋交易 Gate。這是保守 bootstrap，並不聲稱還原了歷史交易條件。

歷史修訂新增 raw／feature；原 acquisition 不改寫。後續 archive manifest 應按 checksum 去重重試；目前 raw observation 也保存 acquisition 時間，因此不同取得批次可能有不同 record_id，不等於新增交易訊號。

官方來源（查核 2026-10-30）：

- [Binance USDⓈ-M Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
- [Binance Public Data](https://github.com/binance/binance-public-data)
- [Smart Signal](https://www.binance.com/en/support/faq/detail/197ed63fbca34124bae53d8e6d4c9d29)

2026-10-31 source contract v2：新增本機 pilot adapter 與 derived window metadata，Futures aggTrades REST 確認限最近 48h；Taker 的有界 query overlap 與快照 clock lead 處理／證據見 [採集交付](08_acquisition_delivery.md)。來源版本更新不代表已啟用正式採集。
