"""Centralized translation strings. English is fully populated and is what
ships today; Tamil is scaffolded with the same keys so translating the rest
later is a matter of filling in values, not restructuring the app. No
template has been mass-converted yet — `t()` is wired in for a handful of
high-traffic, reused strings (shared product-card labels) to prove the
mechanism works end-to-end before committing to translating everything."""

SUPPORTED_LANGUAGES = {"en": "English", "ta": "தமிழ் (Tamil) — coming soon"}
DEFAULT_LANGUAGE = "en"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "add_to_cart": "Add to Cart",
        "out_of_stock": "Out of Stock",
        "in_stock": "In Stock",
        "low_stock": "Low Stock — {count} left",
        "save_for_later": "Save for later",
        "buy_now": "Buy Now",
    },
    "ta": {
        # Populated as real Tamil translations are added — any key missing
        # here simply falls back to English (see t() below), so this can be
        # filled in incrementally without ever breaking the page.
    },
}


def t(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    strings = TRANSLATIONS.get(lang, TRANSLATIONS[DEFAULT_LANGUAGE])
    text = strings.get(key) or TRANSLATIONS[DEFAULT_LANGUAGE].get(key, key)
    return text.format(**kwargs) if kwargs else text
