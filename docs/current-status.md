# 目前狀態

更新：2026-11-01。使用者已決定退役 Futures 多空壓力主功能，轉向 BSC Agent ID 新項目雷達。

## 本次交付

BSC 只讀觀察與候選追蹤，沿用既有 DB、Redis、演算法介面、不可變 journal、CAS、Preview／Production 隔離與 Dashboard 快取。新增獨立 BSC stream，不改寫舊研究歷史、不產生 Futures validation jobs。

真實 pilot 已保存一個完整區塊的兩個合約證據，9 次 RPC 無失敗；離線重播與保存狀態一致。候選 watchlist 區間採集已在隔離 preview stream 試跑；完整 RPC 不可得或缺資料時不視為零。沒有啟用錢包或交易。

正式切換及驗證結果以本次 PR／Vercel 與 artifacts/verification/bsc-* 為準。既有部署完成前仍使用原正式版本；不能只因合併而宣稱部署完成。

## 明確限制

免費公共 RPC 不支援本次實測的無地址全市場事件查詢，因此採有界發現＋已發現候選追蹤。全市場覆蓋、proxy／資格／退款語意、長期保留、完整策略及交易執行尚未完成。

本機 worker 由使用者選定在此電腦執行，先採有限輪數驗收。每個 stream 有容量上限，達上限停止，不自動擴大資源。

詳細操作及後續驗收見 [BSC 轉型文件](bsc/transition.md)。舊版狀態與 24 小時運行驗收保留於 [歷史狀態](archive/futures-status-before-bsc.md)；舊運行通過不能替 BSC 模型或交易能力背書。
