"""Hand one job's contact search to Claude in Chrome running Haiku 5.5.

Studio cannot type into the Claude in Chrome side panel, so it fills the saved
prompt for the job, puts it on the Mac clipboard and opens the advert in Google
Chrome. The wording lives in prompts/find-contact-haiku.md and can be edited
without touching code. Nothing is sent and no contact is saved by this step.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "find-contact-haiku.md"
MODEL = "Haiku 5.5"
CHROME = "Google Chrome"


def _http_url(value) -> str:
    try:
        parts = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    return parts.geturl() if parts.scheme in {"http", "https"} and parts.netloc else ""


def advert_url(role: dict) -> str:
    application = role.get("application") or {}
    for value in (application.get("apply_url"), role.get("source_url"), role.get("url")):
        url = _http_url(value)
        if url:
            return url
    return ""


def fill_prompt(role: dict, template: str | None = None) -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8") if template is None else template
    text = re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.S)
    values = {
        "company": str(role.get("company") or "").strip() or "not recorded",
        "title": str(role.get("title") or "").strip() or "not recorded",
        "location": str(role.get("location_label") or role.get("location") or "").strip() or "not recorded",
        "advert_url": advert_url(role) or "no advert link saved",
    }
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda match: values.get(match[1], match[0]), text).strip() + "\n"


def start_url(role: dict) -> str:
    url = advert_url(role)
    if url:
        return url
    company = str(role.get("company") or "").strip()
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{company} recruiter".strip())


def hand_off(role: dict, *, run=subprocess.run, platform: str = sys.platform) -> dict:
    prompt = fill_prompt(role)
    url = start_url(role)
    copied = opened = False
    if platform == "darwin":
        try:
            copied = run(["pbcopy"], input=prompt.encode("utf-8"), timeout=5, check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            copied = False
        try:
            opened = run(["open", "-a", CHROME, url], timeout=10, check=False, capture_output=True).returncode == 0
        except (OSError, subprocess.SubprocessError):
            opened = False
    return {"prompt": prompt, "url": url, "copied": copied, "opened": opened, "model": MODEL,
            "prompt_file": "Studio/prompts/" + PROMPT_PATH.name}
