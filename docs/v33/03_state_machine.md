# FSM、Gate 與 Journal：canonical ownership

`src/v33/fsm.py` 有獨立 Market Phase 與 Trade State；矩陣是工程契約，條件讀取 registry parameters。每次至多走一條 Market Phase edge；Trade State 可以在同 evaluation 逐級走 SETUP→READY→TRIGGERED，沒有跳級捷徑。phase event 先寫，trade events 依 sequence_no 排序。

每個 trade event 具有 chain_id、parent_event_id、evaluation_key、symbol、rule_version、state_version。READY parent 必須是同 namespace/run/symbol/rule/chain 的 SETUP；TRIGGERED parent 必須是 READY。父 event 已在 journal 或同交易中較早產生，不能指向未來。phase/trade coupling 按主規格第 14 節逐組驗證。

ActiveBlocks 是唯一 Gate 真值。DisplayGate 取 DATA→LIQUIDITY→SQUEEZE→PRICE_CONFIRM 優先序；Executable 嚴格等於 blocks 空集合。**Executable 只是 Gate 通過；真正訊號需 signal_eligible = SHORT_TRIGGERED 且 Executable。** 避免把 WATCH、研究結果或 OPEN 直接當成交指令。

PRICE_CONFIRM 嚴格在 SHORT_READY 且 phase 非 MARKDOWN 時加入。這也揭露主規格的命名限制：WATCH／terminal 在其他 blocks 全空時仍可能 Executable=true，因為指定公式未納入 trade state；本輪保留原公式並另外提供 signal_eligible，前端應將前者標為「Gate 通過」，不能逕稱「可下單」。

多次讀取同一 closed-bar observation 不會增加 confirmation 或解除 squeeze 計數；確認 bar 必須連續、不在未來。條件未知不算成立。Squeeze 使用不同 observation 解除，保持 hysteresis。設定集中於 experimental registry。

TTL 於首次 SETUP 固定，不因 READY 重估而延長。主規格未指定 TTL 起點和數字，本輪明確採 SETUP 起點及 reference experimental TTL；必須另行研究。終結優先序為 INVALIDATED→EXPIRED→promotion；terminal absorbing，不能對同一 chain 再追加另一個 terminal。新的獨立 setup 暫以新的研究 run 啟動；不自行發明 MARKDOWN reset／冷卻政策。

## 必須揭露的規格解讀

1. **I9 與 terminal 衝突**：INSUFFICIENT⇒WATCH 不能同時覆蓋已持久化的 INVALIDATED／EXPIRED。本輪 I9 限制非 terminal 狀態；活躍 chain 證據不足會新增 INVALIDATED，保留 terminal 而不擦除歷史。此為保守工程補充，未宣稱是經驗最佳政策。
2. **Reduced breadth 退化**：禁止新推進或維持不符合 breadth policy 的 READY／TRIGGERED；已有 READY 失去 breadth qualification 會 INVALIDATED。Market Phase 保留最後已觀察到的 phase，不新增規格未定義的倒退 edge；Reduced cap 約束新推進。之後需要決定獨立 phase freshness／UNKNOWN 語意。
3. **尚無完整 recovery/reset 規則**：DELEVERAGING／MARKDOWN 的恢復、新 cycle、失效結構、TTL 起點均沒有完整定義；本輪不自動重開、不猜測交易退出，終結是訊號資格終結，不是實際持倉已平倉。
4. **quality／結構公式缺失**：本輪是能消費 causal predicates 的 framework；沒有把 boolean fixture 當作真實市場演算法完成。觀察資料未接上時，DATA/PRICE_CONFIRM 阻擋有效訊號。

## 原子性與修訂

`ContractStore` 沿用 ResearchJournal 的 SQLite BEGIN IMMEDIATE／PG transaction 與 timeout。評估僅鎖定自己的 V3 head，避免研究批次阻塞正式 scanner 的 runtime row；registry 初次登錄沿用短暫 ledger lock 防止同版本並行衝突。evaluation result、events、V3 head 同交易提交；沒有推進 V2 runtime revision 或發布 Redis。

evaluation_key 為規格指定的 symbol/time/rule_version/feature_snapshot_hash hash；DB uniqueness 加 namespace/run。全域 evaluation_id 再 hash namespace/run/key。相同 key 回傳原結果；不同 input_hash 卻同 key 視為 identity conflict。rule_version 在所有 namespace/run 共用 immutable registry，不能在另一個研究 run 偷改同名規則參數。

修訂使用新 feature hash、新 evaluation_id、data_revision 及 supersedes_evaluation_id；只能在 REPLAY/BACKTEST 的獨立 run 重播，原 run 不倒退時鐘。supersedes 必須指向相同 symbol/time，並不代表重新計算了所有後續 state；完整修訂需從更動前重播整個研究 run。規則行為、特徵公式、狀態 schema 改動均需 bump 對應版本。
