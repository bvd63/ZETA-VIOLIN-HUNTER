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
- Also exposes HTTP endpoints: `POST /search` (manual trigger) and `GET /health`

Non-goals: web UI, multi-user, real-time, mobile app, other instruments (no 
violas, cellos, basses, mandolins — violins only).

---

## 2. CURRENT STATE (verified from live Railway logs on 2026-04-14)

### Infrastructure
- Deployment status: Active
- Last run: daily at 09:00 UTC (scheduler working)
- Cost: ~$0.38/month actual, ~$1.96/month estimated (well under $5 free tier)
- HTTP server: port 8080, endpoints `/search` and `/health` reachable

### What works
- `scrapers/reverb.py` — returns listings from Reverb API (public, no key needed)
- `scrapers/craigslist.py` — returns listings from RSS feeds (scans ~400 US cities)
- `main.py` core orchestration: scheduler, concurrency, dedup, Telegram send
- `database.py` — SQLite dedup with hash(platform:url)
- `notifier.py` — Telegram formatted alerts
- `config.py` — env-based configuration

### What is broken
- `scrapers/ebay.py` — uses eBay Finding API, DECOMMISSIONED 2025-02-05. 
  Returns 0 from all 18 country sites. Needs migration to Browse API (OAuth2).
- `scrapers/facebook_marketplace.py` — STUB. Logs 402 "Searching..." lines 
  per run but does nothing. Returns [].
- `scrapers/instagram.py`, `tiktok.py`, `discord.py`, `telegram.py`, 
  `youtube.py` — all stubs. Return [] with a warning log.
- `scrapers/58com.py` + `scrapers/com_58.py` — DUPLICATE files for the same 
  site (58.com). `main.py` only imports Com58Scraper from com_58.py. 58com.py 
  is dead code.
- `scrapers/kleinanzeigen.py`, `leboncoin.py`, `wallapop.py`, `marktplaats.py`, 
  `willhaben.py`, `ricardo.py`, `blocket.py`, `finn.py`, `tori.py`, 
  `allegro.py`, `gumtree.py`, `kijiji.py`, `mercari.py`, 
  `yahoo_auctions_japan.py`, `rakuten.py`, `carousell.py`, `douban.py`, 
  `tarisio.py`, `maestronet.py`, `audiofanzine.py`, `zikinf.py`, 
  `mercatinomusicale.py`, `sweetwater.py`, `guitar_center.py`, `thomann.py`, 
  `gear4music.py`, `chicago_music_exchange.py`, `vintage_king.py`, 
  `musicians_friend.py`, `catawiki.py`, `invaluable.py`, `hibid.py`, 
  `bonhams.py`, `sothebys.py`, `christies.py`, `reddit.py`, `avito.py`, 
  `yandex_market.py`, `mercadolibre.py`, `tokopedia.py`, `shopee.py`, 
  `lazada.py`, `dafiti.py`, `zeta_music_official.py` — all return 0 raw 
  listings. Outdated CSS selectors, anti-bot blocks, or non-existent endpoints.
- `scrapers/subito.py` — returns some listings but crawls entire __NEXT_DATA__ 
  tree including unrelated ads. Needs strict filter (require "zeta" in the 
  ad's own title/body, not in recommendations).
- `scrapers/google.py` — works when quota available but consumes 65 queries 
  per run (13 keywords × 5 site groups) out of 100/day free tier. A single 
  restart wastes the day's budget.

### Configuration bugs
- `config.py` EXCLUDED_LOCATIONS = ["Romania", "ro"] uses substring match. 
  "ro" matches "Rome", "Toronto", "Brooklyn", "Provo", etc. Must match only 
  full country codes or full words.
- `Procfile` declares `worker: python main.py` but `main.py` binds an HTTP 
  server on port 8080. Railway worker processes do not get a public URL — 
  conflicts with the HTTP server pattern.
- `requirements.txt` includes `scrapy` which is NOT imported anywhere in the 
  code. Adds Twisted + cryptography + many transitive deps. Slows builds.
- `scrapers/reverb.py` passes `state=all` to Reverb API. The correct Reverb 
  param values are `new`, `used`, `b-stock`. `all` is likely ignored.
- `scrapers/reverb.py` runs 30+ keywords × 5 pages = 150+ API calls per cycle. 
  Unnecessarily aggressive. Target: ~8 keywords × 2 pages.

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
  - base.py — Base class: filters, relevance scoring
  - reverb.py — Reverb.com public API
  - ebay.py — eBay (currently broken, Finding API dead)
  - google.py — Google Custom Search (site: operator)
  - craigslist.py — Craigslist RSS across US cities
  - subito.py — Subito.it (Italian classifieds)
  - [many others] — Mostly dead, see "What is broken" above
- requirements.txt
- Procfile — Railway process declaration
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

Names only — values live in Railway Variables tab, never committed.

Required:
- TELEGRAM_BOT_TOKEN — from @BotFather
- TELEGRAM_CHAT_ID — from @userinfobot
- GOOGLE_API_KEY — Google Cloud Console
- GOOGLE_CSE_ID — programmablesearchengine.google.com

Required after eBay migration (Prompt 2):
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

- Prompt 0 — Create CLAUDE.md foundation (IN PROGRESS)
- Prompt 1 — Prune dead code: remove stub scrapers, fix Procfile, clean 
  requirements.txt, remove duplicate 58com.py
- Prompt 2 — Rewrite scrapers/ebay.py using eBay Browse API with OAuth2 
  client credentials
- Prompt 3 — Rewrite scrapers/subito.py with strict Zeta filter (no 
  __NEXT_DATA__ tree walking)
- Prompt 4 — Fix config.py location filter bug (substring "ro" match) + 
  tune Reverb pagination + add daily quota guard for Google
- Prompt 5 — Extend /status endpoint to report per-scraper stats (raw, 
  filtered, sent, errors, last_run) for operator visibility

---

## 12. DECISIONS LOG

| Date | Decision | Justification |
|---|---|---|
| 2026-04-16 | Drop ~50 non-working scrapers rather than fix each | Net savings: fewer false positives, simpler maintenance, rely on Google Custom Search for platforms we can't scrape directly |
| 2026-04-16 | Migrate eBay to Browse API (OAuth2) | Finding API decommissioned 2025-02-05, no alternative |
| 2026-04-16 | Leave 15 junk DB entries in place | They simply dedupe by URL hash — harmless, no re-alert risk |
| 2026-04-16 | Exclude Romania from search | Owner preference (Vlad is in Romania, not buying local) |
| 2026-04-16 | Include signature artists (JLP, Boyd Tinsley, Eileen Ivers, Vanessa-Mae) | High-signal indicators of genuine Zeta violins on second-hand market |
| 2026-04-16 | Scope = violins only (no viola, cello, bass, mandolin) | Owner preference, keeps filter tight |

---

## 13. KNOWN BUGS

| Bug | Severity | Status |
|---|---|---|
| eBay Finding API returns 0 (decommissioned) | High | To fix in Prompt 2 |
| Facebook Marketplace scraper is a stub with 402 log-spam lines | Medium | To delete in Prompt 1 |
| 40+ scrapers have broken CSS selectors / anti-bot blocks | Medium | To delete in Prompt 1 |
| "ro" in "Rome" substring match excludes legit Italian/Canadian listings | Medium | To fix in Prompt 4 |
| Procfile says "worker" but main.py serves HTTP on 8080 | Low | To fix in Prompt 1 |
| scrapy in requirements.txt but not imported | Low | To fix in Prompt 1 |
| Reverb 30 keywords × 5 pages = 150+ API calls per cycle | Low | To tune in Prompt 4 |
| Google CSE quota (100/day) consumed per run, wasted on restarts | Low | To add daily guard in Prompt 4 |
| Duplicate file scrapers/58com.py and scrapers/com_58.py | Low | To fix in Prompt 1 |

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
