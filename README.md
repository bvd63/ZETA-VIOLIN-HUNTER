# 🎻 Zeta Violin Hunter

Bot care caută zilnic viori electrice **Zeta** second-hand pe ~22 de piețe din lume
și trimite alerte pe Telegram, cu poză, preț în euro, semn „livrează în România”
și marcaje pentru modele rare. Rulează pe Railway, o dată pe zi la 12:00 ora României.

Documentația tehnică completă, deciziile și starea verificată a fiecărei surse
sunt în [CLAUDE.md](CLAUDE.md). Acest README e doar harta.

## Surse

| Direct (API sau HTML) | Prin Google / Brave |
|---|---|
| Reverb, eBay (16 piețe), Craigslist (468 zone US+CA), ShopGoodwill, HiBid, Kijiji, Marktplaats + 2dehands, Willhaben, FINN/Tori/DBA/Blocket, Gumtree UK, OLX PL/PT/BG/UA, Subito, Mercari JP, Yahoo Auctions JP, dealeri second-hand (Electric Violin Shop, StringWorks, Guitar Chimp) | OfferUp, Mercari US, Etsy, Facebook Marketplace, Guitar Center, Catawiki, Proxibid, LiveAuctioneers, Kleinanzeigen, Leboncoin, Wallapop, Allegro, forumuri și restul |

Guitar Center și Facebook Marketplace pornesc automat când există `US_PROXY_URL`.

## Ce face la fiecare ciclu

1. Rulează toate scraperele în paralel, fiecare raportează câte articole a citit de la sursă.
2. `filters.classify()` elimină: alte branduri, produse noi și dealeri de Zeta noi, zgomot
   (accesorii, replici, jachete Arc'teryx…), cereri de cumpărare, anunțuri încheiate.
3. Rezultatele din motoarele de căutare sunt deschise și verificate că mai sunt active.
4. Dedup pe id, marcare „posibil duplicat” între platforme, urmărire preț, scăderi ≥ 15 %.
5. Alerte Telegram imediat, per platformă. Watchdog dacă o sursă tace 3 zile. Rezumat duminica.

## Comenzi Telegram

`/cauta` pornește o căutare acum · `/status` starea surselor · `/active` viorile văzute live în ultimele 3 zile · `/help`

## Rulare locală

```bash
pip install -r requirements.txt
cp env.example .env   # completează cheile
python -m tests.test_filters && python -m tests.test_trackers && python -m tests.test_dedup && python -m tests.test_liveness
python main.py
```

Fără `TELEGRAM_BOT_TOKEN` alertele se afișează în log ca preview.

## Variabile importante

Vezi [env.example](env.example). Minim: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `GOOGLE_API_KEY`, `GOOGLE_CSE_ID`, `BRAVE_API_KEY`,
`DB_PATH=/data/zeta_listings.db` (volum Railway), `CONDITION=used`.
