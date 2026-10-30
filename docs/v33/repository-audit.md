# V3.3 修改前盤點

基準：main `51a8b67710d5e38789e231d5fc7e1bdc3b709300`；2026-10-30。新工作由 main 建立獨立分支，不把 PR #19 的 demand 排程變更混入本次。

| 分類 | 盤點結論 |
|---|---|
| 目前已具備 | `run_cycle` 全市場觀察→有界 enrichment→CAS 提交；V2 lifecycle；OI、Funding、K 線；Neon journal／validation lease；Redis 原子 read view；QStash signed scheduler；現行模型 replay、事件配對與匯出 |
| 目前缺失 | V3.3 rule registry、研究命名空間、已知可得時間、feature lineage／revision、雙 FSM、Family coverage、完整 Gate、V3.3 property tests；長期逐筆成交／五分鐘統計 archive |
| 需要修改 | 新增隔離 V3.3 library、加法 schema、離線 replay 工具及 dev-only property tests；後續 ingestion adapter 必須保存 quantity OI 與 notional OI、真正觀察時間和來源修訂 |
| 可以沿用 | `ResearchJournal.sql/transaction/_insert`、SQLite/PG adapter、immutable `csr_records`、UTC helper、現有測試服務、歷史蒐集與事件評估工具的基礎；Dashboard 的簡潔主資訊／展開細節方式 |
| 應淘汰 | 「V2 壓力分數代表 V3 派發」、「用事件時間當歷史可得時間」、「Top Trader 等於 Smart Money／散戶」、「Binance OI 下降等於全球去槓桿」等解讀；不刪除 V2 程式或歷史 |

## 十項現況

1. **Scanner**：`src/run_cycle.py`、`scan_plan.py`；每輪 deep 25、續跑 100，有 backlog、重試和觀測紀錄。沿用容量管理，不因新規格再建第二套正式 scanner。
2. **Lifecycle**：`state_machine.py` 的 V2 WATCH／多空壓力／READY／TRIGGERED 等屬單軸狀態；不直接改名成 V3 雙軸。V3 新建隔離模組。
3. **API**：`live_collector` 查 exchangeInfo、ticker、premiumIndex、OI history；`live_features` 查 K 線／Funding。尚未接 V3 Taker、Top Trader、逐筆 CVD。
4. **DB**：正式運作沿用 `schema/runtime_ledger.sql` 的 runtime／immutable records／validation jobs；`research_schema.sql` 是既有正規化研究方向，不能誤認為 V3 schema 已部署。
5. **排程**：main QStash full scan 每小時 07 分，drain／validate 每五分鐘；GitHub workflow 只保留手動。PR #19 的 demand 模式仍未合併，本輪不改它。
6. **Dashboard**：現有 manifest／ETag、Redis projection、重要資訊優先和細節展開；尚無真實 V3 結果。新增研究 projection helper，不在正式畫面捏造 V3 值。
7. **Journal**：已有 durable append-only records、CAS、operation receipt。V3 沿用交易底層，新增 namespace/run-scoped evaluation／event／head，與 V2 signals 分離。
8. **Vercel**：main 是 Python functions，sin1、scheduler/scan/research 60 秒。已只讀確認正式 manifest HTTP 200、scan_status=ok。沒有重新部署或調整環境。
9. **OI/Funding/Taker**：目前 OI 變化使用 `sumOpenInterestValue`，會混入價格效應，不能直接當數量減倉。Funding 同時有 current quote 與 settlement history，需區分。Taker 新源尚未連接。
10. **歷史**：已有觀察、模型 features、signals、outcomes 與有界歷史 capture/replay；不是持續保存全市場五分鐘原始統計與 Spot/Perp aggTrades。既有時間重建不證明當時已可取得修訂後資料。

本輪只驗證工程契約；不把以上既有資產或新骨架當成 V3 模型已有 OOS 增益。
