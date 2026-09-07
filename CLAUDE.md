# ZETA VIOLIN HUNTER — CLAUDE.md

> Single source of truth for every AI coding session on this repo.
> Read this file BEFORE writing any code.

---

## 1. WHAT THIS PROJECT IS

A Python bot that:
- Runs on Railway.app (container, always-on, restart policy "always")
- Scrapes global online marketplaces once per day at 09:00 UTC (12:00 Romania)
- Detects NEW Zeta electric violin listings (dedup via SQLite)
- Sends Telegram alerts to a single user (Vlad, owner)
- Also exposes HTTP endpoints: `POST /search` (manual trigger), `GET /health`, `GET /status` (dashboard)

Non-goals: web UI, multi-user, real-time, mobile app, other instruments (no 
violas, cellos, basses, mandolins — violins only).

---

## 2. CURRENT STATE (last verified 2026-09-07 — Prompt 13 audit/rewrite + Prompt 14 coverage expansion)

### Infrastructure
- Deployment status: Active
- Last run: 2x daily at 09:00 + 21:00 UTC (scheduler working)
- Cost: ~$0.38/month actual, ~$1.96/month estimated (well under $5 free tier)
- HTTP server: port 8080, endpoints `/search`, `/health`, `/status` reachable

### What works
- `filters.py` — NEW Prompt 13. Single implementation of §4 keyword lists and §5 logic, WORD-BOUNDARY regex matching (no more substring bugs: "hard shell case" ≠ jacket, "sold as is" ≠ sold, "no defects" ≠ defective). Accepts unique model codes / signature artists / Strados without the brand string (per §4.1–4.2), handles JP (ゼータ/バイオリン) and NL (viool) terms, drops other brands (title only), replicas, accessories (MIDI controllers, covers, pedals), other instruments (cello, bass, viola). `classify(listing)` returns a drop reason or "". Tests: `python -m tests.test_filters` (46 cases).
- `scrapers/reverb.py` — REWRITTEN Prompt 13. Root cause of 0 results was the User-Agent: Reverb's edge returns 403 (HTML) to `ZetaViolinHunter/1.0` and 200 to a browser UA — NO token needed (the Prompt 11 "401 / needs REVERB_API_TOKEN" diagnosis was wrong). Skips non-`live` listings, strips HTML from descriptions, derives seller region from shipping rates (the API no longer returns a shop address). 13 keywords × 2 pages. Verified live: 13 raw → 5 real Zeta violins pass.
- `scrapers/craigslist.py` — REWRITTEN Prompt 13 on the internal JSON API `sapi.craigslist.org/web/v8/postings/search/full` (the same call the JS frontend makes). Area IDs from `reference.craigslist.org/Areas` (413 US + ~55 CA areas, `CRAIGSLIST_COUNTRIES`). 3 broad queries per area ("zeta" msa, "strados" msa, "zeta violin" sss) + local Zeta-signal filter. Verified live: 1404 requests, all HTTP 200, 28 s. Old approach was dead: geo index lists only `www`, RSS is 403, HTML is JS-rendered, JSON-LD items have no URL.
- `scrapers/shopgoodwill.py` — NEW Prompt 13. Goodwill's US auction site via `POST buyerapi.shopgoodwill.com/api/Search/ItemListing` (no auth, description search). 4 broad queries, Zeta-signal filter.
- `scrapers/hibid.py` — NEW Prompt 14. HiBid (largest US/CA estate & liquidation auction platform) via unauthenticated GraphQL `lotSearch` at hibid.com/graphql (the HTML is a JS shell; introspection disabled, fields discovered by trial). Lot URL `hibid.com/lot/{id}`, live bid as price.
- `scrapers/kijiji.py` — NEW Prompt 14. Kijiji.ca via the Apollo cache in `__NEXT_DATA__` (`StandardListing:*`). Verified live: found "Zeta Electric Violin With Case", 795 CAD, Oshawa.
- `scrapers/marktplaats.py` — NEW Prompt 14. Marktplaats (NL) + 2dehands (BE) via the open `/lrp/api/search` JSON API. Verified live: found "Zeta Jazz Violin Viool 5", 2200 €, Gent.
- `scrapers/willhaben.py` — NEW Prompt 14. Willhaben (AT) via `__NEXT_DATA__ → searchResult.advertSummaryList`. Verified live: found "Zeta Geige Jazz Fusion", 6.900 €, Wien.
- `scrapers/schibsted.py` — NEW Prompt 14. FINN (NO), Tori (FI), DBA (DK), Blocket (SE): one parser reading the schema.org `ItemList` of `Product` in JSON-LD.
- `scrapers/gumtree.py` — NEW Prompt 14. Gumtree UK server-rendered tiles (`article[data-q=search-result]`).
- `scrapers/olx.py` — NEW Prompt 14. OLX PL/PT/BG/UA via the open `/api/v1/offers/` JSON API (olx.ro excluded on purpose).
- `scrapers/brave.py` — NEW Prompt 14. Brave Search API, reuses Google's keyword × site-group matrix; skips if `BRAVE_API_KEY` unset. Planned Google CSE replacement for 2027.
- `scrapers/guitar_center.py` — re-enabled Prompt 14 but only runs when `US_PROXY_URL` is set (site TCP-blocks EU datacenter IPs).
- **Watchdog** (main.py `_watchdog`) — NEW Prompt 14. After each cycle, any configured scraper with 0 fetched items for `WATCHDOG_ZERO_STREAK` (3) consecutive cycles, or errors in 2 consecutive cycles, triggers a Telegram warning (repeated every 10 cycles). Each scraper reports `self.fetched` = items returned by the source BEFORE local Zeta filtering, so "site answered, nothing matched" ≠ "site blocked us".
- **Price-drop alerts** (price_tracker.py `update_price`) — NEW Prompt 14. Already-seen listings whose price fell ≥ `PRICE_DROP_PCT` (15%) are re-alerted. `parse_amount()` handles EU/US number formats ("€ 6.900" = 6900, "2,749.00" = 2749).
- **Photos in Telegram** (notifier.py) — NEW Prompt 14. Listings with `image_url` go out as `sendPhoto` with HTML caption, falling back to text.
- **Persistent DB path** — NEW Prompt 14. `DB_PATH` env (all modules use `database.connect()`); point it at a Railway volume so dedup, Google/Brave cursors and price history survive deploys.
- **Craigslist full descriptions** — NEW Prompt 14. The few Zeta-signal hits get their posting page fetched (`#postingbody`, first image), max 20 per cycle.
- **Reverb `make=Zeta`** — NEW Prompt 14. Structured brand filter query added to the keyword list (catches odd titles).
- **HTTP hardening** — `PORT` env honoured; `SEARCH_TOKEN` protects `/search` and `/status` (header `X-Search-Token` or `?token=`).
- `.github/workflows/tests.yml` — compiles everything and runs `tests/test_filters.py` on every push.
- `scrapers/ebay.py` — REWRITTEN in Prompt 2. Now uses eBay Browse API
  with OAuth2 client_credentials grant. Searches 13 marketplaces with 8
  keywords. Requires EBAY_CLIENT_ID + EBAY_CLIENT_SECRET.
- `scrapers/subito.py` — REWRITTEN Prompt 13. Ads live in `__NEXT_DATA__ → initialState.items.originalList/galleryList`, NOT `.items.list` (always empty → the scraper had returned 0 since the Subito redesign). Nationwide search, 6 keywords, no city suffix (appending "Milan"/"Rome" to the query killed results), browser UA, DuckDuckGo fallback removed. Verified live: found the Zeta Acoustic Pro 5-string in Taranto (2000 €, posted 2026-09-03) that the old code missed.
- `scrapers/google.py` — REWRITTEN Prompt 13. Budget `GOOGLE_QUERIES_PER_RUN` (48; 2 runs/day fit the 100/day quota, which resets at midnight Pacific so both runs share one quota day). Each run: 6 global fresh queries (`dateRestrict=w2`, `sort=date`) + a rotating slice of the 6-keyword × 12-site-group matrix (cursor in SQLite → full matrix every 2 runs). 12 site groups now include US (Craigslist, OfferUp, Mercari US, FB Marketplace, ShopGoodwill, Etsy, Guitar Center, Sweetwater, Sam Ash, Music Go Round, Electric Violin Shop), auctions (HiBid, LiveAuctioneers, Invaluable, Proxibid, EstateSales), UK/CA/AU, DE/AT/CH/NL/BE, FR/IT/ES/PT, Nordics/PL/CZ, JP/Asia, forums, CEE, LatAm. 429/403 stop the run AND still mark it (previously the guard was skipped on 429). Image/price from `pagemap`.
- `main.py` core orchestration: scheduler (`SEARCH_HOURS`), concurrency, `filters.classify()` gate with per-reason counters, dedup, per-platform Telegram send
- `database.py` — SQLite dedup with hash(platform:url)
- `notifier.py` — Telegram formatted alerts
- `config.py` — env-based configuration. FIXED in Prompt 3: location exclusion
  uses word-boundary matching (EXCLUDED_COUNTRY_CODES = ["RO"]).
- `scrapers/kleinanzeigen.py` — Playwright headless Chromium, searches German
  musical instruments category (Prompt 5).
- `scrapers/wallapop.py` — public API, Spanish marketplace (Prompt 5).
- `scrapers/leboncoin.py` — httpx + __NEXT_DATA__ JSON, French marketplace (Prompt 5).
- `scrapers/mercari_jp.py` — mercapi async wrapper, Japan's #1 C2C marketplace (Prompt 6). FIXED Prompt 13: mercapi exposes the id as `id_` (old code read `id` → always empty → 0 results), sold status is `ITEM_STATUS_SOLD_OUT` (old check for `sold_out` never matched), Mercari Shops products use `/shops/product/{id}` URLs.
- `scrapers/reddit_scraper.py` — praw official Reddit API, searches violin subreddits (Prompt 6). Requires REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET; skips gracefully if not set.
- `scrapers/maestronet.py` — Maestronet forum classifieds, httpx + BeautifulSoup (Prompt 7).
- `scrapers/violinist_com.py` — Violinist.com forum, httpx + BeautifulSoup (Prompt 7).
- `scrapers/audiofanzine.py` — Audiofanzine classifieds, httpx + BeautifulSoup (Prompt 7).
- `ai_verifier.py` — GPT-4o-mini re-verification layer. Every listing passing keyword filters is checked by AI before Telegram send. Requires OPENAI_API_KEY; passes all through if not set (Prompt 7). Enhanced with image verification for MAYBE listings via GPT-4o-mini vision (Prompt 8).
- `price_tracker.py` — SQLite price history, deal detection (30%+ below average). Currency conversion for 10 currencies. Integrated into main.py pipeline (Prompt 8).
- `status_tracker.py` — SQLite per-scraper stats + /status JSON dashboard (Prompt 9).
- `GET /status` endpoint — comprehensive bot health info: last cycle, per-scraper stats, all-time totals, price history (Prompt 9).

### Disabled scrapers (anti-bot blocked, files retained for future re-activation)

- `scrapers/kleinanzeigen.py` — disabled (Cloudflare anti-bot). Covered by Google CSE.
- `scrapers/wallapop.py` — disabled (Datadome anti-bot). Covered by Google CSE.
- `scrapers/leboncoin.py` — disabled (anti-bot). Covered by Google CSE.
- `scrapers/maestronet.py` — disabled (404 / search errors). Covered by Google CSE.
- `scrapers/violinist_com.py` — disabled (404 / search errors). Covered by Google CSE.
- `scrapers/audiofanzine.py` — disabled (404 / search errors). Covered by Google CSE.

### What is broken / known issues post-Prompt 14
- **Google CSE API sunsets January 1, 2027** (confirmed; already closed to new customers). Brave scraper is ready (`BRAVE_API_KEY`); Google must be removed before 2027.
- **Sites blocked from EU datacenter IPs (Railway)** — verified 2026-09-07: OfferUp (403 geo), Mercari US (Cloudflare), Etsy (403), Guitar Center (TCP timeout), Facebook Marketplace (login wall), Yahoo JP web + API, Buyee, ZenMarket, Catawiki, Proxibid, Heritage, the-saleroom, Bidspotter, Lot-tissimo, Easylive, Interencheres, Auction.fr, Allegro, Ricardo, tutti.ch, Wallapop API, Vinted (Datadome), Invaluable + LiveAuctioneers + EstateSales (JS shells). Covered only via Google/Brave site groups. Setting `US_PROXY_URL` (a US residential/VPS HTTP proxy) re-enables Guitar Center and routes Facebook's Chromium through it; OfferUp/Mercari US scrapers are NOT written yet (cannot be verified without the proxy).
- **Brave, US proxy, Reddit need owner-side setup** — keys/URLs in Railway Variables; every one of these scrapers skips gracefully and is excluded from the watchdog when unconfigured.
- **Facebook Marketplace scraper (Playwright)** most likely returns 0 on Railway (login wall). Not verifiable locally (no Chromium). Check Railway logs for `Facebook Marketplace: N listings found`; if always 0, disable to save ~40 s/cycle.
- **eBay and Reddit not verifiable locally** — the local `.env` has empty values; credentials live only in Railway Variables. Confirm in Railway logs that eBay answers HTTP 200 (the bogus `X-EBAY-C-ENDUSERCTX` placeholder header was removed in Prompt 13).
- **Violas are dropped** (title word "viola" in `filters.NOISE_TITLE_RX`) per §1 scope. The Reverb "ZETA Jazz Modern Electric Viola" therefore no longer alerts. Remove "viola"/"violas" from that list if the owner wants violas.
- **Railway DB is ephemeral** — after this deploy the first cycle will alert every currently live listing (≈5 Reverb + 1 Subito) as "new".

### Database state (zeta_listings.db)
- 18 total entries, of which 3 are real Zeta violins:
  - "Zeta Strados Modern - Electric Violin" (Craigslist sfbay)
  - "ZETA Jazz Modern 4-String Electric Viola violin w/MIDI" (Reverb)
  - "Zeta Strados Electric Violin" (Reverb)
- 15 entries are noise captured by the old Subito scraper from unrelated 
  ads crawled out of __NEXT_DATA__ tree (Yamaha YEV-104, Bridge Aquila, 
  Fender FV-1, Roland, Ibanez, Rocktile, "casa di alluminio", vinyl records, 
  Sennheiser microphones). Owner decision: leave the DB as is — these 15 
  URLs will simply never re-alert, no harm done.

---

## 3. ARCHITECTURE

File layout (top level):

- main.py — Entry point + AsyncIO scheduler + HTTP server
- filters.py — ALL keyword lists (§4) and filter logic (§5); word-boundary regex
- config.py — Env-var-based configuration
- database.py — SQLite dedup (zeta_listings.db)
- notifier.py — Telegram sendMessage wrapper
- tests/test_filters.py — filter regression tests (`python -m tests.test_filters`)
- scrapers/ — Per-platform scraper modules
  - __init__.py
  - base.py — Base class: BROWSER_UA, price/year/location helpers, relevance scoring
  - reverb.py — Reverb.com public API (browser UA, token optional)
  - ebay.py — eBay Browse API (OAuth2, 13 marketplaces)
  - google.py — Google Custom Search (rotating keyword × site-group matrix)
  - brave.py — Brave Search API (Google replacement; same site matrix)
  - craigslist.py — Craigslist internal JSON API (sapi) across all US + CA areas, posting pages for descriptions
  - shopgoodwill.py — ShopGoodwill.com buyer API (US auctions)
  - hibid.py — HiBid GraphQL lotSearch (US/CA estate & liquidation auctions)
  - kijiji.py — Kijiji.ca (Apollo cache in __NEXT_DATA__)
  - marktplaats.py — Marktplaats.nl + 2dehands.be (open JSON API)
  - willhaben.py — Willhaben.at (__NEXT_DATA__ advertSummaryList)
  - schibsted.py — FINN.no, Tori.fi, DBA.dk, Blocket.se (JSON-LD Product lists)
  - gumtree.py — Gumtree UK (server-rendered tiles)
  - olx.py — OLX PL/PT/BG/UA (open JSON API)
  - subito.py — Subito.it (Italian classifieds; nationwide __NEXT_DATA__ parse)
  - kleinanzeigen.py — Kleinanzeigen.de (Playwright headless Chromium)
  - wallapop.py — Wallapop (Spain, public API)
  - leboncoin.py — Leboncoin.fr (httpx + __NEXT_DATA__)
  - mercari_jp.py — Mercari Japan (mercapi async wrapper)
  - reddit_scraper.py — Reddit (praw, asyncio.to_thread)
  - maestronet.py — Maestronet forum (httpx + BeautifulSoup)
  - violinist_com.py — Violinist.com forum (httpx + BeautifulSoup)
  - audiofanzine.py — Audiofanzine classifieds (httpx + BeautifulSoup)
- ai_verifier.py — GPT-4o-mini re-verification (httpx direct, no openai package)
- status_tracker.py — Per-scraper stats + /status dashboard (SQLite)
- price_tracker.py — Price history + deal detection (SQLite)
- requirements.txt
- railway.toml — Railway build + deploy config
- env.example — Template for env vars
- zeta_listings.db — SQLite dedup (currently committed to repo, should be 
  gitignored in future cleanup)

Execution flow per run:
1. Scheduler fires at 09:00 UTC (or manual POST /search)
2. `run_search_cycle()` builds list of scrapers, runs them concurrently 
   (semaphore = 4)
3. Each scraper returns a list of dicts `{id, platform, title, price, 
   location, url, description, relevance_score, ...}`
4. Main filter pipeline = `filters.classify()`: other-brand (title) → noise → 
   intent → sold/ended (title) → Zeta-violin acceptance (§5) → URL validity → DB dedup
5. New listings flushed to Telegram immediately per-platform (so partial 
   results are not lost on container restart)
6. Cycle summary logged; if no new listings, a "no changes" message is sent

---

## 4. MASTER KEYWORD LIST — ZETA VIOLIN

This is the ONLY authoritative list. All scrapers and filters reference it.

### 4.1 CLASS A — Strong Zeta identifiers (stand-alone)

Brand strings (case-insensitive):
- Zeta, ZETA
- Zetta (common misspelling)
- ZetaMusic, Zeta Music, Zeta Music Systems

Unique model codes (Zeta-specific — accept without brand string):
- JV44, JV45 — Jazz Vintage 4/5-string
- SV24, SV25 — Strados V2 4/5-string
- SV43 — Jazz Modern
- CV44 — Jazz Classic
- EV25, EV44 — EV Acoustic Pro

Signature artists (Zeta-only — accept without brand string):
- Jean-Luc Ponty, Jean Luc Ponty, JLP, JLP5
- Boyd Tinsley (signature Strados Modern, black)
- Eileen Ivers (signature Strados Acoustic Pro, blue)

Partial signature (requires additional Zeta or violin context):
- Vanessa-Mae, Vanessa Mae (also used other violins in her career)

### 4.2 CLASS B — Zeta model names (require Zeta OR violin context)

- Strados (alone is OK — very few other brands use this name)
- Jazz Fusion, Jazz Standard (former name), Jazz Modern, Jazz Classic, 
  Jazz Acoustic Pro
- Strados Modern, Strados Fusion, Strados Acoustic Pro, 
  Strados Standard, Strados Legacy
- E-Fusion, E-Modern, EV Acoustic Pro
- Acoustic Pro (requires Zeta in context — generic term)

### 4.3 CLASS C — Violin words (multi-language context)

| Language | Keywords |
|---|---|
| EN | violin, violins, fiddle |
| IT | violino, violino elettrico |
| FR | violon, violon électrique |
| ES | violín, violín eléctrico |
| DE | Geige, elektrische Geige |
| NL | viool, violijn, elektrische viool |
| PL | skrzypce, skrzypce elektryczne |
| PT | violino |
| JP | バイオリン, ヴァイオリン, electric violin |

### 4.4 Configuration variants (do NOT filter out)

- 4-string, 4 string, 4-corzi
- 5-string, 5 string, 5-corzi
- 6-string (rare custom)
- fretted, with frets
- MIDI, MIDI violin
- legacy, prototype, custom

### 4.5 BLACKLIST — Noise (drop immediately if matched)

Non-violin Zeta-brand products:
- Arc'teryx, Arcteryx, jacket, jackets, hoodie, coat, shell, 
  hardshell, backpack, pants, ski, snowboard
- Zeta phi beta (US sorority)
- Zeta reticuli (astronomy)
- Zeta cartridge, Zeta pump, Zeta potential

Non-purchase intent:
- WTB, wanted to buy, looking for, in search of, ISO
- part only, parts only, for parts, repair, broken, defect, not working
- case only, bow only, bridge only, pickup only, gig bag only, cover only

Other electric violin brands that are NOT Zeta:
- Yamaha, Silent violin, YEV, YEV-104, YEV-105, SV-200, SV-250, EV-205 
  (note: SV-200 series is Yamaha, distinct from Zeta SV24/SV25)
- Bridge violin, Bridge Aquila, Bridge Draco, Bridge Lyra, Bridge Golden Tasman
- NS Design, NS WAV, NS CR
- Wood violin, Mark Wood, Wood Viper, Stingray
- Fender FV-1, Fender electric violin
- Stagg, Cantini, Cecilio, Kinglos, Glasser, ECO-ION
- Electric Violin Lutherie, EVL

### 4.6 EXCLUDED LOCATIONS (owner preference)

- Country "Romania" / ISO code "RO" (match only full word/code, not substring)

---

## 5. FILTER LOGIC (pseudo-code)

    def is_valid_zeta_violin(title, description, platform):
        text = (title + " " + description + " " + platform).lower()

        # DROP: noise blacklist
        if any(term in text for term in NOISE_BLACKLIST):
            return False

        # DROP: non-purchase intent
        if any(term in text for term in EXCLUDE_INTENT):
            return False

        # DROP: other electric violin brands
        if any(brand in text for brand in OTHER_VIOLIN_BRANDS):
            return False

        # ACCEPT: unique Zeta model code stand-alone
        if any(code in text for code in UNIQUE_MODEL_CODES):
            return True

        # ACCEPT: unique Zeta signature artist
        if any(artist in text for artist in ZETA_ONLY_ARTISTS):
            return True

        # ACCEPT: Zeta brand + violin context
        has_zeta = "zeta" in text or "zetta" in text
        has_violin = any(v in text for v in VIOLIN_TERMS_MULTILANG)
        has_model = any(m in text for m in ZETA_MODEL_NAMES)
        if has_zeta and (has_violin or has_model):
            return True

        return False

---

## 6. CODING CONVENTIONS (Python)

- Python 3.11+ (Railway runtime)
- async/await everywhere I/O happens; use httpx.AsyncClient (not requests)
- Type hints on every function signature
- logging module, never print(). Log levels: INFO for normal flow, 
  WARNING for non-fatal issues, ERROR for failures
- Every scraper subclasses BaseScraper from scrapers/base.py
- Every scraper returns a list of dicts with REQUIRED keys: id, platform, 
  title, price, location, url, description, relevance_score
- NO per-scraper state in instance variables between runs. Each .search() 
  call is independent.
- Environment variables loaded via os.getenv() in config.py. Never 
  os.environ[...] without a default — container must start even with 
  missing optional vars.
- NO print(), NO "except: pass" (always log the exception), NO hardcoded 
  secrets
- Timeouts on EVERY network call (default 15s, max 30s)
- Retries via main.py orchestrator, NOT per-scraper (avoid double retries)
- HTTP status codes: check resp.status_code before resp.json() / resp.text
- Use follow_redirects=True for sites that redirect (Subito, Allegro)
- User-Agent: set a realistic browser UA for scrape targets, or 
  "ZetaViolinHunter/1.0" for sites where identifying ourselves is OK

---

## 7. ANTI-PATTERNS (NEVER DO)

- Don't use requests (sync) — always httpx.AsyncClient
- Don't swallow exceptions silently — always log
- Don't add new scrapers that return dummy/empty results — delete the file 
  instead
- Don't log the full response body — truncate to 300 chars
- Don't commit zeta_listings.db changes to git (it's runtime state)
- Don't commit .env files
- Don't hardcode API keys, tokens, or URLs with credentials
- Don't make blocking calls in async functions (use asyncio.to_thread if 
  needed)
- Don't use time.sleep() in async code — use asyncio.sleep()
- Don't iterate __NEXT_DATA__ trees blindly — extract only the specific 
  listing nodes to avoid capturing unrelated recommended ads
- Don't use substring match for country codes — match exact tokens
- Don't increment Google Custom Search quota without a daily guard
- Don't re-run a scrape cycle on container restart (wastes quota) — add a 
  "last_run" DB guard before running on startup
- Don't push breaking changes without testing via POST /search first on 
  Railway

---

## 8. DEPLOYMENT (Railway.app)

Build system: railway.toml declares builder = "railpack". Railway 
auto-installs from requirements.txt.

Start command: python main.py (via railway.toml [deploy] startCommand).

Restart policy: always (container restarts on crash).

Port: Railway auto-assigns PORT env var — main.py currently hardcodes 
8080. This works for internal health checks but does NOT give a public URL. 
Manual /search triggering is done via "railway run curl -X POST 
http://localhost:8080/search" from a local Railway shell, or by temporarily 
exposing port 8080 in Railway settings.

Logs: Railway dashboard → Deployments → Logs. Python logging writes to 
stderr which Railway labels as [error] tag (cosmetic — not actual errors).

Persistent storage: zeta_listings.db is on ephemeral container filesystem. 
For production, a Railway volume mount would be needed — currently acceptable 
because dedup is rebuilt naturally from Telegram history + short memory.

---

## 9. ENVIRONMENT VARIABLES

### Build-time (set automatically by nixpacks.toml — do NOT configure in Railway Variables)

- `PLAYWRIGHT_BROWSERS_PATH` — Playwright sets this during `playwright install`;
  browser binaries land in the virtualenv. No manual action needed.

### New in Prompt 1b (no new required vars — these are SDK keys used starting Prompt 2 and Prompt 9)

- EBAY_CLIENT_ID (optional until Prompt 2)
- EBAY_CLIENT_SECRET (optional until Prompt 2)
- ANTHROPIC_API_KEY (optional until Prompt 9)
- REDDIT_CLIENT_ID (optional until Prompt 8)
- REDDIT_CLIENT_SECRET (optional until Prompt 8)
- REDDIT_USER_AGENT (optional until Prompt 8)

Names only — values live in Railway Variables tab, never committed.

Required:
- TELEGRAM_BOT_TOKEN — from @BotFather
- TELEGRAM_CHAT_ID — from @userinfobot
- GOOGLE_API_KEY — Google Cloud Console
- GOOGLE_CSE_ID — programmablesearchengine.google.com

Required after eBay migration (REQUIRED — configured):
- EBAY_CLIENT_ID — developer.ebay.com (formerly EBAY_APP_ID)
- EBAY_CLIENT_SECRET — developer.ebay.com Cert ID

Optional tuning:
- SEARCH_HOURS (default "9,21" UTC; legacy SEARCH_HOUR still honoured as the first hour)
- MIN_PRICE (default 0)
- MAX_PRICE (default 99999)
- CONDITION (default "used" = second-hand only, drops New/Brand New/Open box/B-Stock + dealers + shop language; "all" = no condition filter)
- EXCLUDED_SELLERS (default "electricviolinshop,electric violin shop,zetaviolins,zeta violins,zetamusic.com" — new-stock Zeta dealers, matched against seller/shop/URL host)
- MIN_YEAR (default 1980)
- MAX_YEAR (default = next calendar year, computed at startup — Prompt 13; a fixed 2026 would have dropped every "2027" mention from January)
- REVERB_API_TOKEN (optional — Reverb works without it with a browser UA; a token only raises rate limits)
- GOOGLE_QUERIES_PER_RUN (default 48 — 2 runs/day must stay ≤ 100)
- GOOGLE_GUARD_HOURS (default 10 — skip Google if it ran less than N hours ago)
- SCRAPER_TIMEOUT_SEC (default 900)
- SCRAPER_RETRIES (default 1)
- SCRAPER_CONCURRENCY (default 4)
- CRAIGSLIST_CONCURRENCY (default 10, clamped 4–16 — requests against sapi.craigslist.org)
- CRAIGSLIST_MAX_US_CITIES (default 0 = all areas of CRAIGSLIST_COUNTRIES)
- CRAIGSLIST_COUNTRIES (default "US,CA")

Added in Prompt 14 (all optional):
- DB_PATH (default "zeta_listings.db" — set to e.g. /data/zeta_listings.db with a Railway volume mounted at /data)
- BRAVE_API_KEY, BRAVE_QUERIES_PER_RUN (16), BRAVE_GUARD_HOURS (10)
- US_PROXY_URL (http://user:pass@host:port — US egress for Guitar Center + Facebook Marketplace)
- SEARCH_TOKEN (protects POST /search and GET /status when set), PORT (default 8080; Railway injects it)
- PRICE_DROP_PCT (15), WATCHDOG_ZERO_STREAK (3), SEND_NO_CHANGES (true), SEND_PHOTOS (true)

---

## 10. MANUAL STEPS

### Done
- Railway project created, GitHub repo connected, auto-deploy on push to main
- Telegram bot created (@BotFather), token stored
- Telegram chat ID captured (@userinfobot)
- Google Custom Search: API key + CSE ID created, "Search entire web" enabled
- eBay developer account created, App ID issued (but Finding API now dead — 
  needs Client Secret for Browse API migration)

### Pending
- ~~Obtain eBay Client Secret (Cert ID) for OAuth2 Browse API~~ ✅ Done
- ~~Create Reverb Personal Access Token~~ — NOT needed (Prompt 13: browser UA fixes the 403; token optional)
- After deploying Prompt 13: trigger `POST /search` on Railway and check the logs for `Reverb: N listings found`, `Craigslist: … requests`, `Subito: N listings found`, eBay HTTP status, `Facebook Marketplace: N listings found`
- Optional: create Reddit API credentials (reddit.com/prefs/apps, "script" type) → REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET in Railway, enables r/Gear4Sale etc.
- **Mount a Railway volume** (e.g. at /data) and set `DB_PATH=/data/zeta_listings.db` — otherwise every deploy re-alerts all live listings and resets the Google/Brave cursors
- **Create a Brave Search API key** (api-dashboard.search.brave.com, $5 monthly credit) → `BRAVE_API_KEY`
- **Optional US proxy** (`US_PROXY_URL`) to unlock Guitar Center + Facebook Marketplace; then write OfferUp / Mercari US scrapers
- Optional: `SEARCH_TOKEN` + a public Railway domain to trigger `/search` from the phone
- Remove Google CSE before January 1, 2027 (Brave already wired in)

---

## 11. PHASES PLAN

- Prompt 0 — Create CLAUDE.md foundation (COMPLETED 2026-04-16)
- Prompt 1a — Prune dead code: remove 52 stub scrapers, Procfile, scrapy dep, duplicate 58com.py ✅ COMPLETED (2026-04-16)
- Prompt 1b — Add Playwright + praw + mercari deps + Railway Chromium install ✅ COMPLETED (2026-04-16)
- Prompt 2 — eBay Browse API migration ✅ COMPLETED (2026-04-16)
- Prompt 3 — Subito strict + Reverb tune + location fix + Google quota ✅ COMPLETED (2026-04-16)
- Prompt 4 — MERGED into Prompt 3
- Prompt 5 — European marketplaces + Dockerfile ✅ COMPLETED (2026-04-16)
- Prompt 6 — eBay dedup + Mercari JP + Reddit ✅ COMPLETED (2026-04-16)
- Prompt 7 — Music forums (Maestronet, Violinist.com, Audiofanzine) + AI re-verification (GPT-4o-mini) ✅ COMPLETED (2026-04-16)
- Prompt 8 — Price history + image verification + deal detection ✅ COMPLETED (2026-04-16)
- Prompt 9 (FINAL) — Dashboard /status + disable blocked scrapers + per-scraper stats ✅ COMPLETED (2026-04-16)

- Prompt 10 — Reverb +3 keywords, 2x/day schedule (09:00+21:00 UTC), Google 10h guard ✅ COMPLETED (2026-04-16)
- Prompt 11 — Deep audit + zero-results root cause fixes ✅ COMPLETED (2026-05-27)
- Prompt 12 — Craigslist rewrite (JSON-LD + 2023 HTML selectors) + Yahoo Auctions JP + Guitar Center Used + Facebook Marketplace (Playwright) ✅ COMPLETED (2026-05-27)
- Prompt 13 — Live-probe audit + fixes: Reverb UA, Craigslist sapi rewrite (all US+CA areas), Subito originalList, filters.py word-boundary rewrite + tests, Google CSE rotating matrix with US/auction/forum groups, ShopGoodwill scraper, Mercari id_/sold fixes, Yahoo JP deleted, MAX_YEAR dynamic, .env untracked ✅ COMPLETED (2026-09-07)
- Prompt 14 — Reliability + coverage: watchdog, price-drop alerts, Telegram photos, DB_PATH volume, Craigslist descriptions, Reverb make=Zeta, Brave Search, US proxy plumbing, SEARCH_TOKEN/PORT, GitHub Actions tests; 8 new direct scrapers (HiBid, Kijiji, Marktplaats/2dehands, Willhaben, FINN/Tori/DBA/Blocket, Gumtree UK, OLX ×4); multilingual WTB + violin terms; EU price parsing ✅ COMPLETED (2026-09-07)

Bot is operational. See Section 2 "What is broken" for remaining known issues.

---

## 12. DECISIONS LOG

| Date | Decision | Justification |
|---|---|---|
| 2026-09-07 | **Search-engine hits are verified before alerting** (`liveness.py`): (1) hits on hosts we scrape directly (Reverb, eBay, Craigslist, Subito, Marktplaats, Kijiji, Willhaben, HiBid, ShopGoodwill, OLX, Nordics, Gumtree, Mercari JP) are dropped — those sites are removed from the Google/Brave site groups too; (2) every other new hit is fetched once: 404/410, redirect to home/search, or a multilingual "listing has ended / posting expired / non più disponibile" marker = dead → marked seen, never alerted; Reverb URLs are checked via the API `state`; (3) matrix queries limited to the past year (`dateRestrict=y1`, `freshness=py`). Max 30 checks per scraper per cycle; network errors keep the listing | Owner received Brave/Google alerts for Reverb/eBay pages ended in 2008–2013. Direct scrapers only return live inventory, so search engines are useful only for sites we cannot reach, and only after a liveness check. |
| 2026-09-07 | **Second-hand only, any model/year** (`CONDITION=used` default): drop platform conditions New/Brand New/Open box/B-Stock, new-stock dealers (`EXCLUDED_SELLERS`: electricviolinshop, zetaviolins, …) and shop language in titles (brand new, NIB, authorized dealer, in stock). MIN_YEAR/MAX_YEAR kept only as a loose text guard | Owner: "modele noi sunt OK dacă sunt second-hand, nu nou-nouțe". The text year filter cannot tell manufacture year (sellers write purchase years), so MAX_YEAR=2014 would again drop vintage listings; condition + seller are the reliable signals. Verified live: the 2 Electric Violin Shop "Brand New" Zetas are dropped, the 3 used ones pass. |
| 2026-09-07 | Prompt 14: watchdog on `fetched` (pre-filter count) instead of on filtered results | Broad-query scrapers (Craigslist, ShopGoodwill, HiBid, OLX) legitimately return 0 Zeta candidates most cycles; only "source returned nothing" is a failure signal. Unconfigured scrapers (`is_configured()` False) are excluded. |
| 2026-09-07 | Prompt 14: direct scrapers only for sites that answered a live probe from an EU IP (HiBid GraphQL, Kijiji, Marktplaats/2dehands, Willhaben, FINN/Tori/DBA/Blocket, Gumtree UK, OLX). Catawiki, Proxibid, Heritage, the-saleroom, Bidspotter, Lot-tissimo, Easylive, Interencheres, Buyee, ZenMarket, Allegro, Ricardo, tutti.ch, Vinted, Invaluable, LiveAuctioneers, EstateSales left to Google/Brave | §7: no scrapers that return empty results. Probe table in this row is the authoritative list of what a Railway (EU) container can and cannot reach as of 2026-09-07. |
| 2026-09-07 | Prompt 14: US proxy as env plumbing only (`US_PROXY_URL`), no OfferUp/Mercari US scrapers yet | Cannot verify blind scrapers without a US egress; Guitar Center (existing, verified selectors) and Facebook Chromium are wired to the proxy. |
| 2026-09-07 | Prompt 14: photos via `sendPhoto` with 1000-char caption, fallback to text | Owner sees the instrument without opening the link; Telegram caption limit is 1024. |
| 2026-09-07 | Prompt 14: price-drop threshold 15%, stored per listing in `last_prices` | Reverb/eBay/Craigslist re-list the same item with new prices; a 15% cut on a known Zeta is actionable, smaller moves are noise. |
| 2026-09-07 | Reverb: browser User-Agent instead of `ZetaViolinHunter/1.0`; token optional | Live probe: 403 (HTML) with bot UA, 200 without any token with a Chrome UA. The Prompt 11 "needs token" diagnosis was wrong; owner never had to create a token. |
| 2026-09-07 | Craigslist rewritten on `sapi.craigslist.org` JSON API + `reference.craigslist.org/Areas`; 3 broad queries per area instead of 8 keywords × 2 categories | RSS 403, geo index dead, HTML JS-rendered, JSON-LD without URLs → old scraper returned 0. sapi: 1404 requests / 28 s / all 200. Broad "zeta"+"strados" queries + local signal filter catch titles like "Zeta Strados 5-string" that narrow queries missed. |
| 2026-09-07 | Subito: parse `originalList`/`galleryList`, nationwide query without city, DuckDuckGo fallback removed | `.items.list` is always empty; "Zeta violino Milan" returned 0 while "Zeta violino" returned 2 (incl. a real Acoustic Pro). Fallback produced stale search-engine results. |
| 2026-09-07 | New `filters.py` with word-boundary regex; substring lists in main.py/config.py removed | Substring matches dropped legit listings: "hard shell case"→shell, "clear coat"→coat, "skilled"→ski, "sold as is"→SOLD, "recommended"→ENDED, "no defects"→defect, "repaired"→repair, "unwanted gift"→wanted, "if you're looking for"→looking for. Also implements §4.1/§4.2 acceptance without brand string, §4.5 other-brand blacklist (title only), JP/NL terms, replica and accessory blacklists (AI verifier is gone, so noise must be caught here). |
| 2026-09-07 | Violas dropped (title word "viola") | §1 scope says violins only. Flip by removing "viola"/"violas" from `NOISE_TITLE_RX`. |
| 2026-09-07 | Google CSE: 48 queries/run, rotating keyword × site-group matrix with cursor, 6 fresh global queries with `dateRestrict=w2`, 12 site groups incl. US/auctions/forums; mark run on 429 | 65 × 2 runs = 130 > 100/day (quota resets at midnight Pacific, both runs same day) → evening run truncated. Same 10 relevance-ranked results every run → 0 new. US retail/auction/forum groups were never queried. |
| 2026-09-07 | New ShopGoodwill scraper (buyer API) | Verified reachable from EU IP without auth; US-wide donated instruments; description search. |
| 2026-09-07 | Yahoo JP scraper deleted | API answers 403, web blocks EEA, no app id ever configured → permanently 0. Google CSE `site:auctions.yahoo.co.jp` covers it. Per §7: no scrapers that return empty results. |
| 2026-09-07 | Mercari JP: read `id_`, match "sold" in status, Shops URLs | mercapi field is `id_`; old code read `id` → every item skipped → scraper always returned 0. Status strings are `ITEM_STATUS_SOLD_OUT`. |
| 2026-09-07 | MAX_YEAR default = next calendar year; eBay `X-EBAY-C-ENDUSERCTX` placeholder header removed; `.env` untracked from git; `SEARCH_HOURS` env | Fixed 2026 would drop every "2027" mention from Jan 1. Header contained literal `<ePNCampaignId>`. `.env` was tracked despite `.gitignore` (values were empty). Scheduler ignored SEARCH_HOUR. |
| 2026-04-16 | Added price history (SQLite), deal detection (30% below avg), image verification (GPT-4o-mini vision), enhanced Telegram alerts | Prompt 8. Cost ~$0.001/listing text + ~$0.003/listing image. |
| 2026-04-16 | Added Maestronet, Violinist.com, Audiofanzine forum scrapers + GPT-4o-mini AI re-verification layer | Prompt 7. Forums catch niche listings missed by marketplaces. AI rejects false positives (jackets, accessories, wrong brand) before Telegram send. Fail-open: OPENAI_API_KEY absent or API error → listing passes through. Used httpx directly, no openai pip package. |
| 2026-04-16 | Fixed eBay duplicates (dedup on itemId not URL), added Mercari JP (mercapi), Reddit (praw) | Prompt 6. eBay same item had different URLs per marketplace. Mercari uses async API wrapper. Reddit uses praw in asyncio.to_thread. |
| 2026-04-16 | Fixed Kleinanzeigen (BS4 fallback selectors), Wallapop (Origin/Referer headers + web fallback), Leboncoin (switched to Playwright) | Prompt 5-fix. All 3 had anti-bot issues on first deploy. |
| 2026-04-16 | Added Kleinanzeigen (Playwright), Wallapop (API), Leboncoin (__NEXT_DATA__). Switched to Dockerfile with Playwright base image. Skipped Facebook Marketplace Playwright (requires login, ban risk) — covered by Google CSE. | Prompt 5 |
| 2026-04-16 | Fixed Subito noise, Reverb over-querying, location "ro" bug, Google quota waste | Prompt 3. Subito requires zeta_signals in ad text. Reverb reduced to 8kw×2pg. Location uses word-boundary match. Google guards with 20h cooldown in SQLite. |
| 2026-04-16 | Rewrote eBay scraper: Finding API → Browse API with OAuth2 | Prompt 2. 13 marketplaces, 8 keywords, client_credentials grant, 50 results per query, token cached with 30min early refresh |
| 2026-04-16 | Added 5 new Python deps + Chromium via nixpacks.toml + .gitignore | Prompt 1b. Playwright for anti-bot sites; praw/mercari for official API access; tenacity for retries; anthropic for AI re-verification in Prompt 9. Build time increases 5-10 min for Chromium download. |
| 2026-04-16 | Deleted 52 dead scrapers, Procfile, and scrapy dep; active scraper count 55 → 5 | Completed as Prompt 1a. Future marketplace coverage will be rebuilt in Prompts 5-8 using Playwright + official libraries (praw, mercari, ebay-oauth-python-client). |
| 2026-04-16 | Drop ~50 non-working scrapers rather than fix each | Net savings: fewer false positives, simpler maintenance, rely on Google Custom Search for platforms we can't scrape directly |
| 2026-04-16 | Migrate eBay to Browse API (OAuth2) | Finding API decommissioned 2025-02-05, no alternative |
| 2026-04-16 | Leave 15 junk DB entries in place | They simply dedupe by URL hash — harmless, no re-alert risk |
| 2026-04-16 | Exclude Romania from search | Owner preference (Vlad is in Romania, not buying local) |
| 2026-04-16 | Include signature artists (JLP, Boyd Tinsley, Eileen Ivers, Vanessa-Mae) | High-signal indicators of genuine Zeta violins on second-hand market |
| 2026-04-16 | Scope = violins only (no viola, cello, bass, mandolin) | Owner preference, keeps filter tight |
| 2026-04-16 | Switched Railway builder from railpack → nixpacks; added nixpacks.toml | Required to run `playwright install --with-deps chromium` at build time; railpack has no hook for post-install browser download |
| 2026-04-16 | Install only Chromium, not Firefox/WebKit | Saves ~400MB of build disk and download time; all targeted JS-heavy sites work in Chromium |
| 2026-04-16 | Reverb +3 keywords (Strados violin, 5-string MIDI, Jean-Luc Ponty), schedule 2x/day (09:00+21:00 UTC), Google guard 20h→10h | Prompt 10. Broader Reverb coverage catches listings without "Zeta" in title. Evening run doubles detection frequency. |
| 2026-04-16 | Disabled 6 anti-bot-blocked scrapers, added /status dashboard with per-scraper stats and price history | Prompt 9 (FINAL). Disabled scrapers remain in codebase for future re-activation. Active scraper count: 7. |
| 2026-04-16 | Pin playwright==1.47.0 explicitly | Each Playwright Python release bundles specific browser versions; pinning prevents surprise breakage on Railway rebuild |
| 2026-05-27 | MAX_YEAR default changed from 2014 to 2026 (config.py) | PRIMARY cause of zero results for weeks. _year_in_range() found years like "2019"/"2022" in listing descriptions (purchase year, not manufacture year) and dropped them. Zeta stopped manufacturing ~2014 but listings appear every year. |
| 2026-05-27 | Removed year_min/year_max from Reverb API parameters (reverb.py) | Reverb uses these params to filter by instrument manufacture year. Sellers rarely fill in this field; passing year_max=2014 excluded ~90% of listings silently. |
| 2026-05-27 | Added REVERB_API_TOKEN optional header to reverb.py | Reverb API now returns 401 without authentication. Token is optional with graceful degradation + warning log. Owner must create Personal Access Token at reverb.com/account/applications (scope: public). |
| 2026-05-27 | Removed /forum from URL bad_fragments filter in main.py | Forum listing URLs (maestronet.com/forum/topic/...) and Google CSE results from forum sites were being silently dropped. Forum classifieds are valid listings. |
| 2026-05-27 | Removed relevance_score threshold filtering from all scrapers and main.py | Score filter was a secondary gate that could silently drop valid listings (e.g. non-English titles, unusual formulations). _is_strict_zeta_violin() in main.py is the real guard. Score field kept in listing dict for informational purposes only. |
| 2026-05-27 | Craigslist RSS confirmed dead since ~2020 | Craigslist removed RSS support. Scraper silently returns 0 from RSS loop, then falls back to DuckDuckGo and Google CSE. No code change needed — fallback works. |

---

## 13. KNOWN BUGS

| Bug | Severity | Status |
|---|---|---|
| __pycache__ and .pyc files accidentally tracked in git | Low | Fixed in Prompt 1b (.gitignore created, files untracked via git rm --cached) |
| eBay Finding API returns 0 (decommissioned) | High | Fixed in Prompt 2 — migrated to Browse API |
| eBay sends same listing 2-3 times (different marketplace URLs) | Medium | Fixed in Prompt 6 (dedup on itemId) |
| Facebook Marketplace scraper is a stub with 402 log-spam lines | Medium | Fixed (deleted) in Prompt 1a |
| 40+ scrapers have broken CSS selectors / anti-bot blocks | Medium | Fixed (deleted) in Prompt 1a |
| "ro" in "Rome" substring match excludes legit Italian/Canadian listings | Medium | Fixed in Prompt 3 |
| Procfile says "worker" but main.py serves HTTP on 8080 | Low | Fixed (deleted Procfile) in Prompt 1a |
| scrapy in requirements.txt but not imported | Low | Fixed in Prompt 1a |
| Reverb 30 keywords × 5 pages = 150+ API calls per cycle | Low | Fixed in Prompt 3 (8×2) |
| Google CSE quota (100/day) consumed per run, wasted on restarts | Low | Fixed in Prompt 3 (20h guard) |
| Duplicate file scrapers/58com.py and scrapers/com_58.py | Low | Fixed (both deleted) in Prompt 1a |
| Kleinanzeigen/Wallapop/Leboncoin permanently blocked by anti-bot | Medium | Disabled in Prompt 9, covered by Google CSE |
| Maestronet/Violinist.com/Audiofanzine 404 on search URLs | Low | Disabled in Prompt 9, covered by Google CSE |
| MAX_YEAR=2014 caused ALL listings with modern years in description to be silently dropped | Critical | Fixed in Prompt 11 (changed default to 2026) |
| Reverb API returns 401 without auth token — scraper returned 0 results silently | High | Fixed in Prompt 11 (REVERB_API_TOKEN support + warning log). Owner must set token in Railway. |
| Reverb API year_min/year_max params silently excluded unlabeled listings | Medium | Fixed in Prompt 11 (params removed) |
| /forum in URL bad_fragments dropped maestronet.com/forum/* and similar Google CSE results | Medium | Fixed in Prompt 11 (removed from filter) |
| Craigslist RSS feed returns 0 (removed by Craigslist ~2020) | Medium | Superseded — Prompt 13 rewrote Craigslist on the sapi JSON API |
| Reverb returned 403 for bot User-Agent → 0 results for months (misdiagnosed as missing token in Prompt 11) | Critical | Fixed in Prompt 13 (browser UA) |
| Craigslist scraper returned 0 everywhere (geo index dead, JSON-LD items lack URL, HTML JS-rendered) | Critical | Fixed in Prompt 13 (sapi API, 468 areas) |
| Subito scraper parsed 0 ads (`items.list` empty; city appended to query) | High | Fixed in Prompt 13 (`originalList`, nationwide) |
| Substring filters dropped legit listings ("hard shell case", "sold as is", "no defects", "recommended", ...) | High | Fixed in Prompt 13 (`filters.py`, word boundaries, 46 tests) |
| Strict filter required literal "zeta" — dropped Strados/JV44/JLP-only, Japanese-only and Dutch titles | Medium | Fixed in Prompt 13 |
| Google CSE: 130 queries/day > 100 quota; no date restriction → same stale top-10; US groups never queried; 429 skipped the guard | High | Fixed in Prompt 13 |
| Mercari JP always 0 (read `id` instead of `id_`; sold check never matched) | Medium | Fixed in Prompt 13 |
| MAX_YEAR fixed at 2026 → time bomb on 2027-01-01 | Medium | Fixed in Prompt 13 (dynamic) |
| Yahoo JP API 403 / EEA-blocked, never configured | Low | Scraper deleted in Prompt 13; covered by Google CSE |
| `.env` tracked in git despite `.gitignore` | Low | Fixed in Prompt 13 (`git rm --cached`) |
| No alert when a scraper silently returns 0 for weeks | High | Fixed in Prompt 14 (watchdog) |
| EU price strings ("€ 6.900") parsed as 6.9 | Medium | Fixed in Prompt 14 (`parse_amount`) |
| Dutch/German/Italian "wanted" ads ("Gezocht:", "Suche") alerted as listings | Low | Fixed in Prompt 14 (multilingual INTENT_TITLE_RX) |
| Ephemeral DB re-alerts everything on each deploy | Medium | Code ready in Prompt 14 (`DB_PATH`); owner must mount a Railway volume |

---

## 14. HOW TO WORK ON THIS REPO

At the start of every coding session:
1. Read this entire CLAUDE.md file
2. Read the specific prompt given to you
3. Confirm understanding by restating the task in your own words
4. Ask the owner for clarification if anything is ambiguous — DO NOT guess
5. Make the requested changes, nothing more
6. Verify per the VERIFY section of the prompt
7. Update this CLAUDE.md file at the end of every task:
   - Move completed items from "Known Bugs" / "Pending" to "Done"
   - Add an entry to the Decisions Log if a non-obvious choice was made
   - Update "Current State" section if architecture changed

Every prompt ends with: "Update CLAUDE.md with what was built."

---

## 15. TECHNICAL STACK (updated 2026-04-16)

### Runtime

- Python 3.11
- Railway Hobby plan ($5/month, 8GB RAM)
- nixpacks builder (via nixpacks.toml)

### Core libraries (all used)

- httpx 0.27 — async HTTP client (all scrapers)
- beautifulsoup4 4.12 — HTML parsing
- lxml 5.2 — fast XML/HTML parser backend
- apscheduler 3.10 — async daily scheduler
- aiohttp 3.9 — async web server for /search, /health
- python-dotenv 1.0 — .env file loading (dev only)

### Marketplace libraries (added in Prompt 1b, used starting Prompt 5+)

- playwright 1.47 + Chromium — headless browser for FB Marketplace,
  Kleinanzeigen, Leboncoin, Wallapop
- praw 7.7 — official Reddit API
- mercapi 0.3+ — Mercari JP async API wrapper (replaces archived mercari package)
- tenacity 8.5 — retry with exponential backoff
- anthropic 0.39 — Claude Haiku SDK (Prompt 9: AI re-verification)

### Data & verification (added Prompt 8)

- price_tracker.py — SQLite price_history table, currency conversion (10 currencies), deal detection (30%+ below average)

### Infrastructure files

- requirements.txt — pip dependencies
- Dockerfile — based on mcr.microsoft.com/playwright/python:v1.47.0-noble
  (Chromium pre-installed, replaces nixpacks.toml — deleted in Prompt 5)
- railway.toml — Railway deploy config (start command, restart policy)
- .gitignore — standard Python + project-specific ignores
- CLAUDE.md — this file, single source of truth

### Why these choices

- playwright over selenium: Playwright is faster, better async
  support, modern API, better stealth out-of-the-box.
- praw over aiohttp+manual OAuth: official, maintained, handles
  rate limits natively.
- mercari over custom scraping: the package simulates Mercari JP's
  signed API requests — much more robust than HTML scraping.
- tenacity over manual retry: de-facto Python retry library; clean
  decorator syntax, exponential backoff built-in.
- anthropic over openai: we're using Claude (Haiku) for re-verif;
  using Anthropic's own SDK avoids reverse engineering.
