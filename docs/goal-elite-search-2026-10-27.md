# Obiectiv persistent — Zeta Hunter, 27 octombrie 2026

Proprietarul a cerut explicit continuarea autonomă până la implementare și verificare riguroasă, cu țintă de performanță profesională comparabilă ca atenție și disciplină cu agenții de vârf. Obiectivul este activ în Codex; acest dosar păstrează cerințele și acceptarea tehnică, fără promisiunea imposibilă a accesului la întregul inventar extern.

## Contractul proprietarului

- Toate căutările, limbile, regiunile, modelele și sursele existente se păstrează. Singura sursă eliminată explicit este Yahoo Japonia.
- 10:00 și 22:00 Europe/Bucharest, cu DST. Între ture se pot verifica detalii deja descoperite, fără relansări necontrolate ale căutărilor plătite.
- Prioritate vintage JLP/Jean-Luc Ponty, Jazz/Jazz Fusion/Jazz Standard și familia JV, toate aliasurile existente inclusiv JV5/JV44/JV45, sunburst/burst. Anul1993 nu este cerință. Alte modele rămân eligibile. Strados are interes redus, inclusiv varianta de căluș cu pini.
- Titlul fără violin nu justifică respingerea. Descrierea primară, modelul, marca și categoria sunt dovezi; se păstrează suportul multilingv și variantele4/5strings. Accesoriul inclus nu înseamnă vânzare exclusiv de accesoriu.
- Separă identitate, disponibilitate, preferință și livrare. Niciun prag relevance/AI/queryMatchScore nu ascunde viori eligibile. Identitatea sigură cu disponibilitate necunoscută își păstrează ruta de alertă etichetată. Pistele neconcludente rămân durabile.
- Ofertele confirmat încheiate, articolele, înregistrările muzicale, alte obiecte, accesoriile-only și paginile de categorie nu se anunță drept viori active.
- Telegram și Pushover: mesaje vizibile/sonore, retry/outbox, reminders până la Am văzut, oprire pe ambele canale la confirmare, feedback pe anunț fără blacklist global. Trimiterea acceptată nu demonstrează sunetul fizic al telefonului.
- Buget total comunicat25USD/lună. Fără abonamente, achiziții, proxy, recharge automat, copiere de cookies sau export de secrete. Aprobarea calității nu este aprobare pentru cheltuieli nelimitate.

## Lucrările aprobate și probele de acceptare

1. Bugete/cursori: protejează atomic turele de apelurile auxiliare. Păstrează costurile incerte și istorice; rezervă înainte de HTTP. Exa comun search/contents și contoarele de pagini, Brave, Tavily, Apify și ciclurile de facturare trebuie evaluate separat. Regresie: probele de dimineață nu pot consuma ultima căutare/rezervă de seară.
2. Mercatino și alte liste: recunoaște tipul paginii, extrage linkurile individuale, păstrează pagina ca pistă pentru actualizări. Regresii: pagina de căutare cu prețuri nu este produs; produsul real cu recomandări nu devine categorie.
3. Worker detalii: coadă durabilă, limită de batch/concurență/timp, fairness și lease, prioritate pentru cron/alerte, zero căutări/extrageri plătite suplimentare. Include unde este sigur și pistele native de licitații/Craigslist. Expune vechimea corect, distingând încă nepreluat de păstrat pentru reverificare.
4. Facebook: backoff pe candidat pentru no_items/incomplet/refuz, fără a-l șterge/declară sold. Țintele sunt persistate înainte de POST, rezultatul trebuie să corespundă ID-ului cerut, run-ID se reia fără dublă facturare. Cache și dedup nu pot ascunde livrări eșuate.
5. Actori alternativi: adaptare/normalizare și comparație controlată Curious Coder, apoi Black Falcon și variante relevante. Păstrează colectorul actual până la dovadă. Memo23 Free=1URL/60, fără monitoring. Evită ferestre de vechime, strict-title/radius, new-only și dedup extern neconfirmat. Compară aceiași termeni/regiuni, descriere/foto/monedă/stare, paginare, erori, cost total și viori eligibile unice suplimentare. Pilotul nu trimite automat alerte de vânzare.
6. Surse suplimentare: grupuri Facebook publice, inventare reale ale dealerilor/caselor de licitații, feeduri și monitorizare de schimbări, RAG pentru rezerve punctuale. Validează sursa și formatul înainte de activare; nu instala colectori goi sau surse fără relevanță demonstrată.
7. Jurnal per candidat: proveniență, descoperire, coadă, verificare/amânare/respingere cu motiv, livrare și confirmare. Diagnostic accesibil și metrici fresh/cache, brute/confirmate, latență, backlog și cost. Nu pretinde că jurnalul conține anunțuri pe care niciun furnizor nu le-a expus.

## Cercetarea rămâne deschisă, fără limită arbitrară de repo-uri

Clarificare ulterioară a proprietarului: pentru selectarea surselor NOI, dovezile istorice de vânzare trebuie să fie din ultimii2ani, cu maximum3ani acceptați (27octombrie2023–27octombrie2026, preferabil după27octombrie2024). Nu folosim anul fabricației, data indexării sau SOLD fără dată verificabilă drept probă de vânzare recentă. O ofertă activă actuală rămâne o pistă utilă, etichetată separat de o vânzare istorică datată. Aceasta este fereastra cercetării surselor, nu o limită de vechime a anunțurilor active sau a viorilor și nu elimină surse existente.

Proprietarul a cerut explicit doi subagenți GPT-6.1 Terra High pentru această cercetare: unul SUA/Europa, unul Japonia/Coreea deSud/China. Ei cercetează și raportează; răspunderea pentru validare/integrare/teste rămâne la agentul principal. Rapoarte separate în outputs/research-20261027/usa-europe-agent.md și east-asia-agent.md.

Evaluează toate cele8 repo-uri identificate și orice alte resurse relevante; consemnează în matrice fișierul inspectat, licența, mecanismul, beneficiul, costul/memoria, proba și decizia:

- Crawlee Python: cozi SQL/lease/reclaim, concurență adaptată resurselor.
- Secondhand MCP: GraphQL Facebook anonim + HTML alternativ, refuz versus zero explicit, timp și retry limitate.
- Scrapling: backoff per domeniu și Retry-After numeric/dată HTTP, parsare adaptivă verificată.
- urlwatch: ETag/If-Modified-Since/304 și filtre pentru inventare.
- changedetection.io: monitorizare și metadate primare de stoc/preț.
- extruct: JSON-LD/Microdata/OpenGraph cu proveniență și erori vizibile.
- AI Marketplace Monitor: istoric de decizii/digest; fără importarea pragului AI de respingere. Licența AGPL se respectă.
- Actor RAG Web Browser: URL/link normalization, citire HTML/browser și Google separat de CSE; contul Apify rămâne o resursă comună.

Codul fără licență ori dependent de sesiunea Chrome/macOS nu se copiază orbește. Gratuitatea codului nu implică acces/index/proxy/calcul gratuit. Refolosirea ideii fără nouă dependență poate fi rezultatul cel mai potrivit. Nicio țintă arbitrară de număr de biblioteci, servicii sau modele LLM.

## Verificare, publicare și încheiere

Baseline8204a53. Difful complet trebuie revizuit împreună cu contractul de acoperire. Păstrează14suite și adaugă regresii semnificative pentru bugurile reale, concurență/bugete, schema Actorilor, pagini multilingve/listă/produs/ended, restart/cozi/deadline, outbox și oprirea alarmelor. Niciun test plătit sau TEST live repetitiv.

Actualizează CLAUDE.md/docs; commit/push main după verificări; CI/predeploy și versiunea Railway efectivă se verifică. Citește până la capăt o rulare relevantă: interogări NOI, nu doar cache/cozi; rezultate brute, nu număr presupus de viori; livrare, nu citire presupusă. Nu reseta cote/cursori sau istoric pentru un rezultat verde.

Verifică SQLite/WAL, backup+restore, readiness/monitorizare, autentificarea endpointurilor și lipsa scurgerilor de secrete. Tokenul Telegram a fost deja rotit; nu îl reciti/exporta sau roti fără motiv.

Raportul final trebuie să distingă implementat/testat/activ de propus și blocat extern, cu dovezi, beneficii măsurate și limite investigate. Nu marca obiectivul complet doar pentru că s-a încheiat un mesaj. Fără monitorizare permanentă nouă nesolicitată sau subagenți neautorizați.

Auditul de pornire și dovezile de producție sunt păstrate local în outputs/research-20261027/ (ignorat de Git); identificatorii operaționali rămân în obiectivul privat Codex.

## Stadiu de execuție la 27octombrie, înaintea turei naturale de10:00

- Publicate și verificate CI/deploy:994911f,8bbb47e,ddb1f16. Două cicluri auxiliare complete au păstrat guardurile programate; numai prima probă a avut16interogări Brave noi. Nu se confundă cu o tură nouă a tuturor furnizorilor.
- Eroarea live EasyLive Closed a fost corectată, mesajul original editat și reamintirile închise tehnic, fără confirmare de citire inventată. Backup/restaurare izolată verificate, inclusiv registrul noii închideri.
- Auditul cardurilor native adaugă3suite obligatorii (total18), cu verificarea primară înainte de alertă pentru pistele incomplete, separarea metadatelor/contextului și păstrare la refuz. Implementarea este supusă propriului CI/deploy și ciclului final, nu este acceptată doar dintr-un test izolat.
- Verificare unică programată după tura naturală10:00, la10:20, fără declanșare de alt ciclu. Va controla interogările noi pe furnizor, resursele, stările parțiale/cozile, livrarea și eventualele regresii. Obiectivul rămâne activ până la rezolvarea/verificarea lucrărilor necesare.
- Comparațiile Actorilor rămân condiționate de credit verificat peste rezerva turelor; protocoalele gratuite Facebook testate au fost necitibile. Aceste limite externe nu sunt raportate drept funcții active și nu se ocolesc prin resetarea protecțiilor sau achiziții automate.
