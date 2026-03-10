# 🎻 Zeta Violin Hunter Agent

Agent automat care caută zilnic viori Zeta pe 40+ platforme globale și trimite alerte pe Telegram.

---

## 📦 Structura proiectului

```
zeta-agent/
├── main.py              # Entry point — rulează agentul
├── config.py            # Configurație și keywords
├── database.py          # SQLite — tracking listinguri văzute
├── notifier.py          # Alerte Telegram
├── scrapers/
│   ├── base.py          # Clasă de bază (filtrare, relevance score)
│   ├── reverb.py        # Reverb.com (API public, fără cheie)
│   ├── ebay.py          # eBay global (Finding API, gratis)
│   ├── google.py        # Google Search → prinde toate platformele
│   └── craigslist.py    # Craigslist (scraping direct)
├── requirements.txt
├── Procfile             # Pentru Railway.app
├── railway.toml
└── .env.example         # Template variabile de mediu
```

---

## 🚀 Setup în 5 pași

### Pasul 1 — Creează botul de Telegram (2 minute)

1. Deschide Telegram → caută **@BotFather**
2. Trimite `/newbot` → alege un nume → copiază **token-ul**
3. Caută **@userinfobot** → trimite `/start` → copiază **Chat ID**

### Pasul 2 — eBay API (gratis, 2 minute)

1. Mergi la [developer.ebay.com](https://developer.ebay.com)
2. Creează cont → **Get Application Keys**
3. Copiază **App ID (Client ID)**

### Pasul 3 — Google Custom Search (gratis, 5 minute)

1. Mergi la [console.cloud.google.com](https://console.cloud.google.com)
2. Activează **Custom Search API** → creează cheie API
3. Mergi la [programmablesearchengine.google.com](https://programmablesearchengine.google.com)
4. Creează un motor nou → bifează **"Search the entire web"** → copiază **Search engine ID**

> **Limită gratuită:** 100 căutări/zi — suficient pentru rulare zilnică.

### Pasul 4 — Deploy pe Railway.app (5 minute)

1. Mergi la [railway.app](https://railway.app) → **New Project**
2. **Deploy from GitHub repo** → upload sau conectează repo-ul
3. Mergi la **Variables** → adaugă toate variabilele din `.env.example`:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
EBAY_APP_ID=...
GOOGLE_API_KEY=...
GOOGLE_CSE_ID=...
SEARCH_HOUR=9
MIN_PRICE=0
MAX_PRICE=99999
CONDITION=all
```

4. Railway detectează automat `Procfile` și pornește agentul ✅

### Pasul 5 — Test

Agentul rulează automat la pornire + zilnic la ora configurată.
Vei primi pe Telegram:

```
🎻 ZETA VIOLIN HUNTER
📦 3 new listing(s) found!
──────────────────────────────

🎻 #1 — Zeta Strados Electric Violin
💰 Price: 850.00 USD
📍 Location: CA, US
🛒 Platform: Reverb
📅 Posted: 2025-06-12
⭐ Relevance: 9/10
🔗 View Listing
```

---

## 🔍 Platforme acoperite

| Categorie | Platforme |
|-----------|-----------|
| **API direct** | Reverb, eBay (toate țările) |
| **Via Google Search** | Kleinanzeigen, Leboncoin, Subito, Wallapop, Marktplaats, Willhaben, Blocket, Finn, Tori, Allegro, Catawiki, Tarisio, Mercari, Gumtree, Reddit, Maestronet, Violinist.com, Guitar Center, Thomann, Audiofanzine + multe altele |
| **Scraping direct** | Craigslist (20 orașe globale) |

---

## ⚙️ Filtre disponibile

| Filtru | Variabilă | Default |
|--------|-----------|---------|
| Preț minim | `MIN_PRICE` | 0 |
| Preț maxim | `MAX_PRICE` | 99999 |
| Stare | `CONDITION` | all |
| Ora căutare (UTC) | `SEARCH_HOUR` | 9 |

---

## 🔧 Rulare locală (opțional)

```bash
pip install -r requirements.txt
cp .env.example .env
# editează .env cu cheile tale
python main.py
```

---

## 📊 Cum funcționează Relevance Score

| Scor | Semnificație |
|------|-------------|
| 8-10 | Model specific identificat (Strados, JV44 etc.) |
| 5-7  | „Zeta violin" / „Zeta electric" clar menționat |
| 2-4  | Posibil Zeta — verifică manual |
| 1    | Exclus automat |

---

## ❓ FAQ

**Nu primesc nimic pe Telegram?**
→ Verifică `TELEGRAM_BOT_TOKEN` și `TELEGRAM_CHAT_ID`. Trimite un mesaj botului tău înainte de prima rulare.

**eBay nu returnează rezultate?**
→ Verifică că `EBAY_APP_ID` e corect și aplicația e activă în developer portal.

**Google returnează eroare 429?**
→ Ai depășit limita zilnică de 100 queries. Consideră un upgrade la Google API paid sau reduce numărul de keyword-uri.
