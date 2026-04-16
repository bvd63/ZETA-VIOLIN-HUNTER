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

## 2. CURRENT STATE (verified from live Railway logs on 2026-04-14)

### Infrastructure
- Deployment status: Active
- Last run: daily at 09:00 UTC (scheduler working)
- Cost: ~$0.38/month actual, ~$1.96/month estimated (well under $5 free tier)
- HTTP server: port 8080, endpoints `/search`, `/health`, `/status` reachable

### What works
- `scrapers/reverb.py` — returns listings from Reverb API. FIXED in Prompt 3: reduced to 8 keywords × 2 pages (was 30+ × 5).
- `scrapers/craigslist.py` — returns listings from RSS feeds (scans ~400 US cities)
- `scrapers/ebay.py` — REWRITTEN in Prompt 2. Now uses eBay Browse API
  with OAuth2 client_credentials grant. Searches 13 marketplaces with 8
  keywords. Requires EBAY_CLIENT_ID + EBAY_CLIENT_SECRET.
- `scrapers/subito.py` — FIXED in Prompt 3. Strict Zeta filter — requires
  "zeta" or model code in ad's own text. 5 focused keywords.
- `scrapers/google.py` — FIXED in Prompt 3. 20h quota guard via SQLite prevents
  double-run on container restart.
- `main.py` core orchestration: scheduler, concurrency, dedup, Telegram send
- `database.py` — SQLite dedup with hash(platform:url)
- `notifier.py` — Telegram formatted alerts
- `config.py` — env-based configuration. FIXED in Prompt 3: location exclusion
  uses word-boundary matching (EXCLUDED_COUNTRY_CODES = ["RO"]).
- `scrapers/kleinanzeigen.py` — Playwright headless Chromium, searches German
  musical instruments category (Prompt 5).
- `scrapers/wallapop.py` — public API, Spanish marketplace (Prompt 5).
- `scrapers/leboncoin.py` — httpx + __NEXT_DATA__ JSON, French marketplace (Prompt 5).
- `scrapers/mercari_jp.py` — mercapi async wrapper, Japan's #1 C2C marketplace (Prompt 6).
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

### What is broken

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
- config.py — Env-var-based configuration
- database.py — SQLite dedup (zeta_listings.db)
- notifier.py — Telegram sendMessage wrapper
- scrapers/ — Per-platform scraper modules
  - __init__.py
  - base.py — Base class: filters, relevance scoring
  - reverb.py — Reverb.com public API
  - ebay.py — eBay Browse API (OAuth2, 13 marketplaces)
  - google.py — Google Custom Search (site: operator)
  - craigslist.py — Craigslist RSS across US cities
  - subito.py — Subito.it (Italian classifieds; strict Zeta filter)
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
4. Main filter pipeline: strict Zeta check → noise check → intent check → 
   URL validity → platform score threshold → DB dedup
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
- SEARCH_HOUR (default 9 = 09:00 UTC = 12:00 Romania)
- MIN_PRICE (default 0)
- MAX_PRICE (default 99999)
- CONDITION (default "all": all | new | used)
- MIN_YEAR (default 1980)
- MAX_YEAR (default 2014)
- SCRAPER_TIMEOUT_SEC (default 900)
- SCRAPER_RETRIES (default 1)
- SCRAPER_CONCURRENCY (default 4)
- CRAIGSLIST_CONCURRENCY (default 24)
- CRAIGSLIST_MAX_US_CITIES (default 0 = all discovered)

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
- Obtain eBay Client Secret (Cert ID) for OAuth2 Browse API (Prompt 2 
  manual step)
- Verify Railway persistent volume for zeta_listings.db (optional, post-MVP)

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

ALL PROMPTS COMPLETED. Bot is fully operational.

---

## 12. DECISIONS LOG

| Date | Decision | Justification |
|---|---|---|
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
| 2026-04-16 | Disabled 6 anti-bot-blocked scrapers, added /status dashboard with per-scraper stats and price history | Prompt 9 (FINAL). Disabled scrapers remain in codebase for future re-activation. Active scraper count: 7. |
| 2026-04-16 | Pin playwright==1.47.0 explicitly | Each Playwright Python release bundles specific browser versions; pinning prevents surprise breakage on Railway rebuild |

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
