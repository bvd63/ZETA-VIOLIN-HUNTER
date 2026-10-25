"""Resource visibility. Reservations are not a provider account balance.

Metering GETs do not start Actors or search requests. Only whitelisted numeric
account/plan fields are stored; credentials and personal profile are discarded.
"""
import html
import logging
import time
from datetime import datetime, timezone

import httpx
from config import Config
from database import connect
from provider_state import read, write, used, claim_window

log = logging.getLogger(__name__)


def snapshot() -> dict:
    now = datetime.now(timezone.utc)
    data = {
        "brave": {"configured": bool(Config.BRAVE_API_KEY), "reserved": 0,
                  "limit": Config.BRAVE_MONTHLY_QUOTA, "unit": "cereri", "period": f"{now:%Y-%m} UTC"},
        "apify": {"configured": bool(Config.APIFY_TOKEN), "reserved": 0,
                  "limit": Config.APIFY_MONTHLY_LIMIT_USD, "unit": "USD", "period": f"{now:%Y-%m} UTC"},
    }
    conn = connect()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "scraper_runs" in tables:
            row = conn.execute("SELECT last_run FROM scraper_runs WHERE scraper=?",
                               (f"brave_quota:{now:%Y-%m}",)).fetchone()
            data["brave"] = {"configured": bool(Config.BRAVE_API_KEY), "reserved": int(row[0]) if row else 0,
                             "limit": Config.BRAVE_MONTHLY_QUOTA, "unit": "cereri", "period": f"{now:%Y-%m} UTC"}
        if "apify_jobs" in tables:
            if 'apify_costs' in tables:
                from apify_costs import spent_micro
                amount = spent_micro(conn, f'{now:%Y-%m}')
            else:
                amount = conn.execute("SELECT coalesce(sum(cap),0) FROM apify_jobs WHERE period=?",
                                      (f"{now:%Y-%m}",)).fetchone()[0]
            data["apify"] = {"configured": bool(Config.APIFY_TOKEN), "reserved": amount / 1000000,
                             "limit": Config.APIFY_MONTHLY_LIMIT_USD, "unit": "USD",
                             "period": f"{now:%Y-%m} UTC"}
        if "scraper_stats" in tables:
            for provider, source in (("brave", "Brave Search"), ("apify", "Facebook Marketplace")):
                row = conn.execute("""SELECT error,run_at FROM scraper_stats
                    WHERE scraper=? AND raw_count>=0 ORDER BY id DESC LIMIT 1""", (source,)).fetchone()
                if row:
                    data[provider]["health"] = {"error": row[0] or "", "checked_at": row[1]}
        if "discovery_queue" in tables:
            data["deferred_details"] = {name: {"saved": total, "due": due} for name, total, due in conn.execute(
                "SELECT namespace,count(*),sum(next_attempt<=?) FROM discovery_queue GROUP BY namespace", (time.time(),))}
    finally:
        conn.close()
    for name, key, limit, scale in (("tavily", Config.TAVILY_API_KEY, Config.TAVILY_ROLLING_CREDITS, 1),
                                  ("exa", Config.EXA_API_KEY, Config.EXA_ROLLING_BUDGET_USD, 1000000)):
        data[name] = {"configured": bool(key), "reserved": used(name) / scale,
                      "limit": limit, "unit": "credite" if name == "tavily" else "USD",
                      "period": "ultimele 31 zile", "health": read(name + ":health", {})}
    data["apify_account"] = read("apify:account", {})
    data['backup'] = read('operations:backup', {})
    data["pushover"] = {"configured": bool(Config.PUSHOVER_APP_TOKEN and Config.PUSHOVER_USER_KEY),
                        "health": read("pushover:health", {}), "quota": read("pushover:quota", {})}
    return data


async def refresh_accounts() -> None:
    if not claim_window("metering:last_check", 6 * 3600):
        return
    async with httpx.AsyncClient(timeout=15) as client:
        if Config.APIFY_TOKEN:
            cached = read("apify:account", {})
            try:
                headers = {"Authorization": "Bearer " + Config.APIFY_TOKEN}
                usage = await client.get("https://api.apify.com/v2/users/me/usage/monthly", headers=headers)
                usage.raise_for_status()
                profile = await client.get("https://api.apify.com/v2/users/me", headers=headers)
                profile.raise_for_status()
                limits = await client.get("https://api.apify.com/v2/users/me/limits", headers=headers)
                limits.raise_for_status()
                u, p, lim = usage.json()["data"], profile.json()["data"].get("plan", {}), limits.json()["data"]
                account_used = float(u["totalUsageCreditsUsdAfterVolumeDiscount"])
                value = {"usage_usd": account_used, "usage_cycle": u.get("usageCycle", {}),
                    "included_monthly_usd": p.get("monthlyUsageCreditsUsd"),
                    "plan_base_usd": p.get("monthlyBasePriceUsd"),
                    "account_usage_ceiling_usd": (lim.get("limits") or {}).get("maxMonthlyUsageUsd"),
                    "checked": time.time(), "error": ""}
                write("apify:account", value)
                from apify_costs import reconcile
                # Reuse the authenticated metadata client; GETs spend no Actor
                # credits and do not replace a single discovery/detail request.
                await reconcile(client)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                cached.update({"error": type(exc).__name__, "failed_at": time.time()})
                write("apify:account", cached)
                log.warning("Apify metering unavailable (%s)", type(exc).__name__)
        if Config.PUSHOVER_APP_TOKEN:
            from pushover_notifier import PushoverNotifier
            push = PushoverNotifier()
            await push.validate()
            result = await push.request("GET", "apps/limits.json")
            if result:
                write("pushover:quota", {k: result[k] for k in ("limit", "remaining", "reset") if k in result}
                      | {"checked": time.time()})


def warning_events(data: dict) -> list:
    result = []
    backup = data.get('backup', {})
    if backup.get('error'):
        result.append(('backup:failed', 'Copia SQLite sau testul de restaurare a eșuat; verificare necesară.'))
    for name in ("brave", "apify", "tavily", "exa"):
        row = data.get(name, {})
        if not row.get("configured"):
            continue
        amount, limit = row.get("reserved", 0), row.get("limit", 0)
        if limit <= 0 or amount >= limit * 0.8:
            level = "exhausted" if limit <= 0 or amount >= limit else "95" if amount >= limit * .95 else "80"
            result.append((f"{name}:budget:{level}",
                f"{name.title()}: rezervări bot {amount:.2f}/{limit:.2f} {row.get('unit', '')}; verifică resursele."))
        if (row.get("health") or {}).get("error"):
            result.append((f"{name}:access:{row['health']['error']}",
                           f"{name.title()}: ultimul acces a eșuat ({row['health']['error']})."))
    account = data.get("apify_account", {})
    if not account.get("error") and account.get("checked", 0) > time.time() - 12 * 3600:
        included = account.get("included_monthly_usd")
        if isinstance(included, (int, float)) and included > 0 and account.get("usage_usd", 0) >= included * 0.8:
            level = "exhausted" if account['usage_usd'] >= included else "95" if account['usage_usd'] >= included * .95 else "80"
            result.append((f"apify:account:{level}", f"Apify cont: consum ${account['usage_usd']:.2f} din ${included:.2f} incluse în plan; alte taxe/credite pot exista."))
    push = data.get("pushover", {})
    if push.get("configured") and (push.get("health") or {}).get("error"):
        result.append(("pushover:access:" + str(push["health"]["error"]),
                       "Pushover: canalul suplimentar raportează o eroare; verifică /resurse."))
    return result


def warnings(data: dict) -> list:
    return [message for _, message in warning_events(data)]


def text() -> str:
    data = snapshot()
    lines = ["🔋 <b>Resurse de căutare și alerte</b>", "Rezervările botului includ cererile cu răspuns incert; nu reprezintă soldul contului."]
    for name in ("brave", "apify", "tavily", "exa"):
        row = data.get(name, {})
        if not row.get("configured"):
            lines.append(f"⚪ {name.title()}: neconfigurat")
            continue
        lines.append(f"• {name.title()}: {row['reserved']:.2f}/{row['limit']:.2f} {row['unit']} rezervate · {row['period']}")
        if (row.get("health") or {}).get("error"):
            lines.append("⚠️ " + html.escape(str(row["health"]["error"])))
    account = data.get("apify_account", {})
    if account.get("checked"):
        checked = datetime.fromtimestamp(account["checked"], timezone.utc).strftime("%d.%m %H:%M UTC")
        lines.append(f"Apify: consum real de cont ${account['usage_usd']:.2f} (verificat {checked}).")
        cycle = account.get("usage_cycle", {})
        lines.append(f"Ciclu cont: {str(cycle.get('startAt', ''))[:10]} – {str(cycle.get('endAt', ''))[:10]}.")
        lines.append("Plafonul contului și creditele incluse sunt diferite de soldul disponibil.")
    if account.get("error"):
        lines.append("⚠️ Actualizarea consumului Apify a eșuat; valorile păstrate pot fi vechi.")
    push = data["pushover"]
    lines.append("🔔 Pushover: " + ("configurat" if push["configured"] else "neconfigurat; Telegram rămâne activ"))
    if push.get("quota"):
        lines.append(f"Pushover: {push['quota'].get('remaining', '?')} mesaje API rămase la ultima verificare.")
    backup = data.get('backup', {})
    if backup.get('checked'):
        lines.append('💾 Copie SQLite cu restaurare verificată: ' +
                     datetime.fromtimestamp(backup['checked'], timezone.utc).strftime('%d.%m %H:%M UTC'))
    cycles = Config.RUNS_PER_DAY * 31
    brave = cycles * Config.BRAVE_QUERIES_PER_RUN
    tavily = cycles * (Config.TAVILY_QUERIES_PER_RUN + Config.TAVILY_EXTRA_QUERIES_PER_RUN)
    exa = cycles * (Config.EXA_QUERIES_PER_RUN + Config.EXA_EXTRA_QUERIES_PER_RUN) * .017
    exa += 2 * 620 * .001  # Primary-page checks + identical Willhaben query fallback.
    lines.append(f'Plan la 31 zile: Brave {brave} cereri; Tavily {tavily} credite; Exa ≤ ${exa:.2f}. '
                 'Apify: costurile confirmate înlocuiesc rezervările; cele incerte rămân integral rezervate.')
    lines += [html.escape(s) for s in warnings(data)]
    return "\n".join(lines)[:3900]


async def tick(notifier) -> None:
    await refresh_accounts()
    issues = [(key, message) for key, message in warning_events(snapshot())
              if (read("resources:warned:" + key, 0) or 0) <= time.time() - 24 * 3600]
    if not issues:
        return
    # Deduplicate a persistent problem across hourly checks and deploys. A
    # failed Telegram send is not marked as delivered and is retried next time.
    if await notifier.send("⚠️ <b>Resurse / acces: verificare necesară</b>\n" +
                           "\n".join(html.escape(message) for _, message in issues) +
                           "\nCelelalte surse continuă. Detalii: /resurse"):
        for key, _ in issues:
            write("resources:warned:" + key, time.time())
