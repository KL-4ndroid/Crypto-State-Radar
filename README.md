# BSC Agent ID Launch Radar

專案已轉向 BSC Agent ID 新項目觀察。沿用 Crypto-State-Radar 的版本化演算法、不可變研究帳本、CAS、隔離環境與快取 Dashboard；Futures 多空壓力功能退役，歷史保留。

目前為 **只讀 MVP**：有界區塊樣本發現、候選合約 Mint 追蹤、程式碼引用線索、持久游標與離線重播。沒有錢包簽署、授權、買賣或獲利保證；不是 BSC 全市場持續覆蓋。

- [目前狀態](docs/current-status.md)
- [BSC 架構、操作與切換](docs/bsc/transition.md)
- [演算法介面與更新](docs/algorithm-updates.md)
- [歷史 Futures 專案說明](docs/archive/futures-readme.md)

```powershell
.venv/Scripts/python.exe tools/algorithms.py list
.venv/Scripts/python.exe tools/bsc_radar.py status --root data/bsc/pilot
.venv/Scripts/python.exe -m pytest -q
```

雲端 profile 位於忽略版控的 .vercel/private；不要提交連線資料。操作只讀 worker 與 replay 前請參閱 BSC 文件。
