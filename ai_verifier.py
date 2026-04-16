"""
AI re-verification — uses OpenAI GPT-4o-mini to confirm each listing
is genuinely a Zeta electric violin for sale before alerting.

Cost: ~$0.001 per call. At 50 listings/day = ~$0.05/day = ~$1.50/month.
"""

import httpx
import logging
from config import Config

log = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = """You are a verification assistant for a Zeta electric violin search bot.

Your job: determine if a marketplace listing is GENUINELY a Zeta brand electric violin for sale.

ZETA is a specific brand (Zeta Music Systems) that makes electric violins. Models include:
Jazz Fusion, Strados, Jazz Modern, Jazz Classic, Acoustic Pro, E-Fusion, E-Modern.
Model codes: JV44, JV45, SV24, SV25, SV43, CV44, EV25, EV44.
Signature artists: Jean-Luc Ponty (JLP), Boyd Tinsley, Eileen Ivers.

Reply with EXACTLY one of these three words:
- YES — this is a Zeta brand electric violin actively for sale
- NO — this is NOT a Zeta violin, or not for sale, or is an accessory/part only
- MAYBE — unclear, could be a Zeta but not certain

Rules:
- "Zeta" in the title/description must refer to the VIOLIN BRAND, not:
  - Arc'teryx Zeta (jacket/clothing)
  - Zeta potential (chemistry)
  - Zeta phi beta (sorority)
  - Any non-instrument product
- Accessories alone (case, bow, bridge, pickup, strings) are NO
- "Wanted to buy" / "looking for" posts are NO
- Sold/ended/expired listings are NO
- Other violin brands (Yamaha, NS Design, Bridge, Fender, Stagg) are NO
- If the listing mentions Zeta AND another brand, check if the Zeta
  is the item being sold or just mentioned for comparison — if just
  comparison, reply NO"""


async def verify_listing(listing: dict) -> str:
    """Verify a single listing with GPT-4o-mini.
    Returns: 'YES', 'NO', or 'MAYBE'. On error returns 'MAYBE'."""

    if not Config.OPENAI_API_KEY:
        return "MAYBE"

    title = listing.get("title", "")
    description = listing.get("description", "")
    platform = listing.get("platform", "")
    price = listing.get("price", "")

    user_message = (
        f"Platform: {platform}\n"
        f"Title: {title}\n"
        f"Price: {price}\n"
        f"Description: {description[:500]}"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {Config.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    "max_tokens": 10,
                    "temperature": 0,
                },
            )

            if resp.status_code != 200:
                log.warning(f"AI verify error: HTTP {resp.status_code} — {resp.text[:200]}")
                return "MAYBE"

            data = resp.json()
            answer = data["choices"][0]["message"]["content"].strip().upper()

            if answer in ("YES", "NO", "MAYBE"):
                return answer

            # Handle unexpected responses
            if "YES" in answer:
                return "YES"
            if "NO" in answer:
                return "NO"
            return "MAYBE"

    except Exception as e:
        log.warning(f"AI verify error: {e}")
        return "MAYBE"


async def verify_listings_batch(listings: list) -> list:
    """Verify a batch of listings. Returns only those that pass
    (YES or MAYBE). NO listings are filtered out."""

    if not Config.OPENAI_API_KEY:
        log.info("OPENAI_API_KEY not set — skipping AI verification, "
                 "passing all listings through")
        return listings

    if not listings:
        return []

    verified = []
    rejected = 0

    for listing in listings:
        verdict = await verify_listing(listing)
        if verdict == "NO":
            rejected += 1
            log.info(f"AI rejected: [{listing.get('platform', '')}] "
                     f"{listing.get('title', '')[:60]} — NOT a Zeta violin")
        else:
            listing["ai_verified"] = verdict
            verified.append(listing)

    if rejected > 0:
        log.info(f"AI verification: {len(verified)} passed, {rejected} rejected")

    return verified
