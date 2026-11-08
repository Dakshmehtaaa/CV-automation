"""JD highlighter keywords (23 Oct 2026).

Builds static/jd-keywords.json from the CV bank (Master/cv/bank.json) plus a short curated list,
so the Apply page can mark advert words that match Daksh's CV (and a few known gaps).
Run again after the bank changes:  python3 Studio/jd_keywords.py   (from Resources/)
Only bank facts are "match"; nothing here is written into any CV.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BANK = HERE.parent / "Master" / "cv" / "bank.json"
OUT = HERE / "static" / "jd-keywords.json"

# Tools the bank lists but the CV never shows (bank rule: "Python and SAP are never listed").
NEVER_CLAIM = {"python & r scripting", "python", "sap"}

# Domain words that line up with his experience (ESG reporting, ratings, carbon, procurement, AI, training).
CURATED_MATCH = [
    "ESG", "RSE", "CSR", "sustainability", "durabilité", "développement durable", "reporting extra-financier",
    "non-financial reporting", "double materiality", "double matérialité", "materiality", "matérialité",
    "carbon footprint", "bilan carbone", "Bilan Carbone", "Scope 3", "Scope 1", "Scope 2", "decarbonisation",
    "decarbonization", "décarbonation", "climate", "climat", "net zero", "net-zero", "VSME", "DPEF", "CSDDD",
    "ESG rating", "notation ESG", "supplier assessment", "évaluation fournisseurs", "achats responsables",
    "responsible purchasing", "supply chain", "chaîne d'approvisionnement", "logistics", "logistique",
    "AI", "IA", "GenAI", "IA générative", "generative AI", "artificial intelligence", "intelligence artificielle",
    "data", "données", "dashboard", "tableau de bord", "KPI", "KPIs", "indicateurs", "data analysis",
    "analyse de données", "automation", "automatisation", "training", "formation", "e-learning",
    "change management", "conduite du changement", "stakeholder", "parties prenantes", "consulting", "conseil",
    "project management", "gestion de projet", "chef de projet", "volunteering", "mécénat", "NGO", "ONG",
    "impact", "employee engagement", "engagement des collaborateurs",
]
LANGUAGE_MATCH = ["English", "Anglais", "anglais", "bilingual", "bilingue", "fluent English", "anglais courant",
                  "French", "Français", "français", "Hindi"]
# Things adverts ask for that the bank does not support: shown as a soft warning, never as a match.
GAPS = ["SAP", "Python", "German", "Allemand", "allemand", "Spanish", "Espagnol", "espagnol", "Italian", "Italien",
        "Mandarin", "Chinese", "chinois", "Dutch", "néerlandais", "CPA", "expert-comptable", "audit financier",
        "10 ans", "10 years", "8 ans", "8 years", "7 ans", "7 years", "senior manager", "habilitation"]


def build() -> dict:
    bank = json.loads(BANK.read_text(encoding="utf-8"))
    match: set[str] = set(CURATED_MATCH)
    for group in ("tools", "frameworks"):
        for item in bank.get(group, {}).get("items", []):
            for lang in ("EN", "FR"):
                name = str(item.get(lang) or "").strip()
                if not name or name.casefold() in NEVER_CLAIM:
                    continue
                match.add(name)
                # "ISO 14001 / 14064" and "Excel & SQL" also match their parts.
                for part in name.replace("&", "/").split("/"):
                    part = part.strip()
                    if len(part) >= 3 and part.casefold() not in NEVER_CLAIM:
                        match.add(part if not part.isdigit() else f"ISO {part}")
    for role in bank.get("roles", []):
        company = str(role.get("company") or "").strip() if isinstance(role, dict) else ""
        if company:
            match.add(company)
    languages = set(LANGUAGE_MATCH)
    for rows in bank.get("languages", {}).values():
        for row in rows:
            languages.add(str(row.get("name") or "").strip())
    languages.discard("")
    gaps = {g for g in GAPS if g.casefold() not in {m.casefold() for m in match}}
    order = lambda items: sorted(items, key=lambda x: (-len(x), x.casefold()))
    return {"version": 1, "source": "Master/cv/bank.json + curated list in Studio/jd_keywords.py",
            "match": order(match - languages), "language": order(languages), "gap": order(gaps)}


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {OUT.name}: {len(data['match'])} match, {len(data['language'])} language, {len(data['gap'])} gap terms")
