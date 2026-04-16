"""
Base scraper class — all platform scrapers inherit from this.
"""

import re
import hashlib
from config import Config


class BaseScraper:
    name = "Base"

    def _is_excluded(self, text: str) -> bool:
        """Return True if listing should be excluded (sold/ended/Romanian)."""
        text_upper = text.upper()
        for kw in Config.EXCLUDE_KEYWORDS:
            if kw.upper() in text_upper:
                return True
        return False

    def _is_excluded_location(self, location: str) -> bool:
        """Check if location should be excluded. Uses word-boundary
        matching to avoid 'ro' matching 'Rome' or 'Toronto'."""
        import re
        location_lower = location.lower()
        for loc in Config.EXCLUDED_LOCATIONS:
            if loc.lower() in location_lower:
                return True
        for code in getattr(Config, 'EXCLUDED_COUNTRY_CODES', []):
            # Match ISO code as a whole word (surrounded by word
            # boundaries, commas, spaces, or string edges)
            pattern = r'(?:^|[\s,])' + re.escape(code) + r'(?:$|[\s,])'
            if re.search(pattern, location, re.IGNORECASE):
                return True
        return False

    def _year_in_range(self, text: str) -> bool:
        """
        Returns True if:
        - No year is mentioned (include by default — vânzătorul nu a specificat)
        - A year between MIN_YEAR and MAX_YEAR is mentioned
        Returns False if a year OUTSIDE the range is clearly mentioned.
        """
        import re
        # Find all 4-digit years between 1950 and 2030 in text
        years_found = re.findall(r"\b(19[5-9]\d|20[0-2]\d)\b", text)
        if not years_found:
            return True  # No year mentioned — include it
        # If any year is in range, include the listing
        for y in years_found:
            if Config.MIN_YEAR <= int(y) <= Config.MAX_YEAR:
                return True
        return False  # All years mentioned are outside range

    def _price_in_range(self, price_str: str) -> bool:
        """Extract numeric price and check range."""
        try:
            nums = re.findall(r"[\d,.]+", price_str.replace(",", ""))
            if not nums:
                return True  # Unknown price — include it
            price = float(nums[0])
            return Config.MIN_PRICE <= price <= Config.MAX_PRICE
        except Exception:
            return True

    def _make_id(self, platform: str, url: str) -> str:
        """Generate a stable unique ID for a listing."""
        return hashlib.md5(f"{platform}:{url}".encode()).hexdigest()

    def _relevance_score(self, title: str, description: str = "") -> int:
        """Score 1-10 how likely this is a genuine Zeta violin listing."""
        text = (title + " " + description).lower()
        score = 1

        # High confidence model names
        high = ["strados", "zeta sv24", "zeta sv25", "zeta jv44", "zeta jv45",
                "zeta ev25", "zeta ev44", "zeta cv44", "zeta sv43", "jean-luc ponty",
                "zeta jlp", "zeta acoustic-pro", "zeta jazz fusion"]
        for kw in high:
            if kw in text:
                score += 3

        # Medium confidence
        medium = ["zeta violin", "zeta electric", "zeta music", "zetta violin"]
        for kw in medium:
            if kw in text:
                score += 2

        # Mild confidence
        mild = ["electric violin", "midi violin", "5-string violin", "violino elettrico",
                "violon électrique", "zeta geige"]
        for kw in mild:
            if kw in text:
                score += 1

        return min(score, 10)

    async def search(self) -> list:
        raise NotImplementedError
