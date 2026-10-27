"""Offline Node contract tests for the private Willhaben reader (no server)."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.find_spec('playwright')
NODE = shutil.which('node') or (str(Path(spec.origin).parent / 'driver' / 'node') if spec and spec.origin else 'node')
MODULE = (ROOT / 'ops/willhaben-reader.mjs').as_uri()


def run_js(source):
    prelude = f"import assert from 'node:assert/strict';\nimport {{target,createHandler}} from {MODULE!r};\n"
    result = subprocess.run([NODE, '--input-type=module', '-'], input=prelude + source, text=True,
                            capture_output=True, cwd=ROOT, timeout=12)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)


class WillhabenReaderTests(unittest.TestCase):
    def test_validation_and_fixed_urls(self):
        run_js('''
const base='https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz';
let u=new URL(target({kind:'search',keyword:'zeta + violin',category:'all'}));
assert.equal(u.origin,'https://www.willhaben.at'); assert.equal(u.pathname,new URL(base).pathname);
assert.equal(u.searchParams.get('keyword'),'zeta + violin');
assert.equal(new URL(target({kind:'search',keyword:'zeta',category:'music'})).pathname,
 '/iad/kaufen-und-verkaufen/marktplatz/musikinstrumente-noten-6540');
assert.equal(target({kind:'item',path:'/iad/kaufen-und-verkaufen/d/zeta-123'}),
 'https://www.willhaben.at/iad/kaufen-und-verkaufen/d/zeta-123');
assert.equal(target({kind:'item',path:'/iad/kaufen-und-verkaufen/d/geige-z%C3%A4ta-123/'}),
 'https://www.willhaben.at/iad/kaufen-und-verkaufen/d/geige-z%C3%A4ta-123/');
assert.equal(target({kind:'item',path:'/iad/kaufen-und-verkaufen/d/geige-zäta-123/'}),
 'https://www.willhaben.at/iad/kaufen-und-verkaufen/d/geige-z%C3%A4ta-123/');
for(const p of [{kind:'search',keyword:'x'.repeat(401),category:'all'},
 {kind:'search',keyword:'x',category:'all',headers:{}}, {kind:'search',keyword:' ',category:'all'},
 {kind:'search',keyword:'x',category:'other'}, {kind:'item',path:'https://evil.test/'},
 {kind:'item',path:'/iad/kaufen-und-verkaufen/d/foo-123?evil=1'},
 {kind:'item',path:'/iad/kaufen-und-verkaufen/d/foo%2fbar-123'},
 {kind:'item',path:'/iad/kaufen-und-verkaufen/d/foo%5cbar-123'},
 {kind:'item',path:'/iad/kaufen-und-verkaufen/d/foo%252fbar-123'},
 {kind:'item',path:'/iad/kaufen-und-verkaufen/d/foo%00bar-123'},
 {kind:'item',path:'/iad/kaufen-und-verkaufen/d/foo#bar-123'},
 {kind:'item',path:'/iad/kaufen-und-verkaufen/d/foo%ZZ-123'},
 {kind:'item',path:'/iad/kaufen-und-verkaufen/d/../foo-123'},
 {kind:'item',path:'/iad/kaufen-und-verkaufen/d/foo-noid'}, null, []]) assert.equal(target(p),null);
''')

    def test_bun_startup_binds_private_ipv6_and_uses_replica_region(self):
        run_js(f'''
let options;
globalThis.Bun={{serve:o=>{{options=o;}}}};
delete process.env.PORT;
process.env.RAILWAY_REPLICA_REGION='us-fixture';
process.env.RAILWAY_REGION='wrong';
await import({MODULE!r}+'?startup-test');
assert.equal(options.hostname,'::');assert.equal(options.port,8080);
const response=await options.fetch(new Request('http://internal/health'));
assert.deepEqual(await response.json(),{{version:'willhaben-reader-v2',region:'us-fixture'}});
''')

    def test_dto_one_request_no_redirect_credentials_or_query_mutation(self):
        run_js('''
let calls=[],logs=[];
const h=createHandler({region:'us-test', now:()=>123456, log:s=>logs.push(s), upstreamFetch:async (url,opts)=>{
 calls.push([url,opts]); let r=new Response('hello',{status:302,headers:{location:'https://evil.test'}});
 Object.defineProperty(r,'url',{value:url}); return r;
}});
const req=new Request('http://internal/read',{method:'POST',headers:{'X-Willhaben-Reader-Version':'2'},body:JSON.stringify({kind:'search',keyword:'Jazz & JV5',category:'all'})});
let r=await h(req), d=await r.json(); assert.equal(r.status,200);
assert.deepEqual(Object.keys(d).sort(),['body','checked','region','requested','status','url','version','location'].sort());
assert.equal(d.version,'willhaben-reader-v2');assert.equal(d.status,302);assert.equal(d.body,'hello');
assert.equal(d.location,'https://evil.test/');
assert.equal(d.region,'us-test');assert.equal(d.checked,123456);assert.equal(d.url,d.requested);
assert.equal(calls.length,1);assert.equal(calls[0][1].redirect,'manual');assert.equal(calls[0][1].method,'GET');
assert.equal(new URL(calls[0][0]).searchParams.get('keyword'),'Jazz & JV5');
assert.equal(Object.keys(calls[0][1].headers).some(k=>/cookie|authorization/i.test(k)),false);
assert.equal(logs.length,1);assert.equal(logs[0].includes('Jazz'),false);assert.equal(logs[0].includes('hello'),false);
let health=await h(new Request('http://internal/health')); assert.deepEqual(await health.json(),{version:'willhaben-reader-v2',region:'us-test'});
assert.equal((await h(new Request('http://internal/read'))).status,405);
assert.equal((await h(new Request('http://internal/other'))).status,404);
''')

    def test_input_and_decompressed_body_limits(self):
        run_js('''
let calls=0;
const h=createHandler({log:()=>{},upstreamFetch:async url=>{calls++;let r=new Response('a'.repeat(2097153));
 Object.defineProperty(r,'url',{value:url});return r;}});
const request=body=>new Request('http://internal/read',{method:'POST',body});
assert.equal((await h(request('x'.repeat(4097)))).status,400);assert.equal(calls,0);
assert.equal((await h(request(JSON.stringify({kind:'search',keyword:'zeta',category:'all'})))).status,502);
assert.equal(calls,1);
''')

    def test_relative_location_is_resolved_without_follow(self):
        run_js('''
let calls=0;
const h=createHandler({log:()=>{},upstreamFetch:async (url,options)=>{
 calls++;assert.equal(options.redirect,'manual');
 let r=new Response('redirect',{status:308,headers:{location:'/iad/kaufen-und-verkaufen/d/new-slug-123/'}});
 Object.defineProperty(r,'url',{value:url});return r;
}});
const req=new Request('http://internal/read',{method:'POST',headers:{'X-Willhaben-Reader-Version':'2'},body:JSON.stringify({kind:'item',path:'/iad/kaufen-und-verkaufen/d/old-slug-123/'})});
const result=await (await h(req)).json();
assert.equal(result.location,'https://www.willhaben.at/iad/kaufen-und-verkaufen/d/new-slug-123/');
assert.equal(result.status,308);assert.equal(calls,1);
''')

    def test_legacy_without_header_and_invalid_version(self):
        run_js('''
let calls=0;
const h=createHandler({log:()=>{},upstreamFetch:async url=>{calls++;
 let r=new Response('old',{status:308,headers:{location:'/iad/kaufen-und-verkaufen/d/new-123'}});
 Object.defineProperty(r,'url',{value:url});return r;}});
const body=JSON.stringify({kind:'item',path:'/iad/kaufen-und-verkaufen/d/old-123'});
let r=await h(new Request('http://internal/read',{method:'POST',body}));
let d=await r.json();assert.equal(d.version,'willhaben-reader-v1');assert.equal(d.status,308);
assert.deepEqual(Object.keys(d).sort(),['body','checked','region','requested','status','url','version'].sort());
for(const version of ['1','3','']) {
  r=await h(new Request('http://internal/read',{method:'POST',headers:{'X-Willhaben-Reader-Version':version},body}));
  assert.equal(r.status,400);
}
assert.equal(calls,1);
''')

    def test_concurrency_and_deadline(self):
        run_js('''
let calls=0;
const h=createHandler({timeoutMs:40,log:()=>{},upstreamFetch:(url,opts)=>{calls++;
 return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('aborted'))));}});
const req=()=>new Request('http://internal/read',{method:'POST',body:JSON.stringify({kind:'search',keyword:'zeta',category:'all'})});
const a=h(req()), b=h(req()); await new Promise(r=>setTimeout(r,5));
assert.equal((await h(req())).status,429);assert.equal((await a).status,504);assert.equal((await b).status,504);
assert.equal(calls,2);
''')

    def test_deadline_covers_input_and_response_body(self):
        run_js('''
let calls=0;
const forever=()=>new ReadableStream({pull(){return new Promise(()=>{});}, cancel(){}});
const h=createHandler({timeoutMs:25,log:()=>{},upstreamFetch:async url=>{calls++;
 let r=new Response(forever()); Object.defineProperty(r,'url',{value:url}); return r;}});
const input=new Request('http://internal/read',{method:'POST',body:forever(),duplex:'half'});
assert.equal((await h(input)).status,504);assert.equal(calls,0);
const req=new Request('http://internal/read',{method:'POST',body:JSON.stringify({kind:'item',path:'/iad/kaufen-und-verkaufen/d/zeta-123'})});
assert.equal((await h(req)).status,504);assert.equal(calls,1);
''')

    def test_daily_cap_and_reset(self):
        run_js('''
let seconds=86400,calls=0;
const h=createHandler({now:()=>seconds,log:()=>{},upstreamFetch:async url=>{calls++;let r=new Response('ok');
 Object.defineProperty(r,'url',{value:url});return r;}});
const req=()=>new Request('http://internal/read',{method:'POST',body:JSON.stringify({kind:'item',path:'/iad/kaufen-und-verkaufen/d/zeta-123'})});
for(let i=0;i<200;i++) assert.equal((await h(req())).status,200);
assert.equal((await h(req())).status,429);assert.equal(calls,200);
seconds+=86400;assert.equal((await h(req())).status,200);assert.equal(calls,201);
''')


if __name__ == '__main__':
    unittest.main()
