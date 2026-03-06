# snshack-threads

Threads (Meta) の投稿自動化・アナリティクスツール。

## 機能

- **投稿管理**: Threads への投稿作成・一覧表示
- **スケジュール投稿**: 指定時刻に自動投稿
- **アナリティクス**: エンゲージメント率・閲覧数・いいね数などの分析レポート

## セットアップ

### 1. インストール

```bash
pip install -e ".[dev]"
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して Threads API のアクセストークンとユーザーIDを設定
```

Meta Developer Portal でアクセストークンを取得してください:
https://developers.facebook.com/docs/threads/getting-started

## 使い方

### プロフィール表示

```bash
snshack profile
```

### 投稿の作成

```bash
snshack post "Hello Threads!"
```

### 投稿一覧

```bash
snshack posts --limit 10
```

### スケジュール投稿

```bash
snshack schedule "おはようございます！" --at "2026-03-07 08:00"
snshack queue          # 予約一覧
snshack publish-due    # 予定時刻を過ぎた投稿を公開
```

### アナリティクス

```bash
snshack analytics --limit 25 --top 5
snshack metrics <post_id>
```

## 開発

```bash
pip install -e ".[dev]"
pytest
ruff check src/ tests/
```
