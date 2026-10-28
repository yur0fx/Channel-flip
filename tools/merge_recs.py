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


def votes_to_score(v):  # 1 vote -> 60, 10 -> 74, 100 -> 92: always above similarity-only picks (~25-50)
    return int(min(99, 55 + 8 * math.log(1 + v)))


def merge(kind, content, titles):
    try:
        with gzip.open(f"{d}/cache/anilist-recs.json.gz", "rt", encoding="utf-8") as f:
            human = {int(k): v for k, v in (json.load(f).get(kind) or {}).items()}
    except FileNotFoundError:
        human = {}
    out, n_h = {}, 0
    for sid in titles:
        b = {base(x) for x in titles[sid]} - {""}
        same = lambda rid: any(x == y or x.startswith(y + " ") or y.startswith(x + " ")
                               for x in b for y in {base(t) for t in titles[rid]} - {""})
        picks, seen = [], {sid}
        for rid, v in human.get(sid, []):
            if rid in titles and rid not in seen and not same(rid):
                picks.append([rid, votes_to_score(v)]); seen.add(rid)
            if len(picks) == N: break
        n_h += bool(picks)
        for rid, s in content.get(str(sid), []):
            if len(picks) == N: break
            if rid in titles and rid not in seen and not same(rid):
                picks.append([rid, min(s, 54)]); seen.add(rid)
        if picks:
            out[sid] = picks
    print(f"{kind}: {len(out)} titles with picks, {n_h} with community picks", file=sys.stderr)
    return out


anime = json.load(open(f"{d}/catalog.json"))["a"]
a_titles = {r[0]: [r[1], r[13] if len(r) > 13 else ""] for r in anime}
a_content = json.load(open(f"{d}/cache/anime-recs-content.json"))
json.dump(merge("anime", a_content, a_titles), open(f"{d}/recs.json", "w"), separators=(",", ":"))

manga = json.load(open(f"{d}/manga.json"))["a"]
m_titles = {r[0]: [r[1], r[11]] for r in manga}
m_content = json.load(open(f"{d}/manga-recs.json"))
json.dump(merge("manga", m_content, m_titles), open(f"{d}/manga-recs.json", "w"), separators=(",", ":"))
