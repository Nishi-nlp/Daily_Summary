import os
import subprocess
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)

# ── 設定 ──────────────────────────────────────────────

# セミコロン区切りで複数リポジトリ指定可能
_raw_paths = os.environ.get("GIT_REPO_PATH")
if not _raw_paths:
    raise EnvironmentError("環境変数 GIT_REPO_PATH が設定されていません")
GIT_REPO_PATHS: list[Path] = [
    Path(p.strip()) for p in _raw_paths.split(";") if p.strip()
]

NOTES_DIR = Path(os.environ.get("NOTES_DIR"))

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "output_dir"))
TODAY = datetime.now().strftime("%Y-%m-%d")

api_key = os.getenv("OPENAI_API_KEY")


# ── 1. ActivityWatch のデータ取得 ──────────────────────
def _fmt_duration(seconds: float) -> str:
    """秒を「X時間Y分」形式に変換"""
    m = int(seconds // 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}時間{m}分"
    return f"{m}分"


def get_activity_logs() -> str:
    try:
        buckets = requests.get("http://localhost:5600/api/0/buckets", timeout=3).json()
    except Exception:
        return "ActivityWatch に接続できません（起動していない可能性があります）"

    start = (
        datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    )
    end = datetime.now().isoformat()

    lines = []

    for bucket_id, bucket_info in buckets.items():
        try:
            events = requests.get(
                f"http://localhost:5600/api/0/buckets/{bucket_id}/events",
                params={"start": start, "end": end, "limit": 500},
                timeout=3,
            ).json()
        except Exception:
            continue

        if not events:
            continue

        bucket_type = bucket_info.get("type", "")

        # ── ウィンドウ監視バケット（Cursorなどのアプリ作業） ──
        if bucket_type == "currentwindow":
            # アプリごとに合計時間を集計
            app_time: dict[str, float] = {}
            # Cursorのファイル別作業時間
            cursor_files: dict[str, float] = {}

            STEAM_APPS = {"steam", "steamwebhelper", "gameoverlayui"}
            game_time: dict[str, float] = {}

            for e in events:
                data = e.get("data", {})
                duration = e.get("duration", 0)
                app = data.get("app", "不明")
                title = data.get("title", "")

                app_time[app] = app_time.get(app, 0) + duration
                app_lower = app.lower()

                # Steam関連の判定
                is_steam = (
                    app_lower in STEAM_APPS
                    or "steam" in app_lower
                    or "steam" in title.lower()
                )

                if is_steam:
                    # ゲーム名はタイトルから（"Steam"単体は除外）
                    game_name = (
                        title.strip()
                        if title and title.lower() not in ("steam", "")
                        else app
                    )
                    game_time[game_name] = game_time.get(game_name, 0) + duration
                else:
                    app_time[app] = app_time.get(app, 0) + duration

                # Cursorの場合はタイトルからファイル名・プロジェクトを抽出
                # タイトル例: "daily_summary.py - myproject - Cursor"
                if "cursor" in app_lower or "cursor" in title.lower():
                    # タイトルからファイル名部分を取得（" - " で分割）
                    parts = [p.strip() for p in title.split(" - ")]
                    if parts:
                        file_key = (
                            " / ".join(parts[:-1]) if len(parts) > 1 else parts[0]
                        )
                        cursor_files[file_key] = (
                            cursor_files.get(file_key, 0) + duration
                        )

            # 合計5分以上のアプリのみ表示、時間順にソート
            lines.append("\n【アプリ別作業時間】")
            for app, sec in sorted(app_time.items(), key=lambda x: -x[1]):
                if sec >= 300:
                    lines.append(f"  {app}: {_fmt_duration(sec)}")

            # Cursorの作業ファイル詳細
            if cursor_files:
                lines.append("\n【Cursor 作業ファイル】")
                for file_key, sec in sorted(cursor_files.items(), key=lambda x: -x[1]):
                    if sec >= 60:
                        lines.append(f"  {_fmt_duration(sec):8s}  {file_key}")

            # Steamゲーム
            if game_time:
                lines.append("\n【🎮 ゲーム（Steam）】")
                for game, sec in sorted(game_time.items(), key=lambda x: -x[1]):
                    if sec >= 60:
                        lines.append(f"  {_fmt_duration(sec):8s}  {game}")

        # ── AFK（離席）バケット ──
        elif bucket_type == "afkstatus":
            total_active = sum(
                e.get("duration", 0)
                for e in events
                if e.get("data", {}).get("status") == "not-afk"
            )
            lines.append("\n【PC作業時間（AFK除く）】")
            lines.append(f"  {_fmt_duration(total_active)}")

        # ── ブラウザ閲覧バケット ──
        elif bucket_type in ("web.tab.current", "currently-active-browser-tab"):
            EXCLUDED_DOMAINS = {"www.youtube.com", "youtube.com", "youtu.be"}
            url_time: dict[str, float] = {}
            for e in events:
                data = e.get("data", {})
                url = data.get("url", "")
                title = data.get("title", url)
                duration = e.get("duration", 0)
                # ドメインだけ取り出す
                domain = url.split("/")[2] if url.startswith("http") else url
                if domain in EXCLUDED_DOMAINS:
                    continue
                url_time[domain] = url_time.get(domain, 0) + duration

            lines.append("\n【ブラウザ閲覧（ドメイン別）】")
            for domain, sec in sorted(url_time.items(), key=lambda x: -x[1])[:10]:
                if sec >= 60:
                    lines.append(f"  {_fmt_duration(sec):8s}  {domain}")

    return "\n".join(lines) if lines else "ActivityWatch のデータなし"


# ── 2. git diff の取得（複数リポジトリ対応）────────────
def _get_git_info_single(repo_path: Path) -> tuple[str, str]:
    """1つのリポジトリからgit情報を取得する"""
    try:
        stat = subprocess.check_output(
            ["git", "diff", "--stat"],
            text=True,
            encoding="utf-8",
            cwd=repo_path,
        )
        diff = subprocess.check_output(
            ["git", "diff"],
            text=True,
            encoding="utf-8",
            cwd=repo_path,
        )
        return stat or "変更なし", diff
    except subprocess.CalledProcessError:
        return "git情報取得失敗（リポジトリ外？）", ""
    except FileNotFoundError:
        return "gitが見つかりません", ""


def get_git_info() -> tuple[str, str]:
    """全リポジトリのgit情報をまとめて返す"""
    all_stat: list[str] = []
    all_diff: list[str] = []

    for repo_path in GIT_REPO_PATHS:
        name = repo_path.resolve().name  # フォルダ名をプロジェクト名として使用
        stat, diff = _get_git_info_single(repo_path)
        all_stat.append(f"### {name}\n{stat}")
        if diff:
            all_diff.append(f"### {name}\n{diff}")

    return "\n\n".join(all_stat), "\n\n".join(all_diff)


# ── 3. 手動メモの取得 ─────────────────────────────────
def get_manual_note() -> str:
    note_path = Path(NOTES_DIR / f"{TODAY}.md")
    if note_path.exists():
        return note_path.read_text(encoding="utf-8")
    return "手動メモなし"


# ── 4. AIでサマリー生成 ───────────────────────────────
def generate_summary(
    activity_logs: str,
    git_stat: str,
    git_diff: str,
    manual_note: str,
) -> str:
    client = OpenAI(api_key=api_key)

    prompt = f"""
以下は{TODAY}の活動ログです。日本語で整理してください。

# ActivityWatch（PC作業ログ）
{activity_logs}

# git diff --stat
{git_stat}

# git diff（詳細）
{git_diff[:3000] if len(git_diff) > 3000 else git_diff}

# 手動メモ
{manual_note}

---

以下の形式でMarkdownにまとめてください：

## 今日やったこと
## 学んだこと
## 問題点・気になること
## 明日のTODO


"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini", messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content


# ── 6. 生ログのMarkdown生成（AIなしバージョン）────────
def generate_raw_log(git_stat: str) -> str:
    lines = [
        f"# 作業記録 {TODAY}\n",
        "## 🔧 git変更",
        git_stat,
    ]
    return "\n".join(lines)


# ── メイン ────────────────────────────────────────────
def main():
    print(f"📅 {TODAY} のサマリーを生成中...\n")

    activity_logs = get_activity_logs()
    print("✅ ActivityWatchデータ取得完了")

    git_stat, git_diff = get_git_info()
    print("✅ gitデータ取得完了")

    manual_note = get_manual_note()
    print("✅ 手動メモ取得完了")

    # 出力フォルダ作成
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{TODAY}.md"

    # AIサマリー生成
    if api_key:
        print("\n🤖 AIでサマリー生成中...")
        summary = generate_summary(activity_logs, git_stat, git_diff, manual_note)
        content = f"# 作業記録 {TODAY}\n\n{summary}\n\n---\n\n"
    else:
        print("\n⚠️  OPENAI_API_KEY が未設定のため生ログのみ保存します")
        content = generate_raw_log(git_stat)

    output_path.write_text(content, encoding="utf-8")
    print(f"\n✅ 保存完了: {output_path}")


if __name__ == "__main__":
    main()
