# セットアップ（GitHub Actions ＋ Discord）

このリポジトリは **毎日自動**で Pauper Challenge / Preliminary を集計し、要約を Discord に送ります。  
全文レポートは GitHub 上の Markdown に残ります。

所要: Discord のチャンネル権限があること。プログラミングは不要です。

---

## 全体の流れ

1. Discord で Webhook（投稿口）を作る  
2. その URL を GitHub の **Secret**（秘密の保管庫）に入れる  
3. Actions（自動実行）を有効にし、一度手動で動かす  
4. Discord と GitHub のレポートを確認する  

以降は毎日 **日本時間 11:00**（UTC 02:00）に自動実行されます。  
月曜は追加で **週次ダイジェスト**（UTC 02:30）も投稿します。

---

## 1. Discord で Webhook を作る

1. 投稿したい Discord サーバーのチャンネルを開く  
2. チャンネル設定 → **連携サービス** → **ウェブフック** → **新しいウェブフック**  
3. 名前は例: `Pauper Meta`  
4. **ウェブフック URL をコピー**（`https://discord.com/api/webhooks/...` で始まる長い文字列）  

この URL は **パスワードと同じ**です。チャットやスクリーンショットに貼らないでください。

---

## 2. GitHub に Secret を登録する

リポジトリ: [MarkG666/mtgo-pauper-reports](https://github.com/MarkG666/mtgo-pauper-reports)

1. GitHub でリポジトリを開く  
2. **Settings** → **Secrets and variables** → **Actions**  
3. **New repository secret**  
4. Name: `DISCORD_WEBHOOK_URL`（一字一句この名前）  
5. Secret: さっきコピーした Webhook URL を貼る  
6. **Add secret**

Secret が無いときでも集計自体は動きますが、Discord には投稿されません（ログに警告が出ます）。

---

## 3. Actions を有効にして初回実行

1. リポジトリの **Actions** タブを開く  
   - 直リンク: https://github.com/MarkG666/mtgo-pauper-reports/actions  
2. 初回は「I understand my workflows, enable them」などが出たら有効化  
3. 左のワークフロー一覧から **Pauper daily** を選ぶ  
4. **Run workflow** → ブランチ `main` のまま **Run workflow**  

数分待ち、緑のチェックが付けば成功です。

失敗したら該当実行の赤い行をクリックし、ログを確認してください。  
よくある原因: Actions 未有効化、ネットワーク一時エラー、mtgo.com 側の遅延。

---

## 4. 確認すること

| 確認場所 | 期待する結果 |
|----------|----------------|
| Actions の実行ログ | `OK report=...` や Discord 投稿ログ |
| Discord チャンネル | 日付・イベント・Top8・メタ上位・特徴の短い要約＋全文リンク |
| GitHub の `reports/daily/` | その日の `.md` が増えている／更新されている |
| GitHub の `data/pauper.sqlite` | 更新時刻が進んでいる |

全文リンク例:  
`https://github.com/MarkG666/mtgo-pauper-reports/blob/main/reports/daily/YYYY-MM-DD.md`  
（プライベートリポジトリなので、GitHub にログインした自分だけが開けます）

---

## 5. 週次ダイジェストを今すぐ試す（任意）

1. Actions → **Pauper daily** → **Run workflow**  
2. **Also post weekly digest to Discord** にチェック  
3. Run  

通常は毎週月曜 UTC 02:30 に自動投稿されます。

---

## トラブル時

| 症状 | 対処 |
|------|------|
| Discord に何も来ない | Secret 名が `DISCORD_WEBHOOK_URL` か確認。Webhook を作り直して Secret を更新 |
| Actions が灰色／動かない | Actions タブでワークフローを Enable。リポジトリ Settings → Actions → General で許可されているか確認 |
| push（書き込み）が失敗する | Settings → Actions → General → Workflow permissions で **Read and write** を選ぶ |
| Webhook URL が漏れた | Discord 側でその Webhook を削除→新規作成→Secret を差し替え |

PAT（Personal Access Token）は、通常は不要です（`GITHUB_TOKEN` で書き戻します）。

---

## 関連

- リポジトリ概要・ローカル実行（任意）: [README.md](./README.md)  
- 秘密情報の考え方: プロジェクト Context の `docs/secrets-github.md`
