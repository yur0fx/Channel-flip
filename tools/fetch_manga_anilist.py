"""Download every manga on AniList (~80,000, explicit titles excluded) via AniList's free GraphQL API.

Entries linked to MyAnimeList keep their MAL id (the site links them to MyAnimeList); AniList-only entries get a
negative id (-AniList id) and the site links them to AniList instead.

Writes gzipped JSON lines in the field layout build_manga.py reads.
AniList allows roughly 30-90 requests a minute; at 50 manga per request the full list takes about an hour.

Usage: python tools/fetch_manga_anilist.py OUT.jsonl.gz [--max-pages N] [--url URL] [--delay SECONDS]
"""
import gzip, json, re, sys, time, urllib.error, urllib.request

QUERY = """query ($page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    media(type: MANGA, sort: ID) {
      id idMal isAdult format countryOfOrigin chapters volumes status
      title { romaji english native }
      startDate { year }
      averageScore popularity genres
      tags { name rank category isMediaSpoiler isAdult }
      staff(perPage: 6, sort: RELEVANCE) { edges { role node { name { full } } } }
      coverImage { large }
      description(asHtml: false)
    }
  }
}"""

FORMAT = {"NOVEL": "Light Novel", "ONE_SHOT": "One-shot"}
COUNTRY = {"KR": "Manhwa", "CN": "Manhua", "TW": "Manhua", "HK": "Manhua"}
STATUS = {"FINISHED": "Finished", "RELEASING": "Publishing", "NOT_YET_RELEASED": "Not yet published",
          "HIATUS": "On Hiatus", "CANCELLED": "Discontinued"}
GENRE = {"Thriller": "Suspense", "Mahou Shoujo": "Mahou Shoujo"}
DEMOS = {"Shounen", "Seinen", "Shoujo", "Josei", "Kids"}


def post(url, page, tries=8):
    delay = 5.0
    body = json.dumps({"query": QUERY, "variables": {"page": page}}).encode()
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
    desc = re.sub(r"<[^>]+>", " ", m.get("description") or "")
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


def write(f, p, n, skipped):
    for m in p.get("media") or []:
        if m.get("isAdult"):
            skipped += 1
            continue
        row = to_row(m)
        if row:
            f.write(json.dumps(row, ensure_ascii=False) + "\n"); n += 1
    return n, skipped


def main():
    out = sys.argv[1]
    arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    max_pages, url, delay = int(arg("--max-pages", 10**6)), arg("--url", "https://graphql.anilist.co"), float(arg("--delay", 2.2))
    page, n, skipped, streak, failed = 1, 0, 0, 0, []
    with gzip.open(out, "wt", encoding="utf-8") as f:
        while page <= max_pages:
            d = post(url, page)
            p = (d or {}).get("data", {}).get("Page") if d else None
            if not p:
                streak += 1; failed.append(page)
                print(f"page {page}: failed, will retry at the end", flush=True)
                if streak >= 10:
                    sys.exit("AniList looks unavailable (10 pages failed in a row).")
                page += 1
                continue
            streak = 0
            n, skipped = write(f, p, n, skipped)
            if page % 50 == 0:
                print(f"page {page}: {n} manga kept, {skipped} explicit titles skipped", flush=True)
            if not (p.get("pageInfo") or {}).get("hasNextPage"):
                break
            page += 1
            time.sleep(delay)
        still = []
        for fp in failed:
            time.sleep(max(delay, 5))
            d = post(url, fp)
            p = (d or {}).get("data", {}).get("Page") if d else None
            if p: n, skipped = write(f, p, n, skipped)
            else: still.append(fp)
    print(f"done: {n} manga from {page} pages; {len(still)} pages could not be fetched {still[:20]}", flush=True)
    if n == 0:
        sys.exit("No manga downloaded; leaving the old data in place.")


if __name__ == "__main__":
    main()
