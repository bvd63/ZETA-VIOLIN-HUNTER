"""Bounded anonymous GET via the Node runtime bundled with Playwright."""
import asyncio
import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl

MAX_BYTES = 2 * 1024 * 1024
_LIMIT = asyncio.Semaphore(2)
_SCRIPT = r'''const input=process.argv[1], max=2097152, origin=new URL(input);
const id=p=>p.match(/^\/p\/(?:[^/]+\/)+(\d+)\/?$/)?.[1];
const query=u=>JSON.stringify([...u.searchParams.entries()].sort());
const valid=s=>{let u;try{u=new URL(s)}catch{return false}
return u.protocol==='https:' && !u.username && !u.password && (!u.port || u.port==='443') &&
['gumtree.com','www.gumtree.com'].includes(u.hostname.toLowerCase()) &&
(origin.pathname==='/search' ? u.pathname==='/search' && query(u)===query(origin)
 : Boolean(id(origin.pathname)) && id(u.pathname)===id(origin.pathname));};
(async()=>{let url=input; for(let i=0;i<4;i++){
if(!valid(url))throw Error('invalid redirect');
let r=await fetch(url,{method:'GET',redirect:'manual',headers:{'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36','Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','Accept-Language':'en-GB,en;q=0.9'}});
if(r.status>=300 && r.status<400 && r.headers.get('location')){url=new URL(r.headers.get('location'),url).href;continue}
let reader=r.body?.getReader(), chunks=[], size=0;
if(reader){while(true){let x=await reader.read();if(x.done)break;size+=x.value.length;if(size>max){await reader.cancel();throw Error('body too large')}chunks.push(x.value)}}
let body=Buffer.concat(chunks.map(x=>Buffer.from(x))).toString('utf8');
process.stdout.write(JSON.stringify({status:r.status,body,url}));return}
throw Error('redirect limit')})().catch(()=>process.exit(1));'''


def allowed_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == 'https' and parts.hostname in ('gumtree.com', 'www.gumtree.com')
                and not parts.username and not parts.password and parts.port in (None, 443)
                and (parts.path == '/search' or bool(re.fullmatch(r'/p/(?:[^/]+/)+\d+/?', parts.path))))
    except (ValueError, TypeError):
        return False


def _node_path() -> str:
    spec = importlib.util.find_spec('playwright')
    if not spec or not spec.origin:
        raise RuntimeError('Playwright runtime unavailable')
    node = Path(spec.origin).parent / 'driver' / 'node'
    if not node.is_file():
        raise RuntimeError('Playwright Node runtime unavailable')
    return str(node)


def same_target(requested: str, final: str) -> bool:
    if not allowed_url(requested) or not allowed_url(final):
        return False
    original, target = urlsplit(requested), urlsplit(final)
    if original.path == '/search':
        return target.path == '/search' and sorted(parse_qsl(original.query, keep_blank_values=True)) == sorted(parse_qsl(target.query, keep_blank_values=True))
    original_id = re.search(r'/(\d+)/?$',original.path)
    target_id = re.search(r'/(\d+)/?$',target.path)
    return bool(original_id and target_id and original_id[1] == target_id[1])


async def fetch(url: str, *, timeout: float = 15) -> tuple:
    """Return (HTTP status, HTML, final URL); failures never imply empty inventory."""
    if not allowed_url(url):
        raise ValueError('unsupported public URL')
    async with asyncio.timeout(timeout):
        return await _fetch(url, timeout)


async def _fetch(url: str, timeout: float) -> tuple:
    async with _LIMIT:
        proc = await asyncio.create_subprocess_exec(_node_path(), '-e', _SCRIPT, url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except BaseException:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.communicate()
            raise
        if proc.returncode != 0 or len(out) > MAX_BYTES * 6 + 2048:
            raise ValueError('public fetch failed or output too large')
        try:
            data = json.loads(out)
            if (not isinstance(data, dict) or set(data) != {'status','body','url'}
                    or type(data['status']) is not int or not 100 <= data['status'] <= 599
                    or not isinstance(data['body'], str) or len(data['body'].encode('utf-8')) > MAX_BYTES
                    or not isinstance(data['url'], str) or not same_target(url, data['url'])):
                raise ValueError('invalid response')
        except (ValueError, UnicodeError, KeyError, TypeError):
            raise ValueError('invalid public fetch response') from None
        return data['status'], data['body'], data['url']
