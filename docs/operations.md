# Operare, bugete și recuperare — 26 octombrie 2026

Programul rămâne 10:00 și 22:00 Europe/Bucharest. Toate cele 23 de module rămase,
modelele, limbile și căutările directe sunt păstrate; Yahoo Japan este exclus la
cererea proprietarului. Prioritatea JLP/Jazz/JV/sunburst nu exclude Strados sau
alte viori Zeta. Călușul din fotografii nu este încă recunoscut automat din imagini.

## Ce verifică livrarea

Anunțurile noi directe și scăderile de preț trec prin verificarea ofertei înainte
de alertă. Anunțurile încheiate confirmate nu sunt trimise. Accesul 403/247,
loginul și challenge-urile sunt incerte, fără a suprascrie identitatea viorii.
O ofertă identificată clar ca vioară poate fi trimisă cu disponibilitate incertă;
un fragment web fără identitate suficientă rămâne în coada persistentă.
Verificările întrerupte rămân pentru reluare; numai livrarea confirmată marchează
anunțul drept trimis. Verificarea paginii nu garantează inventarul real al vânzătorului.

Pentru paginile blocate Heritage, OfferUp, Gardiner Houlgate, Invaluable și Willhaben, Exa
Contents încearcă o extracție live a aceleiași pagini: cel mult20/zi,620/31 zile,
maximum0,620USD/31 zile, în plafonul Exa existent. Sunt acceptate numai rezultate
cu URL identic și status success/source crawled. Se păstrează15 minute pentru
a evita dubla taxare la verificarea finală. Un eșec sau un rezultat doar din
cache păstrează disponibilitatea incertă. Cele șase căutări Exa/ciclu rămân.

Gumtree reîncearcă aceeași interogare prin Chromium anonim când HTTP simplu
întoarce247/403 sau un corp nerecunoscut. Numai anunțuri parsate ori pagina
explicită0ads recuperează starea sursei. Nu sunt eliminate cuvinte cheie.

Willhaben păstrează toate cele10 căutări HTTP și încearcă aceleași URL-uri prin
Exa numai la eșec. Rezerva extrage text live și linkurile reale, cel mult20pagini/zi
și620/31 zile (încă0,620USD). Un număr de anunțuri fără linkuri nu este o căutare
goală; mai puține linkuri decât rezultatele așteptate sunt acoperire parțială.
Gumtree adaugă o ultimă încercare Exa după eșecul HTTP și Chromium, maximum6pagini/zi
și186/31zile (0,186USD). Explicit0ads este o căutare validă; un corp lipsă sau
anunțuri raportate fără linkuri rămân eroare. Linkurile reale dintr-un set
incomplet sunt păstrate, dar sursa rămâne parțială.
Cele trei rezerve de conținut plus căutările Exa înseamnă maximum7,750USD/31zile
și0,250USD/zi, în limitele existente8USD și0,25USD. Niciun rezultat din cache
Exa nu este folosit pentru a afirma disponibilitate curentă.

Butoanele „Nu e vioară” și „Vândut / încheiat” salvează corecția și opresc alarmele
acelui mesaj. Nu introduc automat reguli globale care ar putea elimina alte viori.
„Am văzut” și confirmarea Pushover opresc reamintirile ambelor canale. Telegram
nu oferă confirmare de citire; sunetul depinde și de telefon.

## Plan de consum la 31 zile / 62 cicluri

| Furnizor | Plan bot | Plafon local | Observație |
|---|---:|---:|---|
| Brave | 992 cereri | 1000/lună, 32/zi | Soldul real al contului poate include alte utilizări |
| Tavily | 992 Basic | 1000/31 zile, 32/zi | 8 originale + 8 suplimentare/ciclu |
| Exa | maximum 6,324 USD rezervate | 8 USD/31 zile, 0,25 USD/zi | 4 originale + 2 Facebook/ciclu; creditele gratuite nu sunt garantate perpetuu |
| Apify | 4,6128 USD la tariful și numărul de rezultate observate | 5 USD/lună | 10 rezultate + 2 detalii/ciclu, fără creșterea Actorilor existenți |

Fiecare cerere este rezervată înainte de HTTP. Costul terminal Apify confirmat
prin API înlocuiește plafonul rezervat pentru acel run; răspunsurile incerte și
rulările fără cost confirmat păstrează plafonul integral. Metadatele sunt citite
prin GET, fără lansări de Actori. Tarifele se pot schimba; conturile fără credit
și refuzurile furnizorului sunt raportate, iar celelalte surse continuă. Codul nu
activează reîncărcări, abonamente sau proxy-uri plătite.

Căutările suplimentare Facebook rotesc 20 de zone SUA și toate codurile comune
Zeta; rezultatele publice eligibile intră în coada de două detalii Apify existentă.
Cele două URL-uri regionale originale Apify/ciclu și toate căutările de bază sunt
păstrate. Indexarea web nu înseamnă că sunt văzute toate anunțurile Facebook.

## Copii de siguranță

La pornire dacă ultima copie verificată are peste 23h, apoi la fiecare 24h,
`operations.backup_tick` face online backup SQLite (include WAL), comprimă și
restaurează într-un director temporar separat. `integrity_check` trebuie să fie
`ok` înainte de publicare. Sunt păstrate ultimele 10 copii în `/data/backups`,
cu permisiuni 600. Nu se încarcă baza în GitHub sau în afara Railway. `/resurse`
raportează ultima verificare și erorile.

Aceste copii supraviețuiesc redeployului, dar NU pierderii întregului volum.
Interfața contului Railway Hobby cere Pro pentru backupuri native automate.
Nu s-a activat Pro și nu s-a executat restaurare peste producție. O destinație
externă sau upgrade-ul necesită configurare separată. Testul de restaurare live
folosește o bază temporară, fără modificarea istoricului real.

## Monitor extern

`/health` verifică procesul; `/health/ready` răspunde numai cu `ready` sau
`search_stale`, fără date despre anunțuri, chat sau chei. După 90 minute de grație
verifică dacă există un ciclu finalizat de la ora programată, cu DST automat.
`/search` și `/status` cer întotdeauna `X-Search-Token`; fără secret sunt închise.
Nu se acceptă token în URL. Păstrează secretul exclusiv în Railway.

Workflow-ul GitHub Actions `external health monitor` verifică numai URL-ul
public din variabila de repository `ZETA_HEALTH_URL`, orar la minutul 13 și cu
3 încercări. Nu pornește căutări și nu primește cheile Telegram/Pushover/API.
Rularea standard din acest repo public este gratuită conform GitHub. Eșecurile
apar în Actions; emailul depinde de preferințele GitHub ale proprietarului.
Programările GitHub pot întârzia și pot fi dezactivate după 60 zile fără activitate
într-un repo public. Monitorul nu garantează alarmă telefonică dacă Railway cade.

## Limite externe încă reale

Willhaben 403, Gumtree UK 247 intermitent, Google API 403 și unele site-uri SUA
pot rămâne inaccesibile serverului, chiar dacă sunt vizibile din România.
Căutările în indexuri completează accesul, nu elimină aceste blocaje. Nu există
promisiune de acoperire sau livrare de 100%; testele offline măsoară cazurile
controlate, nu totalitatea anunțurilor existente pe internet.

Surse oficiale: [Apify run metadata](https://docs.apify.com/api/v2/actor-run-get),
[Railway backups](https://docs.railway.com/volumes/backups),
[Railway pricing](https://docs.railway.com/pricing/plans),
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[GitHub scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Test manual cu interogări noi

`POST /search?fresh=1`, cu antetul X-Search-Token, ocolește numai protecția de
timp pentru ciclul cerut explicit. Nu modifică orele de referință ale cronului.
Plafoanele financiare rămân active; Brave permite maximum16 cereri manuale
suplimentare/zi, separat de cele32 normale, cu plafonul lunar comun neschimbat.
Testele suplimentare consumă credite reale și nu intră în prognoza obișnuită
de62 cicluri/lună. Câmpul `discovery_queries_attempted` și logurile arată ce
motoare au făcut cereri noi; mode=manual_fresh nu implică succesul lor.
Google403 API_PROJECT_NOT_ELIGIBLE indică lipsa accesului proiectului la API,
nu epuizarea creditelor. Nu există o remediere software care să acorde acest
drept proiectului; Google nu a fost dezactivat și nu se ascunde eroarea.
