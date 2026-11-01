# 演算法更新、置換與回退

更新日期：2026-11-01。

演算法現在透過 `src/algorithms` 與應用程式連接。正式掃描和 V2 離線重播共用 `V2Algorithm`；V3 的既有 `ContractStore` 共用 `V33Algorithm`。這次抽離沿用原本 scoring、lifecycle、因果 feature store、交易、去重及 journal，不重寫另一套模型。

**現行正式產品已切換為 BSC 只讀雷達（BSC-READONLY-1），沒有錢包或交易訊號。以下 V2／V3 說明保留作歷史介面文件；Futures 主功能已退役。** 介面完成不等於缺少的 feature producers、持續採集、真實 OOS、shadow 或 execution validation 已完成。完整缺項見 [V3.3 進度](v33/10_completion_progress.md)。

## 現行 BSC 演算法更新

- 純判斷器在 `src/algorithms/bsc.py`。修改判斷時增加 model_version，保留特徵契約、程式指紋與狀態版本；不能沿用舊 stream 的狀態直接接新版本。
- 新版本先以已保存 evidence 做隔離 replay，再檢查 preview 的判定理由、資料缺失與公開畫面。變更資料來源或投影時也要版本化；僅改 adapter 不會自動補上新來源。
- 目前採集器明確使用 BSC discovery adapter，沒有任意程式載入或自動交易開關；替換其他 adapter 時需審查其輸入契約與呼叫綁定，不能只改環境變數。
- 變更 watchlist 要另建 stream／重播。觀察名單不是買入白名單；不得宣稱新地址已具備舊期間的完整覆蓋。
- 完整操作及限制見 [BSC 轉型文件](bsc/transition.md)。舊版回測或運行驗收不會自動成為 BSC 新模型的成效證據。

## 模組與責任

| 位置 | 責任 |
|---|---|
| `src/algorithms/contracts.py` | 固定輸入、輸出與綁定版本的狀態 |
| `src/algorithms/registry.py` | 明確列出允許的 adapter 及正式可用性 |
| `src/algorithms/v2.py` | 呼叫原 V2 engine 與 lifecycle |
| `src/algorithms/v33.py` | 呼叫原 V3 Family / 雙 FSM |
| `src/model_contract.py`、`src/v33/*features*` | 特徵公式、單位與版本；不可隨模型切換偷偷改變 |
| `src/run_cycle.py`、`src/v33/journal.py` | 採集／排程／持久化、併發與原子寫入 |

Adapter 建構階段載入並驗證規則、計算程式指紋；`evaluate(AlgorithmInput)` 本身只運算，不讀檔、不呼叫 API、不寫 DB、不發布訊號。每次呼叫必須提供有時區的評估時間，不使用隱藏的「現在」。

共同輸出包括 identity、symbol、evaluation_time、scores、state、reasons、quality、next_state、events、native。`native` 保留各模型完整語意，不將 V3 Distribution Score 偽裝成 V2 pump/dump score。兩模型輸入的特徵契約不同，不能只改環境變數就把 V2 features 當作 V3 features。

V3 必須由既有 ContractStore 驗證 raw lineage、availability、past-only reference 與 source integrity，再交給 adapter。單獨呼叫 pure adapter 的測試輸入不具正式資料認證效力。Dashboard 目前仍使用 V2 投影，V3 的獨立投影尚未整合到正式介面。

## 查看可用模型

在專案根目錄執行：

```powershell
.venv/Scripts/python.exe tools/algorithms.py list
.venv/Scripts/python.exe tools/algorithms.py check --algorithm radar-v2 --target live
.venv/Scripts/python.exe tools/algorithms.py check --algorithm distribution-v33 --target research
```

以上不連網、不寫入資料。`distribution-v33 --target live` 目前會明確拒絕；偽填 CHAMPION metadata 也不會開啟正式 adapter。

## 更新 V3 的門檻或權重

1. 將 `src/v33/experimental.json` 複製成新的規則檔，例如 `config/rules/distribution-experiment-1.json`（目錄／檔名可自行建立）。
2. 指定全新的 `rule_version`，保留 status=EXPERIMENTAL，寫明 hypothesis、feature_dependencies 與變更。調整 `parameters`；不要覆寫既有版本。
3. 用新檔檢查契約：

```powershell
.venv/Scripts/python.exe tools/algorithms.py check --algorithm distribution-v33 --rule config/rules/distribution-experiment-1.json
```

4. 沿用現有因果重播 CLI，以**新 run-id、新輸出檔**比較同一批完整資料：

```powershell
.venv/Scripts/python.exe tools/v33_research.py replay --journal data/v33/research.sqlite3 --namespace REPLAY --run-id experiment-1 --rule config/rules/distribution-experiment-1.json --frames data/v33/frames.jsonl --output artifacts/research/experiment-1.jsonl
```

`frames.jsonl` 必須符合現有 [資料契約](v33/01_data_contract.md)，含完整原始證據與 feature lineage，不是任意補齊的分數。這個命令會寫入指定的本機研究 journal，但不會修改正式服務。不要將 raw 缺失補成 0，也不要將合成 fixture 的結果當作模型成效。

## 改公式或換另一個演算法

1. 新增 adapter，實作 `Algorithm` protocol；輸入 `AlgorithmInput`，回傳 `AlgorithmOutput`。不要在 adapter 內採集、寫 journal 或推送通知。
2. 指定獨立 algorithm_id、model_version、feature_contract、state_schema、implementation_sha256、policy_sha256。特徵公式改變也須增加 feature version 並保留來源 lineage。
3. 在 registry 的明確 allowlist 註冊，先僅允許 research。不要從遠端字串動態 import 或執行程式碼。
4. 以相同固定資料做決策／狀態／事件差異測試，再做成熟 outcome、無洩漏 OOS 與成本評估。需要新來源時先擴充 data adapter，不把網路存取塞進模型核心。
5. 完成獨立 shadow、Dashboard 語意與 execution 驗證，才新增受審查的 live adapter。現有 V3 正式阻擋須在這些工作完成後以程式變更解除，不能只改 status 或旗標繞過。

V2 參數目前仍由 `model_contract.py`／engine 管理，**尚無任意 JSON 調參入口**。修改時增加 MODEL_VERSION，更新對應契約與測試；不能只改常數而沿用同版本與舊 READY。

## 狀態與切換

每份新狀態綁定模型 identity、symbol、評估時間與 payload。不同模型、版本、規則、程式指紋或特徵契約不能直接接手；同一 stream 必須按時間前進，重試由既有 journal 讀回原結果。

第一次導入介面，已知 V2.4.1-alpha／未標版本的舊 V2 state 由明確 compatibility adapter 採用，確認價格、確認時間、TTL、cycle 原樣保留。舊 V3 研究 head 按原 rule/symbol stream 採用，新 head 保存完整 envelope。這只處理這次既有格式，不代表允許 V2→V3 或任意版本自動遷移。

正式 snapshot 新增 `algorithm` 綁定；更換指紋時會拒絕掃描，避免默默覆蓋另一版的狀態。正式切換前，應保存舊部署與 snapshot，在隔離 ledger／Redis key 建立新模型狀態並完成驗收，再切換讀取及排程目的地。需要保留 setup 時另寫經驗證的 migration，不要刪掉 `algorithm` 欄位來繞過保護。

## 回退

回退時使用已驗證的舊部署及其對應 snapshot／儲存命名空間，重播原本 outbox。不要把新模型 head 塞回舊模型，也不要刪掉歷史或重跑市場採集以修補回退。舊 worker 的 lease 與未完成工作必須先確認不會寫入新儲存。

歷史 Futures 入口的 `CSR_ALGORITHM_ID` 預設為 `radar-v2`；現行正式產品另設定 `CSR_PRODUCT_MODE=bsc` 與 `CSR_ALGORITHM_ID=bsc-discovery`，不使用該 Futures live 路徑。未知 ID／尚未開放的 V3 在開啟 journal 或呼叫 Binance 前失敗；release readiness 也會列為不通過。`/api/research` 可讀到目前 snapshot 的 algorithm identity；舊 snapshot 尚未自然更新前該欄位為 null。
