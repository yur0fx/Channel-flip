"""Download every manga on MyAnimeList from the Jikan API (https://jikan.moe) into a gzipped JSON-lines file.

Jikan allows about 3 requests a second and 60 a minute, so this waits a little over a second between
pages. With ~25 entries per page the full catalog takes roughly an hour. Run by .github/workflows/manga-data.yml.

Usage: python tools/fetch_manga.py OUT.jsonl.gz [--max-pages N] [--base URL] [--delay SECONDS]
"""
import gzip, json, sys, time, urllib.error, urllib.request

KEEP = ("mal_id", "title", "title_english", "title_japanese", "type", "chapters", "volumes", "status",
        "published", "score", "scored_by", "members", "authors", "serializations", "genres", "themes",
        "demographics", "explicit_genres", "images", "synopsis")


def get(url, tries=8):
    delay = 2.0
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "channel-flip-data/1.0 (+https://github.com)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            wait = delay if e.code != 429 else max(delay, 5)
            print(f"  HTTP {e.code} on {url}, retry {attempt + 1} in {wait:.0f}s", flush=True)
        except Exception as e:  # timeouts, resets
            wait = delay
            print(f"  {type(e).__name__} on {url}, retry {attempt + 1} in {wait:.0f}s", flush=True)
        time.sleep(wait)
        delay = min(delay * 2, 60)
    return None


def main():
    out = sys.argv[1]
    max_pages = int(sys.argv[sys.argv.index("--max-pages") + 1]) if "--max-pages" in sys.argv else 10**6
    base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "https://api.jikan.moe/v4"
    delay = float(sys.argv[sys.argv.index("--delay") + 1]) if "--delay" in sys.argv else 1.15
    page, last, n, failed, streak = 1, None, 0, [], 0
    url = lambda p: f"{base}/manga?page={p}&limit=25&order_by=mal_id&sort=asc&sfw=true"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        def write(d):
            nonlocal n
            for m in d["data"]:
                f.write(json.dumps({k: m.get(k) for k in KEEP}, ensure_ascii=False) + "\n")
                n += 1
        while page <= max_pages and (last is None or page <= last):
            t0 = time.time()
            d = get(url(page))
            if d is None or "data" not in d:
                failed.append(page); streak += 1
                print(f"page {page}: failed, will retry at the end", flush=True)
                if streak >= 20:
                    sys.exit("Jikan looks unavailable (20 pages failed in a row); leaving the old data in place.")
            else:
                streak = 0
                last = d.get("pagination", {}).get("last_visible_page", last or page)
                write(d)
                if page % 50 == 0 or page == last:
                    print(f"page {page}/{last}: {n} manga so far", flush=True)
            page += 1
            time.sleep(max(0.0, delay - (time.time() - t0)))  # the request time counts toward Jikan's spacing
        still = []
        for p in failed:  # one more pass over pages that failed the first time
            time.sleep(max(delay, 2))
            d = get(url(p))
            if d is None or "data" not in d: still.append(p)
            else: write(d)
        failed = still
    print(f"done: {n} manga, {len(failed)} pages could not be fetched {failed[:20]}", flush=True)
    if n == 0:
        sys.exit("No manga downloaded; leaving the old data in place.")


if __name__ == "__main__":
    main()
