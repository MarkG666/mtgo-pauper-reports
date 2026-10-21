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
- **Glitters 廃止** → **Boros Hawk**（基本）／**Mardu Hawk**（黒スプラ）／**Jeskai Hawk**（青スプラ）／**4c Hawk**（黒＋青）。Sneaky Snacker のみでは色を足さない。緑スプラは Naya/5c（実績があれば）
- W 色ラベルは変更なし
- 今後: チャットで「A→Bにして」、または `aliases.csv` / `overrides.csv` 編集後に `apply-overrides`（全件再分類あり）
- 2026-10-21: Glitters 廃止 → Boros / Mardu / Jeskai / 4c Hawk（Snacker は色に数えない）

## 順位 / Top8

- Challenge の **finish_rank 1–8 はブラケット（SE）結果が正**。`final_rank` 欠落時にスイス Top8 を優勝扱いしない。
- 9位以下はスイス（または `final_rank` の 9+）。ingest 時にブラケットから合成し、レポート生成時にも null なら補完。
- メタ概要の **Top8 列** = `finish_rank ≤ 8` の本数（欠落時はブラケット出場でフォールバック）。
- 安価な検証: 決勝の優勝/準優勝が finish_rank 1/2 と一致するか（追加 HTTP なし）。

## 運用

- **本線:** GitHub Actions（毎日 02:00 UTC = 11:00 JST）→ Discord Webhook（案2）
- 週次ダイジェスト: 月曜 02:30 UTC
- 手順: [SETUP.md](./SETUP.md)
- 任意のローカル: Windows タスクスケジューラ 11:00 JST → `run-daily.bat`
- ストア FS 不安定時: `PAUPER_META_DATA_DIR` で作業 DB を `/tmp` 等へ
- Backfill: `python -m pauper_meta backfill --since 2025-09-25`
