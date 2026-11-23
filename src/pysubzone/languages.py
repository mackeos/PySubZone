"""Language slugs understood by the Subf2m engine running on subzone.ir.

Language slugs are the path segment in ``/subtitles/<slug>/<language>/<id>``.
``resolve()`` accepts a slug, a display name or a short code ("fa", "en",
"tr", ...) and returns the canonical slug.  Unknown values are returned
lower-cased as-is so users can always fall back to the exact site slug.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "farsi_persian"

# canonical slug -> how the site displays it (also used for aliases)
DISPLAY_NAMES = {
    "farsi_persian": "Farsi/Persian",
    "english": "English",
    "arabic": "Arabic",
    "indonesian": "Indonesian",
    "malay": "Malay",
    "bengali": "Bengali",
    "thai": "Thai",
    "turkish": "Turkish",
    "vietnamese": "Vietnamese",
    "italian": "Italian",
    "polish": "Polish",
    "portuguese": "Portuguese",
    "portuguese-br": "Portuguese/Brazilian",
    "spanish": "Spanish",
    "french": "French",
    "german": "German",
    "dutch": "Dutch",
    "russian": "Russian",
    "ukrainian": "Ukrainian",
    "korean": "Korean",
    "japanese": "Japanese",
    "chinese": "Chinese",
    "chinese-bg-code": "Chinese BG code",
    "hindi": "Hindi",
    "urdu": "Urdu",
    "tamil": "Tamil",
    "telugu": "Telugu",
    "hebrew": "Hebrew",
    "greek": "Greek",
    "swedish": "Swedish",
    "norwegian": "Norwegian",
    "danish": "Danish",
    "finnish": "Finnish",
    "czech": "Czech",
    "slovak": "Slovak",
    "hungarian": "Hungarian",
    "romanian": "Romanian",
    "bulgarian": "Bulgarian",
    "croatian": "Croatian",
    "serbian": "Serbian",
    "serbian-cyrillic": "Serbian/Cyrillic",
    "slovenian": "Slovenian",
    "bosnian": "Bosnian",
    "albanian": "Albanian",
    "estonian": "Estonian",
    "latvian": "Latvian",
    "lithuanian": "Lithuanian",
    "macedonian": "Macedonian",
    "georgian": "Georgian",
    "armenian": "Armenian",
    "azerbaijani": "Azerbaijani",
    "kazakh": "Kazakh",
    "uzbek": "Uzbek",
    "mongolian": "Mongolian",
    "nepali": "Nepali",
    "sinhala": "Sinhala",
    "burmese": "Burmese",
    "khmer": "Khmer",
    "lao": "Lao",
    "swahili": "Swahili",
    "amharic": "Amharic",
    "hausa": "Hausa",
    "yoruba": "Yoruba",
    "pashto": "Pashto",
    "kurdish": "Kurdish",
    "esperanto": "Esperanto",
}

# alias (lower case) -> canonical slug
_ALIASES = {}
for _slug, _name in DISPLAY_NAMES.items():
    _ALIASES[_slug.lower()] = _slug
    _ALIASES[_name.lower()] = _slug

_EXTRA_ALIASES = {
    "fa": "farsi_persian",
    "pe": "farsi_persian",
    "per": "farsi_persian",
    "farsi": "farsi_persian",
    "persian": "farsi_persian",
    "farsi-persian": "farsi_persian",
    "en": "english",
    "en-us": "english",
    "en-gb": "english",
    "ar": "arabic",
    "id": "indonesian",
    "ms": "malay",
    "bn": "bengali",
    "th": "thai",
    "tr": "turkish",
    "vi": "vietnamese",
    "it": "italian",
    "pl": "polish",
    "pt": "portuguese",
    "pt-br": "portuguese-br",
    "br": "portuguese-br",
    "brazilian": "portuguese-br",
    "es": "spanish",
    "fr": "french",
    "de": "german",
    "nl": "dutch",
    "ru": "russian",
    "ua": "ukrainian",
    "uk": "ukrainian",
    "ko": "korean",
    "kr": "korean",
    "ja": "japanese",
    "jp": "japanese",
    "zh": "chinese",
    "cn": "chinese",
    "hi": "hindi",
    "ur": "urdu",
    "ta": "tamil",
    "te": "telugu",
    "he": "hebrew",
    "el": "greek",
    "gr": "greek",
    "sv": "swedish",
    "se": "swedish",
    "no": "norwegian",
    "nb": "norwegian",
    "da": "danish",
    "fi": "finnish",
    "cs": "czech",
    "sk": "slovak",
    "hu": "hungarian",
    "ro": "romanian",
    "bg": "bulgarian",
    "hr": "croatian",
    "sr": "serbian",
    "sl": "slovenian",
    "bs": "bosnian",
    "sq": "albanian",
    "et": "estonian",
    "lv": "latvian",
    "lt": "lithuanian",
    "mk": "macedonian",
    "ka": "georgian",
    "hy": "armenian",
    "az": "azerbaijani",
    "kk": "kazakh",
    "uz": "uzbek",
    "mn": "mongolian",
    "ne": "nepali",
    "si": "sinhala",
    "my": "burmese",
    "km": "khmer",
    "lo": "lao",
    "sw": "swahili",
    "am": "amharic",
    "ps": "pashto",
    "ku": "kurdish",
}
_ALIASES.update(_EXTRA_ALIASES)


def display_name(slug: str) -> str:
    """Human readable name for a slug (falls back to the slug itself)."""
    return DISPLAY_NAMES.get(slug, slug.replace("_", " ").title())


def resolve(value: str | None) -> str | None:
    """Return the canonical slug for *value* (alias, name or slug).

    ``None`` and empty strings are passed through, unknown values are
    returned lower-cased unchanged so raw site slugs always work.
    """
    if value is None:
        return None
    cleaned = value.strip().lower().replace(" ", "_")
    if not cleaned:
        return None
    return _ALIASES.get(cleaned, cleaned)


def known_languages() -> list[str]:
    """Sorted list of language slugs known to PySubZone."""
    return sorted(DISPLAY_NAMES)


def codes_for(slug: str) -> list[str]:
    """Short codes and aliases that :func:`resolve` maps to *slug*.

    Excludes the canonical slug itself and the lower-cased display name;
    returns them sorted, so ``codes_for("farsi_persian")`` yields
    ``['fa', 'farsi', 'pe', 'per', 'persian']``.
    """
    canonical = DISPLAY_NAMES.get(slug, "").lower()
    found = {alias for alias, target in _ALIASES.items() if target == slug}
    found -= {slug, canonical}
    return sorted(found)
