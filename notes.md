# メモ（Phase 1）

## レポート形式（現行）

- Top8 表（敗因付き）のみ。トーナメント樹は出さない。
- メタ表（Top32 合算のみ）: 数 / **Top8** / シェア / W-L / 勝率。イベント別 Top32 節なし。
- **W-L**: マッチ単位。スイス＋Top8 SE。勝率 = W/(W+L)。
- 累積: 過去1ヵ月／直近1週間。シェア≤2% → **Other**。直近週に **前週差**（ゼロは `-`）。
- 特徴リスト: 比較基準 `直近2週間の同アーキ N本`（メイン本数）＋メイン／サイド差分。
- 注記はすべてレポート末尾。

## アーキ改名 / 赤アグロ

- `Black Burn` → **BG Gardens**、`Terror` → **U Terror**
- **Kuldotha Aggro 廃止** → **Mono Red Rally**（Rally / BTE 軸・混在・曖昧のデフォルト）／**Mono Red Axe**（Axe / Gingerbrute 軸が明確で Rally なしのときのみ）
- W 色ラベルは変更なし
- 今後: チャットで「A→Bにして」、または `aliases.csv` / `overrides.csv` 編集後に `apply-overrides`（全件再分類あり）

## 運用

- **本線:** GitHub Actions（毎日 02:00 UTC = 11:00 JST）→ Discord Webhook（案2）
- 週次ダイジェスト: 月曜 02:30 UTC
- 手順: [SETUP.md](./SETUP.md)
- 任意のローカル: Windows タスクスケジューラ 11:00 JST → `run-daily.bat`
- ストア FS 不安定時: `PAUPER_META_DATA_DIR` で作業 DB を `/tmp` 等へ
- Backfill: `python -m pauper_meta backfill --since 2025-09-25`
