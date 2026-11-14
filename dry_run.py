"""post.py / harvest.py をローカルで投稿せずに動かす(トークンは画面に出さない)

使い方:
  python dry_run.py post
  python dry_run.py harvest
  python dry_run.py harvest --live   ← 実際に返信を投稿する
"""
import json
import os
import runpy
import sys

SEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets",
                   "threads_token.json")
with open(SEC, encoding="utf-8") as f:
    t = json.load(f)
os.environ["THREADS_ACCESS_TOKEN"] = t["access_token"]
os.environ["THREADS_USER_ID"] = t["user_id"]

which = sys.argv[1] if len(sys.argv) > 1 else "post"
live = "--live" in sys.argv
os.environ["DRY_RUN"] = "0" if live else "1"
print(f"=== {which}.py / {'本番(投稿します)' if live else 'DRY RUN(投稿しません)'} ===")
sys.argv = [f"{which}.py"]
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{which}.py"),
               run_name="__main__")
