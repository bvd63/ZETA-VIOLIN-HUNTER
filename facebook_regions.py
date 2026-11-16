"""Additive regional discovery. The historical US plans remain byte-for-byte.

An index query is not a fresh Marketplace crawl. Returned locations may differ
from the requested radius; neither a URL nor Actor success proves coverage.
"""
from urllib.parse import urlencode
from keywords import MODEL_CODES, STRING_VARIANTS, PREFERRED_QUERIES

WEIGHTS = ('US','CA','US','GB','CA','FR','US','DE','CA','ES','US','CA')
REGIONS = {
    'US': ('en', ('losangeles','newyork','chicago','sanfrancisco','nashville','austin','seattle','boston',
                  'miami','denver','phoenix','atlanta','detroit','houston','portland','sandiego')),
    'CA': ('en', ('toronto','vancouver','montreal','ottawa','calgary','edmonton','winnipeg','quebec')),
    'GB': ('en', ('london','manchester','birmingham','bristol','glasgow','edinburgh')),
    'FR': ('fr', ('paris','lyon','marseille','toulouse','bordeaux','lille')),
    'DE': ('de', ('berlin','hamburg','munich','cologne','frankfurt','stuttgart')),
    'ES': ('es', ('madrid','barcelona','valencia','sevilla','bilbao','malaga')),
}
WORDS = {'en':'violin fiddle','fr':'violon violon électrique','de':'Geige Violine elektrische Geige',
         'es':'violín violin eléctrico'}


def regional_entry(cursor):
    country = WEIGHTS[cursor % len(WEIGHTS)]
    visit = (cursor//len(WEIGHTS))*WEIGHTS.count(country) + WEIGHTS[:cursor%len(WEIGHTS)].count(country)
    language,cities = REGIONS[country]
    terms = ('zeta','zetta','zeta jazz fusion','zeta jean luc ponty','zeta JLP','zeta sunburst') + tuple(
        'zeta '+code for code in MODEL_CODES) + tuple('zeta '+v for v in STRING_VARIANTS) + tuple(PREFERRED_QUERIES)
    term = terms[visit % len(terms)]
    # Mixed-radix traversal covers EVERY term/city pair, even when their
    # lengths share a divisor. Simple synchronized modulo can omit aliases.
    city = cities[(visit+visit//len(terms)) % len(cities)]
    if country=='CA' and city in ('montreal','quebec'):
        language = 'fr'
    signals = '(Zeta OR Zetta OR Strados OR "Jazz Fusion" OR "Jean Luc Ponty" OR ' + ' OR '.join(MODEL_CODES) + ')'
    return {'country':country,'language':language,'city':city,'term':term,
            # Identity/model query has NO mandatory word for violin. A violin
            # with a sparse/foreign title must remain discoverable.
            'query':f'site:facebook.com/marketplace/item/ {signals} {city} {country}',
            'url':'https://www.facebook.com/marketplace/'+city+'/search/?'+urlencode(
                {'query':term,'exact':'false','radius':500})}


def regional_plan(cursor, count=2):
    return [regional_entry(cursor+i) for i in range(count)]
