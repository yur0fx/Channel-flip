"""Download manga from AniList's free GraphQL API (explicit titles excluded).

By default it takes the 10,000 most popular manga plus the 500 most recently added ones, which takes about
10 minutes. Pass --top 0 --newest 0 to download everything (~80,000, about an hour).

Entries linked to MyAnimeList keep their MAL id (the site links them to MyAnimeList); AniList-only entries get a
negative id (-AniList id) and the site links them to AniList instead.

Writes gzipped JSON lines in the field layout build_manga.py reads.
AniList allows roughly 30-90 requests a minute, at 50 manga per request.

Usage: python tools/fetch_manga_anilist.py OUT.jsonl.gz [--cache CACHE.jsonl.gz] [--top N] [--newest N]
       [--budget-minutes M] [--url URL] [--delay SECONDS]

With --cache, results are merged into the previous run's titles, so the catalog grows run by run toward --top
(new releases first, then the next most popular titles not yet in the catalog); once it's full, runs refresh it.

When the time budget runs out it stops and keeps what it has, so a slow day still publishes a (smaller) update.
"""
import gzip, json, re, sys, time, urllib.error, urllib.request

QUERY = """query ($page: Int, $sort: [MediaSort], $below: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    media(type: MANGA, sort: $sort, isAdult: false, popularity_lesser: $below) {
      id idMal isAdult format countryOfOrigin chapters volumes status
      title { romaji english native }
      startDate { year }
      averageScore popularity genres
      tags { name rank category isMediaSpoiler isAdult }
      staff(perPage: 4, sort: RELEVANCE) { edges { role node { name { full } } } }
      coverImage { large }
    }
  }
}"""

FORMAT = {"NOVEL": "Light Novel", "ONE_SHOT": "One-shot"}
COUNTRY = {"KR": "Manhwa", "CN": "Manhua", "TW": "Manhua", "HK": "Manhua"}
STATUS = {"FINISHED": "Finished", "RELEASING": "Publishing", "NOT_YET_RELEASED": "Not yet published",
          "HIATUS": "On Hiatus", "CANCELLED": "Discontinued"}
GENRE = {"Thriller": "Suspense", "Mahou Shoujo": "Mahou Shoujo"}
DEMOS = {"Shounen", "Seinen", "Shoujo", "Josei", "Kids"}


def post(url, page, tries=8, sort="ID", below=None):
    delay = 5.0
    body = json.dumps({"query": QUERY, "variables": {"page": page, "sort": [sort], "below": below}}).encode()
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "User-Agent": "channel-flip-data/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            wait = float(e.headers.get("Retry-After") or delay) + 1 if e.code == 429 else delay
            print(f"  HTTP {e.code} on page {page}, retry {attempt + 1} in {wait:.0f}s", flush=True)
        except Exception as e:
            wait = delay
            print(f"  {type(e).__name__} on page {page}, retry {attempt + 1} in {wait:.0f}s", flush=True)
        time.sleep(wait)
        delay = min(delay * 2, 90)
    return None


def to_row(m):
    """Map one AniList media object onto the fields build_manga.py reads."""
    fmt = m.get("format")
    ty = FORMAT.get(fmt) or (COUNTRY.get(m.get("countryOfOrigin"), "Manga") if fmt == "MANGA" else None)
    if not ty:
        return None
    genres = [GENRE.get(g, g) for g in (m.get("genres") or []) if g not in ("Hentai",)]
    tags = [t for t in (m.get("tags") or []) if not t.get("isMediaSpoiler") and not t.get("isAdult") and (t.get("rank") or 0) >= 55]
    demos = [t["name"] for t in tags if t.get("category", "").startswith("Demographic") and t["name"] in DEMOS]
    authors = []
    for e in ((m.get("staff") or {}).get("edges") or []):
        role = (e.get("role") or "").lower()
        if ("story" in role or "art" in role or "original" in role) and "assist" not in role and "letter" not in role:
            n = ((e.get("node") or {}).get("name") or {}).get("full")
            if n and n not in authors:
                authors.append(n)
    desc = ""  # synopses load live on the page; leaving them out keeps each request light
    t = m.get("title") or {}
    return {
        "mal_id": m.get("idMal") or -m["id"], "title": t.get("romaji") or t.get("english"), "title_english": t.get("english"),
        "title_japanese": t.get("native"), "type": ty, "chapters": m.get("chapters"), "volumes": m.get("volumes"),
        "status": STATUS.get(m.get("status")), "published": {"prop": {"from": {"year": (m.get("startDate") or {}).get("year")}}},
        "score": (m.get("averageScore") or 0) / 10 or None, "members": m.get("popularity") or 0,
        "authors": [{"name": a} for a in authors], "serializations": [],
        "genres": [{"name": g} for g in genres], "themes": [], "demographics": [{"name": d} for d in demos],
        "explicit_genres": [], "al_tags": [t["name"] for t in tags if t["name"] not in DEMOS],
        "cover": (m.get("coverImage") or {}).get("large") or "", "synopsis": desc,
    }


ROWS = {}  # mal_id (or -AniList id) -> row; the cache plus everything fetched this run
NEW = set()


def write(p, core, update_only=False):
    """Store one page of results; returns how many titles were new to the catalog.
    `core` marks titles from the popularity list (the newest-additions pass doesn't count toward it)."""
    added = 0
    for m in p.get("media") or []:
        if m.get("isAdult"):
            continue
        row = to_row(m)
        if row:
            old = ROWS.get(row["mal_id"])
            if old is None and update_only:
                continue
            if old is None:
                added += 1
            row["core"] = core or bool(old and old.get("core"))
            ROWS[row["mal_id"]] = row
            NEW.add(row["mal_id"])
    return added


def fetch(url, sort, max_pages, delay, label, deadline, below=None, want=None, update_only=False):
    """Page through one sorted list. Stops at max_pages, the deadline, the end of the list, or once
    `want` new titles were added. Returns (titles added, lowest popularity seen)."""
    page, streak, added, lowest, failed = 1, 0, 0, None, []
    while page <= max_pages:
        if time.time() > deadline:
            print(f"{label}: time budget reached at page {page}", flush=True)
            break
        t0 = time.time()
        d = post(url, page, sort=sort, below=below)
        p = (d or {}).get("data", {}).get("Page") if d else None
        if not p:
            streak += 1; failed.append(page)
            print(f"{label} page {page}: failed", flush=True)
            if streak >= 10:
                print(f"{label}: AniList looks unavailable, stopping this pass", flush=True)
                break
        else:
            streak = 0
            added += write(p, core=sort != "ID_DESC", update_only=update_only)
            pops = [m.get("popularity") or 0 for m in p.get("media") or []]
            if pops: lowest = min(pops) if lowest is None else min(lowest, min(pops))
            if page % 20 == 0:
                print(f"{label} page {page}: {added} new titles, {len(ROWS)} in catalog", flush=True)
            if not (p.get("pageInfo") or {}).get("hasNextPage") or not p.get("media"):
                break
            if want is not None and added >= want:
                break
        page += 1
        time.sleep(max(0.0, delay - (time.time() - t0)))
    return added, lowest, page


def main():
    out = sys.argv[1]
    arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    target, newest = int(arg("--top", 10000)), int(arg("--newest", 500))
    url, delay = arg("--url", "https://graphql.anilist.co"), float(arg("--delay", 2.2))
    end = time.time() + 60 * float(arg("--budget-minutes", 30))
    cache = arg("--cache", None)
    if cache:
        try:
            with gzip.open(cache, "rt", encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line); ROWS[r["mal_id"]] = r
            print(f"cache: {len(ROWS)} titles", flush=True)
        except FileNotFoundError:
            print("cache: none yet", flush=True)

    # 1. newest additions first, so new releases never get crowded out
    if newest:
        a, _, _ = fetch(url, "ID_DESC", (newest + 49) // 50, delay, "newest", end)
        print(f"newest: {a} new titles", flush=True)

    # 2. grow toward the target: continue down the popularity list below the least popular title we have.
    #    AniList stops a sorted list after 100 pages, so each slice restarts just below the lowest popularity seen.
    core = lambda: [r for r in ROWS.values() if r.get("core")]
    while time.time() < end and len(core()) < target:
        c = core()
        below = min((r.get("members") or 0) for r in c) + 1 if c else None  # +1 so titles tied at the cut-off aren't skipped
        a, lowest, page = fetch(url, "POPULARITY_DESC", 100, delay, f"popular<{below}", end, below=below, want=target - len(c))
        print(f"slice below {below}: {a} new titles, popular core now {len(core())}", flush=True)
        if a == 0:
            break

    # 3. catalog is full: spend the rest of the time refreshing the most popular titles (scores, chapters)
    if len(core()) >= target and time.time() < end:
        fetch(url, "POPULARITY_DESC", 100, delay, "refresh", end, update_only=True)

    with gzip.open(out, "wt", encoding="utf-8") as f:
        for r in ROWS.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"done: {len(ROWS)} titles in catalog, {len(NEW)} fetched this run", flush=True)
    if not ROWS:
        sys.exit("No manga downloaded; leaving the old data in place.")


if __name__ == "__main__":
    main()
