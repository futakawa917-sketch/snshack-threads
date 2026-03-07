# SNShack Threads セットアップガイド

新しいクライアントアカウントを追加する際の手順書です。

---

## 1. Meta Developer Portal でアプリ作成

### 1-1. ログイン
- https://developers.facebook.com にアクセス
- **運用するThreadsアカウントに紐づいたInstagramアカウント**でログイン
- ※ 必ず運用アカウントでログインすること（APIトークンがアカウントに紐づくため）

### 1-2. アプリ作成
1. 右上の「マイアプリ」→「アプリを作成」
2. ユースケース選択: **「Threads APIへのアクセス」**（上から3番目あたり）
3. アプリ名を入力（例: クライアント名）
4. 連絡先メールアドレスを入力
5. 「アプリを作成」をクリック

### 1-3. Threadsテスターの追加
1. 左メニュー「ユースケース」→「Threads APIにアクセス」→「カスタマイズ」
2. 「設定」タブ → Threadsテスター欄に**運用するThreadsのユーザー名**を追加
3. 運用アカウントで threads.net にログイン
4. 設定 → ウェブサイトのアクセス許可 → 招待 → **同意する**

---

## 2. Threads APIトークン取得（OAuth認証）

### 2-1. 認証コード取得
ブラウザで以下のURLにアクセス（値を置き換え）:

```
https://threads.net/oauth/authorize?client_id={THREADS_APP_ID}&redirect_uri=https://localhost/&scope=threads_basic,threads_content_publish,threads_delete,threads_keyword_search,threads_manage_insights,threads_manage_replies&response_type=code
```

- `{THREADS_APP_ID}`: ユースケース → カスタマイズ → 設定タブの「Threads App ID」
- ※ Facebook App IDではなく、**Threads専用のApp ID**を使うこと
- 認可後、リダイレクトURLの `?code=` パラメータが認証コード

### 2-2. 短期トークン取得
```bash
curl -X POST "https://graph.threads.net/oauth/access_token" \
  -d "client_id={THREADS_APP_ID}" \
  -d "client_secret={THREADS_APP_SECRET}" \
  -d "grant_type=authorization_code" \
  -d "redirect_uri=https://localhost/" \
  -d "code={認証コード}"
```

- `{THREADS_APP_SECRET}`: ユースケース → カスタマイズ → 設定タブの「Threads App secret」
- ※ Facebookのapp secretではなく**Threads専用のsecret**を使うこと
- 認証コードは**数分で期限切れ**になるので素早く実行

### 2-3. 長期トークンに交換（60日有効）
```bash
curl "https://graph.threads.net/access_token?grant_type=th_exchange_token&client_secret={THREADS_APP_SECRET}&access_token={短期トークン}"
```

### 2-4. トークン確認
```bash
curl "https://graph.threads.net/v1.0/me?fields=id,username,name&access_token={長期トークン}"
```

正常にユーザー情報が返ればOK。`id`がユーザーIDになる。

---

## 3. Anthropic API キー取得

1. https://console.anthropic.com にアクセス（platform.claude.comではない）
2. 「API keys」→「Create Key」で新しいキーを作成
3. 「Settings」→「Billing」→「Buy credits」でクレジット購入（$5〜）
4. **キーとクレジットが同じ組織にあることを確認**（組織が複数ある場合注意）

---

## 4. プロファイル設定

### ダッシュボードから設定する場合
1. http://localhost:8501 でダッシュボードを開く
2. 「設定」タブ → 「新規プロファイル作成」
3. 各項目を入力して保存

### CLIから設定する場合
```bash
snshack profile create {クライアント名}
```

設定ファイルの場所: `~/.snshack-threads/profiles/{クライアント名}/config.json`

```json
{
  "threads_access_token": "取得した長期トークン",
  "threads_user_id": "ユーザーID",
  "threads_app_id": "Threads App ID",
  "threads_app_secret": "Threads App Secret",
  "anthropic_api_key": "sk-ant-api03-...",
  "research_keywords": "キーワード1,キーワード2,キーワード3"
}
```

---

## 5. Advanced Access 申請（競合リサーチ用）

`threads_keyword_search` のAdvanced Accessが必要。

### 5-1. 前提条件
以下を先に完了させる:
- プライバシーポリシーURL（アプリ設定 → ベーシック）
- アプリアイコン 1024x1024（同上）
- カテゴリ選択（同上）

### 5-2. ビジネスポートフォリオ作成
1. アプリダッシュボード → 左メニュー「公開」
2. 「ビジネス認証」→「認証を開始」
3. 「Metaビジネスマネージャにリンク」→「新しいアカウントを作成」
4. ビジネスポートフォリオ名、氏名、メールアドレスを入力
5. 「ポートフォリオを作成」

### 5-3. ビジネス認証
1. セキュリティセンターで「認証を開始」
2. ビジネスの詳細情報を入力（名称、住所、電話番号、メール、ウェブサイト）
3. 関係の確認
4. 必要に応じて書類アップロード
5. 審査待ち（通常48時間以内）

### 5-4. Advanced Access リクエスト
1. ビジネス認証完了後、「ユースケース」→「カスタマイズ」
2. `threads_keyword_search` の「アクション」→「アプリレビューに追加」
3. レビュー申請を提出

---

## 6. 動作確認

### 投稿テスト
```bash
snshack --profile {クライアント名} ai generate -t "テストテーマ"
```

### Threads投稿テスト
```bash
snshack --profile {クライアント名} threads post "テスト投稿です"
```

### 自動投稿テスト（ドライラン）
```bash
snshack --profile {クライアント名} autopilot --dry-run -t "テーマ1" -t "テーマ2"
```

---

## 7. 自動運用開始

### ローカル（Mac）
launchdで毎朝7:00に自動実行（PCがスリープでも起きたら実行）:
```
~/Library/LaunchAgents/com.snshack.daily.plist
```

### GitHub Actions（PC不要）
`.github/workflows/daily-autopilot.yml` が毎朝7:00 JSTに実行。
GitHub Secretsに以下を登録:
- `THREADS_ACCESS_TOKEN`
- `THREADS_USER_ID`
- `THREADS_APP_ID`
- `THREADS_APP_SECRET`
- `ANTHROPIC_API_KEY`

---

## トラブルシューティング

| 問題 | 原因 | 対処 |
|---|---|---|
| OAuth「app ID not sent」 | Facebook App IDを使っている | Threads App IDを使う |
| OAuth「invalid client_secret」 | Facebookのsecretを使っている | Threads App Secretを使う |
| OAuth「user has not accepted invite」 | テスター招待未承認 | threads.net → 設定 → 招待 → 同意 |
| 認証コード期限切れ | 取得から数分経過 | OAuthフローをやり直す |
| Anthropic「credit balance too low」 | クレジット未購入 or 別組織 | console.anthropic.com でBilling確認 |
| Anthropic「invalid x-api-key」 | キーが不正 or 別組織のキー | 同じ組織でキー再作成 |
| keyword_search権限エラー | Advanced Access未取得 | ビジネス認証 → アプリレビュー申請 |
| トークン期限切れ | 60日経過 | `snshack threads refresh-token` |
