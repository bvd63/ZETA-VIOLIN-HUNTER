# Comparație offline a Actorilor Facebook Marketplace

`apify_compare.py` pregătește un input sau compară două fișiere JSON existente. Nu lansează Actori, nu accesează API-uri, baze de date, notificări, cookie-uri sau servicii plătite. Nu este o probă live; pilotul rămâne condiționat de gardurile de credit existente și de autorizarea aplicabilă. Contractele salvate în `tests/fixtures/apify_actor_input_contracts_2026_10_27.json` sunt un instantaneu public, nu o garanție a comportamentului curent.

## Plan offline

```sh
python apify_compare.py --plan curious --url 'https://www.facebook.com/marketplace/chicago/search/?query=zeta+violin&exact=false&radius=500' --limit 5 --max-pages 1
```

Actorii acceptați sunt `official`, `curious`, `black`, `memo`. Se acceptă exact un URL public de căutare Marketplace; sunt respinse alte gazde, parametri arbitrar sau de vechime, credențiale și filtrarea exactă activată. `--limit` este 1–60; Memo FREE permite un URL și cel mult 60 rezultate. Fiecare input dezactivează explicit filtrele stricte/incrementale și limitele de vechime acolo unde schema le expune, nu adaugă webhook, token, cookie sau altă destinație de notificare. `includeSeller` la Memo cere pagina de detalii și poate genera cost suplimentar; nu implică folosirea unui cont de vânzător.

**Pentru Official și Curious, disponibilitatea este `provider_default`: schema nu demonstrează că toate stările sunt incluse. Black solicită `all` prin `availableOnly=false`, iar Memo are implicit `in_stock`. Curious limitează pagini, nu rezultate:** `--max-pages` este 1–5, iar `--limit` nu este un plafon de rezultate pentru acest Actor. Volumul, prețul și completitudinea pot diferi astfel de celelalte planuri. Schema Memo are implicit disponibilitatea „in stock” și nu oferă în acest câmp o opțiune „all”; nu pretindem că stările sau raza sunt comparabile cu celelalte implementări. Schema publică salvată nu definește tipurile elementelor din array-uri; testul verifică tipurile și câmpurile de nivel superior și formele descrise de contract, nu dovedește executarea payloadului. Nu există garanția că un start URL sau o pagină de detalii este accesibilă din Railway.

## Comparație locală

```sh
python apify_compare.py --compare baseline.json candidate.json
```

Fiecare fișier are forma:

```json
{
  "context": {
    "actor": "official",
    "collection": {"details": true, "bound_kind": "results", "bound": 20, "availability_scope": "provider_default"},
    "query_urls": ["https://www.facebook.com/marketplace/chicago/search/?query=zeta+violin&exact=false&radius=500"],
    "status": "SUCCEEDED",
    "coverage_complete": true,
    "started_at": "2026-10-27T10:00:00Z",
    "actual_cost_usd": 0.0123
  },
  "items": []
}
```

`actor` și `collection` trebuie furnizate pentru comparabilitate; numele Actorului trebuie să fie cunoscut. `collection` declară dacă detaliile au fost cerute, limita în rezultate sau pagini și disponibilitatea solicitată. Planul afișează configurația recomandată; contextul este furnizat de utilizator și nu confirmă ce a executat providerul. `coverage_complete` trebuie să fie explicit `true` pentru comparație și afirmă doar completitudinea în scopul și limitele declarate, nu recall global. Este o declarație a utilizatorului, nu dovadă obținută din API. `actual_cost_usd` poate lipsi și reprezintă costul măsurat furnizat pentru întreaga rulare, nu o estimare din numărul de rânduri; lipsa este `null`, nu zero. Timestampurile au fus orar obligatoriu. Raportul este comparabil numai dacă ambii Actori sunt cunoscuți, configurațiile de colectare declarate coincid și sunt compatibile cu schema Actorului, ambele rulări sunt `SUCCEEDED`, completitudinea declarată este explicit `true`, ambele costuri reale sunt cunoscute, căutările validate coincid, începuturile sunt la maximum o oră și nu există rânduri de eroare sau invalide. Dacă numărul de rânduri atinge limita de rezultate, răspunsul este tratat conservator ca potențial trunchiat chiar dacă este declarat complet. Dacă lipsește contextul sau limitele, tipul limitei, detaliile ori disponibilitatea diferă, setul suplimentar este `null`; listele per rulare rămân vizibile. Statusul furnizat și timestampurile sunt declarații ale datelor de intrare, nu verificate prin API. Comparabilitatea nu demonstrează aceeași acoperire, volum sau locație efectivă; diferențele de paginare, limită și provider pot schimba rezultatele.

Rândurile trec prin normalizatorul și filtrul comun și prin `available`: sunt **eligibile în outputul Actorului**, nu viori sau oferte active confirmate independent. Setul suplimentar este calculat numai din URL-urile eligibile unice și numai când comparația este comparabilă. Raportul separă numărul de erori, rânduri invalide, duplicate, descrieri lipsă, stări indisponibile/necunoscute și rezultate respinse. Un articol cu descriere lipsă poate fi totuși eligibil pe baza câmpurilor sale; numărul separat avertizează asupra acestei limite. Repetițiile nu devin instrumente suplimentare.

Nu se afișează nume de vânzători, contacte, descrieri brute sau parametri ai URL-urilor de item. URL-urile eligibile sunt canonicalizate la ID Facebook. Căutările sunt reprezentate prin amprente SHA-256, nu prin parametri raw; ele sunt identificatori de comparație, nu dovezi că o rulare a folosit efectiv acel URL. Fiecare fișier de intrare este limitat la 10 MiB înainte de parsare; limita se aplică numai acestui instrument offline. Fișierele locale de intrare pot conține date sensibile și rămân în responsabilitatea utilizatorului; raportul nu copiază acele câmpuri.

Validare offline: `python -m unittest tests.test_apify_compare`. Niciun Actor nu a fost rulat și nu au fost cumpărate credite.
