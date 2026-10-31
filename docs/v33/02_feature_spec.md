# 因果 Feature Store：canonical ownership

每個 feature 保存 name、version、symbol、asof_time、source_time、availability_time、value、source_ids、missing_reason；feature_id 為完整內容 hash。任何非有限數字均拒絕，缺值必須有原因。

`ContractStore.put_feature` 驗證每個 immutable raw dependency 存在於同一 namespace/run；source_time 必須等於 dependencies 最新 effective_time，不能用重新讀取時的現在時間假裝資料新鮮；feature availability 不得早於任何 dependency。asof query 同時限制 asof_time 與 availability_time，且固定 feature_version；同時間衝突修訂要求明確 pin feature_id，不任意挑選。

`snapshot` 在 evaluation 前再次拒絕 future／wrong-symbol／duplicate-name feature。`past_percentile` 僅用 asof_time 嚴格早於 evaluation，且 availability_time 不晚於 evaluation 的輸入；percentile 與 minimum samples 由呼叫者的版本化 policy 提供。

Percentile／normalization feature 另帶 `reference_source_ids`，必須是 source_ids 子集合，且每個參考 observation 的 effective_time 嚴格早於 feature asof。已知 normalization feature 名稱由契約固定，registry 可追加但不能移除必要 reference；非空值未附過去參考 lineage 時拒絕 evaluation。不能只把含當期樣本的 percentile 當作合法 scalar 塞入。

`families.aggregate` 接收 **已由上游研究 producer 計算的 component scores**，不自行發明效率／CVD／quality 公式。FamilyScore 依 available component weights 正規化；Coverage 依 configured weights 計算。VALID 才可成為 StrongFamily，score 與 coverage 分開輸出。

三個 VALID 即可算 DistributionScore；四個以上 FULL，三個 REDUCED，不足三個 null／INSUFFICIENT。這是本版布林契約，不沿用 V3.2 的四個才算分數。component／Family 權重、coverage 與 Strong 門檻只在 `experimental.json` 定義。

未實作的 producer：Efficiency、OI deceleration、CVD divergence、Lower High、support rejection、breakdown detector、DynamicQuality、ExecutionQuality、SqueezeRisk、maturity 與 cross-sectional normalization。它們可被外部離線研究 producer 提供有 lineage 的版本化輸入；缺失時不補造數字，不允許有效 Trigger。快照中的 lineage integrity 由 store 計算，忽略呼叫者自稱的 source_integrity。

OI 必須分 quantity 與 notional；Spot/Perp CVD 分市場、quote currency、window 與起點。沒有 Spot pair 就 UNAVAILABLE；Global account ratio 只能稱帳戶偏向，不能直接命名「散戶倉位增加」。日後新增公式需新增 feature_version、研究登錄與因果測試。
