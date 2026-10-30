# Promotion：canonical ownership

本輪唯一附帶規則 `V3.3-EXPERIMENTAL-0` 為 EXPERIMENTAL；沒有 Champion V0，沒有 live trading approval。原 V2 模型與其歷史仍獨立保留。

Registry 保存 rule_id、rule_version、status、hypothesis、feature_dependencies、parameters、train/validation/test periods、metrics、promotion_decision。版本 immutable；同版本不能換參數或 status，升級／退役使用新版本並保留來源版本及研究證據。

Schema 允許 EXPERIMENTAL／CHALLENGER／CHAMPION／RETIRED。CHAMPION registry entry 必須有三個非重疊且按時序的研究窗口、metrics、approved decision 及 evidence_id；這僅驗證資料完整性，**不會自行證明 evidence 真實或 OOS 有效**。目前沒有自動 promotion 或交易執行 endpoint。

Production namespace 預設禁止寫入，必須顯式 live adapter 授權；其中不接受 experimental rule 或 assumed availability。研究 CLI 根本沒有 PRODUCTION 選項。日後啟用還需要獨立儲存／role 邊界、已驗證 evidence 與 guarded release，不能只靠設定 status 字串。

Challenger 升級前需要主規格要求的 OOS EV 改善、Precision/Calibration 不惡化、多 regime 穩定、有效樣本與不確定性，以及 shadow/live execution validation。通過前禁止輸出成功率宣稱。Smart Signal 缺失不阻擋 Core 研究，亦不可用 Top Trader 偽裝補齊。
