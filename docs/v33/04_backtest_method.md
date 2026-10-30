# 研究方法與下一階段（本輪不實作）

V3.3 的研究有效性尚未驗證。本輪只提供 causal store／state replay，不把它稱為 executable trading backtest。

下一輪優先依主規格研究 OI deceleration、Price/OI Efficiency、Spot/Perp CVD divergence。每個假說需要有價值可用的市場分母與完整 acquisition coverage；排除缺資料造成的虛假優勢，分開報告候選選取漏失與模型漏失。

建立 Pump Event Dataset：絕對 7D 漲幅及 P90/P95/P99 事件群，所有 normalization past-only；固定 event/overlap/cooldown 協議，保留新上市／下市與流動性篩選歷史，避免 survivorship bias。Outcome 覆蓋 6h/12h/24h/72h/7d，未成熟窗口不得當負例。

barrier bootstrap 與 grid 只使用 registry 中的 NOT_VALIDATED 設定。執行模型需下一個真正可成交價、費用、滑價、funding、price path、stop-first。同根 K 線同時穿越 stop/target 時需要更細資料或保守 ambiguous label；不得默認有利成交。這些尚未實作。

只允許時間順序 nested walk-forward；training label horizon 跨入 validation/test 的樣本須 purge，必要時 embargo。Test untouched，不用 test 挑閾值、TTL、權重、barrier。執行 ablation，報告 EV、Executable Precision、PR-AUC、Calibration、MAE/MFE、raw_n/effective_n/95% CI 及 day/regime cluster-aware uncertainty。

Canonical ownership：本檔僅管理研究／成交模擬方法；模型權重屬 registry，資料時間屬 Data Contract，不能在這裡重新定義另一份 production 分數。
