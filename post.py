"""Threads 自動投稿スクリプト(GitHub Actions から1日3回呼ばれる)

- posts.json の投稿を、日付とスロット(朝/昼/夜)から決めた順番で1件投稿する
- 本文(リンクなし)を投稿 → その投稿に自分でリプライしてリンク+開示文を付ける(2段階)
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


def publish_text(user_id, token, text, reply_to_id=None):
    """テキスト投稿(またはリプライ)を作成して公開し、media id を返す"""
    params = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    created = api("POST", f"{user_id}/threads", params)
    time.sleep(5)  # コンテナ処理待ち(公式推奨)
    published = api("POST", f"{user_id}/threads_publish", {
        "creation_id": created["id"],
        "access_token": token,
    })
    return published["id"]


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


def build_texts(cfg, post):
    """(本文, リプライ文) を返す。リプライ文が無い旧形式(本文にリンク入り)にも対応"""
    body = post["text"].rstrip()
    disclosure = cfg.get("disclosure", "")
    reply = (post.get("reply") or "").rstrip()
    if reply:
        if disclosure and disclosure not in reply:
            reply = f"{reply}\n\n{disclosure}"
        return body, reply
    # 旧形式: 本文にリンクと開示文をまとめる
    if disclosure and disclosure not in body:
        body = f"{body}\n\n{disclosure}"
    return body, ""


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
    body, reply = build_texts(cfg, post)

    print(f"[{now:%Y-%m-%d %H:%M} JST] index={idx} / {len(posts)}")
    print("---- 本文 ----")
    print(body)
    if reply:
        print("---- リプライ ----")
        print(reply)
    print("--------------")

    problems = []
    for label, t in (("本文", body), ("リプライ", reply)):
        problems += [f"{label}に未記入の箇所({m})があります" for m in PLACEHOLDER_MARKERS if m in t]
        if len(t) > 500:
            problems.append(f"{label}が500文字を超えています({len(t)}文字)")

    if dry_run:
        for p in problems:
            print(f"警告: {p}")
        print("DRY_RUN=1 のため投稿しません")
        return
    if problems:
        raise SystemExit("投稿を中止しました: " + " / ".join(problems))
    if not token or not user_id:
        raise SystemExit("THREADS_ACCESS_TOKEN / THREADS_USER_ID が設定されていません")

    main_id = publish_text(user_id, token, body)
    info = api("GET", main_id, {"fields": "permalink", "access_token": token})
    print(f"投稿しました: {info.get('permalink', main_id)}")

    if reply:
        time.sleep(3)
        reply_id = publish_text(user_id, token, reply, reply_to_id=main_id)
        print(f"リンクをリプライしました: id={reply_id}")


if __name__ == "__main__":
    sys.exit(main())
