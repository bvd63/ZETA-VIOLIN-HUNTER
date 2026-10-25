# Dezvoltarea căutării Zeta — cercetare verificată la 25 octombrie 2026

Recomandarea este să păstrăm colectoarele directe, să adăugăm două servicii de
căutare cu bugete independente și să monitorizăm separat inventarele importante.
O coadă persistentă trebuie să păstreze paginile încă neverificate. Fiecare
candidat trece prin aceleași reguli pentru vioară Zeta second-hand și prin
verificarea disponibilității, indiferent de unde vine.

Acesta este un raport de cercetare și implementare. **Tavily, Exa și Pushover sunt
activate și verificate** în Railway, după ce proprietarul a salvat cheile direct
acolo. Celelalte servicii din tabele rămân opțiuni evaluate. Nu au fost cumpărate
abonamente sau credite în timpul acestei implementări. Codul public și
documentația oficială au fost inspectate; probele de producție sunt identificate
separat mai jos.

**Implementat în continuarea cercetării, înainte de activarea conturilor:**
adaptoare Tavily și Exa cu rezervări persistente înainte de HTTP; coadă SQLite
pentru detalii Craigslist, GovDeals/AllSurplus și rezultate web; comanda
`/resurse`, consum Apify citit din API și avertismente distincte de 80/95/100%;
escaladare Pushover cu confirmare comună Telegram. Cele zece suite offline trec.
Cheile au fost aplicate de proprietar în Railway. Câte o cerere reală a returnat
19 pagini Tavily și 10 pagini Exa; acestea sunt rezultate brute, nu viori noi
confirmate. La cererea explicită a proprietarului s-a trimis un mesaj marcat TEST
pe fiecare canal Telegram/Pushover. Primirea ambelor a fost confirmată de
proprietar, iar apăsarea «Am văzut» a oprit ambele canale, verificat în SQLite și
prin API-ul Pushover. Testul nu a modificat istoricul anunțurilor. Celelalte
aplicații din raport rămân opțiuni evaluate, nu servicii instalate sau abonamente
cumpărate.

**Corecție de identificare, seara de 25 octombrie:** cercetarea a descoperit
[anunțul arhivat «Zeta JV5 1997 – Black»](https://reverb.com/item/97447559-zeta-jv5-1997-black).
Titlul și descrierea nu conțin un cuvânt pentru vioară, dar modelul și categoriile
îl identifică. Filtrul anterior îl respingea. Corecția combină titlul, descrierea
integrală disponibilă și datele produsului; nu cere obligatoriu «violin» și
recunoaște JV5, JLP4/JLP5, variantele de corzi și termeni multilingvi suplimentari.
Descrierile surselor active nu mai sunt tăiate la 300 de caractere înainte de
clasificare. Cardurile Facebook plauzibile, dar incomplete, ajung la verificarea
detaliilor în aceleași limite de cost. Cazurile de accesorii, discuri și machete
de avion rămân teste negative obligatorii. Toate cele unsprezece suite trec.

Preferința nouă a proprietarului este pentru modele vechi Jean-Luc Ponty/JLP și
sunburst. [Zeta numește finisajul «JLP Burst»](https://zetaviolins.com/store/JLP4-Reissue-p530306070),
dar există reeditări moderne; culoarea și o simplă mențiune de preamplificator
JLP nu autentifică un model vintage. Aceste expresii completează descoperirea și
primesc marcajul «PRIORITATEA TA» în alerte, fără excluderea altor Zeta second-hand.
Anunțul JV5 folosit la verificare este încheiat; nu a fost trimis ca oportunitate.

## 1. Ce s-a verificat în botul existent

- Vioara confirmată de proprietar este [Reverb 103084523](https://reverb.com/item/103084523-zeta-jazz-fusion-5-string-violon-jv5-1993-noire).
  Baza de producție înregistrează livrarea pe Telegram la **20 octombrie,
  07:00:11 UTC = 10:00:11 București**. Ciclul are 1 anunț nou și 1 livrare.
  Telegram a acceptat mesajul; aceasta nu dovedește afișarea pe telefon sau citirea.
- Avertismentele din captură sunt Google 403 (proiect fără acces la API),
  Willhaben 403 și Yahoo Japonia (404 neverificat / pagină cu rezultate încă
  nerandate). Ele nu au oprit colectorul Reverb în acea zi.
- Brave: **624 din 960** cereri rezervate în octombrie și **16 din 32** pe
  25 octombrie înaintea rulării de seară. Ultimele șase cicluri au returnat
  204, 247, 264, 258, 199 și 206 rezultate brute, fără eroare de sursă.
  Aceste contoare sunt ale botului, nu soldul contului Brave.
- Apify: registrul botului are **2,86/4 USD** rezervate în octombrie:
  40 lansări discovery × 0,065 și 13 lansări details × 0,02. Rezervarea este
  conservatoare; nu este factura și nu include toate utilizările contului.
- API-ul oficial Apify raportează **aproximativ 3,81 USD utilizare totală**
  în ciclul **5 octombrie–4 noiembrie** și `maxMonthlyUsageUsd=10`.
  Plafonul 10 USD nu este un sold de credit gratuit. Ciclul contului diferă
  de luna UTC a registrului botului. [API usage](https://docs.apify.com/api/v2/users-me-usage-monthly-get),
  [API limits](https://docs.apify.com/api/v2/users-me-limits-get).
- O probă izolată Craigslist a primit HTTP 200 în Bay Area și Phoenix,
  cu 12, respectiv 7 rezultate pentru electric violin. Nu s-au lansat căutări
  Brave/Apify și nu s-au trimis notificări. În aceste 19 rezultate, titlul
  era compatibil cu parserul actual. Nu rezultă că toate regiunile sunt exhaustive.

Nu există dovadă că epuizarea creditelor a cauzat ratarea alertei din 20 octombrie.
Există însă motive să construim rezerve și să afișăm separat consumul, accesul
refuzat, căutările amânate și absența anunțurilor eligibile.

## 2. Alternative pentru căutarea web

| Serviciu | Alocația publicată acum | Utilizare propusă și limită practică |
|---|---|---|
| [Tavily](https://docs.tavily.com/documentation/api-credits) | 1.000 credite/lună gratuite, fără card; Basic=1 credit, Advanced=2 | Prima rezervă. Inițial 8 căutări Basic/ciclu = maximum 496 într-o lună de 31 zile. Restul rămâne pentru recuperarea căutărilor eșuate. Fără generare de răspunsuri sau activare automată Advanced. |
| [Exa](https://exa.ai/pricing) | 10 USD credit lunar gratuit plus bonus inițial de 10 USD; Instant de la 4 USD/1.000, Fast/Auto 7 USD/1.000 cu până la 10 rezultate | A doua rezervă și descoperire suplimentară. Buget explicit; conținutul suplimentar și numărul de rezultate pot schimba costul. Nu transformăm creditul în promisiunea unui număr fix de anunțuri găsite. |
| [Firecrawl Cloud](https://www.firecrawl.dev/pricing) | 1.000 credite/lună, echivalentul publicat a 500 căutări sau 1.000 pagini simple | Rezervă pentru căutare și extragere selectivă. Căutarea și citirea paginilor împart creditele. Extragerile automate suplimentare trebuie bugetate. |
| [SerpApi](https://serpapi.com/pricing) | 250 căutări/lună gratuite; Starter 25 USD/lună | Rezervă mică pentru rezultate Google și geolocalizare. Planul plătit singur consumă întregul buget total; nu îl recomand acum. |
| [Serper](https://serper.dev/) | 2.500 interogări gratuite inițiale; primul pachet 50 USD/50.000, valabil 6 luni | Bun pentru un test comparativ. Nu confundăm oferta inițială cu un plan gratuit lunar; achiziția minimă depășește plafonul lunar comunicat. |

Brave rămâne util: [prețul oficial](https://api-dashboard.search.brave.com/documentation/pricing)
este 5 USD/1.000 cereri și include 5 USD credit lunar. Nu îl eliminăm pentru a
adăuga celelalte servicii. Limita locală 960 necesită atenție: 32 cereri/zi ×
31 zile = 992. Orice ajustare trebuie corelată cu creditul real, cererile manuale
și reîncercările; raportul nu modifică plafonul existent.

API-ul clasic Bing nu este o rezervă disponibilă: Microsoft l-a retras la
[11 august 2025](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement).

Furnizori diferiți oferă rezerve de acces și de buget. Nu presupunem că toți au
indexuri independente sau că o ofertă apare instantaneu în vreun index.

## 3. Facebook și pagini blocate

**ScrapeCreators** este alternativa concretă de testat pentru Marketplace.
[Documentația căutării](https://docs.scrapecreators.com/v1/facebook/marketplace/search/)
include coordonate, rază, paginare prin cursor, sortare după noutate, condiție și
disponibilitate. Exemplul returnează și sold/remaining și costul în credite.
[Oferta publicată](https://scrapecreators.com/) începe cu 100 credite de test;
primul pachet este 47 USD/25.000 credite, fără expirare. Nu este o alocație lunară
gratuită. Este un serviciu terț, nu API-ul oficial Meta pentru Marketplace.

Proba propusă compară aceleași regiuni deja verificate în bot, aceleași expresii
Zeta/Strados/Jazz Fusion și variante 4/5 strings. Se măsoară anunțuri unice
eligibile, pagini suplimentare, stare sold/pending/live, cost și întârziere.
Zero rezultate într-un oraș nu validează acoperirea națională. Datele vechi din
cache nu sunt dovadă de disponibilitate actuală. Păstrăm Apify în paralel până
când avem această comparație.

**Zyte API** merită o probă pentru Willhaben/Yahoo și alte pagini cu probleme.
[Tarifele oficiale](https://www.zyte.com/pricing/) PAYG sunt 0,13–1,27 USD/1.000
răspunsuri HTTP și 1,01–16,08 USD/1.000 pagini randate, în funcție de site;
funcțiile suplimentare pot costa separat. Există un credit inițial de 5 USD,
nu o gratuitate lunară. [Documentația](https://docs.zyte.com/zyte-api/pricing.html)
permite plafoane personalizate. Suportul efectiv pentru fiecare site trebuie
demonstrat; un IP american nu rezolvă singur indexarea, autentificarea sau parserul.

## 4. Repo-uri: ce am inspectat și ce putem adapta

Licențele și starea de arhivare au fost verificate prin API-ul GitHub.
Fișierele indicate au fost citite pe secțiunile relevante; un README sau numărul
de stele nu reprezintă un test în Railway. Referințele sunt pentru adaptare sau
integrare selectivă; nu s-a instalat întregul set de proiecte în producție.

| Proiect | Cod / mecanism concret | Aplicare în Zeta Hunter |
|---|---|---|
| [Crawlee Python](https://github.com/apify/crawlee-python), Apache-2.0 | [Coada SQL](https://github.com/apify/crawlee-python/blob/master/src/crawlee/storage_clients/_sql/_request_queue_client.py) păstrează cereri, termene de blocare și starea handled; [pool-ul](https://github.com/apify/crawlee-python/blob/master/src/crawlee/_autoscaling/autoscaled_pool.py) limitează concurența și ritmul | Cozi durabile pentru detaliile amânate la Craigslist/licitații, reluare după restart, prioritate pentru candidați noi și licitații apropiate de închidere. Biblioteca poate rula în afara Apify; nu implică folosirea creditelor Actors. |
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io), Apache-2.0 | [Specificația API](https://github.com/dgtlmoon/changedetection.io/blob/master/docs/api-spec.yaml): CSS/XPath, text exclus, browser steps, JSON-LD, procesor pentru stoc/preț | Urmărirea listelor de produse/loturi ale dealerilor și caselor de licitații. Comparăm identificatori și legături, nu reclame sau contoare din pagină. O schimbare devine candidat; nu este automat o vioară eligibilă. |
| [RSSHub](https://github.com/DIYgod/RSSHub), AGPL-3.0 | [Ruta Craigslist](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/craigslist/search.ts) folosește SAPI, câmpuri etichetate, cache pentru referințe și descrieri complete | Confirmă abordarea SAPI deja folosită de bot. Nu aduce un inventar independent pentru Craigslist. Este util ca referință de parser și pentru fluxuri pe site-uri fără RSS propriu. |
| [Huginn](https://github.com/huginn/huginn), MIT | [WebsiteAgent](https://github.com/huginn/huginn/blob/master/app/models/agents/website_agent.rb): extragere HTML/JSON/XML, evenimente la schimbare, reguli de unicitate | Adaptoare declarative pentru magazine și licitații: URL + selector + câmpuri, cu teste pe pagini reale. Nu este necesară migrarea întregului bot într-o aplicație Rails. |
| [Scrapling](https://github.com/D4Vinci/Scrapling), BSD-3-Clause | [Parser adaptiv](https://github.com/D4Vinci/Scrapling/blob/main/docs/parsing/adaptive.md) care memorează elemente și caută corespondentul după modificarea HTML | Recuperare controlată când se schimbă structura. O potrivire aproximativă trebuie verificată prin URL/identitatea produsului, pentru a nu lega titlul unei viori de prețul altui produs. |
| [Firecrawl](https://github.com/firecrawl/firecrawl), AGPL-3.0 | [Routerul de căutare](https://github.com/firecrawl/firecrawl/blob/main/apps/api/src/search/index.ts) selectează infrastructura proprie, SearXNG sau DuckDuckGo; există adaptor SearXNG separat | Referință pentru interfața comună între furnizori. Instalarea locală nu creează un index propriu și nu garantează că sursele din spate răspund din Railway. |
| [Open Deep Research](https://github.com/langchain-ai/open_deep_research), MIT, **arhivat** | [utils.py](https://github.com/langchain-ai/open_deep_research/blob/main/src/open_deep_research/utils.py) execută căutări asincrone, deduplică URL-uri și selectează instrumente; configurarea limitează iterațiile | Referință pentru coordonarea furnizorilor. Nu recomand integrarea integrală: sumarizarea și buclele LLM consumă alte resurse, iar proiectul este arhivat. |
| [RSS-Bridge](https://github.com/RSS-Bridge/rss-bridge), Unlicense | [CraigslistBridge](https://github.com/RSS-Bridge/rss-bridge/blob/master/bridges/CraigslistBridge.php) citește rezultate HTML statice | O rută alternativă pentru fluxuri, de validat separat. Nu o presupunem superioară SAPI: are alte selectoare și altă paginare. |
| [SearXNG](https://github.com/searxng/searxng), AGPL-3.0; [DDGS](https://github.com/deedy5/ddgs), MIT | Agregare peste alte servicii de căutare | Rezervă experimentală. [SearXNG explică](https://searx.space/) că instanțele publice pot fi blocate mai des. Nu le folosim drept singura cale de descoperire. |
| [extruct](https://github.com/scrapinghub/extruct), BSD-3-Clause | Extragerea metadatelor încorporate în pagini | Extinderea verificării Product/Offer din JSON-LD și alte formate. Trebuie păstrată identificarea produsului principal: paginile editoriale pot avea produse recomandate. |
| [RapidOCR](https://github.com/RapidAI/RapidOCR), Apache-2.0 | OCR pe imagini, inclusiv prin ONNX | Etapă ulterioară: căutarea inscripției Zeta/modelului în fotografii la anunțuri cu titlu generic. Rezultatul OCR oferă indicii; nu garantează marca sau autenticitatea. Necesită măsurarea memoriei și preciziei pe fotografii reale. |

Copiile de lucru ale fișierelor inspectate sunt păstrate local în artefactele
cercetării. Codul extern nu a fost executat, iar cheile și sesiunile utilizatorului
nu au fost trimise proiectelor analizate.

## 5. Aplicații web care pot completa botul

| Aplicație | Ce ajută | Limita relevantă |
|---|---|---|
| [Visualping](https://help.visualping.io/en/articles/14476033) | Monitorizare externă, API și webhooks inclusiv pe Free | 150 verificări/lună și 5 pagini; 2 pagini verificate de două ori/zi folosesc până la 124 verificări. Erorile consumă și ele verificări. |
| [PageCrawl](https://pagecrawl.io/pricing) | Monitorizarea unor liste de produse sau loturi | Free: 6 pagini, 220 verificări/lună. Frecvența minimă publicată nu înseamnă că bugetul permite toate paginile verificate orar. |
| [Distill](https://distill.io/pricing/) | Monitorizare în cloud sau în browserul utilizatorului | Free: 5 monitoare cloud, 1.000 verificări/lună, interval minim cloud 6 ore. Webhooks nu sunt incluse în Free. [Monitorizarea locală](https://distill.io/docs/web-monitor/cloud-local-monitors/) cere calculatorul pornit. |
| [Browse AI](https://www.browse.ai/pricing) | Extracții de liste și monitorizare configurabile vizual | Free: 50 credite/lună, 2 domenii. Un tabel cu multe rânduri poate consuma mai multe credite/verificare. Tariful 19 USD afișat este anualizat, cu plată anuală; nu îl tratăm drept abonament lunar de 19 USD. |
| [Inoreader](https://www.inoreader.com/pricing) / [FreshRSS](https://freshrss.github.io/FreshRSS/en/) | Centralizarea feed-urilor și a regulilor de alertare | Inoreader Pro are funcții de monitorizare; FreshRSS este găzduit de noi. Ambele au nevoie de fluxuri/surse reale. Feed-urile de pagini Facebook nu sunt o garanție de căutare în Marketplace. |
| [Google Alerts](https://support.google.com/websearch/answer/4815696) | Un canal separat de semnalare a paginilor nou indexate | Util ca rezervă, cu regiune și limbă configurabile. Nu garantează fiecare ofertă și nu înlocuiește colectarea directă. |
| [Căutări salvate eBay](https://www.ebay.com/help/buying/finding-items-managing-purchases/save-search?id=4051) și [Reverb](https://help.reverb.com/hc/en-us/articles/41988383227163-Saving-items-on-Reverb) | Notificări furnizate chiar de platformă, fără consum Brave/Apify | Plasă suplimentară de siguranță. Importul automat al emailurilor cere o integrare separată autorizată; nu este activat acum. |

Selectarea câtorva inventare importante pentru monitoare poate acoperi intervalul
dintre căutările generale. Aplicațiile de monitorizare urmăresc pagini cunoscute;
nu descoperă singure orice site sau anunț nou de pe internet.

## 6. Alerte mai greu de ratat

Telegram este deja actualizat: mesaj vizibil, fotografie unde permite caption-ul,
buton de deschidere și **Am văzut**. Reamintire după 10 minute, apoi la 30 minute,
până la confirmare. Citirea sau deschiderea linkului nu poate fi detectată prin
Bot API. Setările de sunet/Focus ale telefonului rămân importante.

**Pushover** este prima opțiune suplimentară pe care aș evalua-o pentru acest scop.
[API-ul](https://pushover.net/api#priority) are prioritate de urgență și confirmări;
o cerere de urgență se poate repeta până la confirmare, cu interval și expirare
(maximum 3 ore pe acea cerere). [Prețul individual](https://pushover.net/pricing)
este 4,99 USD o singură dată/platformă după proba de 30 zile, cu până la 10.000
mesaje/lună prin API fără abonament individual. Necesită instalarea aplicației și
chei proprii. Nu este activat și nu s-a cumpărat nimic.

WhatsApp rămâne posibil prin integrarea oficială Business, cu configurarea
expeditorului și aprobarea mesajelor inițiate în afara ferestrei de răspuns.
[Meta](https://business.whatsapp.com/products/platform-pricing) stabilește costuri
după țară/categorie; [Twilio](https://www.twilio.com/en-us/whatsapp/pricing) adaugă
0,005 USD/mesaj. Numărul destinatarului singur nu configurează această integrare.
Nu presupunem că alertele pentru produse se vor încadra automat în cea mai ieftină
categorie. Numărul utilizatorului nu a fost solicitat sau transmis unui furnizor.

## 7. Ordinea de implementare recomandată

1. **Observabilitatea resurselor:** contoare distincte pentru cereri rezervate,
   consum real, plafon, data resetării, căutări planificate/efectuate/amânate.
   Avertizare anticipată pe risc de insuficiență, nu numai la zero.
2. **Tavily + Exa**, păstrând Brave și colectoarele directe. Mai întâi teste
   comparative cu limite mici și fără Telegram, apoi rezultate live prin aceeași
   verificare de ofertă. Firecrawl/SerpApi devin rezerve pentru cererile nereușite.
   Sunt necesare conturi/chei proprii; acestea se salvează în Railway, nu în chat.
3. **Coadă persistentă de detalii**, inspirată de Crawlee: paginile amânate nu se
   pierd la încheierea ciclului. Reîncercările trebuie să fie echitabile, iar o
   pagină veche problematică să nu țină permanent în urmă candidații noi.
4. **Monitoare pentru inventare selectate**, folosind mecanismele changedetection
   și Huginn. Începem cu 2–3 liste unde există dovezi de vânzări Zeta; măsurăm
   costul/memoria înainte de extindere. Schema de anunț rămâne identică.
5. **Al doilea colector Marketplace**, după testarea celor 100 credite
   ScrapeCreators pe același set de regiuni. Nu eliminăm Apify pe baza unei probe
   goale și nu declarăm acoperire națională pe baza unui singur oraș.
6. **Al doilea canal de alertă**, preferabil Pushover pentru confirmare și
   insistență; WhatsApp dacă proprietarul preferă acel canal și configurarea sa.
7. **OCR selectiv** pentru anunțuri ambigue, după existența unui set de imagini
   verificate. Este o etapă de descoperire/triage, cu justificarea potrivirii.

Nu măresc automat abonamentele sau limitele plătite. Prima etapă de diversificare
poate începe cu alocațiile gratuite. Bugetul total rămâne 25 USD/lună; un abonament
în plus și costurile de găzduire trebuie evaluate împreună, inclusiv plățile
anuale și taxele. O instanță proprie de browser nu este lipsită de cost de calcul.

## 8. Ce înseamnă că o extindere a trecut verificarea

- Set comun de exemple: vioara Reverb din 20 octombrie, variante JV44/JV45,
  Strados, 4/5-string și forme multilingve; separat, cazul SKB fără vioară,
  discurile japoneze, revistele și loturile încheiate.
- Potrivirea instrumentului și disponibilitatea sunt două rezultate distincte.
  O vioară vândută poate fi perfect relevantă, fără să fie o oportunitate activă.
  Scorul vechi 4/10 a fost eliminat din notificări; nu reprezenta probabilitatea
  că este o vioară Zeta și nu era prag de blocare a livrării.
- Se compară anunțurile eligibile unice, momentul primei detectări, costul pe
  candidat verificat, rata mesajelor greșite, paginile amânate și livrările reale.
- Un timeout, 403, 429 sau schelet JavaScript rămâne eroare/coverage partial;
  nu este transformat în „nu există viori”. O eroare la un furnizor nu oprește
  celelalte colectoare. O căutare amânată rămâne înregistrată pentru recuperare.
- Creditele nu sunt consumate prin bucle de research fără limită; reluările după
  restart nu recumpără aceeași muncă. Nicio extindere nu scurtează descrierile,
  nu elimină expresiile existente și nu reduce lista de surse pentru a părea sănătoasă.

Nu există un repo sau serviciu care să garanteze toate anunțurile și zero erori
pe site-uri externe. Criteriul este descoperirea demonstrabil mai bună, recuperarea
după probleme și semnalarea exactă a ceea ce a rămas neverificat.
