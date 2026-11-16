# Recuperare Franța/Canada și accesul la căutare

## Probe de producție și regresii găsite la verificare

Release 297c541 a trecut toate cele 33 de suite, CI 43866267789 și activarea Railway. Proprietarul a salvat Serper și a înlocuit tokenul Apify; deploymentul cu aceste variabile a trecut inclusiv verificarea autentificată a ambelor taskuri Apify. Cele trei endpointuri de cont au răspuns 200. Consumul observat este 1,023549 USD din cele 5 USD incluse pentru ciclul 5 noiembrie–4 decembrie; plafonul contului de 15 USD nu reprezintă credit gratuit.

Tura auxiliară de la 12:35 UTC a executat 8 cereri Serper valide: toate cele 4 căutări regionale și primele 4 căutări ale planului Google. A primit 15 rânduri brute, 14 URL-uri distincte; 7 piste au rămas durabile pentru verificarea primară. Nu sunt 14 viori confirmate. Kijiji a executat 13 interogări native FR/EN. Google/Brave/Tavily/Exa au păstrat protecțiile turelor; zero apeluri noi la acestea nu înseamnă o nouă cercetare completă a tuturor motoarelor.

Pilotul nativ Leboncoin a fost refuzat de rezerva financiară înainte de crearea vreunui job/Actor. Nu s-a cheltuit credit pentru pilot și sursa rămâne oprită până la validarea reală. Au fost identificate costuri istorice nereconciliate și rezervări incerte; acestea se investighează prin GET-uri autentificate, fără resetarea sau eliberarea presupusă a costurilor.

Cele 30 de run-ID-uri reale din octombrie fără decontare răspund acum toate 404, deci păstrează rezervarea cumulată de 1,59 USD. Există și 8 POST-uri incerte din noiembrie fără run-ID, cu rezervă de 0,52 USD; nu sunt declarate gratuite sau șterse. Aceste rețineri conservatoare explică de ce soldul observat singur nu permite un pilot auxiliar. Nu a fost ocolită protecția și nu s-a repetat lansarea.

Auditul a găsit o eroare separată în totalul turei 169: Telegram a acceptat alerta Reverb de scădere a prețului, dar `total_sent` nu includea această rută. Contorul adună acum numai scăderile returnate ca livrate de notifier; regresiile acoperă ruta nouă, reluarea din outbox și livrarea refuzată. Mesajul API vorbește despre alerte livrate, fără a prezenta scăderile de preț drept anunțuri nou descoperite. Serper distinge explicit limita auxiliară de 8/zi de limita totală a creditelor și înregistrează rezultatele fiecărei interogări; nicio cotă nu a fost mărită sau resetată.

Aceeași tură a reprodus o schimbare nouă GovDeals/AllSurplus: configurația anonimă este acum în obiectul JSON `__APP_CONFIG__` al paginii, iar main.js are doar valori implicite goale. Parserul suplimentar citește JSON strict, verifică destinația fixă `https://maestro.lqdt1.com` și păstrează ruta JavaScript anterioară. Nu execută scripturi și nu exportă cheia publică. Proba reală a recuperat căutarea și detaliile cu identitate validă pentru ambele business-uri; cele 31 de rânduri ale controlului includ obiecte fără legătură cu viorile. Testele verifică noua schemă, fallbackul vechi, destinații/valori invalide și absența execuției JS. Activarea acestei ultime corecții și rularea completă ulterioară sunt probe separate.

Prețul neobișnuit de 1,73 USD pentru Reverb 104223395 este chiar `price.amount` din API-ul primar, cu `amount_cents=173` și state=live; nu este o eroare de separator monetar. Acest lucru nu dovedește că prețul este intenționat de vânzător sau că oferta este sigură.

Exemplu real: Leboncoin3269447854, „Violon Zeta 5 corde”,3500EUR, publicat14septembrie2026, citibil în Chrome pe16noiembrie. Filtrul exact acceptă titlul/descrierea; ID-ul nu exista în coada/istoricul/livrările actuale. Auditul și dovezile private sunt în outputs/verification-20261116. Nu se injectează linkul drept probă de descoperire.

## Modificări

- Toate sursele, interogările, aliasurile, limbile, regiunile, limitele vechi și programul10/22Bucharest rămân. Prioritățile nu devin filtre exclusive. Yahoo rămâne exclus explicit.
- Kijiji păstrează toate formulările franceze și adaugă cele englezești distincte, fără dublarea termenilor identici; fiecare apel are jurnal de interogare/categorie/țară.
- Serper este un furnizor opțional separat. Patru căutări explicite FR/CA în fiecare tură eligibilă: Leboncoin; Zikinf/Audiofanzine; Kijiji/eBayCA/CraigslistCanada înfranceză și engleză. Căutările largi includ marca, familiile de modele și Strados, fără prag de relevanță ori limită de vechime. Cele trei orașe Craigslist din supliment NU înlocuiesc colectorul canadian existent.
- Când ultima încercare reală Google dovedește API_PROJECT_NOT_ELIGIBLE sau lipsesc configurările Google, Serper poate executa suplimentar întregul plan Google existent48, cu un cursor separat. Nu mută cursorul Google/Brave și nu împrumută cotele celorlalți furnizori. Răspunsul trebuie să confirme interogarea și schema organică; cache, refuzul și răspunsul invalid nu sunt căutări sănătoase.
- Tokenul Apify actual a fost refuzat401 laGETuri autentificate, fără o problemă de spații/ghilimele. Noua protecție nu mai lansează Actori după un refuz401 confirmat; păstrează livrarea cache-ului verificat, joburile, costurile și cursorii. Înlocuirea tokenului invalidează numai refuzul vechii credențiale. Un403 de cont poate însemna token cu permisiuni limitate, deci nu dezactivează automat Actorii.
- Colectorul opțional Leboncoin folosește Actorul fix automation_craft/leboncoin-scraper/q5xh9KX4Wbn9i9cPA, cu schemă examinată. Implicit esteoprit, în așteptarea pilotului protejat. Intrarea păstrează8termeni francezi; fără localitate impusă, filtre de vechime, title-only, monitor extern sau tokenuriTelegram. Include descriere și detalii primare. Inputul și run-ID sunt persistate; POST incert nu se reia automat. Fiecare fază citește numai joburile sale.
- Observațiile native sunt legate de URL/ID și de timpul real al sursei/runului. Recitirea unui run vechi nu reîmprospătează stocul și nu se numără drept căutare nouă. O observație mai veche nu înlocuiește una mai nouă. Sold/rezervat/necunoscut sunt distincte; absența într-o listă ori un eșec de acces nu dovedește vânzarea. Numai starea active cu detaliul individual citit poate confirma stocul; maximum6h pentru pozitiv.

## Resurse și activare

SERPER_API_KEY se introduce direct în Railway. Alocația locală implicită2500 este TOTALĂ, nu lunară. Nu se cumpără credite automat. Rezervarea este înainte dePOST; chiar și timeouturile păstrează costul incert. Cu4suplimente+48backup, două ture/zi înseamnă104apeluri/zi, maximum3224în31zile. Trialul2500 acoperă24zile complete la această încărcare, fără a include alte utilizări; nu este o soluție gratuită nelimitată. Planul comercial public are pachet minim50USD,50000credite/6luni; nu a fost cumpărat. Cotele vechi Brave/Tavily/Exa nu cresc. Avertismentele de80/95/100% includ Serper.

Pentru Leboncoin: maximum0.03USD/run,256MB,180s și10anunțuri; limita proprie implicită2USD/lună permite plafonul62×0.03=1.86USD. Aceasta NU mărește bugetul comun Apify5USD și NU dovedește credit disponibil. Rezerva protejează întotdeauna căutările Facebook și limita contului; dacă nu există marjă, noua lansare este amânată explicit. Costul real al fiecărui run terminal se reconciliază, iar capsumele incerte rămân. Este necesar un pilot real pentru schema/costul/accesul actual și, dacă marja nu ajunge, o decizie separată despre credite.

APIFY_TOKEN trebuie înlocuit de proprietar direct în Railway; nu se exportă în chat sau în fișiere. Modificările de variabile în așteptare se revizuiesc înainte de activare. Nu se schimbă politici de cumpărare/reîncărcare ori tokenuri ale altor servicii.

## Verificare

Sunt33suite offline obligatorii. Testele noi includ oferta reală franceză, planul Google păstrat integral, FR/CA și limbile, rezervări concurente/lifetime, quota/time-out fără reîncercări ascunse, token schimbat fără reset, cacheFacebook păstrat, identitate/Actor greșit, stoc vechi și ordinea observațiilor. Verificarea Apify --check continuă să se execute. Un401 cunoscut are exit10 și este raportat explicit ca blocaj extern, nu drept succes; numai acest rezultat permite deploy-ul reparațiilor independente. Teste eșuate și alte erori de verificare rămân blocante.

Acceptarea finală cere CI/predeploy, SHAefectiv Railway, probe HTTP/auth/SQLite/backup, o tură normală autorizată observată până la final și dovezi de apeluri efective separate de cache/cozi. Serviciile care așteaptă credențiale sau pilot nu sunt declarate recuperate. Goal-ul rămâne activ; raportul inițial este un audit, nu finalizarea reparației.
