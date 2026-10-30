# V3.3 Distribution Intelligence
## Internal-Consistency & Research-Ready Specification
### 暴漲後派發、撤離、去槓桿與可執行 Short 訊號系統

> **版本：V3.3**
>
> **日期：2026-10-30**
>
> **文件定位：Engineering-Ready / Model-Pre-Validation**
>
> 這份文件可以用來開始實作：
>
> - 資料管線
> - Feature Store
> - 狀態機
> - Journal
> - Backtest
> - Property-based tests
> - Champion / Challenger 研究流程
>
> 但**不能被解讀為「交易模型已經被驗證有效」**。
>
> 已驗證的是：
>
> - Binance 主要資料來源的可得性與已知限制
> - 規格內部布林一致性
> - 狀態機與 Gate 的工程語意
> - 回測與資料治理方法
>
> 尚未驗證的是：
>
> - 哪些特徵真正有 out-of-sample 預測力
> - 最佳權重
> - 最佳 percentile
> - 最佳 Score 門檻
> - 最佳 TTL
> - 最佳 Stop / Target
> - 真實 Executable Precision
>
> 優先級：
>
> ```text
> Executable Expected Value
> > Executable Precision
> > Calibration
> > Recall
> > Signal Frequency
> ```

---

# 1. 本輪 V3.2 文件審核結論

針對 V3.2 評估文件逐項檢查後：

| 評估項目 | 驗證結果 | V3.3 處理 |
|---|---|---|
| V3.2 的 Pre-Validation 定位正確 | ✅ | 保留 |
| Static Coverage / Maturity / Dynamic Quality 拆分正確 | ✅ | 保留 |
| Family Score + Coverage 正規化正確 | ✅ | 保留並補輸出語意 |
| Pump anchor causal 化正確 | ✅ | 保留 |
| Cross-exchange scope 標籤正確 | ✅ | 保留 |
| Property-based test 有必要 | ✅ | 強化 |
| Barrier bootstrap + nested calibration 正確 | ✅ | 保留 |
| `>=4 valid family` 與 `>=3 strong family` 有矛盾 | ✅ **Blocker** | 正式消除 |
| FamilyScore 高但 VALID=false 容易誤讀 | ✅ | 補 FamilyStatus |
| DELEVERAGING 與 SHORT_TRIGGERED 耦合不完整 | ✅ | 補合法狀態矩陣 |
| EntryGate display / authoritative 語意不清 | ✅ | 正式拆成 ActiveBlocks + DisplayGate |
| SETUP→READY→TRIGGERED 可跳級 | ✅ | 強制逐級 journal |
| `>=4 family` 可能系統性排除新幣 | ✅ | 加 Reduced-Breadth / New Listing policy |
| append-only 與重跑 deterministic 有張力 | ✅ | 補 idempotency / revision protocol |
| 88 節單文件可維護性下降 | ✅ | 本版採模組化章節與 canonical ownership |
| P0 規格先於 P4 研究看似矛盾 | ⚠️ 語意問題 | 區分「contract skeleton」與「model rule promotion」 |

---

# 2. 外部資料可得性驗證

## 2.1 Binance Public Data

官方 Binance Public Data 提供長期：

- Spot `aggTrades`
- Spot `trades`
- Spot `klines`
- USDⓈ-M Futures `aggTrades`
- USDⓈ-M Futures `trades`
- USDⓈ-M Futures `klines`

因此：

```text
Spot CVD
Perp CVD
```

可以從歷史成交資料自行重建。

官方來源：

https://github.com/binance/binance-public-data

---

## 2.2 Open Interest / Taker / Top Trader

Binance USDⓈ-M 官方 Market Data 文件目前對多個 `/futures/data/*` 統計 endpoint 明確標示：

```text
Only the data of the latest 30 days is available.
```

包括：

- Open Interest Statistics
- Taker Buy/Sell Volume
- Top Trader ratios

因此：

> 長期研究不能只依賴 REST backfill，production 必須自行持續保存。

---

## 2.3 Top Trader Position Ratio

目前官方文件對：

```text
/futures/data/topLongShortPositionRatio
```

標示：

```text
X-MBX-APIKEY required
```

因此 data contract 必須明確記錄：

```text
auth_required = true
```

---

## 2.4 Funding Rate History

官方：

```text
GET /fapi/v1/fundingRate
```

支援：

- `startTime`
- `endTime`
- `limit <= 1000`

目前官方文件沒有像 OI / Taker 那樣標示「僅最近 30 天」。

---

## 2.5 Smart Signal

Binance Futures Smart Signal 官方產品提供：

- Smart Money
- Whale
- dominant flow
- net volume
- position notional
- Avg Entry
- Unrealized PnL
- trader count

但截至本版驗證：

> 未確認有等價、穩定承諾的公開 Developer API。

因此仍屬：

```text
OPTIONAL INTELLIGENCE
```

不能進 Core Trigger 的必要條件。

---

# 3. V3.3 最重要修正：Score Availability 與 Evidence Breadth 分離

V3.2 的矛盾來源是把兩件事混在一起：

1. **能不能計算 DistributionScore**
2. **有沒有足夠多的證據 Family 支撐交易**

V3.3 正式分離。

---

# 4. Family Validity

每個 Family 都輸出：

```text
FamilyScore      0–100 or null
FamilyCoverage   0–1
FamilyStatus     VALID / PARTIAL / UNAVAILABLE
```

定義：

```text
Coverage >= 0.50
=> VALID
```

```text
0 < Coverage < 0.50
=> PARTIAL
```

```text
Coverage == 0
=> UNAVAILABLE
```

重要：

> `PARTIAL` 的 FamilyScore 可以顯示，但**不能當成 strong evidence family**。

---

# 5. DistributionScore 可計算條件

只要：

```text
>= 3 VALID Families
```

即可計算：

```text
DistributionScore
```

使用 available valid family weights 重新正規化。

---

# 6. Score Breadth

新增：

```text
ScoreBreadth
```

分為：

## FULL

```text
>=4 VALID Families
```

## REDUCED

```text
exactly 3 VALID Families
```

## INSUFFICIENT

```text
<3 VALID Families
```

---

## INSUFFICIENT

```text
DistributionScore = null
TradeState <= WATCH
```

---

# 7. Strong Evidence Family

定義：

```text
FamilyStatus == VALID
AND
FamilyScore >= 60
```

才算：

```text
StrongFamily
```

因此：

```text
FamilyScore = 82
FamilyCoverage = 0.35
FamilyStatus = PARTIAL
```

**不能**被算成 strong evidence。

UI 必須顯示：

```text
F3 = 82 (PARTIAL, NOT COUNTED)
```

避免誤讀。

---

# 8. 新幣與 Reduced Breadth Policy

V3.3 不再用「>=4 valid family」把所有 3-family symbol 直接封死。

---

## Mature / Transitional Symbol

若：

```text
ScoreBreadth = REDUCED
```

則最多：

```text
DISTRIBUTION_SUSPECTED
SHORT_SETUP
```

不得：

```text
SHORT_READY
SHORT_TRIGGERED
```

---

## New Listing Exception

若：

```text
ModelMaturity = NEW_LISTING
```

且只有 3 VALID Families，

允許進一步評估，但三個 Family 必須是：

```text
F1 Price
F2 Leverage
F3 Flow
```

三者全部：

```text
VALID
AND
Score >= 70
```

再要求：

```text
DynamicQuality >= 85
ExecutionQuality >= 70
```

才可進：

```text
SHORT_READY
```

以及：

```text
Phase = MARKDOWN
ActiveBlocks = empty
```

才可：

```text
SHORT_TRIGGERED
```

這是一個：

```text
HYPOTHESIS POLICY DEFAULT
```

必須在 New Listing 專用回測中驗證。

---

# 9. Family Score 通用公式

```text
FamilyScore =
Σ(component_score_i * component_weight_i)
/
Σ(available_component_weight_i)
```

---

## FamilyCoverage

```text
FamilyCoverage =
Σ(available_component_weight_i)
/
Σ(configured_component_weight_i)
```

---

# 10. Family 組成

## F1 Price Structure

初始 Hypothesis：

| Component | Weight |
|---|---:|
| Price Efficiency Collapse | 35 |
| Lower High | 30 |
| VWAP / Pump Support Rejection | 20 |
| Return Deceleration | 15 |

---

## F2 Leverage

| Component | Weight |
|---|---:|
| OI Deceleration | 30 |
| OI 15m Retreat | 30 |
| OI 1h Retreat | 25 |
| OI 4h Retreat | 15 |

---

## F3 Flow

| Component | Weight |
|---|---:|
| Perp CVD Reversal | 40 |
| Taker Buy/Sell Reversal | 30 |
| Spot CVD Divergence | 30 |

無 Spot pair 時：

```text
Spot CVD = unavailable
```

重新正規化 available component。

---

## F4 Positioning / Crowding

| Component | Weight |
|---|---:|
| Funding Percentile | 35 |
| Top Trader Position Long Reduction | 35 |
| Retail Long Increase | 15 |
| Basis / Premium Crowding | 15 |

---

## F5 Market Context

| Component | Weight |
|---|---:|
| BTC Regime | 50 |
| Alt Breadth | 50 |

---

# 11. DistributionScore

初始 Family Weight：

```text
F1 25
F2 25
F3 25
F4 15
F5 10
```

僅對：

```text
VALID Family
```

重新正規化。

所有權重仍為：

```text
HYPOTHESIS DEFAULT
```

---

# 12. Market Phase FSM

Market Phase：

```text
NORMAL
PUMP
LEVERAGE_EXPANSION
SATURATION
DISTRIBUTION_SUSPECTED
DELEVERAGING
MARKDOWN
```

---

# 13. Trade State FSM

Trade State：

```text
WATCH
SHORT_SETUP
SHORT_READY
SHORT_TRIGGERED
INVALIDATED
EXPIRED
```

---

# 14. Market Phase 與 Trade State 合法組合

V3.3 正式加入 coupling matrix。

| Market Phase | WATCH | SHORT_SETUP | SHORT_READY | SHORT_TRIGGERED |
|---|---:|---:|---:|---:|
| NORMAL | ✅ | ❌ | ❌ | ❌ |
| PUMP | ✅ | ❌ | ❌ | ❌ |
| LEVERAGE_EXPANSION | ✅ | ❌ | ❌ | ❌ |
| SATURATION | ✅ | ❌ | ❌ | ❌ |
| DISTRIBUTION_SUSPECTED | ✅ | ✅ | ❌ | ❌ |
| DELEVERAGING | ✅ | ✅ | ✅ | ❌ |
| MARKDOWN | ✅ | ✅ | ✅ | ✅ |

Terminal state：

```text
INVALIDATED / EXPIRED
```

不受上表限制，但必須 reference active signal chain。

---

# 15. 核心結論

```text
SHORT_SETUP
=> Phase ∈ {DISTRIBUTION_SUSPECTED, DELEVERAGING, MARKDOWN}
```

```text
SHORT_READY
=> Phase ∈ {DELEVERAGING, MARKDOWN}
```

```text
SHORT_TRIGGERED
=> Phase == MARKDOWN
```

因此：

> SHORT_TRIGGERED 不可能在 SATURATION 直接發生。

---

# 16. Phase Transition

## NORMAL → PUMP

初始 Hypothesis：

```text
24h Return >= trailing 30d P95
OR
trailing 7D Return >= +50%
```

且：

```text
24h quote volume >= trailing 30d median * 2
```

連續：

```text
2 x 5m evaluation
```

---

## PUMP → NORMAL

PUMP condition false：

```text
3 x 15m
```

且沒有進入後續 phase。

---

## PUMP → LEVERAGE_EXPANSION

```text
OI_1h > 0
```

且：

```text
OI_1h >= past-only P75
OR
OI_4h >= past-only P75
```

連續：

```text
2 x 5m
```

---

## LEVERAGE_EXPANSION → SATURATION

```text
Price Efficiency Collapse Score >=75
```

連續：

```text
2 x 15m
```

且 OI 尚未全面轉負。

---

## SATURATION → LEVERAGE_EXPANSION

```text
Efficiency recovery
+
OI acceleration positive
+
Flow positive
```

連續：

```text
2 x 15m
```

---

## SATURATION → DISTRIBUTION_SUSPECTED

```text
DistributionScore >=65
>=3 StrongFamilies
F1 >=50 and VALID
```

---

## DISTRIBUTION_SUSPECTED → LEVERAGE_EXPANSION

若：

```text
DistributionScore <50
```

連續：

```text
2 x 15m
```

且：

```text
Price resumes new high
+
OI acceleration positive
+
Flow positive
```

則回復。

---

## DISTRIBUTION_SUSPECTED → DELEVERAGING

```text
OI_15m retreat confirmed
```

且：

```text
F2 >=60
```

再至少一項：

```text
OI_1h <0
Perp CVD <0
Taker Ratio <0.95
```

---

## DELEVERAGING → MARKDOWN

```text
Price Breakdown Confirmed
```

且：

```text
F1 >=60
F2 >=60
```

---

# 17. Price Breakdown Confirmed

初始 Hypothesis：

```text
5m close
<
recent 30m swing low
-
0.1 * ATR_15m
```

必須使用：

```text
past-only swing low
```

不得事後選低點。

---

# 18. Trade State Transition

## WATCH → SHORT_SETUP

必須：

```text
Phase ∈ {DISTRIBUTION_SUSPECTED, DELEVERAGING, MARKDOWN}
```

以及：

```text
DistributionScore >=70
>=3 StrongFamilies
OI_15m retreat confirmed
```

再至少一項：

```text
Perp CVD <0
Spot CVD <0 if available
Taker <0.95
Lower High candidate
```

---

# 19. SHORT_SETUP → SHORT_READY

必須：

```text
active SHORT_SETUP exists
```

且：

```text
Phase ∈ {DELEVERAGING, MARKDOWN}
DistributionScore >=75
DynamicQuality >=60
ExecutionQuality >=50
>=3 StrongFamilies
```

以及：

```text
Lower High confirmed
OR
VWAP / pump support rejection confirmed
```

Breadth policy 必須通過。

---

# 20. SHORT_READY → SHORT_TRIGGERED

必須：

```text
active SHORT_READY exists
```

以及：

```text
Phase == MARKDOWN
DynamicQuality >=75
ExecutionQuality >=60
```

且至少：

```text
OI_15m <0
```

再至少一個：

```text
OI_1h <0
Perp CVD <0
Spot CVD <0
Taker <0.90
```

最後：

```text
ActiveBlocks = empty
```

才：

```text
SHORT_TRIGGERED
```

---

# 21. 禁止狀態跳級

Trade State 必須遵守：

```text
WATCH
→ SHORT_SETUP
→ SHORT_READY
→ SHORT_TRIGGERED
```

不得：

```text
WATCH -> SHORT_READY
WATCH -> SHORT_TRIGGERED
SHORT_SETUP -> SHORT_TRIGGERED
```

---

# 22. 同一 evaluation 多級成立

如果同一個 evaluation 同時滿足：

```text
SETUP
READY
TRIGGERED
```

Journal 必須依序 append：

```text
1. SHORT_SETUP
2. SHORT_READY
3. SHORT_TRIGGERED
```

三個 event 可以：

```text
evaluation_id 相同
```

但：

```text
sequence_no = 1 / 2 / 3
```

且 parent reference 必須完整。

---

# 23. Entry Gate：Authoritative 定義

V3.2 的 EntryGate 同時被當成 display 與 state。

V3.3 正式拆開：

```text
ActiveBlocks[]
DisplayGate
Executable
```

---

# 24. ActiveBlocks 是唯一 authoritative source

可能值：

```text
DATA
LIQUIDITY
SQUEEZE
PRICE_CONFIRM
```

---

# 25. Executable

唯一公式：

```text
Executable = (len(ActiveBlocks) == 0)
```

---

# 26. DisplayGate

只是 UI 顯示。

precedence：

```text
DATA
>
LIQUIDITY
>
SQUEEZE
>
PRICE_CONFIRM
>
OPEN
```

---

## OPEN

只有：

```text
ActiveBlocks = []
```

才：

```text
DisplayGate = OPEN
```

因此不存在：

> UI 顯示 OPEN，但其實還有 block。

---

# 27. Block 規則

## DATA

任一：

```text
DynamicQuality < required threshold
Fast Core drift >180s
required Core field unavailable
SourceIntegrity = 0
unknown effective_time semantics
```

---

## LIQUIDITY

```text
ExecutionQuality < required threshold
```

---

## SQUEEZE

```text
SqueezeRisk >=70
```

解除：

```text
SqueezeRisk <=50
```

連續至少 2 個 observation。

---

## PRICE_CONFIRM

當：

```text
SHORT_READY
```

但：

```text
Phase != MARKDOWN
```

時 active。

---

# 28. Property-based invariant 更新

## I1

```text
SHORT_TRIGGERED
=> active/referenced SHORT_READY exists
```

## I2

```text
SHORT_READY
=> active/referenced SHORT_SETUP exists
```

## I3

```text
SHORT_TRIGGERED
=> Phase == MARKDOWN
```

## I4

```text
SHORT_READY
=> Phase in {DELEVERAGING, MARKDOWN}
```

## I5

```text
SHORT_SETUP
=> Phase in {DISTRIBUTION_SUSPECTED, DELEVERAGING, MARKDOWN}
```

## I6

```text
SHORT_TRIGGERED
=> ActiveBlocks == []
```

## I7

```text
DisplayGate == OPEN
<=> ActiveBlocks == []
```

## I8

```text
FamilyStatus != VALID
=> family cannot count as StrongFamily
```

## I9

```text
ScoreBreadth == INSUFFICIENT
=> TradeState == WATCH
```

## I10

Mature / Transitional：

```text
SHORT_READY
=> ScoreBreadth == FULL
```

## I11

New Listing Reduced-Breadth exception：

```text
SHORT_READY
=> F1,F2,F3 VALID
AND F1,F2,F3 >=70
AND DynamicQuality >=85
AND ExecutionQuality >=70
```

## I12

```text
SHORT_TRIGGERED
=> SHORT_SETUP and SHORT_READY events precede it in journal order
```

## I13

同一 active signal：

```text
INVALIDATED XOR EXPIRED
```

terminal event 只允許一個。

## I14

```text
feature.availability_time <= evaluation_time
```

## I15

```text
percentile training observations < evaluation_time
```

## I16

相同：

```text
evaluation_key
```

不得 append duplicate events。

---

# 29. Deterministic + Append-only 的重跑規格

V3.2 P11 / P12 的張力由 idempotency 解決。

---

# 30. Evaluation Key

```text
evaluation_key =
hash(
  symbol,
  evaluation_time,
  rule_version,
  feature_snapshot_hash
)
```

建立 DB UNIQUE constraint：

```text
UNIQUE(evaluation_key, event_type, sequence_no)
```

---

# 31. Same-input rerun

相同：

```text
evaluation_key
```

重跑：

```text
不得新增 duplicate event
```

應回傳原 evaluation result。

---

# 32. Data revision

如果歷史資料後來更新：

產生新的：

```text
feature_snapshot_hash
data_revision
evaluation_id
```

並：

```text
supersedes_evaluation_id = old_id
```

舊 journal：

```text
不刪
不改
```

---

# 33. Rule revision

若規則版本改變：

```text
rule_version = new
```

同一歷史時間重跑視為：

```text
research evaluation
```

不得混入原 production journal。

建議：

```text
journal_namespace =
PRODUCTION
REPLAY
BACKTEST
```

分離。

---

# 34. Pump Anchor causal 規格

```text
pump_anchor_time =
first evaluation timestamp where NORMAL -> PUMP
```

不能：

- 事後回推最低點
- 用完整 future pump path 定義 anchor
- 用 future max return 定義 phase 起點

---

# 35. Trailing 7D Return

在 evaluation time `t`：

```text
Return(t-7d, t)
```

已知。

因此：

```text
不是 look-ahead
```

---

# 36. Dynamic Quality

繼續拆成：

```text
Freshness
Alignment
Completeness
SourceIntegrity
```

初始：

```text
0.30 / 0.25 / 0.30 / 0.15
```

仍是：

```text
HYPOTHESIS DEFAULT
```

---

# 37. Effective Time Contract

每個 source 都要有：

```text
raw_timestamp
effective_time
availability_time
ingested_at
source_revision
```

Backtest 只能看到：

```text
availability_time <= simulation_time
```

---

# 38. Binance-local / Global 語意

Core 系統只看 Binance 時：

```text
DeleveragingScope = BINANCE_LOCAL
```

禁止描述：

```text
全市場主力撤離
全球去槓桿
```

---

## Optional Cross-exchange

若正規化多交易所：

```text
GLOBAL_CONFIRMED
```

才允許：

```text
跨交易所同步退潮
```

若：

```text
Binance OI ↓
other exchanges OI ↑
```

標：

```text
POSSIBLE_POSITION_MIGRATION
```

---

# 39. Squeeze Risk 與 Distribution 分離

```text
DistributionScore
```

描述：

> 是否有派發 / 撤離 / 去槓桿證據。

```text
SqueezeRisk
```

描述：

> 現在做空是否仍面臨被 squeeze 的風險。

因此可以同時：

```text
DistributionScore = 82
SqueezeRisk = 88
```

結論：

```text
Distribution evidence strong
but entry blocked
```

而不是取消 Distribution 判定。

---

# 40. Research-first 原則

V3.3 的所有規則分兩層。

---

## Contract Rules

這些是工程不變量：

- 不可 look-ahead
- journal append-only
- Trigger 必須有 Ready parent
- ActiveBlocks empty 才能 executable
- invalidated / expired 互斥
- Availability time 必須 causal

這些可直接 production。

---

## Model Hypotheses

這些必須被研究驗證：

- P25 / P75
- Score 65 / 70 / 75
- Family >=60
- ATR 0.1 breakdown buffer
- weights
- 2x5m / 2x15m confirmation
- Squeeze 70 / 50
- TTL
- Stop / Target

不能因為寫進規格就視為真理。

---

# 41. Rule Registry

每個 Model Hypothesis 必須有：

```text
rule_id
rule_version
status
hypothesis
feature_dependencies
parameters
training_period
validation_period
test_period
metrics
promotion_decision
```

status：

```text
EXPERIMENTAL
CHALLENGER
CHAMPION
RETIRED
```

---

# 42. 文件治理

V3.2 一份文件同時負責：

- Data Contract
- Feature Spec
- FSM
- Backtest
- Promotion

可維護性下降。

V3.3 定義 canonical ownership。

即使目前先使用單一 Markdown，章節仍視為五個 logical modules。

---

# 43. Logical Modules

```text
00_master_spec.md
01_data_contract.md
02_feature_spec.md
03_state_machine.md
04_backtest_method.md
05_promotion_policy.md
```

目前 V3.3 可以先作：

```text
00_master_spec.md
```

未來實作時應拆檔。

---

# 44. Canonical Ownership

## Data Contract

只允許定義：

- endpoint
- auth
- retention
- timestamp
- availability
- source quality

---

## Feature Spec

只允許定義：

- feature formula
- component
- Family
- normalization
- missing behavior

---

## State Machine

只允許定義：

- Phase
- Trade State
- Gate
- transition
- invariant
- journal

---

## Backtest Method

只允許定義：

- label
- barrier
- entry simulation
- fee
- slippage
- CI
- clustering

---

## Promotion Policy

只允許定義：

- Champion
- Challenger
- acceptance
- ablation
- retirement

避免同一規則在多處重複定義。

---

# 45. P0–P10 優化計畫重新排序

V3.2 的 P0 與 Research-first 看起來衝突。

V3.3 改為：

---

## P0｜Contract Skeleton

只實作：

- schema
- journal
- idempotency
- data contract
- FSM framework
- property tests

**不把 hypothesis threshold 升為 Champion。**

---

## P1｜Data Acquisition

建立：

- REST snapshot archive
- Spot aggTrades
- Perp aggTrades
- Funding
- OI
- Taker
- Top Trader
- Mark / Index

---

## P2｜Data QA + Causal Feature Store

建立：

```text
effective_time
availability_time
known_bad_interval
feature_version
```

---

## P3｜Exploratory Research

先回答：

1. OI deceleration 是否區分 crash / continuation？
2. Price Efficiency 是否有 OOS 預測力？
3. Spot/Perp CVD divergence 是否有增益？
4. Top Trader Position 是否有獨立增益？
5. Funding 是否只是 context？

---

## P4｜Feature Ablation

比較：

- without Efficiency
- without Spot CVD
- without Funding
- without Top Trader
- without BTC regime

---

## P5｜Baseline Model

才建立：

```text
Deterministic Baseline V0
```

將最有證據的 feature 放入。

---

## P6｜Nested Walk-forward

校準：

- threshold
- weight
- barrier
- TTL

---

## P7｜Champion / Challenger

Challenger 必須 OOS 證明增益。

---

## P8｜Shadow Production

只產生：

```text
signals
```

不實際交易。

比較：

```text
backtest expectation
vs
live shadow behavior
```

---

## P9｜Execution Validation

驗證：

- slippage
- depth
- latency
- capacity
- stop fill

---

## P10｜Guarded Production

只有 Acceptance Criteria 全部通過才進。

---

# 46. Triple Barrier Bootstrap

第一輪僅為 pipeline bootstrap：

```text
Stop = 1.5 ATR_15m
Target = 2.0 ATR_15m
Horizon = 12h
```

標記：

```text
BOOTSTRAP_V0
NOT VALIDATED
```

研究 grid：

```text
Stop:
1.0 / 1.5 / 2.0 ATR

Target:
1.5 / 2.0 / 3.0 ATR

Horizon:
6h / 12h / 24h / 72h
```

---

# 47. Nested Calibration

```text
Outer Train
  └─ Inner Validation
       ├─ thresholds
       ├─ barrier
       ├─ TTL
       └─ weights

Outer Test
  └─ untouched
```

禁止：

```text
用 test set 選參數
```

---

# 48. Regime Matching

不做 8 維 exact matching。

---

## Hard strata

優先：

```text
BTC regime
Liquidity bucket
```

---

## Soft adjustment

其他：

- volatility
- breadth
- pump magnitude
- listing age
- liquidation context

使用：

```text
propensity weighting
regression adjustment
```

---

# 49. Executable Precision

成功必須是：

```text
Signal
→ next executable price
→ slippage
→ fee
→ Stop/Target path
```

如果：

```text
Stop first
```

即使之後跌 30%：

```text
LOSS
```

---

# 50. 必須報告的不確定性

每個研究結果至少：

```text
raw_n
effective_n
estimate
95% CI
```

並使用：

- day / regime block bootstrap
- cluster-aware uncertainty

避免把同一板塊同一天的五個 Short 當成五個完全獨立樣本。

---

# 51. Acceptance Criteria

## Engineering

- 所有 invariants pass
- no duplicate events
- idempotent replay
- append-only journal
- deterministic same-input evaluation
- no look-ahead

---

## Data

- timestamp semantics known
- known bad intervals isolated
- DynamicQuality 可重現
- Core missing behavior 明確

---

## Research

- walk-forward only
- Champion vs Challenger 有 OOS 比較
- feature ablation 完成
- n / effective_n / CI 完整

---

## Trading

- fee included
- slippage included
- stop-first
- liquidity gate
- capacity gate
- executable entry simulation

---

## Production

先：

```text
SHADOW
```

再：

```text
GUARDED LIVE
```

不得從 backtest 直接跳 live。

---

# 52. 最小使用者輸出

對使用者不展示所有內部維度。

預設：

| Symbol | Phase | Trade State | Distribution | Squeeze | Data Quality | Exec | Breadth | TTL | 結論 |
|---|---|---|---:|---:|---:|---:|---|---|---|

Debug 才展開：

```text
F1–F5
Coverage
ActiveBlocks
all feature values
```

---

# 53. 語言規則

禁止：

```text
莊家已撤離
莊家正在出貨
一定會跌
```

除非有可直接確認的資料來源。

預設使用：

```text
疑似派發
大戶部位下降
Binance 槓桿退潮
現貨 / 合約背離
空方條件正在成熟
```

---

# 54. V3.3 最重要的工程決策

## 決策 A

```text
3 VALID Families
```

可以計算 DistributionScore。

因此消除 V3.2 的布林矛盾。

---

## 決策 B

交易執行要求不只看 Family 數量。

使用：

```text
ScoreBreadth
+
ModelMaturity
+
DynamicQuality
+
ExecutionQuality
```

共同決定。

---

## 決策 C

```text
SHORT_TRIGGERED => MARKDOWN
```

正式鎖定 Phase / Trade State 耦合。

---

## 決策 D

```text
ActiveBlocks[]
```

是 authoritative。

```text
DisplayGate
```

只是 UI。

---

## 決策 E

Journal 不允許跳級。

---

## 決策 F

重跑使用 idempotency key。

資料修訂與規則修訂皆新增 evaluation，不改歷史。

---

# 55. 目前仍然未知的問題

V3.3 不假裝以下問題已有答案：

1. OI deceleration 是否真的領先暴跌？
2. Price/OI efficiency 是否比單純 price deceleration 更好？
3. Spot CVD divergence 有多少額外增益？
4. Top Trader Position Ratio 是否有 OOS predictive value？
5. Funding percentile 的最佳 window 是 7d / 30d / adaptive？
6. New Listing 的 Reduced-Breadth exception 是否可靠？
7. 3 Family 與 4 Family 的 Precision 差距多少？
8. DistributionScore 是否需要非線性模型？
9. Squeeze Risk 最佳 hysteresis 是多少？
10. Phase FSM 是否比直接 probability model 更有價值？

這些問題必須由：

```text
historical + walk-forward + executable backtest
```

回答。

---

# 56. 最終定位

V3.3 不是：

```text
Validated Trading Model
```

也不是：

```text
Production-ready Signal Model
```

而是：

# **Research-Ready, Internally Consistent Implementation Specification**

意思是：

> 現在可以安全地開始寫資料、特徵、回測與狀態機，而不會因規格本身的布林矛盾、Gate 語意或 journal 跳級問題讓研究結果失真。

真正的下一階段：

> **不是 V3.4 再加更多規則，而是拿 V3.3 的研究框架去回答哪些規則真的有效。**

---

# 57. 官方驗證來源

## Binance USDⓈ-M Market Data

官方 Developer Docs：

https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data

已驗證：

- Open Interest Statistics
- Taker Buy/Sell Volume
- Top Trader Long/Short Position Ratio
- Funding Rate History

---

## Binance Public Data

https://github.com/binance/binance-public-data

已驗證：

- Spot aggTrades / trades
- Futures aggTrades / trades
- Klines
- daily / monthly archive
- checksum

---

## Binance Futures Smart Signal

https://www.binance.com/en/support/faq/detail/197ed63fbca34124bae53d8e6d4c9d29

官方產品說明包含：

- Smart Money
- Whale
- dominant flow
- net volume
- current position notional
- Avg Entry
- Unrealized PnL

V3.3 仍不把它視為 Core Developer API dependency。

---

# 58. Version

```text
System:
V3.3 Distribution Intelligence

Document Type:
Research-Ready,
Internally Consistent Implementation Specification

Engineering Status:
Ready for pipeline / feature store / FSM / backtest implementation

Model Status:
Pre-Validation

Live Trading Status:
NOT APPROVED

Core Principle:
Overheated != Distribution
Distribution != Deleveraging
Deleveraging != Executable Short

Executable Short requires:
Validated phase progression
+ leverage retreat
+ price breakdown
+ evidence breadth
+ data quality
+ execution quality
+ no active block
```
