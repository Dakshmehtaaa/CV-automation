"""Company icons (23 Oct 2026): pick an industry icon for companies that have no logo yet.

Reads every role folder in Tailored CVs (folder name "Company - Title - …" + JD.md), scores industry
keywords (company name counts 5x, advert text 1x) and writes static/company-icons.json:
  {"<company key>": {"industry": "defense", "hue": 212}, ...}
The Apply list shows the icon on an industry colour; with no clear industry it shows initials on a
colour picked from the name, so every row still looks different. Real logos always win.
Run again any time:  python3 Studio/company_icons.py
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROLES = HERE.parent / "Tailored CVs"
OUT = HERE / "static" / "company-icons.json"

INDUSTRIES = {
    "defense": ["thales", "safran", "mbda", "dassault", "naval group", "nexter", "knds", "arquus", "défense", "defense",
                "armement", "military", "militaire", "aerospace", "aéronautique", "aeronautique", "spatial", "airbus"],
    "luxury": ["luxe", "luxury", "lvmh", "kering", "hermès", "hermes", "chanel", "lacoste", "dior", "cartier", "fashion",
               "mode ", "joaillerie", "maroquinerie", "prêt-à-porter"],
    "fmcg": ["fmcg", "grande consommation", "biens de consommation", "consumer goods", "retail", "distribution",
             "cosmétique", "cosmetics", "beauté", "agroalimentaire", "food", "alimentaire", "boissons", "danone",
             "nestlé", "nestle", "l'oréal", "loreal", "unilever", "carrefour", "auchan", "andros", "pernod", "lactalis"],
    "energy": ["énergie", "energie", "energy", "engie", "edf", "totalenergies", "renouvelable", "renewable", "solaire",
               "solar", "éolien", "wind", "pétrole", "oil", "gaz", "gas", "hydrogène", "nucléaire", "maurel"],
    "finance": ["banque", "bank", "bnp", "crédit agricole", "credit agricole", "société générale", "natixis", "bpce",
                "bpifrance", "bip ", "assurance", "insurance", "axa", "ag2r", "allianz", "asset management",
                "gestion d'actifs", "investissement", "investment", "finance", "fintech", "mutuelle"],
    "consulting": ["cabinet", "conseil", "consulting", "consultant", "advisory", "deloitte", "pwc", "kpmg", "ey ",
                   "accenture", "capgemini", "wavestone", "sia partners", "bearingpoint", "mckinsey", "bcg"],
    "logistics": ["logistique", "logistics", "supply chain", "transport", "ceva", "cma cgm", "sncf", "transdev",
                  "fret", "freight", "maritime", "shipping", "airline", "aéroport", "ferroviaire", "rail", "geodis"],
    "tech": ["software", "logiciel", "saas", "tech", "digital", "numérique", "cloud", "startup", "start-up",
             "plateforme", "platform", "data", "intelligence artificielle", "dataiku", "it services"],
    "public": ["nations unies", "united nations", "unep", "onu", "ong", "ngo", "association", "fondation",
               "foundation", "ministère", "ministry", "collectivité", "public", "mairie", "région", "agence publique",
               "programme", "humanitaire", "non-profit", "sans but lucratif"],
    "climate": ["climat", "climate", "carbone", "carbon", "environnement", "environment", "biodiversité", "eco",
                "écologie", "transition écologique", "décarbonation", "bilan carbone", "durable"],
    "construction": ["bâtiment", "batiment", "construction", "btp", "immobilier", "real estate", "vinci", "bouygues",
                     "eiffage", "architecture", "génie civil", "infrastructure"],
    "health": ["santé", "sante", "health", "pharma", "pharmaceutique", "hôpital", "hopital", "hospital", "sanofi",
               "medical", "médical", "biotech", "laboratoire", "clinique"],
    "industry": ["industrie", "industrial", "manufacturing", "usine", "automobile", "automotive", "chimie",
                 "chemical", "matériaux", "materials", "acier", "steel", "atalian", "apave", "afnor", "certification"],
    "media": ["média", "media", "communication", "éditorial", "editorial", "agence", "agency", "publicité",
              "advertising", "presse", "journal", "wordappeal"],
}
# Industry colour (hue). The Apply page draws the icon on this colour.
HUES = {"defense": 212, "luxury": 42, "fmcg": 330, "energy": 48, "finance": 160, "consulting": 255, "logistics": 25,
        "tech": 190, "public": 200, "climate": 140, "construction": 30, "health": 0, "industry": 220, "media": 290}


def key(company: str) -> str:
    """Same as companyKey() in app.js."""
    text = unicodedata.normalize("NFKD", company)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def classify(company: str, advert: str) -> str | None:
    name, body = f" {company.lower()} ", f" {advert[:4000].lower()} "
    scores = {}
    for industry, words in INDUSTRIES.items():
        score = sum(5 for w in words if w in name) + min(6, sum(body.count(w) for w in words))
        if score:
            scores[industry] = score
    if not scores:
        return None
    # Almost every advert talks about carbon/ESG, so "climate" only wins when nothing else is clear.
    if "climate" in scores and not any(w in name for w in INDUSTRIES["climate"]):
        others = {k: v for k, v in scores.items() if k != "climate" and v >= 3}
        if others:
            scores = others
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else None


def build() -> dict:
    result = {}
    for folder in sorted(p for p in ROLES.iterdir() if p.is_dir() and not p.name.startswith(".")):
        company = folder.name.split(" - ")[0].strip()
        advert = (folder / "JD.md").read_text(encoding="utf-8", errors="replace") if (folder / "JD.md").is_file() else ""
        k = key(company)
        if not k or k in result and result[k].get("industry"):
            continue
        industry = classify(company, advert)
        hue = HUES[industry] if industry else int(hashlib.sha1(k.encode()).hexdigest()[:4], 16) % 360
        result[k] = {"industry": industry, "hue": hue}
    return result


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    counts = {}
    for row in data.values():
        counts[row["industry"] or "colour only"] = counts.get(row["industry"] or "colour only", 0) + 1
    print(f"Wrote {OUT.name}: {len(data)} companies", json.dumps(counts, ensure_ascii=False))
