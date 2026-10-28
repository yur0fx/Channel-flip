"""Combine AniList's community recommendations with the site's own similarity picks.

For every title, picks that real readers/viewers voted for come first (most votes first); the remaining slots
up to 8 are filled with the closest matches by genres, themes, tags and creators. Picks from the same series
as the title itself are left out either way.

Usage: python tools/merge_recs.py DATA_DIR
  reads  DATA_DIR/cache/anilist-recs.json.gz, DATA_DIR/cache/anime-recs-content.json, DATA_DIR/manga-recs.json
         (similarity picks written by build_manga.py), DATA_DIR/catalog.json, DATA_DIR/manga.json
  writes DATA_DIR/recs.json (anime) and DATA_DIR/manga-recs.json
"""
import gzip, json, math, re, sys

d = sys.argv[1]
N = 8


def base(t):
    t = (t or "").lower()
    t = re.split(r"[:(\[/]| - | -|～|~", t)[0]
    t = re.sub(r"[^a-z0-9 ]", "", t).strip()
    return " ".join(t.split()[:2])


BASES = {}


def same(a, b):
    """True when two titles look like entries of one series (same leading title words)."""
    return any(x == y or x.startswith(y + " ") or y.startswith(x + " ") for x in BASES[a] for y in BASES[b])


def votes_to_score(v):  # 1 vote -> 60, 10 -> 74, 100 -> 92: always above similarity-only picks (~25-50)
    return int(min(99, 55 + 8 * math.log(1 + v)))


def merge(kind, content, titles, starter=None):
    try:
        with gzip.open(f"{d}/cache/anilist-recs.json.gz", "rt", encoding="utf-8") as f:
            human = {int(k): v for k, v in (json.load(f).get(kind) or {}).items()}
    except FileNotFoundError:
        human = {}
    out, n_h = {}, 0
    BASES.clear(); BASES.update({k: {base(x) for x in v} - {""} for k, v in titles.items()})
    for sid in titles:
        picks, seen = [], {sid}

        def add(rid, score):
            """Add a pick unless it's from the source's series; one entry per series among the picks,
            preferring its first season (so "Attack on Titan", not "Attack on Titan Season 3 Part 2")."""
            if rid not in titles or rid in seen or same(sid, rid):
                return
            for k, (pid, ps) in enumerate(picks):
                if same(pid, rid):
                    if starter and starter.get(rid) and not starter.get(pid):
                        picks[k] = [rid, max(ps, score)]; seen.add(rid)
                    return
            picks.append([rid, score]); seen.add(rid)

        for rid, v in human.get(sid, []):
            if len(picks) == N: break
            add(rid, votes_to_score(v))
        n_h += bool(picks)
        for rid, s in content.get(str(sid), []):
            if len(picks) == N: break
            add(rid, min(s, 54))
        if picks:
            out[sid] = picks
    print(f"{kind}: {len(out)} titles with picks, {n_h} with community picks", file=sys.stderr)
    return out


anime = json.load(open(f"{d}/catalog.json"))["a"]
a_titles = {r[0]: [r[1], r[13] if len(r) > 13 else ""] for r in anime}
a_content = json.load(open(f"{d}/cache/anime-recs-content.json"))
json.dump(merge("anime", a_content, a_titles, {r[0]: bool(r[8]) for r in anime}), open(f"{d}/recs.json", "w"), separators=(",", ":"))

manga = json.load(open(f"{d}/manga.json"))["a"]
m_titles = {r[0]: [r[1], r[11]] for r in manga}
m_content = json.load(open(f"{d}/manga-recs.json"))
json.dump(merge("manga", m_content, m_titles), open(f"{d}/manga-recs.json", "w"), separators=(",", ":"))
