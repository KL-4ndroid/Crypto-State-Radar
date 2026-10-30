# Continuous Data Archive 設計（P2；尚未啟用）

目標是保存研究能重播的 raw，而非讓每次 Dashboard 或 evaluation 重新向 Binance 查歷史。沿用已有 QStash、有界 work budget、durable backlog／lease／CAS／retry，不新增第二套正式排程。

```text
metadata/catalog + bounded acquisition jobs
  → source adapters + actual observed availability + raw payload/checksum
  → gap/revision/known-bad QA + immutable acquisition manifest
  → compressed raw archive (backend to be selected)
  → 5m aggregates + causal feature store
  → namespace/run pinned evaluation + immutable event chain
  → research projection / later executable backtest
```

## 採集工作

| 工作 | 行為 |
|---|---|
| OI/Taker/Top Account/Top Position/Global accounts | 每五分鐘保存已封閉統計；按 symbol/source/interval 排工作、保存兩種 OI 單位、ratio 與原始 payload；權限不足標示 coverage gap |
| premiumIndex | 重用全市場 batch response，保存 funding quote、Mark、Index、response time；不能視為已結算 funding 或 premium 完整歷史 |
| Funding settlement | 依合約 next funding time 增量保存；不反覆拉七天歷史，也不假設全部八小時結算 |
| Spot/Perp aggTrades | 各自持續增量 cursor；按 aggregate trade ID 去重並偵測 gap，保留 buyer-is-maker 語意和單位；quote delta 為成交價×數量帶買賣正負號，不稱資金流入／流出 |
| 官方 daily/monthly archive | checksum 與 checksum revision 驗證後補歷史／修復缺口；不可用當日事件時間假裝原始 publication time |

原始逐筆成交使用有界 buffer + 壓縮 partition／manifest；不能全部長期放 Redis、Vercel /tmp 或免費 Neon runtime table。Neon 保存索引、acquisition manifest、QA、五分鐘 aggregate 和 research records；大型 raw archive 的 backend 仍需容量／成本／帳號條件決策，本輪不建立新服務。

## 成本與覆蓋先量測

五種 per-symbol statistics 每五分鐘完整抓取，理論請求量為 `5 × N × 288`／日。以最近 522 標的為例約 **751,680 請求／日**，還不含成交、Funding 或重試。這不是現行用量；也不是可直接套用的免費方案。Binance weight 與各 endpoint/IP request quota 必須分別量測，不能只看 weight=0。

正式啟用前需驗證 worker time、max backlog delay、QStash free quota、Neon 吞吐／休眠與 raw archive bytes/day。全市場五分鐘 coverage 不能用「只採候選」冒充；容量不足時必須明列 universe、selection、gap、觀察延遲與研究偏誤，再決定持續 worker 或分批架構。現有 60 秒 function 不適合未經容量評估就承擔全市場逐筆 stream。

## 持久 cursor 與一致性

同交易寫入 manifest／事件範圍／下次 cursor；raw upload 成功但 manifest 失敗則按 content hash 重試，manifest 不存在不得提前推進 cursor。每個 job 有 lease token，過期可重取、過期 worker 不得 finish；按來源節流、429/Retry-After/backoff，不以失敗任務吃光名額。

記錄 ingested_at、observed availability、interval start/end、source_revision/checksum、row count、首末 trade ID、known-bad interval、採集 universe 與失敗原因。新修訂 append 新 manifest，舊 raw／feature／evaluation 不刪改。retention／封存需先提出 bytes/day、容量上限與 restore 驗證，不自動清除研究歷史。

本輪交付來源契約、schema、因果 feature store 與 replay；上述 continuous acquisition、raw object backend、CVD builder、gap repair worker、歷史 event dataset 均尚未連接或啟用。

後續進度（2026-10-31）：有界本機採集、可恢復游標、原始保存與完整窗口 CVD 的實作／限制見 [採集交付](08_acquisition_delivery.md)。本檔保留為初始設計；正式 continuous acquisition 與遠端 raw backend 仍未啟用。
