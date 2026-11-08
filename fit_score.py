"""Offline profile-fit score, 0-100 with one decimal (Daksh, 26 Oct 2026). No AI calls.

Reads only the advert text of a saved JD.md, turns it into ~25 simple signals (CV keyword matches,
seniority words in the title, years asked, other languages, domain words...) and adds them up with
weights learned from Daksh's past 1-5 fit ratings and application outcomes (fit_weights.json).

    python3 Studio/fit_score.py train --data rows.json  # rebuild fit_weights.json from rated jobs
    python3 Studio/fit_score.py score PATH/JD.md # score one advert
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    from .jd_facts import languages, years
except ImportError:
    from jd_facts import languages, years

HERE = Path(__file__).resolve().parent
KEYWORDS = HERE / "static" / "jd-keywords.json"
WEIGHTS = HERE / "fit_weights.json"
MIN_BODY, TRAIN_MIN_BODY = 80, 300

FEATURE_LABELS = {
    "match_terms": "CV keywords in the advert",
    "match_in_title": "CV keywords in the job title",
    "gap_terms": "Known gaps asked (languages, tools, 7+ years)",
    "esg_title": "ESG / CSR / climate job title",
    "title_head": "Head / director title",
    "title_senior": "Senior / lead / manager title",
    "title_junior": "Officer / analyst / project-level title",
    "title_intern": "Internship or apprenticeship",
    "yrs_unknown": "No years of experience stated",
    "yrs_le3": "Asks 3 years or less",
    "yrs_4_5": "Asks 4-5 years",
    "yrs_6p": "Asks 6+ years",
    "junior_hint": "Junior / first-job wording",
    "other_lang": "Needs another language (not EN/FR)",
    "english_req": "English asked",
    "contract_cdd": "Fixed-term contract (CDD)",
    "contract_freelance": "Freelance",
    "len_log": "Advert length",
    "domain_finance": "Finance / responsible investment",
    "domain_engineering": "Engineering / HSE / QSE",
    "domain_reporting": "CSRD / ESG reporting",
    "domain_carbon": "Carbon / climate",
    "domain_supply": "Purchasing / supply chain",
    "domain_ai_data": "Data / AI / KPIs",
    "salary_high": "Salary 60k+",
}

_META = re.compile(r"^\s*\*\*[^*]{1,60}:\*\*")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)")
_DROP = re.compile(r"fit|score|pourquoi|screening|audit|adéquation|adequation|candidate|candidat|contact|gaps?\b|why |notes|evidence|preuve|stretch|review|decision|décision", re.I)
_ABOUT = re.compile(r"À propos de l[’']offre d[’']emploi|About the job")


def _rx(pattern: str) -> re.Pattern:
    return re.compile(r"(?<![\w])(?:" + pattern + r")(?![\w])", re.I)


RX = {
    "esg_title": _rx(r"rse|csr|esg|durab\w*|sustainab\w*|climat\w*|climate|carbone?|environnement\w*|environment\w*|décarbon\w*|decarbon\w*|transition|impact|ess|énergie|energy|biodiversit\w*|extra-financ\w*"),
    "title_head": _rx(r"head|director|directeur|directrice|vp|vice[- ]president|chief|cso|chef de service"),
    "title_senior": _rx(r"senior|sr\.?|lead|principal|confirmée?|experte?|manager"),
    "title_junior": _rx(r"junior|chargée?|charge de mission|assistante?|analyst|analyste|officer|coordinat\w*|consultante?|specialist|spécialiste|project|projet"),
    "title_intern": _rx(r"stage|stagiaire|intern|internship|alternance|alternant|apprenti|apprentice|v\.?i\.?e"),
    "contract_cdd": _rx(r"cdd|fixed[- ]term|temporary|intérim|interim|mission de \d+ mois"),
    "contract_freelance": _rx(r"freelance|indépendant|portage salarial"),
    "domain_finance": _rx(r"isr|investissement responsable|asset management|gestion d'actifs|actuari\w*|banque|banking|crédit|credit risk|private equity|fund"),
    "domain_engineering": _rx(r"ingénieur|engineer|génie|hse|qse|qhse|iso 14001|iso 45001|chantier|btp|bureau d'études|thermique|électrique|mécanique|chimie|laboratoire"),
    "domain_reporting": _rx(r"csrd|vsme|dpef|gri|esrs|reporting extra-financier|non-financial reporting|double matérialité|double materiality|cdp|ecovadis|taxonomie|taxonomy"),
    "domain_carbon": _rx(r"bilan carbone|scope 3|ghg|ges|carbon footprint|empreinte carbone|décarbonation|decarbonization|decarbonisation|net zero|sbti"),
    "domain_supply": _rx(r"achats responsables|responsible purchasing|procurement|supply chain|chaîne d'approvisionnement|fournisseurs|suppliers|logistique|logistics"),
    "domain_ai_data": _rx(r"ia|ai|data|données|power bi|dashboard|tableau de bord|kpi|automatisation|automation"),
}
_SALARY = re.compile(r"(?<!\d)(\d{2,3})(?:[ .,  ]?(000)(?:[.,]\d+)?|\s?[kK])\s?(?:€|eur|k€|euros)?", re.I)

_cache: dict = {}


def _keywords() -> tuple[re.Pattern, re.Pattern]:
    """One alternation per list (longest first), so a whole advert is scanned once per list."""
    if "kw" not in _cache:
        data = json.loads(KEYWORDS.read_text(encoding="utf-8"))
        pat = lambda terms: _rx("|".join(re.escape(t) for t in sorted({t.casefold() for t in terms if t.strip()}, key=len, reverse=True)))
        _cache["kw"] = (pat(data.get("match", [])), pat(data.get("gap", [])))
    return _cache["kw"]


def _distinct(pattern: re.Pattern, text: str) -> int:
    return len({m.group(0).casefold() for m in pattern.finditer(text)})


def clean_advert(md: str) -> tuple[str, str]:
    """(title, advert body) with metadata lines, fit/screening notes and LinkedIn chrome removed."""
    title, keep, drop_level = "", [], 0
    for line in md.splitlines():
        if not title and line.startswith("# "):
            title = line[2:].strip()
            continue
        head = _HEADING.match(line)
        if head:
            level = len(head.group(1))
            if drop_level and level > drop_level:
                continue
            drop_level = level if _DROP.search(head.group(2)) else 0
            if drop_level:
                continue
        elif drop_level or _META.match(line):
            continue
        keep.append(line)
    body = "\n".join(keep)
    if "Show more Show less" in body:
        body = body.split("Show more Show less", 1)[0]
    about = _ABOUT.search(body)
    if about:
        body = body[about.end():]
    return title, " ".join(body.split())


def features(title: str, body: str) -> dict[str, float]:
    match, gap = _keywords()
    low_title, both = title.lower(), f"{title}\n{body}"
    y = years(body)
    langs = languages(body)
    salary = any(int(m.group(1)) >= 60 for m in _SALARY.finditer(body) if int(m.group(1)) < 400)
    yrs = y["min"]
    x = {
        "match_terms": math.log1p(_distinct(match, body)),
        "match_in_title": float(min(3, _distinct(match, title))),
        "gap_terms": math.log1p(_distinct(gap, body)),
        "esg_title": float(bool(RX["esg_title"].search(low_title))),
        "title_head": float(bool(RX["title_head"].search(title))),
        "title_senior": float(bool(RX["title_senior"].search(title))),
        "title_junior": float(bool(RX["title_junior"].search(title))),
        "title_intern": float(bool(RX["title_intern"].search(title) or RX["title_intern"].search(body[:600]))),
        "yrs_unknown": float(yrs is None),
        "yrs_le3": float(yrs is not None and yrs <= 3),
        "yrs_4_5": float(yrs is not None and 4 <= yrs <= 5),
        "yrs_6p": float(yrs is not None and yrs >= 6),
        "junior_hint": float(bool(y["junior_hint"])),
        "other_lang": float(any(r["language"] not in ("English", "French") and r["level"] for r in langs)),
        "english_req": float(any(r["language"] == "English" for r in langs)),
        "contract_cdd": float(bool(RX["contract_cdd"].search(both))),
        "contract_freelance": float(bool(RX["contract_freelance"].search(both))),
        "len_log": math.log1p(len(body)) / 10,
        "salary_high": float(salary),
    }
    for name in ("domain_finance", "domain_engineering", "domain_reporting", "domain_carbon", "domain_supply", "domain_ai_data"):
        x[name] = float(bool(RX[name].search(body)))
    return {name: x[name] for name in FEATURE_LABELS}


def _weights() -> dict | None:
    try:
        mtime = WEIGHTS.stat().st_mtime_ns
    except OSError:
        return None
    if _cache.get("w_mtime") != mtime:
        _cache["w"], _cache["w_mtime"] = json.loads(WEIGHTS.read_text(encoding="utf-8")), mtime
    return _cache["w"]


def score_advert(md: str, weights: dict | None = None) -> dict | None:
    w = weights or _weights()
    if not w or not md:
        return None
    title, body = clean_advert(md)
    if len(body) < MIN_BODY:  # short summaries still score; trained only on full adverts (TRAIN_MIN_BODY)
        return None
    x = features(title, body)
    a, b = w["stretch"]["a"], w["stretch"]["b"]
    contrib = {}
    for name, mean, std, coef in zip(w["features"], w["mean"], w["std"], w["coef"]):
        contrib[name] = coef * (x.get(name, 0.0) - mean) / (std or 1.0)
    pred = a * (w["intercept"] + sum(contrib.values())) + b
    points = {k: round(v * a * 20, 1) for k, v in contrib.items() if round(v * a * 20, 1)}
    up = sorted((k for k in points if points[k] > 0), key=lambda k: -points[k])[:3]
    down = sorted((k for k in points if points[k] < 0), key=lambda k: points[k])[:3]
    return {"score": round(min(100.0, max(0.0, pred * 20)), 1), "out_of": 100, "basis": "offline_model",
            "estimated": True, "model_version": w.get("trained_at"),
            "factors": [{"feature": k, "label": FEATURE_LABELS[k], "points": points[k]}
                        for k in sorted(up + down, key=lambda k: -abs(points[k]))]}


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


def train(rows: list[dict], out: Path = WEIGHTS) -> dict:
    import numpy as np

    names = list(FEATURE_LABELS)
    X, y = [], []
    for row in rows:
        title, body = clean_advert(row["md"])
        if len(body) >= TRAIN_MIN_BODY:
            x = features(title, body)
            X.append([x[n] for n in names])
            y.append(float(row["y"]))
    X, y = np.array(X), np.array(y)
    mean, std = X.mean(0), X.std(0)
    std[std == 0] = 1.0
    Z = (X - mean) / std

    def fit(Zt, yt, lam):
        A = Zt.T @ Zt + lam * np.eye(Zt.shape[1])
        coef = np.linalg.solve(A, Zt.T @ (yt - yt.mean()))
        return coef, yt.mean()

    folds = np.random.RandomState(7).permutation(len(y)) % 5
    best = None
    for lam in (0.3, 1, 3, 10, 30, 100, 300):
        oof = np.zeros(len(y))
        for f in range(5):
            tr, te = folds != f, folds == f
            coef, icpt = fit(Z[tr], y[tr], lam)
            oof[te] = icpt + Z[te] @ coef
        mae = float(np.abs(oof - y).mean())
        if best is None or mae < best[0]:
            best = (mae, lam, oof)
    _, lam, oof = best
    a = float(y.std() / (oof.std() or 1.0))
    b = float(y.mean() - a * oof.mean())
    stretched = a * oof + b
    ry, ro = np.array(_rank(list(y))), np.array(_rank(list(stretched)))
    spearman = float(np.corrcoef(ry, ro)[0, 1])
    coef, icpt = fit(Z, y, lam)
    r6 = lambda v: [round(float(t), 6) for t in v]
    result = {"version": 1, "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "n": int(len(y)),
              "lambda": lam, "features": names, "mean": r6(mean), "std": r6(std), "coef": r6(coef),
              "intercept": round(float(icpt), 6), "stretch": {"a": round(a, 6), "b": round(b, 6)},
              "cv": {"mae": round(float(np.abs(stretched - y).mean()), 3), "mae_points": round(float(np.abs(stretched - y).mean()) * 20, 1),
                     "spearman": round(spearman, 3), "n": int(len(y))}}
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--data", required=True, help='JSON list of {"md": JD markdown, "y": target 1-5}')
    s = sub.add_parser("score")
    s.add_argument("file")
    args = ap.parse_args()
    if args.cmd == "train":
        print(json.dumps(train(json.loads(Path(args.data).read_text(encoding="utf-8")))["cv"]))
    else:
        print(json.dumps(score_advert(Path(args.file).read_text(encoding="utf-8")), ensure_ascii=False, indent=1))
