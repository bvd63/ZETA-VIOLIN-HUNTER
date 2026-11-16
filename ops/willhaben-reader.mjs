// Private, anonymous Willhaben reader; no arbitrary destination or credentials.
const VERSION = 'willhaben-reader-v2';
const HOST = 'https://www.willhaben.at';
const MAX_INPUT = 4096;
const MAX_BODY = 2 * 1024 * 1024;
// This RAM cap is only a secondary guard. The main app's durable ledger
// protects 40 slots for core native searches; core primary details use the
// auxiliary pool, never those protected search slots.
const DAILY_CAP = 200;

export function target(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
  const keys = Object.keys(payload).sort().join(',');
  if (keys === 'category,keyword,kind' && payload.kind === 'search' &&
      typeof payload.keyword === 'string' && payload.keyword.trim() && payload.keyword.length <= 400 &&
      (payload.category === 'all' || payload.category === 'music')) {
    const path = '/iad/kaufen-und-verkaufen/marktplatz' +
      (payload.category === 'music' ? '/musikinstrumente-noten-6540' : '');
    const url = new URL(path, HOST);
    url.searchParams.set('keyword', payload.keyword);
    return url.href;
  }
  if (keys === 'kind,path' && payload.kind === 'item' && typeof payload.path === 'string') {
    const match = /^\/iad\/kaufen-und-verkaufen\/d\/([^/]+)\/?$/.exec(payload.path);
    if (!match) return null;
    let slug;
    try { slug = decodeURIComponent(match[1]); } catch { return null; }
    // Decode exactly once before validating: escaped slash, backslash, query,
    // control, double encoding and traversal cannot become path components.
    if (!/^[\p{L}\p{N}][\p{L}\p{N}\p{M}_-]*-[0-9]+$/u.test(slug)) return null;
    return new URL(payload.path, HOST).href;
  }
  return null;
}

// Separate protocol and allowance: Gumtree must not consume Willhaben's slots.
export function gumtreeTarget(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
  const keys = Object.keys(payload).sort().join(',');
  if (keys === 'keyword,kind' && payload.kind === 'search' &&
      typeof payload.keyword === 'string' && payload.keyword.trim() &&
      payload.keyword.length <= 400) {
    const url = new URL('/search', 'https://www.gumtree.com');
    url.searchParams.set('search_category', 'all');
    url.searchParams.set('q', payload.keyword);
    return url.href;
  }
  if (keys !== 'kind,path' || payload.kind !== 'item' || typeof payload.path !== 'string') return null;
  if (!/^\/p\/(?:[^/]+\/)+[0-9]+\/?$/.test(payload.path)) return null;
  const segments = payload.path.split('/').filter(Boolean);
  try {
    for (const encoded of segments) {
      const segment = decodeURIComponent(encoded);
      if (!/^[\p{L}\p{N}][\p{L}\p{N}\p{M}_-]*$/u.test(segment)) return null;
    }
  } catch { return null; }
  return new URL(payload.path, 'https://www.gumtree.com').href;
}

export function leboncoinTarget(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) ||
      Object.keys(payload).sort().join(',') !== 'keyword,kind,offset' || payload.kind !== 'search' ||
      typeof payload.keyword !== 'string' || !payload.keyword.trim() || payload.keyword.length > 400 ||
      !Number.isSafeInteger(payload.offset) || payload.offset < 0 || payload.offset > 2147483647) return null;
  return 'https://api.leboncoin.fr/finder/search';
}

async function readBounded(stream, limit, signal) {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks = [];
  let size = 0;
  const cancel = () => { reader.cancel().catch(() => {}); };
  signal.addEventListener('abort', cancel, { once: true });
  try {
    for (;;) {
      if (signal.aborted) throw new Error('timeout');
      const {done, value} = await reader.read();
      if (signal.aborted) throw new Error('timeout');
      if (done) break;
      size += value.byteLength;
      if (size > limit) { cancel(); throw new Error('too large'); }
      chunks.push(value);
    }
  } finally {
    signal.removeEventListener('abort', cancel);
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}

export function createHandler({upstreamFetch = globalThis.fetch, region = 'unknown',
    now = () => Date.now() / 1000, timeoutMs = 12000, log = console.log} = {}) {
  let active = 0, day = null, count = 0, gumtreeCount = 0, leboncoinCount = 0, leboncoinAgent;
  const json = (value, status) => Response.json(value, {status, headers: {'cache-control':'no-store'}});
  return async function handle(request) {
    const path = new URL(request.url).pathname;
    if (path === '/health') {
      if (request.method !== 'GET') return json({error:'method'},405);
      return json({version:VERSION, region},200);
    }
    if (path === '/health-gumtree') {
      if (request.method !== 'GET') return json({error:'method'},405);
      return json({version:'gumtree-reader-v1', region},200);
    }
    const gumtree = path === '/read-gumtree';
    const leboncoin = path === '/read-leboncoin';
    if (path !== '/read' && !gumtree && !leboncoin) return json({error:'not found'},404);
    if (request.method !== 'POST') return json({error:'method'},405);
    const protocol = request.headers.get(leboncoin ? 'x-leboncoin-reader-version' : gumtree ? 'x-gumtree-reader-version' : 'x-willhaben-reader-version');
    if (gumtree || leboncoin ? protocol !== '1' : protocol !== null && protocol !== '2') return json({error:'version'},400);
    if (active >= 2) return json({error:'busy'},429);
    active++;
    const abort = new AbortController();
    let timer;
    const timedOut = new Promise((_, reject) => { timer = setTimeout(() => {
      abort.abort(); reject(new Error('timeout'));
    }, timeoutMs); });
    try {
      const work = async () => {
        if (Number(request.headers.get('content-length') || 0) > MAX_INPUT) return json({error:'input too large'},413);
        let payload;
        try {
          const bytes = await readBounded(request.body, MAX_INPUT, abort.signal);
          payload = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(bytes));
        } catch (err) {
          if (abort.signal.aborted) throw err;
          return json({error:'invalid input'},400);
        }
        const url = leboncoin ? leboncoinTarget(payload) : gumtree ? gumtreeTarget(payload) : target(payload);
        if (!url) return json({error:'invalid input'},400);
        const currentDay = Math.floor(now() / 86400);
        if (currentDay !== day) { day = currentDay; count = 0; gumtreeCount = 0; leboncoinCount = 0; }
        if ((leboncoin ? leboncoinCount : gumtree ? gumtreeCount : count) >= DAILY_CAP) return json({error:'daily cap'},429);
        if (leboncoin) leboncoinCount++; else if (gumtree) gumtreeCount++; else count++;
        const options = {method:'GET', redirect:'manual', signal:abort.signal,
          headers:{'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                   'Accept':'text/html,application/xhtml+xml',
                   'Accept-Language':gumtree ? 'en-GB,en;q=0.9' : 'de-AT,de;q=0.9,en;q=0.8'}};
        if (leboncoin) {
          // One anonymous app session for this process, never rotate per query.
          leboncoinAgent ||= `LBC;iOS;26.2;iPhone;phone;${crypto.randomUUID()};wifi;101.44.0`;
          options.method = 'POST';
          options.headers = {'Content-Type':'application/json',
            'User-Agent':leboncoinAgent,
            'Sec-Fetch-Dest':'empty', 'Sec-Fetch-Mode':'cors', 'Sec-Fetch-Site':'same-site'};
          options.body = JSON.stringify({filters:{category:{id:'30'},enums:{ad_type:['offer']},
              keywords:{text:payload.keyword},location:{}},limit:35,limit_alu:0,offset:payload.offset,
              disable_total:false,extend:true,listing_source:'direct-search',sort_by:'time',sort_order:'desc'});
        }
        const response = await upstreamFetch(url, options);
        const bytes = await readBounded(response.body, MAX_BODY, abort.signal);
        const body = new TextDecoder().decode(bytes);
        let location = null;
        const rawLocation = response.headers.get('location');
        if (rawLocation) {
          try { location = new URL(rawLocation, response.url || url).href; } catch { location = null; }
        }
        const result = {version:leboncoin ? 'leboncoin-reader-v1' : gumtree ? 'gumtree-reader-v1' : protocol === '2' ? VERSION : 'willhaben-reader-v1',
                        requested:url, url:response.url, status:response.status,
                        body, checked:Math.floor(now()), region};
        if (leboncoin || gumtree || protocol === '2') result.location = location;
        if (leboncoin) result.request = payload;
        log(`${leboncoin ? 'leboncoin' : gumtree ? 'gumtree' : 'willhaben'} ${payload.kind} HTTP ${response.status} bytes ${bytes.length}`);
        return json(result,200);
      };
      return await Promise.race([work(), timedOut]);
    } catch (err) {
      return json({error:abort.signal.aborted ? 'timeout' : 'upstream unavailable'},abort.signal.aborted ? 504 : 502);
    } finally {
      clearTimeout(timer);
      active--;
    }
  };
}

if (typeof Bun !== 'undefined') {
  Bun.serve({hostname:'::', port: Number(process.env.PORT || 8080),
             fetch:createHandler({region:process.env.RAILWAY_REPLICA_REGION || 'unknown'})});
}
