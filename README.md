# snshack-threads

Threads (Meta) の投稿自動化・アナリティクスツール。**Metricool API** を使用。

## 機能

- **1日5投稿の自動スケジュール**: 8:00, 11:00, 14:00, 18:00, 21:00 の5スロット
- **スケジュール管理**: Metricool 経由で投稿を予約・確認
- **アナリティクス**: エンゲージメント率・閲覧数・いいね数・フォロワー推移

## セットアップ

### 1. インストール

```bash
pip install -e ".[dev]"
```

### 2. Metricool API トークン取得

1. [Metricool](https://app.metricool.com) にログイン
2. Settings > API タブ で REST API Access token をコピー
3. ブラウザ URL から `blogId` と `userId` を確認

### 3. 環境変数の設定

```bash
cp .env.example .env
```

`.env` を編集:
```
METRICOOL_USER_TOKEN=your_api_token
METRICOOL_USER_ID=your_user_id
METRICOOL_BLOG_ID=your_blog_id
METRICOOL_TIMEZONE=Asia/Tokyo
```

## 使い方

### ブランド一覧

```bash
snshack brands
```

### 投稿一覧（メトリクス付き）

```bash
snshack posts --days 30
```

### 1日5投稿をスケジュール

```bash
# テキスト直接指定
snshack schedule-day 2026-03-08 \
  -t "朝の投稿 ☀️" \
  -t "午前の投稿" \
  -t "午後の投稿" \
  -t "夕方の投稿" \
  -t "夜の投稿 🌙"

# ファイルから読み込み
snshack schedule-day 2026-03-08 -f posts.txt
```

### 単発スケジュール

```bash
snshack schedule "投稿テキスト" --at "2026-03-08 14:00"
```

### 予約確認・空きスロット

```bash
snshack queue            # 今後7日の予約一覧
snshack slots 2026-03-08 # 空いてる時間帯
```

### アナリティクス

```bash
snshack analytics --days 30 --top 5
```

## 開発

```bash
pip install -e ".[dev]"
python3 -m pytest -v
ruff check src/ tests/
```
