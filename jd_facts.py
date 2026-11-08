"""JD facts (23 Oct 2026): languages asked and years of experience, read from an advert (EN or FR).

The patterns were tuned on the saved adverts in Tailored CVs/*/JD.md. The same patterns are exported
to static/jd-keywords.json ("facts") so the Apply page runs them on every advert in the browser.

    python3 Studio/jd_facts.py            # scan all saved adverts, print what was found, update the JSON
    python3 Studio/jd_facts.py --show 20  # also print 20 example matches
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent / "Tailored CVs"
KEYWORDS = HERE / "static" / "jd-keywords.json"

# canonical language -> words adverts use (EN + FR)
LANGUAGES = {
    "English": ["english", "anglais", "anglaise", "toeic", "toefl", "ielts"],
    "French": ["french", "français", "française", "francais"],
    "German": ["german", "allemand", "allemande"],
    "Spanish": ["spanish", "espagnol", "espagnole"],
    "Italian": ["italian", "italien", "italienne"],
    "Portuguese": ["portuguese", "portugais"],
    "Dutch": ["dutch", "néerlandais", "neerlandais"],
    "Chinese": ["chinese", "mandarin", "chinois"],
    "Japanese": ["japanese", "japonais"],
    "Arabic": ["arabic", "arabe"],
    "Russian": ["russian", "russe"],
    "Hindi": ["hindi"],
}
LEVEL = r"(fluent|fluency|courant|bilingue|bilingual|native|maternelle|professional|professionnel|business|opérationnel|operational|lu,? écrit|written and spoken|[ABC][12])"
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "ten": 10,
             "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six ": 6, "sept": 7, "huit": 8, "dix": 10}
NUM = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|ten|une?|deux|trois|quatre|cinq|sept|huit|dix)"
# A range or a single number followed by years/ans, close to the word experience/expérience.
YEARS = re.compile(NUM + r"\s*(?:\+|plus)?\s*(?:(?:-|–|à|to|a)\s*" + NUM + r"\s*)?(?:years?|yrs|ans|années?)", re.I)
CONTEXT = re.compile(r"exp[ée]rience|experienced|exp\.|minimum|at least|au moins|similar role|poste similaire", re.I)
JUNIOR = re.compile(r"premi[èe]re exp[ée]rience|jeune dipl[ôo]m[ée]|d[ée]butant(?:e|\(e\))? accept[ée]|entry[- ]level|graduate|junior|alternance|stage|internship", re.I)


def _num(word: str) -> int | None:
    word = word.strip().lower()
    return int(word) if word.isdigit() else NUM_WORDS.get(word)


def languages(text: str) -> list[dict]:
    low = text.lower()
    found = []
    for name, words in LANGUAGES.items():
        hit = next((w for w in words if re.search(r"(?<![a-zà-ÿ])" + re.escape(w) + r"(?![a-zà-ÿ])", low)), None)
        if not hit:
            continue
        at = re.search(r"(?<![a-zà-ÿ])" + re.escape(hit), low).start()
        level = re.search(LEVEL, low[max(0, at - 15): at + 45], re.I)
        found.append({"language": name, "level": level.group(1) if level else ""})
    # Some job sites print a whole language picker (20 adverts list all 12). Then keep only languages
    # with a level word next to them, plus English and French.
    if len(found) > 5:
        found = [row for row in found if row["level"] or row["language"] in ("English", "French")]
    return found


def years(text: str) -> dict:
    """Smallest and largest years of experience asked, from numbers near 'experience'."""
    asks = []
    for m in YEARS.finditer(text):
        window = text[max(0, m.start() - 70): m.end() + 70]
        if not CONTEXT.search(window) or re.search(r"\b(ans?|years?) (?:old|d'âge|d'existence|de croissance)", window, re.I):
            continue
        low, high = _num(m.group(1)), _num(m.group(2)) if m.group(2) else None
        if low is None or low > 25:
            continue
        asks.append({"min": low, "max": high or low, "quote": " ".join(m.group(0).split())})
    junior = JUNIOR.search(text)
    return {"asks": asks, "min": min((a["min"] for a in asks), default=None),
            "max": max((a["max"] for a in asks), default=None), "junior_hint": junior.group(0) if junior else ""}


def scan(show: int = 0) -> dict:
    files = sorted(CORPUS.glob("*/JD.md"))
    langs, spans, quotes, none = Counter(), Counter(), Counter(), 0
    for i, path in enumerate(files):
        text = path.read_text(encoding="utf-8", errors="replace")
        for row in languages(text):
            langs[row["language"]] += 1
        y = years(text)
        if y["min"] is None:
            none += 1
        else:
            spans[f"{y['min']}-{y['max']}" if y["max"] != y["min"] else f"{y['min']}+"] += 1
            for a in y["asks"]:
                quotes[a["quote"].lower()] += 1
        if i < show and y["asks"]:
            print(f"- {path.parent.name[:60]}: {[a['quote'] for a in y['asks']]} {y['junior_hint']}")
    return {"adverts": len(files), "languages": dict(langs.most_common()), "years": dict(spans.most_common(15)),
            "no_years": none, "top_phrases": dict(quotes.most_common(15))}


def export() -> None:
    data = json.loads(KEYWORDS.read_text(encoding="utf-8")) if KEYWORDS.exists() else {}
    data["facts"] = {"languages": LANGUAGES, "level": LEVEL, "num": NUM, "num_words": NUM_WORDS,
                     "years": YEARS.pattern, "context": CONTEXT.pattern, "junior": JUNIOR.pattern}
    KEYWORDS.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    show = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 0
    print(json.dumps(scan(show), ensure_ascii=False, indent=1))
    export()
    print(f"Updated {KEYWORDS.name} with the facts patterns.")
