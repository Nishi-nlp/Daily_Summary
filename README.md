# Daily Summary

ActivityWatch logs + Git diff + OpenAI API + Obsidian を使って
毎日の活動ログを自動で要約し、Obsidianに保存するツールです。

## Features

- ActivityWatch から作業ログ取得
- Git diff から開発内容を解析
- OpenAI API で日次要約生成
- Obsidian に Daily Note として保存

## Example

```markdown
# Daily Summary - 2026-05-10

## 今日やったこと
- Python開発
- Ruff設定追加
- README作成
- 英語学習
- 運動

## コード変更
- pyproject.toml 修正
- Ruff自動format導入
- GitHub公開準備

## 作業傾向
- 開発時間が長め
- 夜は集中力低下
```

## Tech Stack

- Python
- OpenAI API
- ActivityWatch
- Git
- Obsidian
- uv
- Ruff

## How It Works

1. ActivityWatch から作業ログ取得
2. Git diff からコード変更取得
3. OpenAI API で内容を整理・要約
4. Obsidian Daily Note に保存

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/Nishi-nlp/Daily_Summmary.git
cd Daily_Summmary
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

`.env` を編集して各項目を設定してください。

### 4. Run

```bash
uv run daily_summary.py
```

## License

MIT