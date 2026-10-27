"""A native search card is a discovery clue, not the complete item page."""
from filters import has_zeta_signal, has_violin_word, PICKUP_OBJECT_TITLE_RX, INSTRUMENT_ABSENT_RX


def card_hints(title: str, description: str, query: str) -> dict | None:
    text = title + ' ' + description
    brand_visible = has_zeta_signal(text)
    if not brand_visible and not (has_zeta_signal(query) and has_violin_word(text)):
        return None
    return {'_native_search_query':query, '_query_identity_unverified':not brand_visible}


def incomplete_pickup_card(listing: dict, reason: str) -> bool:
    """Read a native search summary before treating its title as the sold object.

    Only adapters which persist a query hint qualify. Primary pages receive no
    such exemption: a standalone pickup is still rejected after it is read.
    """
    return bool(reason == 'noise' and listing.get('_native_search_query')
                and not INSTRUMENT_ABSENT_RX.search(str(listing.get('description') or ''))
                and PICKUP_OBJECT_TITLE_RX.fullmatch(str(listing.get('title') or '')))
