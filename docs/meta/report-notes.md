---
layout: default
title: "日次レポート共通の注記・対象"
date: report-not
---

各大会ファイル（`reports/daily/YYYY-MM-DD.md`）からは繰り返さず、ここに集約する。

## 対象

- **単一**の Pauper Challenge / Preliminary（League 除外）。
- 日次ファイルは **1大会 = 1 Markdown**。
- ファイル名は **MTGO 開催日**（decklist URL の日付）`YYYY-MM-DD.md`。同じ開催日に複数大会がある稀な場合のみ `-1` / `-2`。
- メタ概要（Top32）・Top8・特徴リストはその大会のみ。Challenge 主メタ％への League / Paper 合算はしない。

## 特徴リスト

- Top16 までから 3〜5 本。同アーキ内のカード差分を優先。
- 同アーキ比較は直近約2週間の**メイン／サイド別**平均（不足時さらに拡張。スコアはメイン主＋サイド0.55倍）。
- 比較基準は `直近2週間の同アーキ N本`（メイン比較に使った本数）。イベント名・選出理由は載せない。
- 選出後の並びは **最終 finish_rank 昇順**（優勝→下位）。
- リンク方針は MTGGoldfish 個別デッキ優先（取れなければ大会ページまたは mtgo.com）。

## メタ概要・累積

- **W-L / 勝率**はマッチ単位（ゲームではない）。スイス順位表の W-L に Top8 シングルエリムのブラケット勝敗を合算。勝率 = W / (W+L)。**Top8** 列は当該アーキの finish_rank≤8 本数（メタ概要・1ヵ月／1週累積とも同じ定義）。
- 累積分布（1ヵ月／1週間）は各イベントの **Top32** デッキ合算。列順は 数 → Top8 → シェア → …。シェア≤2% は **Other** に合算（`config/report.json` の `other_share_threshold`）。
- **直近1週間**: **前週差1**=シェア差、**前週差2**=勝率差（前週に無い／計算不能は —。丸め後 0 は `-`）。
- **過去1ヵ月**: **前月差1**=シェア差、**前月差2**=勝率差（直前の約30日窓との差。丸め後 0 は `-`）。
- 前窓のイベント数・デッキ数が少ないと差はノイジーになり得る（レポート生成時に件数を見て短い注意を出す場合あり）。

## Top8 リンク

- GitHub 日次の Top8 は **アーキタイプ名のみ**を mtgo.com 個別デッキアンカー（`#deck_<player>`、空白は `_`）へリンク。プレイヤー名はプレーンテキスト（`_` / `*` 等は Markdown 強調を避けるためエスケープ）。
- Discord は大会ごとに1投稿（短い `[リスト](同URL)` を優先）。

## イベント概要の MTGGoldfish

- 大会ページ URL は `https://www.mtggoldfish.com/tournament/{slug}-{YYYY-MM-DD}` 形式（例: `pauper-challenge-32-2026-11-01`）。
- 日付は **MTGO 開催日**（decklist URL）を優先。
- **Challenge N**（`pauper-challenge-\d+`）のみ、パターン検証済みとして無条件でリンクを出す。
- **Preliminary** は naive slug（`pauper-preliminary-YYYY-MM-DD`）が 404 になりやすい。キャッシュ済み HTML があるときだけリンクし、無ければ省略（壊れた URL を捏造しない）。
- 個別デッキリスト解決（`goldfish_links.py`）と同じ slug 規則を共有するが、概要リンクは上記の公開ポリシーが厳しめ。

## その他

- 未分類デッキはアーキ名の代わりに **色アイデンティティ**（W/U/B/R/G、複数色は WUBRG 順、無色は C）を表示。
- 常連プレイヤー集計は DB / `player-stats` CLI のみ（日次レポート非掲載）。
- Phase 1 — ローカル／Actions 生成。
