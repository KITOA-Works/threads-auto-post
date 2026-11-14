"""伸びた投稿にだけ、後からリンクのリプライを足す(収穫)

なぜこれが必要か:
  投稿の平均表示は111。そこにリンクを埋めても誰も見ない。
  一方で60本に1本くらい2万表示まで伸びる投稿がある(10/25「夜に干した洗濯物が…」21,917)。
  リンクを出す価値があるのはそこだけなので、伸びてから足す。

仕組み:
  posts.json の各投稿に任意の "harvest" を持たせておく(伸びたときに出す返信文)。
  このスクリプトは直近の自分の投稿を見て、
    - posts.json の harvest と本文が一致し
    - 表示数が harvest_min_views 以上で
    - まだ自分の返信が付いていない
  ものに、その返信を1件だけ投稿する。

環境変数は post.py と同じ(THREADS_ACCESS_TOKEN / THREADS_USER_ID)。
DRY_RUN=1 なら投稿せず判定だけ表示する。
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
LOOKBACK_DAYS = 14   # これより古い投稿は対象にしない
MAX_PER_RUN = 1      # 1回の実行で足す返信は1件まで


def api(method, path, params):
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    if method == "GET":
        req = urllib.request.Request(f"{url}?{data.decode()}")
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"API error {e.code} on {path}: {e.read().decode(errors='replace')}")


def views_of(media_id, token):
    try:
        d = api("GET", f"{media_id}/insights", {"metric": "views", "access_token": token})
    except SystemExit:
        return None
    for m in d.get("data", []):
        if m["name"] == "views":
            return m.get("values", [{}])[0].get("value", 0)
    return None


STATE = "harvested.json"


def load_state():
    """すでに収穫した投稿のid。

    当初は /{media_id}/replies で「自分が返信済みか」を見ていたが、この
    アプリは返信を読む権限が無く(Application does not have permission)、
    常に「確認できない=投稿しない」に倒れて一度も動かなかった。
    そのため、収穫した投稿のidをリポジトリのファイルに残す方式に変えた。
    """
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def publish_reply(user_id, token, text, reply_to_id):
    created = api("POST", f"{user_id}/threads", {
        "media_type": "TEXT", "text": text,
        "reply_to_id": reply_to_id, "access_token": token})
    time.sleep(5)
    return api("POST", f"{user_id}/threads_publish", {
        "creation_id": created["id"], "access_token": token})["id"]


def main():
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    user_id = os.environ.get("THREADS_USER_ID")
    dry_run = os.environ.get("DRY_RUN") == "1"
    if not token or not user_id:
        raise SystemExit("THREADS_ACCESS_TOKEN / THREADS_USER_ID が設定されていません")

    with open("posts.json", encoding="utf-8") as f:
        cfg = json.load(f)
    threshold = cfg.get("harvest_min_views", 1500)
    disclosure = cfg.get("disclosure", "")
    plan = {p["text"].strip(): p["harvest"] for p in cfg["posts"] if p.get("harvest")}
    if not plan:
        print("harvest を持つ投稿がありません")
        return

    state = load_state()
    now = datetime.now(JST)
    mine = api("GET", f"{user_id}/threads",
               {"fields": "id,text,timestamp,permalink", "limit": 50, "access_token": token})

    done = 0
    for m in mine.get("data", []):
        if done >= MAX_PER_RUN:
            break
        body = (m.get("text") or "").strip()
        reply = plan.get(body)
        if not reply:
            continue
        ts = datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00")).astimezone(JST)
        if (now - ts).days > LOOKBACK_DAYS:
            continue
        head = body.split("\n", 1)[0][:24]
        if m["id"] in state:
            continue  # 収穫済み
        v = views_of(m["id"], token)
        if v is None or v < threshold:
            print(f"見送り {ts:%m-%d %H:%M} {v}表示 (基準{threshold}) 「{head}」")
            continue

        if disclosure and disclosure not in reply:
            reply = f"{reply}\n\n{disclosure}"
        print(f"\n★ {v}表示まで伸びたので返信を足します: 「{head}」")
        print(m.get("permalink", ""))
        print("---- 返信 ----")
        print(reply)
        print("--------------")
        if dry_run:
            print("DRY_RUN=1 のため投稿しません")
            continue
        rid = publish_reply(user_id, token, reply, m["id"])
        state[m["id"]] = {"at": now.isoformat(timespec="seconds"), "views": v,
                          "reply_id": rid, "head": head}
        save_state(state)
        print(f"返信しました: id={rid}")
        done += 1

    if done == 0:
        print(f"[{now:%Y-%m-%d %H:%M} JST] 基準({threshold}表示)に届いた投稿はありませんでした")


if __name__ == "__main__":
    sys.exit(main())
