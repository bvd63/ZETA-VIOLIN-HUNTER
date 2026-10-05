# Apify Facebook in the Railway bot

Set `APIFY_TOKEN`, `APIFY_DISCOVERY_TASK_ID`, and `APIFY_DETAILS_TASK_ID` in Railway Variables. Keep the token out of source, logs, screenshots and chat. `APIFY_ENABLED=false` disables managed Facebook collection. The old direct collector remains available when no Apify token is configured.

The existing 10:00/22:00 Europe/Bucharest jobs invoke this source alongside the other scrapers. There is no separate Apify schedule. The task inputs in Console are manual-pilot templates: API calls override them with two regional URLs, five results per URL, details off, and a $0.065 run cap. The detail stage collects at most two relevant, queued items per cycle under a $0.02 cap. Bay Area/Chicago anchors alternate; another query rotates across Los Angeles, Seattle, Chicago and Bay Area. Regional recommendations may extend beyond the requested 500 km.

The $4 default cap is for **reserved maximum charges in a UTC calendar month**, stored in the persistent bot SQLite database. Every run reserves its full cap before the POST. Reservations are not refunded on errors or preliminary billing statistics. This is conservative: the source can stop while actual spend is below $4. Manual runs, other Actors and provider billing periods are separate; the Apify Free account can exhaust its shared credit sooner. No paid upgrade is activated by the bot. Raising any cap requires checking the total account budget.

Discovery uses the shared filter before requesting descriptions. The queue deduplicates by canonical Facebook item URL and retains candidates across restarts. Detail cache TTL is 72 hours. Only explicit live, not sold and not pending detail results enter the normal bot pipeline; that pipeline applies its shared filters and alert dedup again. Missing flags, error rows and partial runs are withheld or recorded as source errors. Dates in descriptions remain evidence supplied by sellers, not authenticated manufacture dates.

Known interrupted runs resume by ID. A POST timeout can mean the remote job started: its full budget stays reserved, no immediate duplicate is launched, and a request without a returned run ID may lose that run's results. This limitation is preferable to uncontrolled duplicate spending.

Validate without Telegram:

```sh
python -m apify_preview --check
python -m apify_preview --reference-run RUN_ID
python -m apify_preview --search
```

`--check` verifies authenticated access to both private task IDs. If `APIFY_VALIDATION_RUN_ID` is temporarily configured, it also parses an existing reference dataset; remove that variable after rollout so future deployments do not depend on dataset retention. `--search` performs real, budgeted collection and updates the source ledger/cache; it never imports Telegram or marks anything delivered. A manual preview consumes the source's 10-hour discovery guard.

Primary API references: [start a task](https://docs.apify.com/api/v2/actor-task-runs-post), [read a run](https://docs.apify.com/api/v2/actor-run-get), [read dataset items](https://docs.apify.com/api/v2/dataset-items-get). Authorization is sent in a Bearer header. Each HTTP call has a 30-second timeout; waiting for an Actor is split into 20-second server waits. Console limits alone do not protect API calls.
