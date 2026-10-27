// Private, anonymous Willhaben reader; no arbitrary destination or credentials.
const VERSION = 'willhaben-reader-v1';
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
  let active = 0, day = null, count = 0;
  const json = (value, status) => Response.json(value, {status, headers: {'cache-control':'no-store'}});
  return async function handle(request) {
    const path = new URL(request.url).pathname;
    if (path === '/health') {
      if (request.method !== 'GET') return json({error:'method'},405);
      return json({version:VERSION, region},200);
    }
    if (path !== '/read') return json({error:'not found'},404);
    if (request.method !== 'POST') return json({error:'method'},405);
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
        const url = target(payload);
        if (!url) return json({error:'invalid input'},400);
        const currentDay = Math.floor(now() / 86400);
        if (currentDay !== day) { day = currentDay; count = 0; }
        if (count >= DAILY_CAP) return json({error:'daily cap'},429);
        count++;
        const response = await upstreamFetch(url, {method:'GET', redirect:'manual', signal:abort.signal,
          headers:{'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                   'Accept':'text/html,application/xhtml+xml', 'Accept-Language':'de-AT,de;q=0.9,en;q=0.8'}});
        const bytes = await readBounded(response.body, MAX_BODY, abort.signal);
        const body = new TextDecoder().decode(bytes);
        const result = {version:VERSION, requested:url, url:response.url, status:response.status,
                        body, checked:Math.floor(now()), region};
        log(`willhaben ${payload.kind} HTTP ${response.status} bytes ${bytes.length}`);
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
