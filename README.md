# MTGO Pauper メタトラッカー

Magic Online の **Pauper Challenge** と **Preliminary** を毎日取得し、日本語の Markdown レポートを作ります。  
運用の本線は **GitHub Actions**（Discord 到着目安: 米DST中 **~13:00–14:00 JST**、標準時 **~14:00–15:00 JST**）＋ **Discord Webhook**（要約＋GitHub 全文リンク）です。

- リポジトリ: https://github.com/MarkG666/mtgo-pauper-reports  
- Actions: https://github.com/MarkG666/mtgo-pauper-reports/actions  
- **公開サイト（GitHub Pages）:** https://markg666.github.io/mtgo-pauper-reports/（`docs/` を main から公開。初回は Settings → Pages で有効化）  
- **はじめての人向け手順:** [SETUP.md](./SETUP.md)（Webhook → Secret → 手動実行）

---

## 何が自動で起きるか

| タイミング | 内容 |
|------------|------|
| 毎日 **~13:00–14:00 JST**（米DST=EDT期）／**~14:00–15:00 JST**（米標準時=EST期） | 取得・集計 → 前日キャッチアップ → DB/レポート commit → Discord **日次要約**（未投稿日のみ） |
| 月曜・日次の **+30分** | Discord **週次ダイジェスト**（1ヵ月／直近1週＋前週差）。UTC cron は 00:30（EDT期）／01:30（EST期） |
| 手動（Actions → Run workflow） | いつでも同じ処理を再実行可能（DSTゲートを bypass） |

Cron は UTC **00:00** と **01:00** の両方に置く（Actions の ~4.5–5h 遅延を見込んだ前倒し）。`America/New_York` の夏時間判定で正しい枠だけ実行します。

Discord には **大会ごとに1投稿**の短い要約を送ります。表つきの全文は GitHub の `reports/daily/YYYY-MM-DD.md`（MTGO 開催日）か、GitHub Pages の一覧（上記 URL）から読んでください。同じ開催日に稀に複数大会があるときだけ `-1` / `-2`。

`reports/daily/` が更新されると Actions（`Sync Pages daily docs`）が `docs/daily/` を同期します。手動なら `python scripts/sync_pages_daily.py`。

---

## 初回セットアップ（必須）

[SETUP.md](./SETUP.md) を上から順に:

1. Discord Webhook 作成  
2. GitHub Secret `DISCORD_WEBHOOK_URL` 登録（日次・週次）。任意で個人用 `DISCORD_ALERT_WEBHOOK_URL`（取得失敗アラートのみ）  

3. Actions で **Pauper daily** を一度手動実行  

Secret が無い間も集計は動きますが、Discord 投稿はスキップされます。

---

## レポートの要点

- **Top8 ブラケット**: 優勝〜Top8・プレイヤー＋アーキ＋敗因  
- **メタ概要**: **その大会の** Top32（数 / Top8 / シェア / W-L / 勝率）。同日の他大会と合算しない  
- **ファイル**: 1大会 = 1 Markdown（`YYYY-MM-DD.md` = MTGO decklist URL の開催日。衝突時のみ `-1` / `-2`）  
- **累積**: 過去1ヵ月／直近1週間。列順は 数／シェア／差1／Top8／8率／W-L／勝率／差2。8率=Top8/数。シェア≤2% は Other。差分配列は前月差1/2・前週差1/2（1=シェア、2=勝率）
- **特徴リスト**: Top16 から 3〜5 本。直近2週間の同アーキ平均との差分（メイン／サイド）  
- アーキ改名: `config/aliases.csv` / 個別: `config/overrides.csv` → `apply-overrides`  
- 旧 **Glitters** は色つき Hawk 名（**Boros Hawk** 基本。Sneaky Snacker のみでは色を足さない。黒スプラッシュ→**Mardu Hawk**、青→**Jeskai Hawk**、黒＋青→**4c Hawk**）

---

## ローカル PC で動かす（任意）

Actions が本線です。PC でも同じコマンドが使えます（Python 3.11+）。

```bat
cd パス\へ\mtgo-pauper-reports
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=%CD%\src
python -m pauper_meta run-daily
```

| コマンド | 意味 |
|----------|------|
| `python -m pauper_meta run-daily` | 取得 → DB → 今日（JST）のレポート → 前日キャッチアップ |
| `python -m pauper_meta discord-summary --mode daily` | Discord 日次要約を表示（`--post` で投稿） |
| `python -m pauper_meta discord-summary --mode weekly --post` | 週次ダイジェストを投稿 |
| `python -m pauper_meta apply-overrides` | 別名／個別修正を適用してレポート再生成 |
| `python -m pauper_meta report --date 2026-10-15` | レポートだけ作り直し |
| `python -m pauper_meta backfill --since 2025-09-25` | 履歴の遡及取得 |

出力:

- DB: `data/pauper.sqlite`（Git に残す）  
- レポート: `reports/daily/YYYY-MM-DD.md`（Git に残す。1大会1ファイル・MTGO 開催日）  
- 生HTML/JSON: `data/raw/`（**Git に入れない**）  
- ログ: `logs/`

Windows 向けに `run-daily.bat` もあります（タスク スケジューラ用・任意）。

---

## 注意

- Webhook URL や Secret をコード・README・チャットに書かない  
- mtgo.com / MTGGoldfish へは低頻度・キャッシュ優先  
- リポジトリは公開想定。日次レポートは GitHub Pages でも閲覧可能  
- Discord 投稿は環境変数 `DISCORD_WEBHOOK_URL`（Actions では Secret）を参照します
