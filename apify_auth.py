"""Stop authenticated Actor launches after a proven invalid-token response.

Only a token fingerprint stays in SQLite. Replacing the environment variable
immediately invalidates the old refusal; no search/budget cursor is reset.
"""
import hashlib
import time

from config import Config
from provider_state import read, write

REASON = 'Apify HTTP 401: APIFY_TOKEN invalid; replace the token in Railway'


def fingerprint():
    return hashlib.sha256(Config.APIFY_TOKEN.encode()).hexdigest()


def mark_rejected(status):
    if status == 401:
        write('apify:authentication', {'fingerprint':fingerprint(),'status':401,'checked':time.time()})


def mark_valid():
    write('apify:authentication', {'fingerprint':fingerprint(),'status':200,'checked':time.time()})


async def check_access(client):
    state = read('apify:authentication',{})
    if state.get('fingerprint') == fingerprint() and state.get('status') == 401:
        if time.time()-float(state.get('checked',0))<6*3600:
            return False
        # A provider incident can recover without a replacement credential.
        # Recheck with GET only; no billable launch while auth is unconfirmed.
        response=await client.get('https://api.apify.com/v2/users/me')
        if response.status_code==200:
            mark_valid()
            return True
        mark_rejected(response.status_code)
        return False
    meter = read('apify:account',{})
    # A previously failing account meter can conceal revoked credentials. Probe
    # with a non-billable GET before reserving or POSTing another Actor run.
    if meter.get('error') and (state.get('fingerprint') != fingerprint()
                              or time.time()-float(state.get('checked',0))>6*3600):
        response = await client.get('https://api.apify.com/v2/users/me')
        if response.status_code == 401:
            mark_rejected(401)
            return False
        if response.status_code == 200:
            mark_valid()
        # 403 may be a scoped token lacking account-read access. It does not
        # prove that Actor access is invalid and must not disable discovery.
    return True
