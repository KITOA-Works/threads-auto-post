"""Threads 自動投稿スクリプト(GitHub Actions から1日3回呼ばれる)

- posts.json の投稿を、日付とスロット(朝/昼/夜)から決めた順番で1件投稿する
- 状態ファイルは持たない(順番は日付から計算するので、どの実行環境でも同じ結果になる)
- 標準ライブラリのみ使用(pip 不要)

環境変数:
  THREADS_ACCESS_TOKEN  長期アクセストークン(GitHub Secrets)
  THREADS_USER_ID       Threads のユーザーID(GitHub Secrets)
  POST_INDEX            (任意)この番号の投稿を強制的に使う。手動テスト用
  DRY_RUN               (任意)"1" なら投稿せず、選ばれた文面だけ表示する
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://graph.threads.net/v1.0"
JST = timezone(timedelta(hours=9))
PLACEHOLDER_MARKERS = ("【リンク】", "【商品名】", "https://amzn.to/xxxx")


def api(method, path, params):
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    if method == "GET":
        url = f"{url}?{data.decode()}"
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise SystemExit(f"API error {e.code} on {path}: {body}")


def choose_index(cfg, posts, now):
    """日付とスロットから posts の番号を決める(0始まり)。"""
    start = datetime.strptime(cfg["start_date"], "%Y-%m-%d").date()
    days = (now.date() - start).days
    hour = now.hour
    if hour < 10:
        slot = 0  # 朝
    elif hour < 16:
        slot = 1  # 昼
    else:
        slot = 2  # 夜
    per_day = cfg.get("posts_per_day", 3)
    return (max(days, 0) * per_day + slot) % len(posts)


def main():
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    user_id = os.environ.get("THREADS_USER_ID")
    dry_run = os.environ.get("DRY_RUN") == "1"

    with open("posts.json", encoding="utf-8") as f:
        cfg = json.load(f)
    posts = cfg["posts"]
    if not posts:
        raise SystemExit("posts.json に投稿がありません")

    now = datetime.now(JST)
    forced = os.environ.get("POST_INDEX")
    idx = int(forced) if forced not in (None, "") else choose_index(cfg, posts, now)
    post = posts[idx % len(posts)]

    text = post["text"].rstrip()
    disclosure = cfg.get("disclosure", "")
    if disclosure and disclosure not in text:
        text = f"{text}\n\n{disclosure}"

    print(f"[{now:%Y-%m-%d %H:%M} JST] index={idx} / {len(posts)}")
    print("-" * 40)
    print(text)
    print("-" * 40)

    problems = [f"未記入の箇所({m})があります" for m in PLACEHOLDER_MARKERS if m in text]
    if len(text) > 500:
        problems.append(f"本文が500文字を超えています({len(text)}文字)")

    if dry_run:
        for p in problems:
            print(f"警告: {p}")
        print("DRY_RUN=1 のため投稿しません")
        return
    if problems:
        raise SystemExit("投稿を中止しました: " + " / ".join(problems))
    if not token or not user_id:
        raise SystemExit("THREADS_ACCESS_TOKEN / THREADS_USER_ID が設定されていません")

    created = api("POST", f"{user_id}/threads", {
        "media_type": "TEXT",
        "text": text,
        "access_token": token,
    })
    time.sleep(5)  # コンテナ処理待ち(公式推奨)
    published = api("POST", f"{user_id}/threads_publish", {
        "creation_id": created["id"],
        "access_token": token,
    })
    info = api("GET", published["id"], {"fields": "permalink", "access_token": token})
    print(f"投稿しました: {info.get('permalink', published['id'])}")


if __name__ == "__main__":
    sys.exit(main())
