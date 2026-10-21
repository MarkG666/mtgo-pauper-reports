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
- 2026-10-21: **Ruby Storm** 追加（Anarchomancer + Thornscape Familiar + Needle/Woodlot）。Kampo 基準。aliases: RubyStorm / RG Storm / Gruul Storm
- 2026-10-21: **Faeries 色分割** — 基幹（Spellstutter + Ninja/Seer）が同じでも黒→**Dimir**、赤→**Izzet**、両方→**Grixis**、なし→**Mono-Blue**。Sneaky Snacker のみでは赤に数えない。aliases: UR Faeries → Izzet Faeries

## 順位 / Top8

- Challenge の **finish_rank 1–8 はブラケット（SE）結果が正**。`final_rank` 欠落時にスイス Top8 を優勝扱いしない。
- 9位以下はスイス（または `final_rank` の 9+）。ingest 時にブラケットから合成し、レポート生成時にも null なら補完。
- メタ概要の **Top8 列** = `finish_rank ≤ 8` の本数（欠落時はブラケット出場でフォールバック）。
- 安価な検証: 決勝の優勝/準優勝が finish_rank 1/2 と一致するか（追加 HTTP なし）。

## Discord 日次 Top8

- 形式: `準優勝: player — Archetype（eliminatorに敗北） <mtgo-deck-url>`
- 優勝は括弧なし。敗因はレポート表の `eliminated_by` からプレイヤー名だけ抜く。
- Top8 デッキ URL は MTGO イベントページの `#deck_<player>`（空白→`_`）。文字数超過時は特徴リスト URL 省略 → メタ行削減 → イベント URL 1本、の順で縮める。

## 運用

- **本線:** GitHub Actions → Discord Webhook（案2）
  - 米DST（EDT）: 毎日 **13:00 JST** = 04:00 UTC
  - 米標準時（EST）: 毎日 **14:00 JST** = 05:00 UTC
  - cron は 04:00/05:00 UTC 両方＋`schedule_gate`（`America/New_York`）
- 週次ダイジェスト: 月曜・日次の **+30分**（04:30 / 05:30 UTC）
- 前日キャッチアップ: 不完全イベント or 欠落 md なら JST 昨日を再取得（Discord は `data/discord_posted.json` で重複防止）
- 手順: [SETUP.md](./SETUP.md)
- 任意のローカル: Windows タスクスケジューラ（13:00 JST 目安）→ `run-daily.bat`
- ストア FS 不安定時: `PAUPER_META_DATA_DIR` で作業 DB を `/tmp` 等へ
- Backfill: `python -m pauper_meta backfill --since 2025-09-25`
- 欠落 md の穴埋めメモ: ストア `docs/report-backfill-note.md`（one-shot `report --date`）
