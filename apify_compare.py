"""Offline Apify Marketplace comparison. No network, actor launches or database writes."""
import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from filters import classify
from scrapers.apify_facebook import available, normalize

ACTORS = {
    'official': 'apify/facebook-marketplace-scraper',
    'curious': 'curious_coder/facebook-marketplace',
    'black': 'blackfalcondata/facebook-marketplace-scraper',
    'memo': 'memo23/facebook-marketplace-scraper-ppe',
}


def search_url(value):
    """Accept only a public Marketplace search with a small, nonsecret query allowlist."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('invalid Marketplace search URL')
    try:
        u = urlsplit(value)
        if (u.scheme != 'https' or u.hostname not in ('www.facebook.com', 'facebook.com')
                or u.username or u.password or u.port not in (None, 443) or u.fragment
                or not re.fullmatch(r'/marketplace/[A-Za-z0-9_-]+/search/?', u.path)):
            raise ValueError
        params = parse_qsl(u.query, keep_blank_values=True, strict_parsing=True)
        allowed = {'query', 'exact', 'radius', 'sortBy'}
        if (not params or any(k not in allowed for k, _ in params)
                or len({k for k, _ in params}) != len(params)
                or not any(k == 'query' and v.strip() for k, v in params)
                or any(len(v) > 200 or any(ord(c) < 32 for c in v) for _, v in params)):
            raise ValueError
        values = dict(params)
        term = values['query']
        if (len(term) > 100 or not re.fullmatch(r'[\w\s\-.,"|]+', term, re.UNICODE)
                or re.search(r'\b(?:token|password|secret|cookie|authorization|api[_ -]?key)\b', term, re.I)
                or values.get('exact', 'false').lower() != 'false'
                or ('radius' in values and (not values['radius'].isdigit() or int(values['radius']) > 500))
                or ('sortBy' in values and values['sortBy'] not in ('creation_time_descend','price_ascend','price_descend','distance_ascend'))):
            raise ValueError
        # Do not carry user-supplied opaque query parameters or credentials.
        return urlunsplit(('https', 'www.facebook.com', u.path.rstrip('/') + '/', urlencode(sorted(params)), ''))
    except (ValueError, TypeError):
        raise ValueError('invalid Marketplace search URL') from None


def plan(actor, url, limit=5, max_pages=1):
    if actor not in ACTORS or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 60:
        raise ValueError('actor or result limit invalid (1–60)')
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 5:
        raise ValueError('page limit invalid (1–5)')
    url = search_url(url)
    if actor == 'official':
        payload = {'startUrls': [{'url': url}], 'resultsLimit': limit, 'includeListingDetails': True}
    elif actor == 'curious':
        payload = {'urls': [url], 'getListingDetails': True, 'getAllListingPhotos': False,
                   'strictFiltering': False, 'onlyNewListings': False, 'maxPagesPerUrl': max_pages}
    elif actor == 'black':
        payload = {'startUrls': [url], 'maxResults': limit, 'strictRadius': False,
                   'strictQueryMatch': False, 'availableOnly': False, 'includeDetails': True,
                   'descriptionMaxLength': 0, 'incrementalMode': False}
    else:
        payload = {'startUrls': [url], 'maxItems': limit, 'daysSinceListed': '0',
                   'maxListingAgeHours': 0, 'exact': False, 'monitoringMode': False,
                   'includeSeller': True, 'locationResultsMode': 'maximize_results'}
    note = ('maxPagesPerUrl limits pages, not results; result count may exceed requested limit.'
            if actor == 'curious' else 'One search URL; result cap requested. Provider behavior is unverified.')
    collection = {'details': True, 'bound_kind': 'pages' if actor == 'curious' else 'results',
                  'bound': max_pages if actor == 'curious' else limit,
                  'availability_scope': ('in_stock' if actor == 'memo' else 'all' if actor == 'black' else 'provider_default')}
    return {'actor': ACTORS[actor], 'input': payload, 'collection': collection, 'note': note,
            'execution': 'not executed; no actor, API, or notification calls'}


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError,OverflowError):
        return None


def summarize(document):
    if not isinstance(document, dict) or not isinstance(document.get('items'), list):
        raise ValueError('input must contain an items array and context object')
    context = document.get('context') if isinstance(document.get('context'), dict) else {}
    raw_urls = context.get('query_urls')
    urls = None
    if isinstance(raw_urls, list) and raw_urls:
        try:
            urls = sorted(set(search_url(u) for u in raw_urls))
        except ValueError:
            urls = None
    actor = context.get('actor')
    actor = ACTORS.get(actor, actor) if isinstance(actor, str) else None
    if actor not in ACTORS.values():
        actor = None
    raw_collection = context.get('collection')
    collection = None
    if isinstance(raw_collection, dict):
        details = raw_collection.get('details')
        kind = raw_collection.get('bound_kind')
        bound = raw_collection.get('bound')
        scope = raw_collection.get('availability_scope')
        if (isinstance(details, bool) and kind in ('results', 'pages')
                and isinstance(bound, int) and not isinstance(bound, bool) and 1 <= bound <= 10000
                and scope in ('all', 'in_stock', 'provider_default')):
            collection = {'details':details, 'bound_kind':kind, 'bound':bound, 'availability_scope':scope}
    coverage = context.get('coverage_complete')
    coverage = coverage if isinstance(coverage, bool) else None
    started = _timestamp(context.get('started_at'))
    status = context.get('status')
    status = status if status in ('SUCCEEDED','FAILED','TIMED-OUT','ABORTED','RUNNING') else None
    cost = context.get('actual_cost_usd')
    try:
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            cost = None
    except OverflowError:
        cost = None
    counts = {'rows':len(document['items']), 'errors':0, 'invalid_rows':0, 'duplicates':0,
              'missing_description':0, 'not_available_or_unknown':0, 'not_eligible':0}
    eligible, seen = set(), set()
    for row in document['items']:
        if not isinstance(row, dict):
            counts['invalid_rows'] += 1
            continue
        if row.get('error'):
            counts['errors'] += 1
            continue
        try:
            item = normalize(row)
            if not item:
                counts['invalid_rows'] += 1
                continue
            url = item['url']
            if url in seen:
                counts['duplicates'] += 1
            seen.add(url)
            if not str(item.get('description') or '').strip():
                counts['missing_description'] += 1
            if not available(item):
                counts['not_available_or_unknown'] += 1
                continue
            if classify(item):
                counts['not_eligible'] += 1
                continue
            eligible.add(url)
        except (TypeError, ValueError, KeyError, AttributeError):
            counts['invalid_rows'] += 1
    fingerprints = [hashlib.sha256(u.encode()).hexdigest() for u in urls] if urls else None
    return {'context': {'actor':actor, 'collection':collection, 'query_fingerprints_sha256':fingerprints, 'status':status,
                        'started_at': started.isoformat() if started else None,
                        'actual_cost_usd':cost, 'coverage_complete':coverage}, 'counts':counts,
            'actor_output_eligible_urls':sorted(eligible)}


def compare(baseline, candidate):
    a, b = summarize(baseline), summarize(candidate)
    ac, bc = a['context'], b['context']
    reasons = []
    if not ac['actor'] or not bc['actor']:
        reasons.append('missing or unknown actor')
    if not ac['collection'] or not bc['collection']:
        reasons.append('missing or invalid collection context')
    elif ac['collection'] != bc['collection']:
        reasons.append('different collection context')
    for context in (ac, bc):
        collection = context['collection']
        actor = context['actor']
        if collection and actor:
            if ((actor == ACTORS['curious'] and collection['bound_kind'] != 'pages')
                    or (actor != ACTORS['curious'] and collection['bound_kind'] != 'results')
                    or (actor == ACTORS['memo'] and (collection['availability_scope'] != 'in_stock' or collection['bound'] > 60))
                    or (actor in (ACTORS['official'], ACTORS['curious']) and collection['availability_scope'] != 'provider_default')):
                reasons.append('collection context inconsistent with actor contract')
                break
    if not ac['query_fingerprints_sha256'] or not bc['query_fingerprints_sha256']:
        reasons.append('missing or invalid query URLs')
    elif ac['query_fingerprints_sha256'] != bc['query_fingerprints_sha256']:
        reasons.append('different query URLs')
    if not ac['started_at'] or not bc['started_at']:
        reasons.append('missing or invalid start time')
    elif abs((_timestamp(ac['started_at']) - _timestamp(bc['started_at'])).total_seconds()) > 3600:
        reasons.append('start times more than one hour apart')
    if ac['status'] != 'SUCCEEDED' or bc['status'] != 'SUCCEEDED':
        reasons.append('both runs must be SUCCEEDED')
    if a['counts']['errors'] or b['counts']['errors']:
        reasons.append('actor output contains error rows')
    if a['counts']['invalid_rows'] or b['counts']['invalid_rows']:
        reasons.append('actor output contains invalid rows')
    if ac['actual_cost_usd'] is None or bc['actual_cost_usd'] is None:
        reasons.append('actual cost missing or invalid')
    if ac['coverage_complete'] is not True or bc['coverage_complete'] is not True:
        reasons.append('coverage completeness not explicitly confirmed')
    if any(c['collection'] and c['collection']['bound_kind'] == 'results'
           and summary['counts']['rows'] >= c['collection']['bound']
           for c, summary in ((ac,a),(bc,b))):
        reasons.append('result limit reached; output may be truncated')
    additional = sorted(set(b['actor_output_eligible_urls']) - set(a['actor_output_eligible_urls'])) if not reasons else None
    return {'comparable':not reasons, 'incomparable_reasons':reasons,
            'baseline':a, 'candidate':b, 'additional_actor_output_eligible_urls':additional,
            'note':'Actor-output eligibility only; these are not independently confirmed violins or live offers.'}


def read_document(path):
    # Read no more than the maximum plus one byte even if the file changes.
    with Path(path).open('rb') as stream:
        data = stream.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError('input exceeds limit')
    return json.loads(data.decode('utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--plan', choices=ACTORS)
    group.add_argument('--compare', nargs=2, metavar=('BASELINE_JSON','CANDIDATE_JSON'))
    parser.add_argument('--url', help='one public Facebook Marketplace search URL')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--max-pages', type=int, default=1)
    args = parser.parse_args()
    try:
        if args.plan:
            if not args.url:
                raise ValueError('one search URL is required')
            result = plan(args.plan,args.url,args.limit,args.max_pages)
        else:
            if args.url:
                raise ValueError('--url is for --plan only')
            docs = [read_document(path) for path in args.compare]
            result = compare(*docs)
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError, OSError, TypeError, UnicodeError):
        parser.exit(2, 'Invalid input; verify the offline JSON and public search URL.\n')


if __name__ == '__main__': main()
