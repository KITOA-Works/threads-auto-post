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
MIN_GAP_HOURS = 4  # 前の投稿からこれだけ空ける(連投はスパム判定されやすい)


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


def slot_of(hour):
    """JSTの時刻から枠を決める。深夜(0〜6時)は「どの枠でもない」= None。

    GitHub Actions の定時実行は遅延・スキップされることがあり、遅れて日付を
    またぐと翌日の番号を投稿してしまう(実際 10/5 02:20 に起きた)。
    深夜の実行は投稿しないことで、それを防ぐ。
    """
    if 6 <= hour < 11:
        return 0  # 朝
    if 11 <= hour < 16:
        return 1  # 昼
    if 16 <= hour < 24:
        return 2  # 夜
    return None


def recent_posts(user_id, token, limit=12):
    """直近の自分の投稿(本文と投稿時刻)を取得する。取れなければ None"""
    try:
        data = api("GET", f"{user_id}/threads", {
            "fields": "text,timestamp", "limit": limit, "access_token": token,
        })
    except SystemExit:
        return None
    out = []
    for m in data.get("data", []):
        ts = datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00")).astimezone(JST)
        out.append((ts, (m.get("text") or "").strip()))
    return out


def choose_index(cfg, posts, now, recent):
    """posts の番号を決める(0始まり)。

    時刻の「枠」ではなく、**今日すでに何件投稿したか**で決める。
    GitHub Actions の定時実行は数時間ずれることがあり、枠で決めていると
    昼の実行が夜の枠に流れ込んで1日2回に減ってしまうため(10/15〜実際に発生)。

    戻り値: 番号 / None(今は投稿しない) / "exhausted"(リストを使い切り)
    """
    per_day = cfg.get("posts_per_day", 3)
    if slot_of(now.hour) is None:
        return None  # 深夜は投稿しない
    if recent is None:
        return None  # 投稿済み件数が確認できないときは、重複を避けて投稿しない

    today = [t for t, _ in recent if t.date() == now.date()]
    if len(today) >= per_day:
        return None  # 今日の分は投稿済み
    if today and (now - max(today)).total_seconds() < MIN_GAP_HOURS * 3600:
        return None  # 連投を避ける(短時間に続けて出すとスパム扱いされやすい)

    start = datetime.strptime(cfg["start_date"], "%Y-%m-%d").date()
    days = max((now.date() - start).days, 0)
    n = days * per_day + len(today)
    if n >= len(posts):
        return "exhausted"
    return n


def build_texts(cfg, post):
    """(本文, リプライ文) を返す。リプライ文が無い旧形式(本文にリンク入り)にも対応"""
    body = post["text"].rstrip()
    disclosure = cfg.get("disclosure", "")
    reply = (post.get("reply") or "").rstrip()
    if reply:
        if disclosure and disclosure not in reply:
            reply = f"{reply}\n\n{disclosure}"
        return body, reply
    # リンクを貼らない雑談投稿には開示文を付けない(広告ではないため)
    if "amazon.co.jp" not in body:
        return body, ""
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
    recent = recent_posts(user_id, token) if (token and user_id) else None
    forced = os.environ.get("POST_INDEX")
    if forced not in (None, ""):
        idx = int(forced)
    else:
        idx = choose_index(cfg, posts, now, recent)
        if idx is None:
            print(f"[{now:%Y-%m-%d %H:%M} JST] 今回は投稿しません"
                  "(深夜 / 今日の分は投稿済み / 前の投稿から時間が空いていない のいずれか)")
            return
        if idx == "exhausted":
            raise SystemExit(
                f"posts.json({len(posts)}件)を使い切りました。同じ投稿の繰り返しを防ぐため投稿しません。"
                " 新しい posts.json をアップロードしてください"
            )
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

    # 直近の投稿と同じ本文なら投稿しない(定時実行の遅延・二重起動による重複を防ぐ)
    if body.strip() in [t for _, t in (recent or [])]:
        print("直近に同じ本文を投稿済みのため、今回はスキップします")
        return

    main_id = publish_text(user_id, token, body)
    info = api("GET", main_id, {"fields": "permalink", "access_token": token})
    print(f"投稿しました: {info.get('permalink', main_id)}")

    if reply:
        time.sleep(3)
        reply_id = publish_text(user_id, token, reply, reply_to_id=main_id)
        print(f"リンクをリプライしました: id={reply_id}")


if __name__ == "__main__":
    sys.exit(main())
