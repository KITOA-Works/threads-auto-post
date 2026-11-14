"""harvest(伸びたときに出す返信)に入っているリンクをHTTPで確認する"""
import json
import re
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
with open("posts.json", encoding="utf-8") as f:
    cfg = json.load(f)

urls = []
for i, p in enumerate(cfg["posts"]):
    for u in re.findall(r"https://\S+", p.get("harvest") or ""):
        urls.append((i, u))

if not urls:
    print("harvest にリンクはありません")
for i, u in urls:
    try:
        req = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read(4000).decode("utf-8", errors="replace")
        title = re.search(r"<title>(.*?)</title>", body, re.S)
        title = re.sub(r"\s+", " ", title.group(1)).strip()[:60] if title else ""
        print(f"{i:>2} {r.status} OK  {title}")
    except urllib.error.HTTPError as e:
        print(f"{i:>2} {e.code} NG  {u}")
    except Exception as e:
        print(f"{i:>2} ERR {type(e).__name__}  {u}")

tags = {u.split("tag=")[-1].split("&")[0] for _, u in urls}
print("トラッキングID:", tags)
