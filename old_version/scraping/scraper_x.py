# scraping/scraper_x.py
import os, json, tempfile, shutil, sys, traceback
from typing import List, Dict, Any
import tweepy

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from paths import resolve_out, ensure_dir  # noqa: E402

OUT_PATH = resolve_out("SALIDAS_X", "salidas_x.json")
BEARER   = os.getenv("TWITTER_BEARER_TOKEN")
USERS    = os.getenv("TWITTER_USERS", "gcba,jorgemacri")
TWEET_COUNT = int(os.getenv("TWITTER_TWEET_COUNT", "8"))
DEBUG    = os.getenv("SCRAPER_DEBUG", "1") == "1"

def log(*a):
    if DEBUG: print("[X]", *a, flush=True)

def _atomic_write_json(path: str, data: Any):
    d = ensure_dir(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_x_", suffix=".json", dir=d)
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)

def _clean(text: str) -> str:
    return (text or "").replace("\r","").strip()

def main():
    if not BEARER:
        _atomic_write_json(OUT_PATH, [])
        print("ERROR: falta TWITTER_BEARER_TOKEN"); sys.exit(1)

    client = tweepy.Client(bearer_token=BEARER)
    out: List[Dict[str, Any]] = []

    for username in [u.strip() for u in USERS.split(",") if u.strip()]:
        try:
            user = client.get_user(username=username)
            if not user.data:
                log("no user:", username); continue
            uid = user.data.id
            tweets = client.get_users_tweets(id=uid, max_results=min(100, max(5, TWEET_COUNT)))
            if not tweets.data:
                log("sin tweets recientes:", username); continue
            for tw in tweets.data[:TWEET_COUNT]:
                txt = _clean(tw.text)
                if not txt: continue
                title = (txt[:100] + "...") if len(txt) > 100 else txt
                out.append({"titulo": title, "descripcion": txt})
        except Exception as e:
            log("error user", username, e)

    _atomic_write_json(OUT_PATH, out or [])
    print(f"OK: Guardado {len(out)} items en {OUT_PATH}")
    if not out:
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try: _atomic_write_json(OUT_PATH, [])
        except Exception: pass
        print("ERROR:", e)
        if DEBUG: traceback.print_exc()
        sys.exit(1)
