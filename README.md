# 🎻 Zeta Violin Hunter

Bot care caută zilnic viori electrice **Zeta** second-hand prin 22 de module de colectare,
unele cu mai multe platforme și sute de regiuni, plus căutări web pe alte domenii,
și trimite alerte pe Telegram, cu poză, preț în euro, semn „livrează în România”
și marcaje pentru modele rare. Rulează pe Railway, la **10:00 și 22:00, ora României** (inclusiv schimbarea orei de vară).

Documentația tehnică completă, deciziile și starea verificată a fiecărei surse
sunt în [CLAUDE.md](CLAUDE.md). Acest README e doar harta.

## Surse

| Direct (API sau HTML) | Prin Google / Brave |
|---|---|
| Reverb, eBay (16 piețe), Craigslist (468 zone US+CA), ShopGoodwill, HiBid, GovDeals, AllSurplus, Kijiji, Marktplaats + 2dehands, Willhaben, FINN/Tori/DBA/Blocket, Gumtree UK, OLX PL/PT/BG/UA, Subito, Mercari JP, Yahoo Auctions JP, dealeri second-hand (Electric Violin Shop, StringWorks, Guitar Chimp) | OfferUp, Mercari US, Etsy, Facebook Marketplace, Guitar Center, Gardiner Houlgate, Tarisio, Heritage Auctions, PublicSurplus, Catawiki, Proxibid, LiveAuctioneers, Kleinanzeigen, Leboncoin, Wallapop, Allegro, forumuri și restul |

Guitar Center și Facebook Marketplace pornesc automat când există `US_PROXY_URL`.

## Ce face la fiecare ciclu

1. Rulează toate scraperele în paralel, fiecare raportează câte articole a citit de la sursă.
2. `filters.classify()` elimină: alte branduri, produse noi și dealeri de Zeta noi, zgomot
   (accesorii, replici, jachete Arc'teryx…), cereri de cumpărare, anunțuri încheiate.
3. Verifică oferta de vânzare, nu doar HTTP 200: elimină articolele și loturile încheiate; disponibilitatea imposibil de confirmat este etichetată explicit. Motoarele completează inclusiv sursele directe.
4. Dedup pe id și URL, marcare „posibil duplicat” între platforme, urmărire preț, scăderi ≥ 15 %. Notificările eșuate rămân în coada SQLite până la livrare. Comparațiile de preț folosesc minimum cinci viori similare, fără licitații sau loturi mixte.
5. Alerte Telegram imediat, per platformă. Watchdog pentru erori și surse neverificate; un răspuns valid fără rezultate nu este o defecțiune. Rezumat duminica.

## Comenzi Telegram

`/cauta` pornește o căutare acum · `/status` starea surselor · `/active` viorile văzute live în ultimele 3 zile · `/verifica LINK` explică starea actuală a unui anunț · `/help`

## Rulare locală

```bash
pip install -r requirements.txt
cp env.example .env   # completează cheile
python -m tests.test_filters && python -m tests.test_trackers && python -m tests.test_dedup && python -m tests.test_liveness && python -m tests.test_reliability
python main.py
```

Fără `TELEGRAM_BOT_TOKEN` alertele se afișează în log ca preview.

## Variabile importante

Vezi [env.example](env.example). Minim: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `GOOGLE_API_KEY`, `GOOGLE_CSE_ID`, `BRAVE_API_KEY`,
`DB_PATH=/data/zeta_listings.db` (volum Railway), `CONDITION=used`.

## Acoperire și buget

Toate sursele folosesc vocabularul comun din `keywords.py`: Zeta/Zetta, Jazz Fusion,
Strados, coduri de model și ediții de artist, cu traduceri locale. Termenii rari se
rotesc între cicluri, astfel încât numărul de cereri rămâne limitat.

Licitațiile au cinci căutări prioritare la fiecare ciclu Google/Brave, inclusiv case
cu istoric Zeta verificat. Lista și exemplele sunt în [docs/auction-sources.md](docs/auction-sources.md).
Patru platforme au și căutare directă: GovDeals, AllSurplus, HiBid și ShopGoodwill.
Loturile cu viori și viole sunt acceptate numai dacă titlul identifică explicit viorile.
Alertele disting oferta curentă de prețul final și indică ridicarea personală când sursa o declară.

Nu sunt adăugate servicii plătite, AI sau proxy-uri. Google are plafon de 96 cereri/zi,
Brave 32/zi și 960/lună, rezervate în SQLite înaintea cererii, inclusiv la restart.
Creditul Brave și limitele contului trebuie să rămână disponibile; Railway facturează
resursele reale, deci abonamentul de 5 USD nu garantează singur un plafon de consum.
`DB_PATH=/data/zeta_listings.db` trebuie păstrat pe volumul existent.

Craigslist folosește tot patru căutări pe regiune, dar trei caută acum în toate
categoriile de vânzări, inclusiv estate/garage sales. Citește descrierea completă,
verifică paginile și datele evenimentelor, și limitează detaliile la 150 de pagini
pe ciclu. Un inventar mixt trebuie să identifice explicit o vioară Zeta/model;
o haină Zeta și o vioară fără legătură nu formează o potrivire. Anunțurile trimise
pentru evenimente arată obiectul din inventar și ultima zi anunțată.

Pe lângă cele cinci grupuri de licitații, trei căutări web prioritare urmăresc
piețe americane, magazine de instrumente folosite și estate sales (inclusiv
AuctionNinja și MaxSold), în același plafon de cereri. Brave păstrează fragmentele
suplimentare de context și dezactivează corectarea automată a numelor de modele.
Acesta este acces prin indexare web, nu colectare directă garantată.

`/status` arată separat sursele omise, cu eroare sau cu acces parțial. Numărul
surselor rulate din ciclurile noi exclude modulele omise de guard/configurație.
Detaliile Craigslist includ candidații verificați, expirați, neverificați și
amânați de plafon. Un răspuns HTTP reușit nu confirmă că există o vioară eligibilă.

Indexarea motoarelor și blocările site-urilor limitează acoperirea; nu este promisă
detectarea fiecărei licitații. Arhivele documentează alegerea surselor și nu generează
alerte de vânzare activă. Testele sunt offline; probele live opționale din liveness
se activează separat prin `RUN_LIVE_TESTS=1`.

Codurile și artiștii sunt indicii de căutare: `JV44` apare și pe machete, iar Jean-Luc Ponty pe discuri. Codurile cer context de vioară în titlu; numele unui artist nu confirmă marca Zeta, chiar dacă titlul spune „violin”. Pentru artiști trebuie și indicii de brand sau model. `JLP5` este tratat drept cod de model. Formatele CD/LP/DVD sunt respinse și când sunt lipite de caractere japoneze sau scrise cu litere full-width. Exemplele reale din probele live sunt teste de regresie.
