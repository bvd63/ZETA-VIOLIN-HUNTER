# Cele cinci puncte autorizate

Cerere explicită a proprietarului după release92e40f2: implementarea tuturor celor patru puncte propuse și un punct5 pentru Facebook Marketplace, cu accent pe SUA și Canada, plus UK, Franța, Germania și Spania.

1. Integritatea traseului descoperire → identitate → disponibilitate → outbox → Telegram/Pushover → confirmare. Regresii pentru omisiunile și alertele greșite demonstrate; SOLD legat de ID și FAKE pe oferta exactă rămân prioritare.
2. Consolidarea surselor relevante: Reverb, Facebook, Leboncoin, eBay; recuperarea cererii Craigslist refuzate și a detaliilor licitațiilor. Accesul extern nedovedit rămâne limită explicită.
3. Verificare suplimentară Reverb la cel mult60minute între ture, cu ruta oficială make=Zeta, aceleași filtre/outbox/dedup, limite persistente separate și prioritate pentru cronul10/22. Nu relansează toate motoarele/Actorii și nu schimbă indicatorul ultimului cron.
4. Acoperire măsurabilă: plan/încercări/răspunsuri validate/refuzuri pe regiuni, vechimea lucrului amânat și costuri/cote separate de solduri. Nu se folosește brutul ca dovadă de inventar.
5. Descoperire Facebook suplimentară în US/CA/GB/FR/DE/ES, termeni locali și model/4/5strings, cursori durabili, ponderare US/CA. Cele două căutări Apify originale și toate căutările web existente rămân. Extinderile nu împrumută creditul rezervat turelor originale; o rută pregătită dar blocată de credit nu este declarată acces verificat.

Condiții: toate sursele/aliasurile/limbile/regiunile/capurile originale rămân; Yahoo exclus. NativeLeboncoin rămâne byte-identic. Fără cumpărături, export chei/cookies, reset cote/cursori sau fresh=1. Teste relevante + toate suitele, CI, deploy efectiv și probă normală verificată. Nu sunt garantate100%inventar sau zero refuzuri externe. Goalul general anterior nu este declarat complet înaintea probelor rămase.

Baseline verificat: main92e40f2, Railway2107ab77SUCCESS, reader603e0a6a, full181/core180. Stare inițială:39suite; Gov/AllSurplus204, DDG202 și un CraigslistLewiston403 rămân. Extensia este în lucru; acceptarea se completează după execuție, nu anticipat.

## Implementare și probe înainte de publicare

Cele16regresii noi și toate40suitele au trecut; revizuireindependentăPASS. Contractele nativeLeboncoin/keywords/Brave/Google/Serper/Tavily/Exa/readerUS sunt byte-identice baseline92. Craigslist: exactLewiston654/msa a primit200valid/2raw din container; nu dovedește acces permanent la toate zonele. Gov204persistăpeunlot cucontrolvalidseparat; DDGLite202challengeconfirmat. Nicio alternativă refuzată nu este declarată reparată. Preamplificatorul,closed,FAKEșiack/remindersrămân acoperite de suitele existente.

Serperregionalindex:2interogări noi/core;4/zi cu pondereUS/CA, plus maximum2aux/zi doar peste rezerva completă a turelor vechi+noi.2500crediteunice, fărăreset/reînnoirepretinsă. Actorregional:maximum4rânduri/.027USD/run, maximum1.7USD/calendar ÎN plafonul5USDexistent; nu crește contul saucapurileoriginale. Ultimulcontextactual are numai0USDheadroomauxverificat, deși creditulinclusrămas3.9764acoperămaximum3.06alcelor36turevechi;2.1224deincertitudinireținuteexplicăprotecțiaconservatoare. Nu ceremcumpărături care nu ar activa singure guardulcredituluiinclus. ProbeleviitoaretrebuiasăsepareindexulpublicdeMarketplaceproaspăt.
