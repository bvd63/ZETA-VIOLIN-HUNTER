"""Strict allowlists for account telemetry; never retain credentials/IDs."""
import math


def tavily_usage(data: dict, checked: float) -> dict:
    if not isinstance(data, dict):
        raise ValueError('invalid usage response')
    result = {'checked':checked, 'error':''}
    for group, required in (('key', ('usage','limit')), ('account', ('plan_usage','plan_limit'))):
        row = data.get(group)
        if not isinstance(row, dict) or any(k not in row for k in required):
            raise ValueError('missing usage totals')
        allowed = ('usage','limit') if group == 'key' else ('plan_usage','plan_limit','paygo_usage','paygo_limit')
        allowed += ('search_usage','extract_usage','crawl_usage','map_usage','research_usage')
        safe = {}
        for key in allowed:
            if key not in row:
                continue
            value = row[key]
            if value is None and key in ('limit','paygo_limit'):
                safe[key] = None  # an unlimited key is different from a zero cap
            elif isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value) and value >= 0:
                safe[key] = value
            else:
                raise ValueError('invalid usage number')
        if group == 'account' and isinstance(row.get('current_plan'),str):
            safe['current_plan'] = row['current_plan'][:80]
        result[group] = safe
    return result
