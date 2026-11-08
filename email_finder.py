"""Find email drawer (5 Nov 2026, Daksh): find a person's PROFESSIONAL email from public pages, then draft an
outreach email in Daksh's approved format. Runs the local `claude` CLI (Sonnet 5.5 by default, Haiku 5.5 optional)
with only WebSearch + WebFetch, in an empty temp folder, so it cannot read or change any file.
The script then checks every email itself (format, no free-mail, domain has mail servers, published emails really
appear on their source page) and lowers the confidence when a check fails. Nothing is ever sent."""
from __future__ import annotations

import html as _html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # .../Resources
PROMPTS = HERE / "prompts"
LOG_DIR = HERE / ".local/email-finder"
MODELS = {"sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
NAMES = {"sonnet": "Sonnet 5.5", "haiku": "Haiku 5.5"}
FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.fr", "hotmail.com", "hotmail.fr", "outlook.com",
             "outlook.fr", "live.com", "live.fr", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "gmx.com",
             "gmx.fr", "proton.me", "protonmail.com", "qq.com", "163.com", "126.com", "sina.com", "yandex.ru",
             "mail.ru", "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net", "bbox.fr", "web.de"}
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
RANK = {"low": 0, "medium": 1, "high": 2}


class FinderError(RuntimeError):
    pass


def _claude() -> str:
    found = shutil.which("claude")
    for cand in [found, "~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]:
        if cand and os.access(os.path.expanduser(cand), os.X_OK):
            return os.path.expanduser(cand)
    raise FinderError("The claude command was not found on this Mac. Install Claude Code, then retry.")


def _template(name: str) -> str:
    text = (PROMPTS / name).read_text(encoding="utf-8")
    return re.sub(r"^\s*<!--[\s\S]*?-->\s*", "", text)


def _run(prompt: str, model_key: str, tools: bool, timeout: int) -> dict:
    model = MODELS.get(model_key)
    if not model:
        raise FinderError("Pick Sonnet or Haiku.")
    cmd = [_claude(), "-p", "--model", model, "--output-format", "json"]
    cmd += ["--allowedTools", "WebSearch", "WebFetch"] if tools else []
    cmd += ["--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "Read", "Glob", "Grep", "Task", "Agent"]
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="studio-email-") as empty:
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=empty,
                                  env={**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        except subprocess.TimeoutExpired as exc:
            raise FinderError(f"{NAMES[model_key]} took longer than {timeout // 60} minutes. Try again.") from exc
    if proc.returncode != 0:
        raise FinderError(f"{NAMES[model_key]} failed: " + (proc.stderr or proc.stdout or "no output").strip()[:300])
    try:
        outer = json.loads(proc.stdout)
        text = str(outer.get("result") or "")
    except json.JSONDecodeError:
        outer, text = {}, proc.stdout
    if outer.get("is_error"):
        raise FinderError(f"{NAMES[model_key]} failed: " + text[:300])
    return {"text": text, "seconds": round(time.time() - started, 1), "model": NAMES[model_key],
            "cost_usd": outer.get("total_cost_usd")}


def _json_block(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise FinderError("The model did not return a readable result. Try again.")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise FinderError("The model's result was not valid JSON. Try again.") from exc


_mx_cache: dict = {}


def _has_mx(domain: str) -> bool | None:
    """True/False if `dig` could answer, None if the check could not run."""
    if domain in _mx_cache:
        return _mx_cache[domain]
    dig = shutil.which("dig") or ("/usr/bin/dig" if os.path.exists("/usr/bin/dig") else None)
    if not dig:
        return None
    try:
        out = subprocess.run([dig, "+short", "+time=4", "+tries=1", "MX", domain], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    _mx_cache[domain] = bool(out.strip())
    return _mx_cache[domain]


def _page_has(url: str, email: str) -> bool | None:
    """Does the public page really print this email? None when the page cannot be read (login wall, PDF, block)."""
    if not re.match(r"^https?://", url or "") or "linkedin.com" in url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (CV Studio email check)"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            if "html" not in (resp.headers.get("Content-Type") or "") and "text" not in (resp.headers.get("Content-Type") or ""):
                return None
            body = resp.read(3_000_000).decode("utf-8", errors="replace")
    except Exception:
        return None
    body = _html.unescape(body).lower().replace("[at]", "@").replace("(at)", "@").replace(" @ ", "@")
    return email.lower() in body


def _cap(conf: str, top: str) -> str:
    return conf if RANK.get(conf, 0) <= RANK[top] else top


def check(result: dict) -> dict:
    """Script layer: drop non-credible emails, add checks, adjust confidence."""
    kept, dropped = [], []
    for row in result.get("emails") or []:
        if not isinstance(row, dict):
            continue
        email = str(row.get("email") or "").strip().strip(".").lower()
        kind = str(row.get("kind") or "pattern").lower()
        conf = str(row.get("confidence") or "low").lower()
        conf = conf if conf in RANK else "low"
        sources = [s for s in (row.get("sources") or []) if isinstance(s, str) and s.startswith("http")]
        examples = [e for e in (row.get("pattern_examples") or []) if isinstance(e, dict) and EMAIL_RE.match(str(e.get("email") or "")) and str(e.get("url") or "").startswith("http")]
        if not EMAIL_RE.match(email):
            dropped.append({"email": email, "reason": "not a valid email"}); continue
        domain = email.split("@", 1)[1]
        if domain in FREE_MAIL:
            dropped.append({"email": email, "reason": "personal address (free mail), not professional"}); continue
        if not sources and not examples:
            dropped.append({"email": email, "reason": "no source link"}); continue
        checks = {}
        mx = _has_mx(domain)
        checks["domain_receives_mail"] = mx
        if mx is False:
            conf = "low"
        if kind == "published":
            seen = None
            for url in sources[:3]:
                seen = _page_has(url, email)
                if seen:
                    break
            checks["seen_on_source_page"] = seen
            if seen is False:
                conf = _cap(conf, "medium")
        elif kind == "pattern":
            same = [e for e in examples if str(e["email"]).lower().endswith("@" + domain)]
            checks["pattern_examples"] = len(same)
            if len(same) < 2:
                dropped.append({"email": email, "reason": "pattern guess with fewer than 2 real examples"}); continue
            conf = _cap(conf, "high" if len(same) >= 3 else "medium")
        else:
            kind = "generic"
            conf = _cap(conf, "medium")
        kept.append({"email": email, "kind": kind, "confidence": conf, "why": str(row.get("why") or "")[:300],
                     "sources": sources[:6], "pattern_examples": examples[:6], "checks": checks})
    kept.sort(key=lambda r: (r["kind"] == "generic", -RANK[r["confidence"]], r["kind"] != "published"))
    result["emails"] = kept
    result["dropped"] = dropped
    best = str(result.get("best") or "").lower()
    if not any(r["email"] == best and r["kind"] != "generic" for r in kept):
        best = next((r["email"] for r in kept if r["kind"] != "generic"), "")
    result["best"] = best
    return result


CONTACTS = LOG_DIR / "contacts.json"
STATUSES = ("found", "drafted", "emailed", "replied")
EDITABLE = {"role_id", "archived", "notes", "language", "last_subject", "last_body", "last_to", "linkedin_note", "pending_followup"}


def contact_key(query: str) -> str:
    m = re.search(r"linkedin\.com/in/([A-Za-z0-9_%-]+)", query or "")
    return ("li:" + m.group(1).lower()) if m else ("q:" + re.sub(r"\s+", " ", str(query or "").strip().lower())[:200])


def contacts() -> list:
    try:
        rows = json.loads(CONTACTS.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_contacts(rows: list) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONTACTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(CONTACTS)


def remember(result: dict) -> dict:
    """History (Daksh 5 Nov): everyone looked up, the email found, and whether he emailed them."""
    rows = contacts()
    key = contact_key(result.get("query", ""))
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    old = next((r for r in rows if r.get("key") == key), None)
    best = next((e for e in result.get("emails") or [] if e.get("email") == result.get("best")), {})
    p = result.get("person") or {}
    row = {**(old or {}), "key": key, "query": result.get("query", ""), "name": p.get("name", ""), "title": p.get("title", ""),
           "company": p.get("company", ""), "email": result.get("best", ""), "confidence": best.get("confidence", ""),
           "kind": best.get("kind", ""), "found_at": now, "model": result.get("model", ""), "result": result}
    row.setdefault("status", "found")
    row.setdefault("emailed_at", "")
    rows = [r for r in rows if r.get("key") != key] + [row]
    _save_contacts(rows)
    return row


def set_status(key: str, status: str, subject: str = "") -> dict:
    if status not in STATUSES:
        raise FinderError("Unknown status.")
    rows = contacts()
    row = next((r for r in rows if r.get("key") == key), None)
    if not row:
        raise FinderError("This person is not in the history.")
    if status == "drafted" and row.get("status") == "emailed":
        return row  # a new draft never hides that he already emailed them
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if status == "emailed":
        pend = row.pop("pending_followup", None)
        if pend and row.get("status") == "emailed":  # a follow-up was sent: keep the thread history
            row.setdefault("followups", []).append({"n": pend.get("n"), "at": now, "subject": pend.get("subject")})
        row.setdefault("first_emailed_at", now)
        row["emailed_at"] = now
    elif status == "replied":
        row["replied_at"] = now
    elif status == "found":
        row["emailed_at"] = ""
    row["status"] = status
    if status == "drafted":
        row["drafted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if subject:
            row["subject"] = subject
    _save_contacts(rows)
    return row


def update_contact(key: str, fields: dict) -> dict:
    rows = contacts()
    row = next((r for r in rows if r.get("key") == key), None)
    if not row:
        raise FinderError("This person is not in the history.")
    for k, v in (fields or {}).items():
        if k in EDITABLE:
            row[k] = v
    _save_contacts(rows)
    return row


def find(query: str, model_key: str = "sonnet") -> dict:
    query = str(query or "").strip()[:1500]
    if len(query) < 3:
        raise FinderError("Type a name, a LinkedIn link or a company first.")
    res = _run(_template("find-email.md").replace("{{query}}", query), model_key, tools=True, timeout=540)
    out = check(_json_block(res["text"]))
    out.update(query=query, model=res["model"], seconds=res["seconds"], cost_usd=res["cost_usd"])
    _log("find", out)
    out["contact"] = {k: v for k, v in remember(out).items() if k != "result"}
    return out


def signature() -> str:
    try:
        return (PROMPTS / "email-signature.html").read_text(encoding="utf-8")
    except OSError:
        return ""


def _job_block(role: dict | None) -> str:
    if not role:
        return ""
    return (f"Company: {role.get('company')}\nTitle: {role.get('title')}\n"
            f"Advert (excerpt):\n{str(role.get('jd') or '')[:5000]}\n"
            f"His cover letter for it:\n{str(role.get('cover_letter_text') or '')[:3000]}")


def _finish(body: dict, email: str, res: dict) -> dict:
    html_body = re.sub(r"(?i)<(?!/?(b|br)\b)[^>]+>", "", str(body.get("html") or "")).strip()
    if not html_body:
        raise FinderError("The draft came back empty. Try again.")
    full = (f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1d2a24;">{html_body}</div>'
            f"<br>{signature()}")
    plain = _html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"(?i)<br\s*/?>", "\n", html_body)))
    return {"to": email.lower(), "subject": str(body.get("subject") or "")[:200], "html": full, "body_html": html_body,
            "plain": plain + "",
            "linkedin_note": str(body.get("linkedin_note") or "")[:300],
            "facts_used": body.get("facts_used") or [], "model": res["model"], "seconds": res["seconds"]}


def draft(person: dict, email: str, note: str = "", language: str = "fr", model_key: str = "sonnet", key: str = "",
          role: dict | None = None) -> dict:
    if not isinstance(person, dict) or not EMAIL_RE.match(str(email or "")):
        raise FinderError("Find an email first.")
    profile = ""
    for p in (ROOT / "PROFILE.md", ROOT.parent / "PROFILE.md"):
        if p.is_file():
            profile = p.read_text(encoding="utf-8", errors="replace")[:60000]
            break
    who = json.dumps({k: person.get(k) for k in ("name", "title", "company", "location")}, ensure_ascii=False)
    lang = "French" if str(language).lower().startswith("fr") else "English"
    prompt = (_template("outreach-email.md").replace("{{person}}", who).replace("{{note}}", str(note or "")[:1500])
              .replace("{{language}}", lang).replace("{{job}}", _job_block(role) or "(none)").replace("{{profile}}", profile))
    res = _run(prompt, model_key, tools=False, timeout=240)
    out = _finish(_json_block(res["text"]), email, res)
    _log("draft", {"to": out["to"], "subject": out["subject"], "model": out["model"], "seconds": out["seconds"]})
    if key:
        try:
            update_contact(key, {"last_subject": out["subject"], "last_body": out["body_html"], "last_to": out["to"],
                                 "linkedin_note": out["linkedin_note"], "language": "fr" if lang == "French" else "en",
                                 **({"role_id": role.get("id")} if role and role.get("id") else {})})
            out["contact"] = {k: v for k, v in set_status(key, "drafted", out["subject"]).items() if k != "result"}
        except FinderError:
            pass
    return out


def followup(key: str, role: dict | None = None) -> dict:
    row = next((r for r in contacts() if r.get("key") == key), None)
    if not row or not row.get("last_body") or not row.get("email"):
        raise FinderError("Write and send a first email to this person before a follow-up.")
    n = len(row.get("followups") or []) + 1
    if n > 2:
        raise FinderError("Two follow-ups already sent. Better to move on.")
    sent = row.get("emailed_at") or row.get("drafted_at") or ""
    try:
        days = max(1, int((time.time() - time.mktime(time.strptime(sent[:19], "%Y-%m-%dT%H:%M:%S"))) // 86400))
    except ValueError:
        days = 5
    who = json.dumps({"name": row.get("name"), "title": row.get("title"), "company": row.get("company")}, ensure_ascii=False)
    prompt = (_template("outreach-followup.md").replace("{{person}}", who)
              .replace("{{language}}", "French" if row.get("language", "fr") == "fr" else "English")
              .replace("{{number}}", str(n)).replace("{{days}}", str(days)).replace("{{job}}", _job_block(role) or "(none)")
              .replace("{{previous_subject}}", str(row.get("last_subject") or ""))
              .replace("{{previous_body}}", _html.unescape(re.sub(r"<[^>]+>", " ", str(row.get("last_body") or "")))[:3000]))
    res = _run(prompt, "sonnet", tools=False, timeout=240)
    out = _finish(_json_block(res["text"]), row["email"], res)
    out["followup_number"] = n
    update_contact(key, {"pending_followup": {"n": n, "subject": out["subject"], "body": out["body_html"]}})
    _log("followup", {"to": out["to"], "n": n, "model": out["model"]})
    return out


def _log(kind: str, row: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / (time.strftime("%Y-%m-%d") + ".jsonl")).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind, **row}, ensure_ascii=False) + "\n")
    except OSError:
        pass
