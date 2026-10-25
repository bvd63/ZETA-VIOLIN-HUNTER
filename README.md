# 🎻 Zeta Violin Hunter

Bot care caută zilnic viori electrice **Zeta** second-hand prin 23 de module de colectare (pornesc cele configurate),
unele cu mai multe platforme și sute de regiuni, plus căutări web pe alte domenii,
și trimite alerte pe Telegram, cu poză, preț în euro, semn „livrează în România”
și marcaje pentru modele rare. Rulează pe Railway, la **10:00 și 22:00, ora României** (inclusiv schimbarea orei de vară).

Documentația tehnică completă, deciziile și starea verificată a fiecărei surse
sunt în [CLAUDE.md](CLAUDE.md). Acest README e doar harta.

## Surse

| Direct (API sau HTML) | Prin Google / Brave / Tavily / Exa |
|---|---|
| Reverb, eBay (16 piețe), Craigslist (468 zone US+CA), ShopGoodwill, HiBid, GovDeals, AllSurplus, Kijiji, Marktplaats + 2dehands, Willhaben, FINN/Tori/DBA/Blocket, Gumtree UK, OLX PL/PT/BG/UA, Subito, Mercari JP, dealeri second-hand (Electric Violin Shop, StringWorks, Guitar Chimp) | OfferUp, Mercari US, Etsy, Facebook Marketplace, Guitar Center, Gardiner Houlgate, Tarisio, Heritage Auctions, PublicSurplus, Catawiki, Proxibid, LiveAuctioneers, Kleinanzeigen, Leboncoin, Wallapop, Allegro, forumuri și restul |

Guitar Center și colectorul Facebook nativ necesită `US_PROXY_URL`. Cu tokenul și cele două taskuri Apify configurate, Facebook folosește Apify în locul colectorului nativ. Căutările web suplimentare nu oferă acces complet la Marketplace.

## Ce face la fiecare ciclu

1. Rulează toate scraperele în paralel, fiecare raportează câte articole a citit de la sursă.
2. `filters.classify()` elimină: alte branduri, produse noi și dealeri de Zeta noi, zgomot
   (accesorii, replici, jachete Arc'teryx…), cereri de cumpărare, anunțuri încheiate.
3. Verifică oferta de vânzare, nu doar HTTP 200: elimină articolele și loturile încheiate; disponibilitatea imposibil de confirmat este etichetată explicit. Motoarele completează inclusiv sursele directe.
4. Dedup pe id și URL, marcare „posibil duplicat” între platforme, urmărire preț, scăderi ≥ 15 %. Notificările eșuate rămân în coada SQLite până la livrare. Comparațiile de preț folosesc minimum cinci viori similare, fără licitații sau loturi mixte.
5. Alerte Telegram imediat, per platformă. Watchdog pentru erori și surse neverificate; un răspuns valid fără rezultate nu este o defecțiune. Rezumat duminica.

## Comenzi Telegram

Alertele cu viori au titlu **🚨🎻 VIOARĂ ZETA GĂSITĂ!**, buton de deschidere și
**✅ Am văzut**. Fără confirmare, botul revine după 10 minute și apoi la fiecare
30 de minute, inclusiv după restart. Deschiderea anunțului sau citirea mesajului
nu reprezintă confirmare: Telegram Bot API nu oferă starea de citire.
Rapoartele obișnuite sunt silențioase; alertele și reamintirile cer notificare cu
sunet. Telefonul trebuie să aibă notificările grupului și sunetul activate.
Livrările eșuate sunt reîncercate în fiecare minut, fără căutări Brave/Apify noi.
Istoricul vechi nu este importat ca necitit, pentru a nu retrimite oferte încheiate.

Canalul suplimentar **Pushover** este opțional: `PUSHOVER_APP_TOKEN` și
`PUSHOVER_USER_KEY`. După 10 minute fără confirmare (sau imediat la eșecul Telegram),
trimite o alertă de urgență repetată la 2 minute. Fiecare sesiune durează o oră și
este reînnoită cât timp alerta rămâne neconfirmată. Confirmarea în oricare aplicație
oprește ambele canale; sincronizarea Pushover este verificată la fiecare minut.
Joburile de notificare sunt independente de căutări și unul de celălalt.
Configurarea fără mesaj de test verifică existența unui dispozitiv înregistrat,
nu dovedește că telefonul a afișat notificarea. Permisiunile se verifică pe telefon.

`/cauta` pornește o căutare acum · `/status` starea surselor · `/resurse` consum și rezerve · `/active` viorile văzute live în ultimele 3 zile · `/verifica LINK` explică starea actuală a unui anunț · `/help`

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
Brave 32/zi și 1000/lună, rezervate în SQLite înaintea cererii, inclusiv la restart.
Creditul Brave și limitele contului trebuie să rămână disponibile; Railway facturează
resursele reale, deci abonamentul de 5 USD nu garantează singur un plafon de consum.
`DB_PATH=/data/zeta_listings.db` trebuie păstrat pe volumul existent.

Tavily și Exa completează motoarele existente după salvarea cheilor în Railway:
8 căutări Tavily Basic/ciclu plus 8 căutări suplimentare și 4 Exa Auto/ciclu plus 2 suplimentare,
fără filtru de vechime a paginii. Cele 8 suplimentare acoperă termeni noi,
priorități și alte aliasuri de modele, fără înlocuirea celor 8 căutări existente. Două dintre cele suplimentare verifică
indexul Willhaben și Gumtree UK; eventualele blocaje directe 403/247 rămân raportate.
Cele două suplimentare noi caută Facebook în SUA; și Exa adaugă două căutări Facebook.
Implicit, Tavily folosește cel mult 992 credite într-o lună de 31 zile.
Termenii comuni și grupurile de domenii se rotesc prin cursori persistenți independenți.
În Exa, domeniile sunt preferințe în textul căutării, astfel încât rezultatele
pot veni și de pe alte site-uri; ghidul oficial recomandă evitarea unei liste
obligatorii de domenii când utilizatorul cere acoperire largă.
Tavily are plafon local 1000 credite/31 zile și 32/zi; Exa rezervă conservator
0,017 USD/cerere, maximum 8 USD/31 zile și 0,25 USD/zi. Rezervarea precede HTTP,
inclusiv pentru răspunsuri incerte. Codul nu activează planuri plătite sau reîncărcări.
Conturile pot avea alte utilizări; la refuzul furnizorului celelalte surse continuă.

`/resurse` și `/status` disting rezervările locale de consumul real al contului
Apify (citire la maximum 6 ore). Avertismentele de 80%, 95%, epuizare și refuz de
acces sunt distincte și persistente; o eroare nouă nu este ascunsă de un avertisment
anterior. Plafonul de cont Apify nu este prezentat ca sold de credit gratuit.

Paginile amânate de Craigslist (150/ciclu), GovDeals/AllSurplus (30/ciclu) și
motoarele web (60/sursă/ciclu) rămân în coada SQLite. Reluarea nu depinde de
reapariția lor în index, iar lease-urile expiră după o întrerupere. Un sfert dintre
sloturi revine celor mai vechi pagini eligibile pentru a evita blocarea cozii de
titluri generice. Descrierile complete pot confirma o Zeta absentă din fragmentul
indexului; accesoriile/articolele și disponibilitatea rămân verificate înaintea alertei.

Cercetarea furnizorilor, aplicațiilor și codului open-source folosit ca referință
este în [raportul din 25 octombrie](docs/search-research-2026-10-25.md).

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

Yahoo Japonia a fost eliminat la cererea proprietarului: colectorul direct, țintele
din motoarele de căutare și verificările de pornire. Linkurile Yahoo Japonia
revenite accidental în rezultate globale nu sunt verificate sau trimise.
Celelalte surse japoneze rămân active.
Magazinele Shopify păstrează toate căutările și descrierile complete din
colecții sau predictive search. Cererile către fiecare magazin sunt distanțate
cu 1,5 secunde, iar o eroare la un cuvânt nu oprește celelalte căutări.
Protecția implicită la repornire acoperă intervalul dintre rulările programate
(12 ore pentru 10:00/22:00), pentru a păstra bugetul și ora următoarei căutări.
Mesajele de monitorizare disting problemele de acces/acoperire de o piață fără
anunțuri noi; nicio sursă și nicio căutare existentă nu au fost eliminate.

Indexarea motoarelor și blocările site-urilor limitează acoperirea; nu este promisă
detectarea fiecărei licitații. Arhivele documentează alegerea surselor și nu generează
alerte de vânzare activă. Testele sunt offline; probele live opționale din liveness
se activează separat prin `RUN_LIVE_TESTS=1`.

Codurile și artiștii sunt indicii de căutare: `JV44` apare și pe machete, iar Jean-Luc Ponty pe discuri. Codurile cer context de vioară în titlu; numele unui artist nu confirmă marca Zeta, chiar dacă titlul spune „violin”. Pentru artiști trebuie și indicii de brand sau model. `JLP5` este tratat drept cod de model. Formatele CD/LP/DVD sunt respinse și când sunt lipite de caractere japoneze sau scrise cu litere full-width. Exemplele reale din probele live sunt teste de regresie.

## Siguranță operațională

Vezi [operarea și recuperarea](docs/operations.md) pentru bugete, copiile SQLite,
monitorizare și limitele cunoscute. Niciun scor numeric nu decide dacă primești vioara.
