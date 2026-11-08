"""Small offline ratings from saved advert evidence and cached company checks."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
import threading
import unicodedata
from urllib.parse import urlsplit

COMPANY_FILE = Path(__file__).with_name("company_ratings.json")
_cache = {"stamp": None, "entries": {}}
_ENGLISH_CACHE_LIMIT = 512
_english_cache = OrderedDict()
_english_cache_lock = threading.Lock()


def normalized(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in value if not unicodedata.combining(c))).strip()


def _score(value):
    return value if type(value) is int and 1 <= value <= 5 else None


def advert_body(jd):
    """Exclude candidate fit/language notes from the advert's language rating."""
    lines = str(jd or "").splitlines()
    start = next((i+1 for i,line in enumerate(lines) if re.match(
        r"^#{1,3}\s+(?:full (?:original )?advert|original advert body|live advert|annonce (?:complete|originale))\b",
        normalized_heading(line), re.I)), 0)
    result = []
    for line in lines[start:]:
        if line.startswith("#") and re.search(r"\b(?:fit|gap|score|candidate|profile evidence|experience stretch|motivation|language review|pourquoi ce score)\b", normalized(line)):
            break
        if re.search(r"PROFILE\.md|\b(?:B2|C2)\b.*\bPROFILE\b", line, re.I):
            continue
        result.append(line)
    return "\n".join(result)[:60_000]


def normalized_heading(line):
    # Keep Markdown heading markers while removing accents for matching.
    value = unicodedata.normalize("NFKD", line)
    return "".join(c for c in value if not unicodedata.combining(c))


def english_rating(jd, advert_language=None):
    # Only this deterministic calculation is reused. Keep exact input digests,
    # not advert text, and copy results so callers cannot alter later reads.
    jd = str(jd or "")
    language = str(advert_language or "")
    key = (hashlib.sha256(jd.encode("utf-8", errors="surrogatepass")).digest(),
           hashlib.sha256(language.encode("utf-8", errors="surrogatepass")).digest())
    with _english_cache_lock:
        cached = _english_cache.get(key)
        if cached is not None:
            _english_cache.move_to_end(key)
            return dict(cached)
    result = _english_rating_uncached(jd, language)
    with _english_cache_lock:
        _english_cache[key] = dict(result)
        _english_cache.move_to_end(key)
        if len(_english_cache) > _ENGLISH_CACHE_LIMIT:
            _english_cache.popitem(last=False)
    return result


def _english_rating_uncached(jd, advert_language=None):
    body = advert_body(jd)
    plain = normalized(re.sub(r"https?://\S+", "", body))
    locale = normalized(advert_language)
    words = plain.split()
    english = sum(w in {"the","and","you","your","our","with","will","we","of","to","for","are","is","in","this","as","have","on"} for w in words)
    french = sum(w in {"le","la","les","de","des","du","et","vous","votre","nos","nous","pour","une","un","dans","au","aux","avec","est","en"} for w in words)
    known_french = locale in {"fr","french","francais","francaise"}
    known_english = locale in {"en","english","anglais","anglaise"}
    french_body = known_french or (french >= 8 and french > english * 1.25)
    english_body = known_english or (not known_french and english >= 8 and english > french * 1.4)
    def answer(score, reason):
        return {"score": score, "reason": reason, "basis": "saved_advert", "estimated": True}
    if known_english or english_body:
        return answer(5, "The saved advert is in English.")
    mentions = []
    for line in body.splitlines():
        value = normalized(line)
        if not re.search(r"\b(?:english|anglais|anglaise)\b", value):
            continue
        if re.search(r"\b(?:no|not|sans|aucun|aucune)\b.{0,35}\b(?:english|anglais)\b|\b(?:english|anglais)\b.{0,25}\b(?:not required|non requis|pas necessaire)\b", value):
            continue
        mentions.append(value)
    if any(re.search(r"\b(?:working language|language of work|langue de travail|langue de l equipe)\b.{0,35}\b(?:english|anglais)\b|\b(?:english|anglais)\b.{0,30}\b(?:working language|langue de travail)\b|\b(?:work|working|operate|operating)\s+(?:(?:mainly|mostly|primarily)\s+)?in english\b", line) for line in mentions):
        return answer(5, "The advert describes English as a working or team language.")
    optional = lambda line: bool(re.search(r"\b(?:bonus|plus|atout|apprecie|souhaite|souhaitee|nice to have|preferred|preference)\b", line))
    if any(not optional(line) and re.search(r"\b(?:fluent|fluency|bilingual|bilingue|courant|courante|c1|c2|maitrise|excellent|excellente)\b", line) for line in mentions):
        return answer(4, "Strong or fluent English is requested in the advert.")
    if mentions and all(optional(line) for line in mentions):
        return answer(2, "English is mentioned as a bonus or preference in the advert.")
    if mentions:
        return answer(3, "English is mentioned in the advert, without a clear English-first team signal.")
    if french_body:
        return answer(1, "The saved advert is in French and does not mention English.")
    return answer(None, "The saved advert does not provide enough language evidence.")


def company_rating(company):
    try:
        stamp = (COMPANY_FILE.stat().st_mtime_ns, COMPANY_FILE.stat().st_size)
        if stamp != _cache["stamp"]:
            document = json.loads(COMPANY_FILE.read_text(encoding="utf-8"))
            entries = {}
            for entry in document.get("entries", []):
                if not isinstance(entry, dict):
                    continue
                for name in [entry.get("company"), *entry.get("aliases", [])]:
                    if normalized(name):
                        entries[normalized(name)] = entry
            _cache.update(stamp=stamp, entries=entries)
        entry = _cache["entries"].get(normalized(company), {})
    except (OSError, ValueError, TypeError):
        entry = {}
    sources = []
    for source in entry.get("sources", []):
        try:
            url = urlsplit(source.get("url", ""))
            if url.scheme in {"http","https"} and url.hostname and not url.username and not url.password:
                sources.append({"label": str(source.get("label") or "Company source"), "url": source["url"]})
        except (ValueError, TypeError, AttributeError):
            pass
    return {"score": _score(entry.get("score")),
            "reason": entry.get("summary") or "No company check saved yet.",
            "basis": entry.get("source_kind") or "unknown", "estimated": True,
            "sources": sources, "checked_at": entry.get("checked_at")}


def role_ratings(role, jd, advert_language=None):
    return {"jd_english": english_rating(jd, advert_language),
            "profile_fit": {"score": _score(role.get("fit")), "reason": "The existing saved profile-fit assessment." if _score(role.get("fit")) else "No profile-fit assessment saved.", "basis": "saved_fit", "estimated": False},
            "company_english": company_rating(role.get("company"))}
