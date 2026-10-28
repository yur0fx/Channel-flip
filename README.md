# Channel Flip

A free random anime and manga picker. Flip through every anime (`index.html`) or manga (`manga.html`) on MyAnimeList, filter by mood, studio, length and era, describe a mood in your own words, and get recommendations based on any show you love.

It's a static site: two pages plus data files in `data/`. The manga data (`data/manga*.json`) is rebuilt weekly by the GitHub Action in `.github/workflows/manga-data.yml` using the scripts in `tools/`. Any free static host works (GitHub Pages, Netlify, Cloudflare Pages).

## Data and credits

- Catalog, tags and relations: [anime-offline-database](https://github.com/manami-project/anime-offline-database) (ODbL 1.0 / DbCL 1.0). The derived files in `data/` are shared under the same license.
- English titles, scores, member counts, studios and cover image paths: MyAnimeList, via [meesvandongen/anime-dataset](https://github.com/meesvandongen/anime-dataset).
- Manga catalog: MyAnimeList via the [Jikan](https://jikan.moe) API, fetched weekly.
- Synopses load live in the browser from the [Jikan](https://jikan.moe) API.

Not affiliated with MyAnimeList.
