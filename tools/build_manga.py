"""Turn the Jikan manga download into the compact files the site loads.

Writes data/manga.json (catalog) and data/manga-recs.json (8 "if you like this" picks per manga).
Synopses are only used here to derive keywords and similarity; they are not published.

Usage: python tools/build_manga.py RAW.jsonl.gz OUT_DIR
"""
import gzip, json, re, sys, collections
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize
from scipy.sparse import hstack

raw, outdir = sys.argv[1], sys.argv[2]

TYPES = ["Manga", "Manhwa", "Manhua", "Light Novel", "Novel", "One-shot", "OEL"]
STATUS = {"Finished": 0, "Publishing": 1, "Not yet published": 2, "On Hiatus": 3, "Discontinued": 4}
BLOCK_GENRES = {"Hentai", "Erotica"}
STOP = set("""a about above after again against all also although always am among an and another any are around as at
away back be became because become becomes been before begin begins being below between both but by came can cannot
could day days did do does doing done down during each else even ever every everyone everything few find finds first
for found from further get gets getting girl give given go goes going gone got great had has have having he her here
hers herself him himself his how however i if in into is it its itself just know known last later life like little
long made make makes many may me meanwhile meet meets more most much must my myself name named never new next no nor
not now of off often old on once one only or other others our out over own part people place put rather really
right same see seems set shall she should since so some something soon still such take takes taking than that the
their them themselves then there these they thing things this those though three through thus time to together too
toward towards two under until up upon us use used very want wants was way ways we well went were what when where
whether which while who whom whose why will with within without world would year years yet you young your boy man
woman story series manga volume volumes chapter chapters written source rewrite mal written also however becomes
begins called comes days decides despite finally first himself herself lives living soon suddenly though tries trying
turns whose wants without""".split())

rows = []
with gzip.open(raw, "rt", encoding="utf-8") as f:
    seen = set()
    for line in f:
        m = json.loads(line)
        mid = m.get("mal_id")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        ty = m.get("type") or ""
        if ty not in TYPES:  # drops Doujinshi and unknown types
            continue
        gnames = [g["name"] for g in (m.get("genres") or []) + (m.get("explicit_genres") or [])]
        if BLOCK_GENRES & set(gnames):
            continue
        rows.append(m)
print("kept", len(rows), file=sys.stderr)

def nm(a):  # "Oda, Eiichiro" -> "Eiichiro Oda"
    n = a.get("name") or ""
    if ", " in n:
        last, first = n.split(", ", 1)
        return f"{first} {last}"
    return n

GV, gix = [], {}
def G(name):
    if name not in gix: gix[name] = len(GV); GV.append(name)
    return gix[name]
AU, aix = [], {}
def A(name):
    if name not in aix: aix[name] = len(AU); AU.append(name)
    return aix[name]

# synopsis keywords → the "detailed tags" mood search matches against
tok = lambda s: [w for w in re.findall(r"[a-z][a-z'-]{3,}", (s or "").lower()) if w not in STOP and not w.endswith("'s")]
docs = [" ".join(tok(m.get("synopsis"))) for m in rows]
kv = TfidfVectorizer(min_df=20, max_df=0.04, sublinear_tf=True, max_features=1500)
KW = kv.fit_transform(docs)
kw_vocab = list(kv.get_feature_names_out())

# tag vocabulary: MAL genres/themes (lowercase) first, then synopsis keywords
theme_names = sorted({g["name"] for m in rows for key in ("genres", "themes", "demographics") for g in (m.get(key) or [])})
TG = [t.lower() for t in theme_names] + [w for w in kw_vocab if w not in {t.lower() for t in theme_names}]
tgi = {t: i for i, t in enumerate(TG)}

out = []
for k, m in enumerate(rows):
    gs = [g["name"] for key in ("genres", "themes", "demographics") for g in (m.get(key) or [])]
    authors = [nm(a) for a in (m.get("authors") or [])][:4]
    pub = (m.get("published") or {}).get("prop", {}).get("from", {}) or {}
    img = ((m.get("images") or {}).get("jpg") or {}).get("image_url") or ""
    mm = re.search(r"/images/manga/(\d+/\d+)\.", img)
    row = KW.getrow(k)
    top = [kw_vocab[j] for j in row.indices[np.argsort(-row.data)][:12]] if row.nnz else []
    tg = sorted({tgi[g.lower()] for g in gs} | {tgi[w] for w in top if w in tgi})
    en = (m.get("title_english") or "").strip()
    out.append([
        m["mal_id"], m.get("title") or en, TYPES.index(m["type"]), m.get("chapters") or 0, m.get("volumes") or 0,
        pub.get("year") or 0, int(round((m.get("score") or 0) * 100)), [A(a) for a in authors],
        sorted({G(g) for g in gs}), STATUS.get(m.get("status"), 0), m.get("members") or 0,
        en if en and en != m.get("title") else "", mm.group(1) if mm else "", tg,
    ])

json.dump({"kind": "manga", "v": GV, "ty": TYPES, "st": AU, "tg": TG, "a": out},
          open(f"{outdir}/manga.json", "w"), ensure_ascii=False, separators=(",", ":"))
print("catalog", len(out), "genres", len(GV), "authors", len(AU), "tags", len(TG), file=sys.stderr)

# ---------- recommendations ----------
def base(t):
    t = t.lower()
    t = re.split(r"[:(\[/]| - | -|～|~", t)[0]
    t = re.sub(r"[^a-z0-9 ]", "", t).strip()
    return " ".join(t.split()[:2])

feat_docs = []
for m in rows:
    gs = [g["name"] for key in ("genres", "themes", "demographics") for g in (m.get(key) or [])]
    toks = ["g_" + re.sub(r"\W", "", g) for g in gs] * 3
    toks += ["a_" + re.sub(r"\W", "", nm(a)) for a in (m.get("authors") or [])] * 2
    toks += ["s_" + re.sub(r"\W", "", s.get("name") or "") for s in (m.get("serializations") or [])]
    feat_docs.append(" ".join(toks))
fv = TfidfVectorizer(token_pattern=r"\S+", min_df=2, sublinear_tf=True, lowercase=False)
F = normalize(hstack([normalize(fv.fit_transform(feat_docs)) * 0.75, KW * 0.55]).tocsr())

score = np.array([(m.get("score") or 0) for m in rows])
members = np.array([(m.get("members") or 0) for m in rows])
types = [m["type"] for m in rows]
cand = np.array([k for k in range(len(rows)) if types[k] != "One-shot" and (score[k] >= 7.0 or members[k] >= 20000)])
print("candidates", len(cand), file=sys.stderr)
Fc = F[cand]
cbase = [base(rows[k]["title"] or "") for k in cand]
cq = 0.85 + 0.15 * np.clip((score[cand] - 6.0) / 3.0, 0, 1) + 0.03 * (members[cand] > 100000)
recs = {}
B = 2000
for start in range(0, len(rows), B):
    sims = (F[start:start + B] @ Fc.T).toarray() * cq
    for r in range(sims.shape[0]):
        k = start + r
        if F[k].nnz < 2:
            continue
        row = sims[r]
        order = np.argpartition(-row, 40)[:40]
        order = order[np.argsort(-row[order])]
        b = base(rows[k]["title"] or ""); picks = []; seenb = set()
        for j in order:
            if row[j] <= 0.05: break
            ck = cand[j]
            if ck == k or (b and cbase[j] == b) or cbase[j] in seenb: continue
            seenb.add(cbase[j])
            picks.append([rows[ck]["mal_id"], int(min(99, round(row[j] * 100)))])
            if len(picks) == 8: break
        if picks: recs[rows[k]["mal_id"]] = picks
json.dump(recs, open(f"{outdir}/manga-recs.json", "w"), separators=(",", ":"))
print("recs", len(recs), file=sys.stderr)
