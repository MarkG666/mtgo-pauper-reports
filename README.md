# MTGO Pauper メタトラッカー

Magic Online の **Pauper Challenge** と **Preliminary** を毎日取得し、日本語の Markdown レポートを作ります。  
運用の本線は **GitHub Actions**（Discord 到着目安: 米DST中 **~13:00–14:00 JST**、標準時 **~14:00–15:00 JST**）＋ **Discord Webhook**（要約＋GitHub 全文リンク）です。

- リポジトリ: https://github.com/MarkG666/mtgo-pauper-reports  
- Actions: https://github.com/MarkG666/mtgo-pauper-reports/actions  
- **はじめての人向け手順:** [SETUP.md](./SETUP.md)（Webhook → Secret → 手動実行）

---

## 何が自動で起きるか

| タイミング | 内容 |
|------------|------|
| 毎日 **~13:00–14:00 JST**（米DST=EDT期）／**~14:00–15:00 JST**（米標準時=EST期） | 取得・集計 → 前日キャッチアップ → DB/レポート commit → Discord **日次要約**（未投稿日のみ） |
| 月曜・日次の **+30分** | Discord **週次ダイジェスト**（1ヵ月／直近1週＋前週差）。UTC cron は 00:30（EDT期）／01:30（EST期） |
| 手動（Actions → Run workflow） | いつでも同じ処理を再実行可能（DSTゲートを bypass） |

Cron は UTC **00:00** と **01:00** の両方に置く（Actions の ~4.5–5h 遅延を見込んだ前倒し）。`America/New_York` の夏時間判定で正しい枠だけ実行します。

Discord には短い要約だけ送ります。表つきの全文は GitHub の `reports/daily/YYYY-MM-DD.md` を開いてください。

---

## 初回セットアップ（必須）

[SETUP.md](./SETUP.md) を上から順に:

1. Discord Webhook 作成  
2. GitHub Secret `DISCORD_WEBHOOK_URL` 登録  
3. Actions で **Pauper daily** を一度手動実行  

Secret が無い間も集計は動きますが、Discord 投稿はスキップされます。

---

## レポートの要点

- **Top8 ブラケット**: 優勝〜Top8・プレイヤー＋アーキ＋敗因  
- **メタ概要**: Top32 合算（数 / Top8 / シェア / W-L / 勝率）  
- **累積**: 過去1ヵ月／直近1週間。シェア≤2% は Other。直近週に前週差  
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
- レポート: `reports/daily/YYYY-MM-DD.md`（Git に残す）  
- 生HTML/JSON: `data/raw/`（**Git に入れない**）  
- ログ: `logs/`

Windows 向けに `run-daily.bat` もあります（タスク スケジューラ用・任意）。

---

## 注意

- Webhook URL や Secret をコード・README・チャットに書かない  
- mtgo.com / MTGGoldfish へは低頻度・キャッシュ優先  
- リポジトリはプライベート想定（全文リンクは権限のある人だけ）  
- Discord 投稿は環境変数 `DISCORD_WEBHOOK_URL`（Actions では Secret）を参照します
