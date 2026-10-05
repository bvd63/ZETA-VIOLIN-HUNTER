# Auction coverage and evidence

Verified on 2026-10-05 against public auction-house pages. These examples establish
where Zeta instruments have appeared; they are historical, not current opportunities.

| House / platform | Public evidence | Search priority |
|---|---|---|
| Gardiner Houlgate | [Lot 2413, 6 September 2024](https://www.musicalinstrument-auctions.co.uk/sale/58/musical-instruments?page=15): Jazz Fusion five-string plus another Zeta four-string, sold £5,200. [Lot 3457, 12 June 2026](https://www.musicalinstrument-auctions.co.uk/sale/75/3457/Rare-four-string-Zeta-electric-violin-with-cream-finish-bow-case): four-string Zeta with MIDI equipment, sold £3,400. | Both `gardinerhoulgate.co.uk` and `musicalinstrument-auctions.co.uk`, every web-search cycle. |
| Tarisio | [2024 Jean-Luc Ponty sale announcement on the auction house's site](https://tarisio.com/press/jean-luc-ponty-violins-to-be-auctioned/): includes a five-string Jean-Luc Ponty edition Zeta. This is evidence for source selection, not an alertable press article. | `tarisio.com`, every web-search cycle; brand, Strados, model codes and Jean-Luc Ponty. |
| Heritage Auctions | [Official archive search](https://www.ha.com/c/search/results.zx?archive_state=5327&mode=archive&sb=1&si=2&sold_status=1526&term=zeta): Charlie Daniels signed Zeta Strados violin, lot 89626, June 2016. | `ha.com`, including subdomains, every web-search cycle. |
| GovDeals | [Owner's lot 370 / 12180](https://www.govdeals.com/en/asset/370/12180): two JV44 violins plus one viola; auction closed August 2026. | Every web-search cycle plus direct anonymous public API. |

The additional discovery groups cover AllSurplus, PublicSurplus, HiBid, ShopGoodwill,
Proxibid, LiveAuctioneers, Invaluable, The Saleroom, AuctionZip, Catawiki, Bonhams,
Bidspotter, Easy Live Auction, Interencheres, Drouot, Lot-tissimo and Dorotheum.
They expand discovery; this list does not assert verified past Zeta sales on every site.

No extra search-API quota is added: priority queries replace part of the rotating
matrix within the existing per-run budgets. Details are verified before alerts;
closed auctions, editorial pages, auction-result archives and unrelated Zeta goods
are rejected. An inaccessible dedicated item page is labelled availability unknown,
not certified active. Without timely indexing, a web-only source can still be missed.

## Maintenance

Keep confirmed history above and executable groups in `scrapers/google.py` aligned.
Add direct adapters only after inspecting an actual public search response, with
bounded pages/details and a working parser; do not add empty adapters to claim coverage.
After a missed auction, `/verifica URL` explains today's state and filters, while
`/status` reports request failures and rejection counts. Neither reconstructs a
historical discovery event unless it was logged by the bot.
