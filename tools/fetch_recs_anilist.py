"""Download AniList's community recommendations ("people who liked X also liked Y", voted on by users)
for the most popular anime and manga.

Usage: python tools/fetch_recs_anilist.py CACHE.json.gz [--anime N] [--manga N] [--budget-minutes M]
       [--summary FILE] [--delay SECONDS]

The cache maps "anime"/"manga" -> {site id: [[site id, votes], ...]}, best-voted first. Site ids are MyAnimeList
ids, or -AniList id for manga that aren't on MyAnimeList (the anime site only uses MAL ids). Titles fetched this
run replace their old entries; titles not reached keep last run's, so a slow day never loses data.
"""
import gzip, json, sys, time, urllib.error, urllib.request

URL = "https://graphql.anilist.co"
QUERY = """query ($page: Int, $type: MediaType, $per: Int, $recs: IntBELOW_VAR) {
  Page(page: $page, perPage: $per) {
    pageInfo { hasNextPage }
    media(type: $type, sort: POPULARITY_DESC, isAdult: falseBELOW_ARG) {
      id idMal popularity
      recommendations(perPage: $recs, sort: RATING_DESC) {
        nodes { rating mediaRecommendation { id idMal isAdult } }
      }
    }
  }
}"""
ERRORS, STATS = [], {}


def post(variables, below, tries=6):
    q = QUERY.replace("BELOW_VAR", ", $below: Int" if below is not None else "").replace(
        "BELOW_ARG", ", popularity_lesser: $below" if below is not None else "")
    v = dict(variables)
    if below is not None:
        v["below"] = below
    body = json.dumps({"query": q, "variables": v}).encode()
    delay = 5.0
    for attempt in range(tries):
        try:
            req = urllib.request.Request(URL, data=body, headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "User-Agent": "channel-flip-data/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            try: msg = e.read().decode("utf-8", "replace")[:300]
            except Exception: msg = ""
            if len(ERRORS) < 8: ERRORS.append(f"HTTP {e.code} ({variables.get('type')} p{variables.get('page')}): {msg}")
            if e.code in (400, 401, 403, 404):
                return None, msg
            wait = float(e.headers.get("Retry-After") or delay) + 1 if e.code == 429 else delay
        except Exception as e:
            if len(ERRORS) < 8: ERRORS.append(f"{type(e).__name__} ({variables.get('type')}): {e}")
            wait = delay
        time.sleep(wait)
        delay = min(delay * 2, 60)
    return None, None


def site_id(m, kind):
    if not m or m.get("isAdult"):
        return None
    if m.get("idMal"):
        return m["idMal"]
    return -m["id"] if kind == "manga" else None


def fetch(kind, target, deadline, delay):
    """Walk the popularity list (in slices: AniList stops a sorted list after 100 pages)."""
    out, below, per, recs = {}, None, 50, 8
    while len(out) < target and time.time() < deadline:
        page, got, streak = 1, 0, 0
        lowest = None
        while page <= 100 and len(out) < target and time.time() < deadline:
            t0 = time.time()
            d, bad = post({"page": page, "type": kind.upper(), "per": per, "recs": recs}, below)
            p = ((d or {}).get("data") or {}).get("Page")
            if not p:
                if bad and "omplex" in bad and per > 10:  # query too heavy for AniList: ask for fewer per page
                    per //= 2; page = (page - 1) * 2 + 1 if per else page
                    continue
                streak += 1
                STATS[kind + " failed"] = STATS.get(kind + " failed", 0) + 1
                if streak >= 5: return out
                page += 1; continue
            streak = 0
            STATS[kind + " pages"] = STATS.get(kind + " pages", 0) + 1
            for m in p.get("media") or []:
                sid = site_id(m, kind)
                pop = m.get("popularity") or 0
                lowest = pop if lowest is None else min(lowest, pop)
                if sid is None or sid in out:
                    continue
                lst = []
                for n in ((m.get("recommendations") or {}).get("nodes") or []):
                    rid = site_id(n.get("mediaRecommendation"), kind)
                    if rid is not None and rid != sid and (n.get("rating") or 0) > 0 and rid not in [x[0] for x in lst]:
                        lst.append([rid, n["rating"]])
                out[sid] = lst; got += 1
            if page % 25 == 0:
                print(f"{kind}: {len(out)} titles", flush=True)
            if not (p.get("pageInfo") or {}).get("hasNextPage") or not p.get("media"):
                return out
            page += 1
            time.sleep(max(0.0, delay - (time.time() - t0)))
        if not got or lowest is None:
            break
        below = lowest + 1  # continue just below the least popular title seen (ties are de-duplicated)
    return out


def main():
    path = sys.argv[1]
    arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    end = time.time() + 60 * float(arg("--budget-minutes", 30))
    delay = float(arg("--delay", 2.2))
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            cache = json.load(f)
    except (FileNotFoundError, ValueError):
        cache = {}
    plan = [("manga", int(arg("--manga", 11000))), ("anime", int(arg("--anime", 12000)))]
    for i, (kind, n) in enumerate(plan):
        share = end if i == len(plan) - 1 else time.time() + (end - time.time()) / 2
        got = fetch(kind, n, share, delay)
        old = {int(k): v for k, v in (cache.get(kind) or {}).items()}
        old.update(got)
        cache[kind] = old
        STATS[kind + " fetched"] = len(got)
        print(f"{kind}: fetched {len(got)}, cache {len(old)}", flush=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"))
    s = arg("--summary", None)
    if s:
        with open(s, "w") as f:
            f.write(f"finished {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")
            f.write(" | ".join(f"{k} {v}" for k, v in STATS.items()) + "\n")
            f.write(" | ".join(f"{k} cached {len(cache.get(k) or {})}" for k in ("anime", "manga")) + "\n")
            f.write("".join(f"error: {e}\n" for e in ERRORS))


if __name__ == "__main__":
    main()
