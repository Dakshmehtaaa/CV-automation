"""Local application choices, separate from saved pipeline and tracker facts.

The store never submits an application, writes a tracker, or moves a role folder.
``ready`` is a local organising status; it is not a CV or live-ad approval.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import http.client
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
from typing import Any
import unicodedata
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlsplit, urlunsplit
import uuid

from data import StudioData, StudioDataError


_STATES = ("ready", "applied", "inactive", "saved", "contacting",
           "interviewing", "offer", "rejected", "withdrawn")
_LABELS = {"ready": "Ready to apply", "applied": "Applied", "inactive": "Inactive",
           "pending": "Review first", "saved": "Saved for later",
           "contacting": "Contact to find", "interviewing": "Interviewing",
           "offer": "Offer", "rejected": "Rejected", "withdrawn": "Withdrawn"}
_ID = re.compile(r"[a-f0-9]{24}")
_CONTACT_JOB_ID = re.compile(r"[a-f0-9]{16}")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+", re.I)
_FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
                       "live.com", "icloud.com", "proton.me", "protonmail.com"}
_DETERMINISTIC_CONTACT_REVIEW = "deterministic-public-source-check-v1"
_GENERIC_SERVICE_LABELS = (
    re.compile(r"\b(?:customer|client)\s+(?:service|support|care)\b"),
    re.compile(r"\b(?:service|support)\s+(?:client|customer|clientele)\b"),
    re.compile(r"\bafter\s+sales(?:\s+(?:service|support))?\b"),
    re.compile(r"\bpost\s+sales?(?:\s+(?:service|support))?\b"),
    re.compile(r"\bservice\s+apres\s+vente\b|\bsav\b"),
    re.compile(r"\b(?:technical|technique)\s+(?:service|support)\b"),
    re.compile(r"\b(?:service|support)\s+technique\b"),
)
_MOTIVATION_LANGUAGES = {"en": "English", "fr": "Français"}
_MOTIVATION_MIN_CHARS = 380
_MOTIVATION_MAX_CHARS = 600
_MAX_STATUS_HISTORY = 1000
_MAX_EMAIL_REJECTIONS = 500
_MAX_REJECTION_QUOTE_CHARS = 1000
_CREDENTIAL_KEYS = {"access_token", "refresh_token", "id_token", "auth_token", "token",
                    "password", "passwd", "secret", "api_key", "apikey", "sessionid",
                    "session_id", "authorization", "credential", "signature"}


class _PublicPage(HTMLParser):
    """Collect only public page text and links; never execute page content."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[str] = []
        self.contexts: list[dict] = []
        self.title = ""
        self._in_title = False
        self._ignored_depth = 0
        self._context_stack: list[dict] = []
        self._element_stack: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "template", "svg"}:
            self._ignored_depth += 1
        if tag == "title":
            self._in_title = True
        identity_container = bool(re.search(
            r"(?:^|[-_ ])(?:person|profile|contact|member|employee|recruit(?:er|ing)?|hiring|talent|staff|card|leader|service|support|customer|client)(?:$|[-_ ])",
            " ".join(str(attrs.get(key) or "") for key in ("class", "id", "itemprop", "role")).casefold()))
        frame = None
        if not self._ignored_depth and (tag in {"li", "article", "address", "tr"}
                                        or (tag in {"div", "section"} and identity_container)):
            # A nested identity card is a narrower proof boundary. Mark every
            # ancestor frame so an article/list wrapper cannot inherit the
            # sibling cards' links when it closes.
            for ancestor in self._context_stack:
                ancestor["has_child_identity"] = True
            frame = {"tag": tag, "text": [], "links": [],
                     "identity_frame": identity_container or tag in {"li", "address", "tr"}}
            self._context_stack.append(frame)
        self._element_stack.append({"tag": tag, "context": frame})
        if not self._ignored_depth and tag == "a" and attrs.get("href"):
            href = html.unescape(attrs["href"])
            self.links.append(href)
            for context in self._context_stack:
                context["links"].append(href)
        if not self._ignored_depth and (tag == "br" or tag in {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3"}):
            self.text.append(" ")

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "template", "svg"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        if tag == "title":
            self._in_title = False
        closing = None
        for index in range(len(self._element_stack) - 1, -1, -1):
            if self._element_stack[index]["tag"] == tag:
                closing = self._element_stack[index:]
                del self._element_stack[index:]
                break
        if closing:
            for element in reversed(closing):
                context = element.get("context")
                if context is None:
                    continue
                for index in range(len(self._context_stack) - 1, -1, -1):
                    if self._context_stack[index] is context:
                        self._context_stack.pop(index)
                        break
                text = " ".join(" ".join(context["text"]).split())
                links = list(dict.fromkeys(context["links"]))
                # Keep person-level containers only; broad wrappers and the footer
                # cannot associate an unrelated LinkedIn link with this person.
                profile_links = {Applications._canonical_link(item) for item in links
                                 if isinstance(item, str) and Applications._valid_linkedin(item)}
                email_values = {item.casefold() for item in _EMAIL.findall(text)}
                ambiguous_identity = len(profile_links) > 1 or len(email_values) > 1
                if (text and len(text) <= 1200 and not context.get("has_child_identity")
                        and not ambiguous_identity):
                    self.contexts.append({"tag": context["tag"], "text": text, "links": links,
                                          "identity_frame": context.get("identity_frame") is True})
        if not self._ignored_depth and tag in {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3"}:
            self.text.append(" ")

    def handle_data(self, data):
        if self._in_title and not self._ignored_depth:
            self.title += data
        # Exclude scripts, styles and templates from the source evidence corpus.
        if not self._ignored_depth:
            self.text.append(data)
            for context in self._context_stack:
                context["text"].append(data)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS with DNS already resolved and checked by the caller."""

    def __init__(self, hostname: str, pinned: tuple, port: int = 443, timeout: float = 8):
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self.pinned_family, self.pinned_socktype, self.pinned_proto, self.pinned_sockaddr = pinned

    def connect(self):
        # Use the exact checked sockaddr; do not perform a second DNS lookup here.
        sock = socket.socket(self.pinned_family, self.pinned_socktype, self.pinned_proto)
        try:
            if self.timeout is not None:
                sock.settimeout(self.timeout)
            if self.source_address:
                sock.bind(self.source_address)
            sock.connect(self.pinned_sockaddr)
            # Keep the URL hostname for SNI and the default certificate hostname check.
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def safe_application_url(value: Any) -> str | None:
    """Return the exact source URL only when it can safely be shown as a link.

    Do not find a URL inside prose or guess a site's application endpoint. Reject
    credentials rather than stripping them and changing the source link.
    """
    if not isinstance(value, str) or not value or len(value) > 8192:
        return None
    decoded = unquote(value)
    if any(c.isspace() for c in value) or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        return None
    if any(c in decoded for c in '\\<>"`'):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme.lower() not in ("http", "https") or not host
                or parsed.username is not None or parsed.password is not None):
            return None
        # Accessing port validates malformed and out-of-range port values.
        parsed.port
        if "%" in host or "@" in host:
            return None
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True) + parse_qsl(parsed.fragment, keep_blank_values=True):
            if key.casefold().replace("-", "_") in _CREDENTIAL_KEYS:
                return None
    except (ValueError, UnicodeError):
        return None
    return value


class Applications:
    """Decorate Studio roles and save reversible choices under Studio/.local.

    Accept either an existing ``StudioData`` instance or its workspace root.
    Reads create no directories. Saves share Studio's thread and process lock.
    A role's revision changes only when its local choice or inherited facts do;
    saving a different role does not invalidate an open card.
    """

    def __init__(self, root: Path | StudioData, *, evidence_reader=None):
        self.data = root if isinstance(root, StudioData) else StudioData(root)
        self.path = self.data.local / "applications.json"
        self.motivation_path = self.data.local / "application-motivations.json"
        self.contacts_path = self.data.local / "application-contacts.json"
        self.evidence_reader = evidence_reader or self._fetch_public_page

    @staticmethod
    def status_options() -> list[dict[str, str]]:
        return [{"id": key, "label": _LABELS[key]} for key in _STATES]

    @staticmethod
    def normalize_language(language: Any) -> str:
        value = str(language or "").strip().casefold()
        if value in {"en", "eng", "english", "anglais", "anglaise"}:
            return "en"
        if value in {"fr", "fra", "français", "francais", "french", "française", "francaise"}:
            return "fr"
        raise StudioDataError("Choose English or French for the motivation copy.")

    @staticmethod
    def _language_from_text(value: str, fallback: str = "en") -> str:
        text = value.casefold()
        fr = len(re.findall(r"\b(?:le|la|les|des|du|une|un|je|j’ai|j'ai|chez|votre|vos|nous|pour|dans|avec|est|au|aux|cette|ce|mon|ma|mes)\b", text))
        en = len(re.findall(r"\b(?:the|a|an|i|my|at|for|with|your|our|we|this|role|company|it|to|from)\b", text))
        if fr >= 3 and fr > en * 1.25:
            return "fr"
        if en >= 3 and en > fr * 1.25:
            return "en"
        return fallback

    @classmethod
    def parse_motivation_document(cls, content: str, fallback: str = "en") -> dict[str, str]:
        """Read motivation text while excluding evidence and notes sections."""
        result: dict[str, list[str]] = {"en": [], "fr": []}
        current = None
        unlabelled: list[str] = []
        for raw in content.replace("\r", "").splitlines():
            line = raw.strip()
            if not line:
                continue
            heading = line.lstrip("#").strip().strip("*_` ")
            label = re.sub(r"[*_`:]", " ", heading).casefold().strip()
            if line.startswith("#") and re.match(
                    r"^(?:sources?|references?|références?|notes?|evidence|preuves?|citations?|bibliograph(?:y|ie)|profile evidence|cv evidence|supporting facts)(?:\b|\s|:)",
                    label):
                break
            if label in {"why this company", "motivation", "motivation letter"}:
                continue
            if re.match(r"^(?:english|anglais|en)(?:\s|$|[-(])", label) or re.search(r"\b(?:english|anglais|\ben\b)\s*(?:version|motivation|copy)\b", label):
                current = "en"
                continue
            if re.match(r"^(?:français|francais|french|fr)(?:\s|$|[-(])", label) or re.search(r"\b(?:français|francais|french|\bfr\b)\s*(?:version|motivation|copy)\b", label):
                current = "fr"
                continue
            if re.match(r"^(?:why\s+in\s+english\b|motivation in english\b)", label):
                current = "en"
                continue
            if re.match(r"^(?:pourquoi\b|motivation en français\b|motivation en francais\b)", label):
                current = "fr"
                continue
            # Accept inline labels such as "EN: ..." and "Français: ...".
            inline = re.match(r"^(?:\*\*)?(EN|English|Anglais|FR|French|Français|Francais)(?:\*\*)?\s*[:–—-]\s*(.+)$", line, re.I)
            if inline:
                current = "fr" if inline.group(1).casefold() in {"fr", "french", "français", "francais"} else "en"
                result[current].append(inline.group(2).strip())
                continue
            if line.startswith("#") or line.startswith(">") or line.startswith("---"):
                continue
            if (re.match(r"^(?:https?://|www\.)", line, re.I)
                    or re.match(r"^(?:source|sources|reference|references|note|evidence|preuve|référence)\s*:", line, re.I)
                    or re.match(r"^\[\d+\]", line)):
                continue
            clean = re.sub(r"^[-*+]\s+", "", line).strip()
            clean = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", clean)
            clean = re.sub(r"https?://\S+|www\.\S+", "", clean, flags=re.I).strip()
            if current:
                if clean:
                    result[current].append(clean)
            else:
                if clean:
                    unlabelled.append(clean)
        if unlabelled:
            guessed = cls._language_from_text(" ".join(unlabelled), fallback)
            result[guessed].extend(unlabelled)
        return {key: " ".join(part for part in value if part).strip() for key, value in result.items()}

    def contact_packet(self, role_id: str) -> dict:
        """Return only the selected public job packet, never PROFILE or other roles."""
        role, _folder = self._application_path(role_id)
        return {"role_id": role_id, "company": str(role.get("company") or "")[:200],
                "title": str(role.get("title") or "")[:300], "location": str(role.get("location") or "")[:200],
                "source_url": safe_application_url(role.get("source_url")),
                "jd": str(role.get("jd") or "")[:16000]}

    def _application_path(self, role_id: str) -> tuple[dict, Path]:
        if not isinstance(role_id, str) or not _ID.fullmatch(role_id):
            raise StudioDataError("This role folder was not found.", "not_found", 404)
        role = self.data.role(role_id)
        folder = self.data._safe(self.data.role_root / role["folder"].removeprefix("Tailored CVs/"), self.data.role_root)
        return role, folder

    def _safe_local_file(self, path: Path) -> None:
        if ((self.data.root / "Studio").is_symlink() or self.data.local.is_symlink()
                or path.is_symlink()):
            raise StudioDataError("The local application data folder must use regular files and folders.", "unsafe_path", 403)
        self.data._safe(path, self.data.local)

    def _load_local_records(self, path: Path, collection: str) -> tuple[dict, bytes | None]:
        self._safe_local_file(path)
        try:
            if not path.exists():
                return {"version": 1, collection: {}}, None
            if not path.is_file() or path.stat().st_size > 10_000_000:
                raise ValueError("Invalid local application data file")
            raw = path.read_bytes()
            value = json.loads(raw)
            if (not isinstance(value, dict) or type(value.get("version")) is not int
                    or value["version"] != 1 or not isinstance(value.get(collection), dict)):
                raise ValueError("Invalid local application data schema")
            return value, raw
        except (OSError, ValueError, UnicodeError, TypeError) as exc:
            if isinstance(exc, OSError):
                raise StudioDataError("Local application data could not be read.", "application_store_unavailable", 503) from exc
            raise StudioDataError("Local application data is unreadable. Its saved contents were preserved.",
                                  "invalid_application_store", 409) from exc

    @staticmethod
    def _encode_json(value: dict) -> bytes:
        return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")

    def _backup_local_data(self, path: Path, raw: bytes | None, category: str) -> str | None:
        if raw is None:
            return None
        base = self.data.local / "backups"
        directory = base / category
        if base.is_symlink() or directory.is_symlink():
            raise StudioDataError("The local application backup folder is unsafe.", "unsafe_path", 403)
        self.data._safe(directory, self.data.local)
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        base.chmod(0o700)
        directory.chmod(0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = directory / f"{stamp}-{uuid.uuid4().hex}.json"
        self.data._atomic(backup, raw)
        return str(backup.relative_to(self.data.root))

    def _why_document(self, role: dict) -> tuple[str, str | None]:
        document = next((row for row in role.get("documents", [])
                         if isinstance(row, dict) and row.get("title") == "Why this company"), None)
        content = document.get("content") if document else ""
        return (content if isinstance(content, str) else "",
                hashlib.sha256(content.encode("utf-8")).hexdigest() if isinstance(content, str) and content else None)

    def _profile_evidence(self, role: dict, why_source: str) -> tuple[list[dict], str | None]:
        path = self.data.root / "Master/cv/bank.json"
        raw = self.data._text(path, 2_000_000)
        if not raw:
            return [], None
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        try:
            bank = json.loads(raw)
        except (ValueError, TypeError):
            return [], digest
        if not isinstance(bank, dict) or not isinstance(bank.get("roles"), list):
            return [], digest
        context = " ".join((str(role.get("company") or ""), str(role.get("title") or ""),
                            str(role.get("jd") or ""), why_source))
        words = {word for word in re.findall(r"[a-zà-öø-ÿ][a-zà-öø-ÿ0-9+.-]*", context.casefold())
                 if len(word) >= 3 and word not in {"the", "and", "with", "for", "from", "the", "les", "des", "une", "dans", "pour", "avec", "chez", "sur", "aux", "est", "par", "who", "which", "that", "this", "your", "our", "you"}}
        choices: list[tuple[int, dict]] = []
        for group in bank["roles"]:
            if not isinstance(group, dict):
                continue
            for bullet in group.get("bullets", []) if isinstance(group.get("bullets"), list) else []:
                if not isinstance(bullet, dict):
                    continue
                en, fr = bullet.get("EN"), bullet.get("FR")
                if not isinstance(en, str) or not isinstance(fr, str):
                    continue
                evidence_words = {word for word in re.findall(r"[a-zà-öø-ÿ][a-zà-öø-ÿ0-9+.-]*", (en + " " + fr).casefold()) if len(word) >= 3}
                score = len(words & evidence_words)
                if score:
                    choices.append((score, {"id": str(bullet.get("id") or ""), "EN": en, "FR": fr,
                                            "source": str(bullet.get("source") or "Approved CV bank")}))
        choices.sort(key=lambda item: (-item[0], item[1]["id"]))
        return [item for _, item in choices[:8]], digest

    def motivation_packet(self, role_id: str, language: Any) -> dict:
        language = self.normalize_language(language)
        role, _folder = self._application_path(role_id)
        why, why_hash = self._why_document(role)
        try:
            fallback = self.normalize_language(role.get("language"))
        except StudioDataError:
            fallback = "en"
        parsed = self.parse_motivation_document(why, self._language_from_text(why, fallback))
        base_language = language if parsed.get(language) else ("fr" if parsed.get("fr") else "en")
        source = parsed.get(base_language) or ""
        evidence, profile_hash = self._profile_evidence(role, why)
        return {"role_id": role_id, "language": language, "company": str(role.get("company") or "")[:200],
                "title": str(role.get("title") or "")[:300], "location": str(role.get("location") or "")[:200],
                "source_url": safe_application_url(role.get("source_url")),
                "jd": str(role.get("jd") or "")[:24000], "existing_motivation": source[:12000],
                "existing_language": base_language if source else None,
                "profile_evidence": evidence, "source_hashes": {"why_this_company": why_hash,
                    "jd": hashlib.sha256(str(role.get("jd") or "").encode("utf-8")).hexdigest(),
                    "profile_bank": profile_hash},
                "source_path": "Tailored CVs/" + role["folder"].removeprefix("Tailored CVs/") + "/why-this-company.md"}

    def _motivation_source(self, role: dict, language: str) -> dict | None:
        why, _ = self._why_document(role)
        if not why:
            return None
        try:
            fallback = self.normalize_language(role.get("language"))
        except StudioDataError:
            fallback = "en"
        fallback = self._language_from_text(why, fallback)
        value = self.parse_motivation_document(why, fallback).get(language, "")
        if not value:
            return None
        return {"text": value, "source": "why-this-company.md", "saved": True,
                "provenance": {"source_path": "Tailored CVs/" + role["folder"].removeprefix("Tailored CVs/") + "/why-this-company.md",
                               "source_sha256": hashlib.sha256(why.encode("utf-8")).hexdigest(),
                               "language": language}}

    def _load_motivations(self) -> tuple[dict, bytes | None]:
        store, raw = self._load_local_records(self.motivation_path, "roles")
        for role_id, languages in store["roles"].items():
            if (not isinstance(role_id, str) or not _ID.fullmatch(role_id) or not isinstance(languages, dict)):
                raise StudioDataError("Local motivation records are unreadable and were preserved.", "invalid_application_store", 409)
            for language, record in languages.items():
                if language not in _MOTIVATION_LANGUAGES or not isinstance(record, dict):
                    raise StudioDataError("Local motivation records are unreadable and were preserved.", "invalid_application_store", 409)
                if (not isinstance(record.get("text"), str) or not isinstance(record.get("provenance"), dict)
                        or not isinstance(record.get("updated_at"), str)):
                    raise StudioDataError("Local motivation records are unreadable and were preserved.", "invalid_application_store", 409)
        return store, raw

    @staticmethod
    def validate_motivation_text(text: Any, language: str) -> str:
        if (not isinstance(text, str) or "\x00" in text or any(ord(char) < 32 and char not in "\n\t" for char in text)
                or len(text.encode("utf-8")) > 20_000):
            raise StudioDataError("The generated motivation was empty or unsafe to save.", "invalid_motivation", 422)
        clean = re.sub(r"[\t\n\r ]+", " ", html.unescape(text)).strip().strip('"“”`')
        if not _MOTIVATION_MIN_CHARS <= len(clean) <= _MOTIVATION_MAX_CHARS:
            raise StudioDataError("The motivation must be 380–600 characters before it can be saved.", "invalid_motivation", 422)
        detected = Applications._language_from_text(clean, language)
        if detected != language:
            raise StudioDataError("The returned motivation does not match the selected language.", "invalid_motivation", 422)
        return clean

    def motivation(self, role_id: str, language: Any, jobs: dict | None = None) -> dict:
        language = self.normalize_language(language)
        role, _folder = self._application_path(role_id)
        store, _ = self._load_motivations()
        record = store["roles"].get(role_id, {}).get(language)
        if record:
            hashes = self.motivation_packet(role_id, language)["source_hashes"]
            if record.get("provenance", {}).get("source_hashes") == hashes:
                return {"role_id": role_id, "language": language, "status": "ready", "text": record["text"],
                        "source": "studio-generated", "saved": True, "provenance": record["provenance"],
                        "updated_at": record["updated_at"], "job_id": record.get("job_id")}
        saved = self._motivation_source(role, language)
        if saved:
            return {"role_id": role_id, "language": language, "status": "ready", "job_id": None, **saved}
        active_job = None
        for job in (jobs or {}).get("items", []):
            if (isinstance(job, dict) and job.get("kind") == "application_motivation"
                    and job.get("role_id") == role_id and job.get("language") == language
                    and job.get("status") in {"queued", "running", "waiting_input", "stopping"}):
                active_job = job
                break
        return {"role_id": role_id, "language": language, "status": "generating" if active_job else "missing",
                "text": "", "source": None, "saved": False,
                "job_id": active_job.get("id") if active_job else None,
                "source_available": bool(self.motivation_packet(role_id, language).get("existing_motivation"))}

    def save_generated_motivation(self, packet: dict, text: Any, *, job_id: str, provider: str,
                                  model_used: str, effort: str, actual_effort: str | None = None,
                                  requested_service_tier: str = "priority",
                                  actual_service_tier: str | None = None) -> dict:
        role_id = packet.get("role_id")
        language = self.normalize_language(packet.get("language"))
        if not isinstance(job_id, str) or not _CONTACT_JOB_ID.fullmatch(job_id):
            raise StudioDataError("The motivation task identity is invalid.", "invalid_motivation", 422)
        expected_route = {"luna": ("gpt-6-luna", "high"), "sol": ("gpt-6.1-sol", "low")}.get(provider)
        if (expected_route != (model_used, effort)
                or requested_service_tier != "priority"):
            raise StudioDataError("The motivation was returned by a different route and was not saved.", "route_mismatch", 409)
        clean = self.validate_motivation_text(text, language)
        current = self.motivation_packet(role_id, language)
        if current["source_hashes"] != packet.get("source_hashes"):
            raise StudioDataError("The advert or approved wording changed while the motivation was being prepared. Refresh and try again.", "stale_motivation", 409)
        provenance = {"provider": provider, "model_used": model_used,
                      "requested_effort": effort, "actual_effort": actual_effort,
                      "requested_service_tier": requested_service_tier,
                      "actual_service_tier": actual_service_tier,
                      "source_path": packet.get("source_path"), "source_hashes": packet.get("source_hashes"),
                      "existing_language": packet.get("existing_language"), "saved_at": datetime.now(timezone.utc).isoformat(),
                      "explicit_user_request": True}
        try:
            with self.data._write_lock():
                store, raw = self._load_motivations()
                self._backup_local_data(self.motivation_path, raw, "application-motivations")
                store["roles"].setdefault(role_id, {})[language] = {"text": clean, "provenance": provenance,
                    "job_id": job_id, "updated_at": datetime.now(timezone.utc).isoformat()}
                self._safe_local_file(self.motivation_path)
                self.data._atomic(self.motivation_path, self._encode_json(store))
                reread, _ = self._load_motivations()
                saved = reread["roles"].get(role_id, {}).get(language)
                if not saved or saved.get("text") != clean or saved.get("job_id") != job_id:
                    raise StudioDataError("The motivation did not pass local readback and was not confirmed.", "readback_failed", 503)
        except OSError as exc:
            raise StudioDataError("The motivation could not be saved locally.", "application_store_unavailable", 503) from exc
        return {"role_id": role_id, "language": language, "status": "ready", "text": clean,
                "source": "studio-generated", "saved": True, "provenance": provenance,
                "updated_at": saved["updated_at"], "job_id": job_id}

    @classmethod
    def _validated_public_target(cls, value: Any) -> tuple[str, str, list[tuple]]:
        safe = safe_application_url(value)
        if not safe:
            raise ValueError("The source URL is invalid or contains credentials.")
        parsed = urlsplit(safe)
        host = parsed.hostname or ""
        if parsed.scheme.casefold() != "https" or parsed.port not in (None, 443) or not host or ":" in host:
            raise ValueError("Only public HTTPS source pages are accepted.")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("Direct IP addresses are not accepted as source pages.")
        ascii_host = host.encode("idna").decode("ascii")
        if ascii_host.casefold() == "localhost" or ascii_host.casefold().endswith(".localhost"):
            raise ValueError("Local source pages are not accepted.")
        try:
            resolved = socket.getaddrinfo(ascii_host, 443, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError) as exc:
            raise ValueError("The source host did not resolve to a public address.") from exc
        if not resolved:
            raise ValueError("The source host did not resolve to a public address.")
        addresses: list[tuple] = []
        for item in resolved:
            try:
                family, socktype, proto = item[0], item[1], item[2]
                sockaddr = tuple(item[4])
                address = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
                if family not in {socket.AF_INET, socket.AF_INET6} or socktype != socket.SOCK_STREAM:
                    raise ValueError("Unsupported resolved socket address")
                if len(sockaddr) < 2 or int(sockaddr[1]) != 443:
                    raise ValueError("Resolved socket port did not match HTTPS")
            except (IndexError, TypeError, ValueError) as exc:
                raise ValueError("The source host returned an invalid network address.") from exc
            if not address.is_global:
                raise ValueError("The source host resolved to a non-public address.")
            pinned = (family, socktype, proto, sockaddr)
            if pinned not in addresses:
                addresses.append(pinned)
        if not addresses:
            raise ValueError("The source host did not resolve to a public address.")
        return safe, host, addresses

    @classmethod
    def _public_source_url(cls, value: Any) -> str:
        """Validate a public HTTPS host; callers fetching it must use the returned DNS pin."""
        safe, _host, _addresses = cls._validated_public_target(value)
        return safe

    @classmethod
    def _fetch_public_page(cls, source_url: str) -> dict:
        current_url = source_url
        redirects = 0
        while True:
            final_url, host, addresses = cls._validated_public_target(current_url)
            parsed = urlsplit(final_url)
            target = urlunsplit(("", "", quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                                 quote(parsed.query, safe="%/?&=;:@!$'()*+,=-._~"), ""))
            response = None
            connection = _PinnedHTTPSConnection(host, addresses[0], 443, timeout=8)
            try:
                connection.request("GET", target, headers={
                    "Host": host.encode("idna").decode("ascii"),
                    "User-Agent": "CV-Studio public source verifier/1.0",
                    "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.1",
                    "Connection": "close",
                })
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location or redirects >= 5:
                        raise ValueError("The public source redirect chain is invalid or too long.")
                    current_url = urljoin(final_url, location)
                    redirects += 1
                    continue
                if response.status != 200:
                    raise ValueError("The public source did not return a readable page.")
                content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().casefold()
                length = response.getheader("Content-Length")
                if length:
                    try:
                        if int(length) > 2_000_000:
                            raise ValueError("The source page is too large to verify safely.")
                    except ValueError as exc:
                        if "too large" in str(exc):
                            raise
                        raise ValueError("The source page length was invalid.") from exc
                if content_type not in {"text/html", "text/plain", "application/json", "application/ld+json"}:
                    raise ValueError("The source page is not readable text.")
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("The source page is too large to verify safely.")
            finally:
                if response is not None:
                    response.close()
                connection.close()
            decoded = raw.decode("utf-8", errors="replace")
            if content_type == "text/html":
                parser = _PublicPage()
                parser.feed(decoded)
                parser.close()
                return {"text": " ".join(" ".join(parser.text).split()), "links": parser.links,
                        "contexts": parser.contexts, "title": " ".join(parser.title.split()), "url": final_url}
            normalized = " ".join(decoded.split())
            return {"text": normalized, "links": [],
                    "contexts": [{"tag": "text", "text": normalized, "links": []}] if normalized else [],
                    "title": "", "url": final_url}

    @staticmethod
    def _normalize_evidence(value: str) -> str:
        return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value)).split()).casefold()

    @staticmethod
    def _valid_linkedin(value: Any) -> str | None:
        safe = safe_application_url(value)
        if not safe:
            return None
        try:
            parsed = urlsplit(safe)
            host = (parsed.hostname or "").casefold().removeprefix("www.")
            if (parsed.scheme.casefold() != "https" or parsed.port not in (None, 443)
                    or host != "linkedin.com" or not re.match(r"^/(?:in|pub)/[A-Za-z0-9%._-]+/?$", parsed.path)):
                return None
        except ValueError:
            return None
        return safe

    @staticmethod
    def _professional_email(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        matches = _EMAIL.findall(value.strip())
        if len(matches) != 1 or matches[0] != value.strip():
            return None
        domain = value.rsplit("@", 1)[-1].casefold()
        if domain in _FREE_EMAIL_DOMAINS:
            return None
        return value.strip()

    @staticmethod
    def _company_in_evidence(company: str, evidence: str) -> bool:
        company_words = " ".join(re.findall(r"[\wÀ-ÿ]+", company.casefold()))
        evidence_words = " ".join(re.findall(r"[\wÀ-ÿ]+", evidence.casefold()))
        return bool(company_words and company_words in evidence_words)

    @staticmethod
    def _canonical_link(value: str) -> str | None:
        try:
            parsed = urlsplit(value)
            if not parsed.hostname:
                return None
            return parsed.hostname.casefold().removeprefix("www.") + parsed.path.rstrip("/")
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _identity_in_context(context_text: str, quote: str, *, name: str, title: str, company: str) -> bool:
        normalized_context = Applications._normalize_evidence(context_text)
        normalized_quote = Applications._normalize_evidence(quote)
        quote_identity = [Applications._normalize_evidence(value) for value in (name, title)]
        context_identity = [Applications._normalize_evidence(value) for value in (name, title, company)]
        return bool(normalized_quote and normalized_quote in normalized_context
                    and all(value and value in normalized_quote for value in quote_identity)
                    and all(value and value in normalized_context for value in context_identity)
                    and Applications._company_in_evidence(company, context_text))

    @staticmethod
    def _generic_service_label(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip() or len(value) > 120:
            return None
        normalized = Applications._normalize_evidence(value)
        plain = "".join(character for character in unicodedata.normalize("NFKD", normalized)
                        if not unicodedata.combining(character))
        plain = re.sub(r"[-_/]+", " ", plain)
        plain = " ".join(plain.split())
        if not any(pattern.search(plain) for pattern in _GENERIC_SERVICE_LABELS):
            return None
        return value.strip()

    @staticmethod
    def _official_email_host(source_url: str, email: str) -> bool:
        try:
            host = (urlsplit(source_url).hostname or "").casefold().removeprefix("www.")
            domain = email.rsplit("@", 1)[-1].casefold().removeprefix("www.")
        except (AttributeError, ValueError):
            return False
        # Only accept an address published on its own domain or a subdomain of
        # that domain. This keeps company service mailboxes tied to an employer-
        # owned public page and rejects third-party directories.
        return bool(host and domain and (host == domain or host.endswith("." + domain)))

    def _verified_company_service_email(self, raw: Any, *, company: str, cache: dict) -> dict | None:
        if not isinstance(raw, dict):
            return None
        label = self._generic_service_label(raw.get("label"))
        email = self._professional_email(raw.get("email"))
        evidence = raw.get("email_evidence") or raw.get("evidence")
        if not label or not email or not isinstance(evidence, dict):
            return None
        source_url, quote = evidence.get("source_url"), evidence.get("quote")
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 1600:
            return None
        try:
            safe_source = self._public_source_url(source_url)
            page = cache.get(safe_source)
            if page is None:
                page = self.evidence_reader(safe_source)
                if not isinstance(page, dict) or not isinstance(page.get("text"), str):
                    return None
                cache[safe_source] = page
            final_url = page.get("url") if isinstance(page.get("url"), str) else safe_source
            if not self._official_email_host(final_url, email):
                return None
            page_descriptor = " ".join((urlsplit(final_url).path, str(page.get("title") or ""))).casefold()
            page_descriptor = "".join(character for character in unicodedata.normalize("NFKD", page_descriptor)
                                       if not unicodedata.combining(character))
            if not re.search(r"contact|customer|client|support|service|assistance|help|\bsav\b|after.?sales|post.?sales",
                             page_descriptor):
                return None
            normalized_quote = self._normalize_evidence(quote)
            normalized_label = self._normalize_evidence(label)
            normalized_company = self._normalize_evidence(company)
            normalized_email = email.casefold()
            if (not normalized_company or normalized_label not in normalized_quote
                    or normalized_company not in normalized_quote or normalized_email not in normalized_quote):
                return None
            contexts = page.get("contexts")
            if not isinstance(contexts, list):
                return None
            matched = []
            for context in contexts:
                if (not isinstance(context, dict) or not isinstance(context.get("text"), str)
                        or not isinstance(context.get("links"), list)
                        or context.get("identity_frame") is not True
                        or len(context["text"]) > 1200):
                    continue
                context_text = context["text"]
                normalized_context = self._normalize_evidence(context_text)
                if (normalized_quote not in normalized_context
                        or normalized_company not in normalized_context
                        or normalized_label not in normalized_context
                        or not self._company_in_evidence(company, context_text)):
                    continue
                links = [str(item) for item in context["links"] if isinstance(item, str)]
                if (normalized_email in normalized_context
                        or any(item.casefold().strip().removeprefix("mailto:").split("?", 1)[0]
                               == normalized_email for item in links)):
                    matched.append(context_text)
            if not matched:
                return None
            host = urlsplit(final_url).hostname or ""
            return {"value": email, "url": email, "verified": True, "source_url": safe_source,
                    "source_name": str(page.get("title") or host)[:180], "source_role": label[:180],
                    "source_kind": "official_company_contact", "checked_at": datetime.now(timezone.utc).isoformat(),
                    "quote": quote.strip()}
        except (OSError, ValueError, TypeError, UnicodeError, socket.gaierror, TimeoutError):
            return None

    def _verified_contact_field(self, value: str, evidence: Any, *, kind: str,
                                name: str, title: str, company: str, cache: dict) -> dict | None:
        if not isinstance(evidence, dict):
            return None
        source_url = evidence.get("source_url")
        quote = evidence.get("quote")
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 1600:
            return None
        if kind == "email":
            validated_value = self._professional_email(value)
        else:
            validated_value = self._valid_linkedin(value)
        if not validated_value:
            return None
        try:
            safe_source = self._public_source_url(source_url)
            cache_key = safe_source
            page = cache.get(cache_key)
            if page is None:
                page = self.evidence_reader(safe_source)
                if not isinstance(page, dict) or not isinstance(page.get("text"), str):
                    return None
                cache[cache_key] = page
            normalized_quote = self._normalize_evidence(quote)
            if (not normalized_quote or not all(self._normalize_evidence(value) in normalized_quote
                    for value in (name, title))):
                return None
            contexts = page.get("contexts")
            if not isinstance(contexts, list):
                return None
            matched = []
            for context in contexts:
                if (not isinstance(context, dict) or not isinstance(context.get("text"), str)
                        or not isinstance(context.get("links"), list)):
                    continue
                context_text = context["text"]
                if len(context_text) > 1200 or not self._identity_in_context(context_text, quote,
                        name=name, title=title, company=company):
                    continue
                links = [str(item) for item in context["links"] if isinstance(item, str)]
                matched.append((context_text, links, context.get("identity_frame") is True))
            if not matched:
                return None
            if kind == "email":
                email = validated_value.casefold()
                if email not in normalized_quote:
                    return None
                if not any(identity_frame and (email in self._normalize_evidence(context_text)
                           or any(item.casefold().strip().removeprefix("mailto:").split("?", 1)[0] == email
                                  for item in links))
                           for context_text, links, identity_frame in matched):
                    return None
                shown = validated_value
            else:
                canonical = self._canonical_link(validated_value)
                if not canonical:
                    return None
                source_is_profile = self._canonical_link(safe_source) == canonical
                has_same_person_profile_link = any(
                    context.get("identity_frame") is True and
                    self._canonical_link(item) == canonical
                    for context in contexts if isinstance(context, dict)
                    and isinstance(context.get("text"), str)
                    and len(context["text"]) <= 1200
                    and self._identity_in_context(context["text"], quote,
                        name=name, title=title, company=company)
                    for item in context.get("links", []) if isinstance(item, str))
                if not source_is_profile and not has_same_person_profile_link:
                    return None
                shown = validated_value
            host = urlsplit(safe_source).hostname or ""
            return {"value": shown, "url": shown, "verified": True, "source_url": safe_source,
                    "source_name": str(page.get("title") or host)[:180], "source_role": title[:180],
                    "checked_at": datetime.now(timezone.utc).isoformat(), "quote": quote.strip()}
        except (OSError, ValueError, TypeError, UnicodeError, socket.gaierror, TimeoutError):
            return None

    def _load_contacts(self) -> tuple[dict, bytes | None]:
        store, raw = self._load_local_records(self.contacts_path, "roles")
        store.setdefault("jobs", {})
        if not isinstance(store["jobs"], dict):
            raise StudioDataError("Local contact records are unreadable and were preserved.", "invalid_application_store", 409)
        store.setdefault("searches", {})
        if not isinstance(store["searches"], dict):
            raise StudioDataError("Local contact search receipts are unreadable and were preserved.", "invalid_application_store", 409)
        for role_id, contacts in store["roles"].items():
            if not isinstance(role_id, str) or not _ID.fullmatch(role_id) or not isinstance(contacts, list):
                raise StudioDataError("Local contact records are unreadable and were preserved.", "invalid_application_store", 409)
            if any(not isinstance(contact, dict) for contact in contacts):
                raise StudioDataError("Local contact records are unreadable and were preserved.", "invalid_application_store", 409)
        for role_id, receipt in store["searches"].items():
            if (not isinstance(role_id, str) or not _ID.fullmatch(role_id)
                    or not isinstance(receipt, dict)):
                raise StudioDataError("Local contact search receipts are unreadable and were preserved.", "invalid_application_store", 409)
        return store, raw

    @staticmethod
    def _approved_contact_rows(contacts: Any, review: Any, *, role_id: str,
                               job_id: str, saved_job: Any) -> list[dict]:
        """Accept saved contacts joined to a completed source-check receipt.

        This is deliberately a read-only recovery check. It never re-verifies an old
        address or upgrades a contact based on a finder claim or a bare verified
        flag in an untrusted backup. Historical Sol receipts remain readable.
        """
        if (not isinstance(saved_job, dict) or saved_job.get("role_id") != role_id
                or not isinstance(job_id, str) or not _CONTACT_JOB_ID.fullmatch(job_id)):
            return []
        saved_review = saved_job.get("review")
        if (not isinstance(saved_review, dict) or saved_review.get("status") != "completed"
                or saved_review.get("actual_model") not in {"gpt-6.1-sol", _DETERMINISTIC_CONTACT_REVIEW}):
            return []
        if isinstance(review, dict) and (review.get("status") != "completed"
                or review.get("actual_model") not in {"gpt-6.1-sol", _DETERMINISTIC_CONTACT_REVIEW}):
            return []
        if type(saved_job.get("verified_count")) is not int or saved_job["verified_count"] < 1:
            return []
        if not isinstance(contacts, list):
            return []
        result = []
        for item in contacts:
            if (not isinstance(item, dict) or item.get("job_id") != job_id
                    or item.get("verified") is not True):
                continue
            email = item.get("email")
            linkedin = item.get("linkedin")
            has_verified_field = ((isinstance(email, dict) and email.get("verified") is True
                                   and isinstance(email.get("value"), str) and bool(email["value"].strip()))
                                  or (isinstance(linkedin, dict) and linkedin.get("verified") is True
                                      and isinstance(linkedin.get("url"), str) and bool(linkedin["url"].strip())))
            if has_verified_field:
                result.append(copy.deepcopy(item))
        return result

    def _contacts_from_backups(self) -> dict[str, list[dict]]:
        """Read only mechanically verified contacts from exact local contact backups."""
        base = self.data.local / "backups"
        directory = base / "application-contacts"
        if base.is_symlink() or directory.is_symlink():
            raise StudioDataError("The local application backup folder is unsafe.", "unsafe_path", 403)
        self.data._safe(directory, self.data.local)
        if not directory.exists():
            return {}
        recovered: dict[str, list[dict]] = {}
        try:
            paths = sorted(directory.glob("*.json"), key=lambda path: path.name)
        except OSError:
            return recovered
        for path in paths:
            if path.is_symlink():
                continue
            try:
                self._safe_local_file(path)
                if not path.is_file() or path.stat().st_size > 10_000_000:
                    continue
                raw = path.read_bytes()
                value = json.loads(raw)
                if (not isinstance(value, dict) or type(value.get("version")) is not int
                        or value.get("version") != 1 or not isinstance(value.get("roles"), dict)
                        or not isinstance(value.get("jobs", {}), dict)):
                    continue
                saved_jobs = value.get("jobs", {})
                for role_id, records in value["roles"].items():
                    if not isinstance(role_id, str) or not _ID.fullmatch(role_id) or not isinstance(records, list):
                        continue
                    for item in records:
                        if not isinstance(item, dict):
                            continue
                        job_id = item.get("job_id")
                        saved_job = saved_jobs.get(job_id) if isinstance(job_id, str) else None
                        review = saved_job.get("review") if isinstance(saved_job, dict) else None
                        approved = self._approved_contact_rows([item], review, role_id=role_id,
                            job_id=job_id, saved_job=saved_job)
                        if approved:
                            recovered.setdefault(role_id, []).extend(approved)
            except (OSError, ValueError, UnicodeError, TypeError, StudioDataError):
                # A bad old backup is not allowed to block the current saved store.
                continue
        return recovered

    @staticmethod
    def _contact_job_rows(role_id: str, jobs: dict | None) -> tuple[list[dict], list[dict]]:
        """Return approved result rows and contact jobs for one role from a jobs view."""
        job_rows = [job for job in (jobs or {}).get("items", [])
                    if isinstance(job, dict) and job.get("kind") == "application_contacts"
                    and job.get("role_id") == role_id]
        approved = []
        for job in job_rows:
            if job.get("status") != "completed":
                continue
            result = job.get("result")
            if not isinstance(result, dict):
                continue
            review = result.get("review")
            saved_job = {"role_id": role_id, "review": review,
                         "verified_count": result.get("verified_count")}
            approved.extend(Applications._approved_contact_rows(result.get("contacts"), review,
                role_id=role_id, job_id=job.get("id") or job.get("job_id"), saved_job=saved_job))
        job_rows.sort(key=lambda row: str(row.get("started_at") or row.get("finished_at") or ""), reverse=True)
        return approved, job_rows

    @staticmethod
    def _contact_key(item: dict) -> tuple[str, ...]:
        email = item.get("email")
        linkedin = item.get("linkedin")
        if isinstance(email, dict) and email.get("verified") is True and email.get("value"):
            return ("email", str(email["value"]).strip().casefold())
        if isinstance(linkedin, dict) and linkedin.get("verified") is True and linkedin.get("url"):
            return ("linkedin", str(linkedin["url"]).strip().casefold())
        return ("identity", str(item.get("name", "")).strip().casefold(),
                str(item.get("title", "")).strip().casefold(),
                str(item.get("organization", "")).strip().casefold())

    @classmethod
    def _merge_contact_rows(cls, *groups: list[dict]) -> list[dict]:
        """Merge repeated people without dropping a previously verified email."""
        result: list[dict] = []
        for group in groups:
            for raw in group:
                if not isinstance(raw, dict):
                    continue
                item = copy.deepcopy(raw)
                email = item.get("email")
                linkedin = item.get("linkedin")
                if not isinstance(email, dict) or email.get("verified") is not True:
                    item["email"] = None
                if not isinstance(linkedin, dict) or linkedin.get("verified") is not True:
                    item["linkedin"] = None
                key = cls._contact_key(item)
                match_index = next((index for index, prior in enumerate(result)
                    if key == cls._contact_key(prior) or
                    (item.get("name") and item.get("title")
                     and str(item.get("name")).strip().casefold() == str(prior.get("name", "")).strip().casefold()
                     and str(item.get("title")).strip().casefold() == str(prior.get("title", "")).strip().casefold()
                     and str(item.get("organization", "")).strip().casefold()
                         == str(prior.get("organization", "")).strip().casefold())), None)
                if match_index is None:
                    result.append(item)
                    continue
                prior = result[match_index]
                merged = {**prior, **item}
                for field in ("email", "linkedin"):
                    incoming = item.get(field)
                    previous = prior.get(field)
                    if not (isinstance(incoming, dict) and incoming.get("verified") is True):
                        merged[field] = copy.deepcopy(previous) if isinstance(previous, dict) and previous.get("verified") is True else None
                result[match_index] = merged
        return result

    def verify_contacts(self, role_id: str, provider: str, model_used: str, payload: Any) -> dict:
        """Mechanically verify Luna's public claims without persisting results."""
        role, _folder = self._application_path(role_id)
        return self.verify_contact_packet(role, provider, model_used, payload)

    def verify_saved_job_contacts(self, packet: dict, provider: str, model_used: str, payload: Any) -> dict:
        """Check the original advert identity before accepting any person claims."""
        if packet.get("_source_capture_path") and packet.get("public_capture"):
            from public_contact_packet import load_capture
            source = load_capture(packet["_source_capture_path"], packet["source_url"])
            prior = packet["public_capture"]
            if any(source.get(key) != prior.get(key) for key in ("company", "title", "capture_sha256", "raw_sha256", "body_sha256")):
                raise StudioDataError("The original public job capture changed.", "job_identity_unverified", 422)
            proof = self.verify_contact_packet(source, provider, model_used, {**payload, "company_emails":[]})
            return {**proof, "job_evidence":{key:source[key] for key in ("company", "title", "source_url", "canonical_url", "captured_at", "capture_sha256", "raw_sha256", "body_sha256", "verification")}}
        evidence = payload.get("job_evidence") if isinstance(payload, dict) else None
        if not isinstance(evidence, dict):
            raise StudioDataError("The original job identity could not be verified.", "job_identity_unverified", 422)
        company, title, quote = (evidence.get(key) for key in ("company", "title", "quote"))
        url = self._public_source_url(evidence.get("source_url"))
        if url != self._public_source_url(packet.get("source_url")):
            raise StudioDataError("Contact research must verify the saved original advert.", "job_identity_unverified", 422)
        if (not all(isinstance(value, str) and value.strip() for value in (company, title, quote))
                or len(quote) > 1600 or len(company) > 200 or len(title) > 300):
            raise StudioDataError("The job identity evidence was incomplete.", "job_identity_unverified", 422)
        page = self.evidence_reader(url)
        normalized = self._normalize_evidence(quote)
        if (not isinstance(page, dict) or normalized not in self._normalize_evidence(page.get("text", ""))
                or not all(self._normalize_evidence(value) in normalized for value in (company, title))):
            raise StudioDataError("The saved advert did not prove the employer and role.", "job_identity_unverified", 422)
        proof = self.verify_contact_packet({"company": company, "title": title}, provider, model_used, payload)
        return {**proof, "job_evidence": evidence}

    def verify_contact_packet(self, role: dict, provider: str, model_used: str, payload: Any) -> dict:
        """Verify public source claims for an opt-in saved job without a CV folder."""
        expected = {"luna": "gpt-6-luna", "sol": "gpt-6.1-sol"}.get(provider)
        if not expected or model_used != expected:
            raise StudioDataError("Contact results came from an unverified route and were not saved.", "route_mismatch", 409)
        people = payload.get("people", []) if isinstance(payload, dict) else None
        service_emails = payload.get("company_emails", []) if isinstance(payload, dict) else None
        if (not isinstance(payload, dict) or not any(key in payload for key in ("people", "company_emails"))
                or not isinstance(people, list) or not isinstance(service_emails, list)
                or len(people) + len(service_emails) > 50):
            raise StudioDataError("The contact response did not match the expected public-source format.", "invalid_contacts", 422)
        verified: list[dict] = []
        rejected = 0
        page_cache: dict[str, dict] = {}
        for person in people:
            if not isinstance(person, dict):
                rejected += 1
                continue
            name, title = person.get("name"), person.get("title") or person.get("role")
            if not isinstance(name, str) or not name.strip() or not isinstance(title, str) or not title.strip():
                rejected += 1
                continue
            name, title = name.strip()[:160], title.strip()[:180]
            email = self._verified_contact_field(person.get("email"), person.get("email_evidence"),
                kind="email", name=name, title=title, company=str(role.get("company") or ""), cache=page_cache) if person.get("email") else None
            linkedin = self._verified_contact_field(person.get("linkedin_url"), person.get("linkedin_evidence"),
                kind="linkedin", name=name, title=title, company=str(role.get("company") or ""), cache=page_cache) if person.get("linkedin_url") else None
            if not email and not linkedin:
                rejected += 1
                continue
            verified.append({"name": name, "title": title, "organization": str(role.get("company") or ""),
                "email": ({**email, "value": email["value"]} if email else None),
                "linkedin": ({**linkedin, "url": linkedin["url"]} if linkedin else None),
                "provider": provider, "model_used": model_used, "found_at": datetime.now(timezone.utc).isoformat(),
                "verified": True})
        company = str(role.get("company") or "")
        for address in service_emails:
            if not isinstance(address, dict):
                rejected += 1
                continue
            label = self._generic_service_label(address.get("label"))
            email = self._verified_company_service_email(address, company=company, cache=page_cache)
            if not label or not email:
                rejected += 1
                continue
            verified.append({"recipient_type": "company_service", "name": label, "title": label,
                "organization": company, "email": email, "linkedin": None, "provider": provider,
                "model_used": model_used, "found_at": datetime.now(timezone.utc).isoformat(), "verified": True})
        return {"verified_contacts": verified, "verified_count": len(verified),
                "rejected_count": rejected,
                "summary": f"{len(verified)} mechanically verified public contact result(s); {rejected} unverified result(s) skipped."}

    def save_contacts(self, role_id: str, job_id: str, provider: str, model_used: str,
                      payload: Any) -> dict:
        if not isinstance(job_id, str) or not _CONTACT_JOB_ID.fullmatch(job_id):
            raise StudioDataError("The contact search task identity is invalid.", "invalid_contact_job", 422)
        proof = self.verify_contacts(role_id, provider, model_used, payload)
        return self.save_reviewed_contacts(role_id, job_id, proof["verified_contacts"],
            review={"status": "completed", "actual_model": _DETERMINISTIC_CONTACT_REVIEW,
                    "verified_count": proof["verified_count"], "rejected_count": proof["rejected_count"]},
            provider=provider, model_used=model_used, rejected_count=proof["rejected_count"])

    def save_reviewed_contacts(self, role_id: str, job_id: str, verified: Any, *, review: dict,
                               provider: str = "luna", model_used: str = "gpt-6-luna",
                               rejected_count: int = 0, workflow: dict | None = None) -> dict:
        """Persist contacts backed by exact checks or an older review receipt."""
        self._application_path(role_id)
        if not isinstance(job_id, str) or not _CONTACT_JOB_ID.fullmatch(job_id):
            raise StudioDataError("The contact search task identity is invalid.", "invalid_contact_job", 422)
        if (not isinstance(review, dict) or review.get("status") != "completed"
                or not isinstance(review.get("actual_model"), str)
                or review.get("actual_model") not in {"gpt-6.1-sol", "fixture-direct-save", _DETERMINISTIC_CONTACT_REVIEW}):
            raise StudioDataError("Contact results did not pass the required source checks.", "review_required", 409)
        if not isinstance(verified, list) or len(verified) > 200:
            raise StudioDataError("The reviewed contact list was invalid and was not saved.", "invalid_contacts", 422)
        if type(rejected_count) is not int or rejected_count < 0:
            raise StudioDataError("The contact rejection count is invalid.", "invalid_contacts", 422)
        verified = copy.deepcopy(verified)
        for item in verified:
            if (not isinstance(item, dict) or item.get("verified") is not True
                    or not isinstance(item.get("name"), str) or not item["name"].strip()
                    or not isinstance(item.get("title"), str) or not item["title"].strip()
                    or not isinstance(item.get("organization"), str) or not item["organization"].strip()
                    or not ((isinstance(item.get("email"), dict) and item["email"].get("verified") is True)
                            or (isinstance(item.get("linkedin"), dict) and item["linkedin"].get("verified") is True))):
                raise StudioDataError("An unverified contact was included in the review result.", "invalid_contacts", 422)
            # A verified LinkedIn result must not make an unrelated email value
            # look verified simply because both were present in the same payload.
            if not isinstance(item.get("email"), dict) or item["email"].get("verified") is not True:
                item["email"] = None
            if not isinstance(item.get("linkedin"), dict) or item["linkedin"].get("verified") is not True:
                item["linkedin"] = None
            item["job_id"] = job_id
        try:
            with self.data._write_lock():
                store, raw = self._load_contacts()
                self._backup_local_data(self.contacts_path, raw, "application-contacts")
                prior = store["roles"].setdefault(role_id, [])
                saved_items = self._merge_contact_rows(prior, verified)
                store["roles"][role_id] = saved_items
                finished_at = datetime.now(timezone.utc).isoformat()
                job_record = {"role_id": role_id, "provider": provider, "model_used": model_used,
                    "finished_at": finished_at, "verified_count": len(verified),
                    "rejected_count": rejected_count, "review": copy.deepcopy(review),
                    "summary": f"{len(verified)} source-verified public contact result(s); {rejected_count} unsupported result(s) skipped."}
                if workflow is not None:
                    job_record["contact_workflow"] = copy.deepcopy(workflow)
                store["jobs"][job_id] = job_record
                store.setdefault("searches", {})[role_id] = {
                    "role_id": role_id, "job_id": job_id, "status": "completed",
                    "search_completed": True, "saved_at": finished_at, "finished_at": finished_at,
                    "provider": provider, "model_used": model_used,
                    "verified_count": len(verified), "rejected_count": rejected_count,
                    "review": copy.deepcopy(review), "summary": job_record["summary"]}
                self._safe_local_file(self.contacts_path)
                self.data._atomic(self.contacts_path, self._encode_json(store))
                reread, _ = self._load_contacts()
                readback = reread["roles"].get(role_id)
                if readback != saved_items or reread["jobs"].get(job_id) != store["jobs"][job_id]:
                    raise StudioDataError("Contact results failed local readback and were not confirmed.", "readback_failed", 503)
        except OSError as exc:
            raise StudioDataError("Verified contacts could not be saved locally.", "application_store_unavailable", 503) from exc
        return {"role_id": role_id, "contacts": saved_items, "items": saved_items, "total": len(saved_items),
                "summary": store["jobs"][job_id]["summary"], "job_id": job_id,
                "verified_count": len(verified), "rejected_count": rejected_count,
                "contact_workflow": copy.deepcopy(workflow), "review": copy.deepcopy(review)}

    @staticmethod
    def _contact_review_is_complete(review: Any) -> bool:
        return (isinstance(review, dict) and review.get("status") == "completed"
                and review.get("actual_model") in {"gpt-6.1-sol", "fixture-direct-save", _DETERMINISTIC_CONTACT_REVIEW})

    def _contact_search_receipts(self, role_id: str, store: dict, jobs: dict | None = None) -> list[dict]:
        receipts: list[dict] = []
        explicit = store.get("searches", {}).get(role_id) if isinstance(store.get("searches"), dict) else None
        if (isinstance(explicit, dict) and explicit.get("role_id") == role_id
                and explicit.get("status") == "completed" and explicit.get("search_completed") is True
                and self._contact_review_is_complete(explicit.get("review"))):
            receipts.append(copy.deepcopy(explicit))
        for job_id, saved_job in (store.get("jobs", {}) or {}).items():
            if (not isinstance(saved_job, dict) or saved_job.get("role_id") != role_id
                    or not self._contact_review_is_complete(saved_job.get("review"))):
                continue
            receipts.append({"role_id": role_id, "job_id": job_id, "status": "completed",
                "search_completed": True, "saved_at": saved_job.get("finished_at"),
                "finished_at": saved_job.get("finished_at"), "provider": saved_job.get("provider"),
                "model_used": saved_job.get("model_used"), "verified_count": saved_job.get("verified_count", 0),
                "rejected_count": saved_job.get("rejected_count", 0),
                "review": copy.deepcopy(saved_job.get("review")), "summary": saved_job.get("summary"),
                "contact_workflow": copy.deepcopy(saved_job.get("contact_workflow"))})
        _approved, job_rows = self._contact_job_rows(role_id, jobs)
        for job in job_rows:
            result = job.get("result")
            review = result.get("review") if isinstance(result, dict) else None
            if job.get("status") != "completed" or not self._contact_review_is_complete(review):
                continue
            receipts.append({"role_id": role_id, "job_id": job.get("id") or job.get("job_id"),
                "status": "completed", "search_completed": True,
                "saved_at": job.get("finished_at"), "finished_at": job.get("finished_at"),
                "provider": job.get("provider"), "model_used": job.get("model_used") or result.get("model_used"),
                "verified_count": result.get("verified_count", 0),
                "rejected_count": result.get("rejected_count", 0), "review": copy.deepcopy(review),
                "summary": result.get("summary") or job.get("summary"),
                "contact_workflow": copy.deepcopy(job.get("contact_workflow") or result.get("contact_workflow"))})
        receipts.sort(key=lambda row: str(row.get("finished_at") or row.get("saved_at") or ""), reverse=True)
        return receipts

    @staticmethod
    def _public_contact_job(job: dict | None, saved_receipt: dict | None = None) -> dict | None:
        if isinstance(job, dict):
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            public = {**{key: copy.deepcopy(job[key]) for key in
                ("id", "job_id", "status", "summary", "provider", "model_used", "started_at", "finished_at",
                 "recovery_required", "cleanup_status")
                if key in job},
                "contact_workflow": copy.deepcopy(job.get("contact_workflow") or result.get("contact_workflow")
                    or (saved_receipt or {}).get("contact_workflow"))}
            public["created_at"] = job.get("started_at")
            public["search_completed"] = bool(saved_receipt)
            return public
        if isinstance(saved_receipt, dict):
            return {"id": saved_receipt.get("job_id"), "job_id": saved_receipt.get("job_id"),
                "status": "completed", "summary": saved_receipt.get("summary"),
                "provider": saved_receipt.get("provider"), "model_used": saved_receipt.get("model_used"),
                "finished_at": saved_receipt.get("finished_at") or saved_receipt.get("saved_at"),
                "created_at": saved_receipt.get("finished_at") or saved_receipt.get("saved_at"),
                "search_completed": True,
                "contact_workflow": copy.deepcopy(saved_receipt.get("contact_workflow"))}
        return None

    def _contacts_snapshot(self, role_id: str, store: dict, jobs: dict | None = None,
                           backup_rows: dict[str, list[dict]] | None = None) -> dict:
        recovered_from_jobs, job_rows = self._contact_job_rows(role_id, jobs)
        receipts = self._contact_search_receipts(role_id, store, jobs)
        receipt = receipts[0] if receipts else None
        backups = backup_rows if backup_rows is not None else self._contacts_from_backups()
        # Older snapshots and retained job results are useful when the primary
        # contact list is absent, while the current store remains authoritative
        # when the same person has since received a newer source-backed value.
        contacts = self._merge_contact_rows(backups.get(role_id, []), recovered_from_jobs,
            store["roles"].get(role_id, []))
        # A persisted successful empty result is still a completed search. A failed
        # or stopped job alone never creates a receipt and can be retried.
        search_completed = bool(receipts)
        latest_job = job_rows[0] if job_rows else None
        last_job = self._public_contact_job(latest_job, receipt)
        if not last_job and receipt:
            last_job = self._public_contact_job(None, receipt)
        saved_at = ((receipt or {}).get("saved_at") or (receipt or {}).get("finished_at"))
        if not saved_at and contacts:
            saved_at = max((item.get("found_at") for item in contacts if isinstance(item.get("found_at"), str)), default=None)
        summary = "Verified public contacts are saved locally. No messages were sent or applications submitted."
        if not contacts:
            summary = ((receipt or {}).get("summary") or
                       (latest_job or {}).get("summary") or
                       "No verified public contacts are saved. Unverified or guessed addresses are not shown.")
        return {"role_id": role_id, "contacts": contacts, "items": contacts, "total": len(contacts),
                "summary": summary, "search_completed": search_completed,
                "last_job": last_job, "saved_at": saved_at,
                # Retain the established role endpoint field for existing Apply UI callers.
                "job": last_job}

    def contact_search_completed(self, role_id: str, jobs: dict | None = None) -> bool:
        """Authoritative duplicate-search guard backed by a durable success receipt."""
        self._application_path(role_id)
        store, _ = self._load_contacts()
        return bool(self._contacts_snapshot(role_id, store, jobs).get("search_completed"))

    def contacts(self, role_id: str, jobs: dict | None = None) -> dict:
        self._application_path(role_id)
        store, _ = self._load_contacts()
        return self._contacts_snapshot(role_id, store, jobs)

    def contacts_catalog(self, jobs: dict | None = None) -> dict:
        """Aggregate locally saved contacts and durable search status by role."""
        store, _ = self._load_contacts()
        backup_rows = self._contacts_from_backups()
        roles_view = self.data.roles()
        roles = {row["id"]: row for row in roles_view.get("items", [])
                 if isinstance(row, dict) and isinstance(row.get("id"), str)}
        role_ids = set(store["roles"]) | set(store.get("searches", {})) | set(backup_rows)
        job_items = (jobs or {}).get("items", [])
        for job in job_items if isinstance(job_items, list) else []:
            if (isinstance(job, dict) and job.get("kind") == "application_contacts"
                    and isinstance(job.get("role_id"), str) and _ID.fullmatch(job["role_id"])):
                role_ids.add(job["role_id"])
        for saved_job in store.get("jobs", {}).values():
            if (isinstance(saved_job, dict) and isinstance(saved_job.get("role_id"), str)
                    and _ID.fullmatch(saved_job["role_id"])):
                role_ids.add(saved_job["role_id"])
        result = []
        for role_id in role_ids:
            role = roles.get(role_id, {})
            snapshot = self._contacts_snapshot(role_id, store, jobs, backup_rows)
            if not (snapshot["contacts"] or snapshot["search_completed"] or snapshot["last_job"]):
                continue
            first_contact = snapshot["contacts"][0] if snapshot["contacts"] else {}
            result.append({"role_id": role_id,
                "company": str(role.get("company") or first_contact.get("organization") or "Unknown company"),
                "title": str(role.get("title") or ""),
                "contacts": snapshot["contacts"], "search_completed": snapshot["search_completed"],
                "last_job": snapshot["last_job"], "saved_at": snapshot["saved_at"],
                "summary": snapshot["summary"]})
        result.sort(key=lambda row: str(row.get("saved_at") or
            (row.get("last_job") or {}).get("started_at") or ""), reverse=True)
        return {"items": result, "total_contacts": sum(len(row["contacts"]) for row in result)}

    def _safe_store(self) -> None:
        if ((self.data.root / "Studio").is_symlink() or self.data.local.is_symlink()
                or self.path.is_symlink()):
            raise StudioDataError("The local application store must use regular files and folders.", "unsafe_path", 403)
        self.data._safe(self.path, self.data.local)

    def _load(self) -> tuple[dict, bytes | None]:
        self._safe_store()
        try:
            if not self.path.exists():
                return {"version": 1, "applications": {}}, None
            if not self.path.is_file() or self.path.stat().st_size > 5_000_000:
                raise ValueError("Invalid store file")
            raw = self.path.read_bytes()
            store = json.loads(raw)
            if (not isinstance(store, dict) or type(store.get("version")) is not int
                    or store["version"] != 1 or not isinstance(store.get("applications"), dict)):
                raise ValueError("Invalid store schema")
            for role_id, record in store["applications"].items():
                if (not isinstance(role_id, str) or not _ID.fullmatch(role_id)
                        or not isinstance(record, dict) or record.get("status") not in _STATES
                        or type(record.get("revision")) is not int or record["revision"] < 1
                        or not isinstance(record.get("updated_at"), str)):
                    raise ValueError("Invalid status record")
                self._validate_status_timestamp(record["updated_at"])
                for field in ("applied_at", "rejected_at"):
                    if field in record:
                        self._validate_status_timestamp(record[field])
                if "status_history" in record:
                    history = record["status_history"]
                    if not isinstance(history, list) or len(history) > _MAX_STATUS_HISTORY:
                        raise ValueError("Invalid status history")
                    for index, event in enumerate(history):
                        if (not isinstance(event, dict)
                                or (event.get("status") not in _STATES
                                    and not (index == 0 and event.get("status") == "pending"))):
                            raise ValueError("Invalid status history event")
                        self._validate_status_timestamp(event.get("at"))
                if "email_rejections" in record:
                    rejections = record["email_rejections"]
                    if not isinstance(rejections, list) or len(rejections) > _MAX_EMAIL_REJECTIONS:
                        raise ValueError("Invalid email rejection evidence")
                    seen_message_ids: set[str] = set()
                    for evidence in rejections:
                        clean = self._validate_email_rejection(evidence)
                        if clean["message_id"] in seen_message_ids:
                            raise ValueError("Duplicate rejection message")
                        seen_message_ids.add(clean["message_id"])
            return store, raw
        except (ValueError, UnicodeError, TypeError, OverflowError) as exc:
            raise StudioDataError("The local application store is unreadable. Its saved contents were preserved.",
                                  "invalid_application_store", 409) from exc
        except OSError as exc:
            raise StudioDataError("The local application store could not be read.", "application_store_unavailable", 503) from exc

    @staticmethod
    def _validate_status_timestamp(value: Any) -> None:
        if not isinstance(value, str) or len(value) > 100:
            raise ValueError("Invalid status timestamp")
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("Invalid status timestamp") from exc
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Missing timestamp timezone")

    @staticmethod
    def _status_date(record: dict | None, status: str) -> str | None:
        """Return only explicit status-mark evidence, including legacy record evidence."""
        if not record:
            return None
        explicit = record.get(f"{status}_at")
        if explicit is not None:
            return explicit
        history = record.get("status_history")
        if isinstance(history, list):
            event = next((item for item in history
                          if isinstance(item, dict) and item.get("status") == status), None)
            if event is not None:
                return event.get("at")
        # Version-1 records predate status history and date fields. Their
        # updated_at is evidence only for the status they currently record.
        if record.get("status") == status:
            return record.get("updated_at")
        return None

    @staticmethod
    def _default(role: dict) -> tuple[str, str, str]:
        if role.get("do_not_send") and role.get("collection") not in ("applied", "inactive") and role.get("applied") is not True:
            return "pending", "final_check", "final_check_hold"
        if role.get("collection") == "applied":
            return "applied", "archive", "applied_archive"
        if role.get("collection") == "inactive":
            return "inactive", "archive", "inactive_archive"
        if role.get("applied") is True:
            return "applied", "archive", "tracker_snapshot"
        if role.get("build_review", {}).get("required"):
            return "pending", "build_review", "requested_build_review"
        return ("ready" if role.get("cv_available") else "pending"), "folder", "role_folder"

    def _decorate(self, role: dict, records: dict) -> dict:
        canonical, inherited_source, canonical_source = self._default(role)
        record = records.get(role["id"])
        status = record["status"] if record else canonical
        canonical_applied = role.get("collection") == "applied" or role.get("applied") is True
        revision_facts = {"id": role["id"], "canonical_status": canonical,
                          "canonical_applied": canonical_applied, "canonical_source": canonical_source,
                          "record": record, "cv_available": bool(role.get("cv_available")),
                          "build_review": role.get("build_review")}
        revision = hashlib.sha256(json.dumps(revision_facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        application = {"status": status, "label": _LABELS[status],
                       "updated_at": record["updated_at"] if record else role.get("updated_at"),
                       "applied_at": self._status_date(record, "applied"),
                       "rejected_at": self._status_date(record, "rejected"),
                       "status_history": copy.deepcopy(record.get("status_history", [])) if record else [],
                       "email_rejections": copy.deepcopy(record.get("email_rejections", [])) if record else [],
                       "source": "local" if record else inherited_source,
                       "revision": revision, "apply_url": safe_application_url(role.get("source_url")),
                       "canonical_status": canonical, "canonical_applied": canonical_applied,
                       "canonical_source": canonical_source, "local_override": bool(record and status != canonical),
                       "cv_available": bool(role.get("cv_available")),
                       "needs_review": not bool(role.get("cv_available")) or bool(role.get("build_review", {}).get("required"))}
        return {**role, "application": application}

    def decorate(self, role: dict) -> dict:
        store, _ = self._load()
        return self._decorate(role, store["applications"])

    @staticmethod
    def _summary(items: list[dict]) -> dict:
        counts = {status: 0 for status in _LABELS}
        for role in items:
            counts[role["application"]["status"]] += 1
        return {**counts, "total": len(items),
                "canonical_applied": sum(r["application"]["canonical_applied"] for r in items),
                "local_overrides": sum(r["application"]["local_override"] for r in items),
                "with_apply_url": sum(r["application"]["apply_url"] is not None for r in items),
                "scope": "local"}

    def decorate_list(self, roles: dict | list[dict]) -> dict | list[dict]:
        store, _ = self._load()
        source = roles["items"] if isinstance(roles, dict) else roles
        items = [self._decorate(role, store["applications"]) for role in source]
        return {**roles, "items": items, "application_summary": self._summary(items)} if isinstance(roles, dict) else items

    def roles(self) -> dict:
        return self.decorate_list(self.data.roles())

    def role(self, role_id: str) -> dict:
        return self.decorate(self.data.role(role_id))

    def summary(self, roles: dict | list[dict] | None = None) -> dict:
        decorated = self.decorate_list(self.data.roles() if roles is None else roles)
        return decorated["application_summary"] if isinstance(decorated, dict) else self._summary(decorated)

    def _backup(self, content: bytes) -> str:
        base = self.data.local / "backups"
        directory = base / "applications"
        if base.is_symlink() or directory.is_symlink():
            raise StudioDataError("The application backup folder is unsafe.", "unsafe_path", 403)
        self.data._safe(directory, self.data.local)
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        # Existing parent directories may predate this store.
        base.chmod(0o700)
        directory.chmod(0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = directory / f"{stamp}-{uuid.uuid4().hex}.json"
        self.data._atomic(path, content)
        return str(path.relative_to(self.data.root))

    def set_status(self, role_id: str, status: str, revision: str | None = None) -> dict:
        if not isinstance(role_id, str) or not _ID.fullmatch(role_id):
            raise StudioDataError("This role folder was not found.", "not_found", 404)
        if not isinstance(status, str) or status not in _STATES:
            raise StudioDataError("Choose Ready to apply, Applied or Inactive.")
        if revision is not None and not isinstance(revision, str):
            raise StudioDataError("The application revision must be a string.")
        role = self.data.role(role_id)
        try:
            with self.data._write_lock():
                store, raw = self._load()
                current = self._decorate(role, store["applications"])
                if revision is not None and revision != current["application"]["revision"]:
                    raise StudioDataError("This application status changed. Refresh the role before saving again.", "stale_revision", 409)
                if current["application"]["status"] == status:
                    return {"saved": False, "unchanged": True, "role": current,
                            "application": current["application"], "revision": current["application"]["revision"],
                            "backup": None, "scope": "local"}
                prior = store["applications"].get(role_id, {})
                stamp = datetime.now(timezone.utc).isoformat()
                record = copy.deepcopy(prior)
                history = copy.deepcopy(record.get("status_history", []))
                if prior and (not history or history[-1].get("status") != prior["status"]):
                    # Preserve a legacy local status and its existing timestamp
                    # on the first write that adds history.
                    history.append({"status": prior["status"], "at": prior["updated_at"]})
                history.append({"status": status, "at": stamp})
                if len(history) > _MAX_STATUS_HISTORY:
                    raise StudioDataError("This application's local status history reached its limit.",
                                          "application_history_full", 409)
                for marked_status in ("applied", "rejected"):
                    first_mark = self._status_date(prior, marked_status)
                    if first_mark is not None:
                        field = f"{marked_status}_at"
                        if record.get(field) is None:
                            record[field] = first_mark
                    elif status == marked_status:
                        record[f"{marked_status}_at"] = stamp
                record.update({"status": status, "updated_at": stamp,
                               "revision": prior.get("revision", 0) + 1,
                               "status_history": history})
                backup = self._backup(raw if raw is not None else self._encode(store))
                store["applications"][role_id] = record
                self._safe_store()
                self.data._atomic(self.path, self._encode(store))
                saved = self._decorate(role, store["applications"])
                return {"saved": True, "unchanged": False, "role": saved,
                        "application": saved["application"], "revision": saved["application"]["revision"],
                        "backup": backup, "scope": "local"}
        except OSError as exc:
            raise StudioDataError("The local application status could not be saved. Existing saved data was preserved.",
                                  "application_store_unavailable", 503) from exc

    @staticmethod
    def _validate_email_rejection(evidence: Any) -> dict[str, str]:
        """Validate and copy the small Gmail proof kept beside one role status.

        The allowlist is deliberate: raw message bodies, headers, sender details,
        and attachments must never be retained by this local application store.
        """
        fields = {"source", "message_id", "thread_id", "received_at", "company", "title",
                  "matched_role_company", "matched_role_title", "identity_basis", "literal_quote"}
        if not isinstance(evidence, dict) or set(evidence) != fields:
            raise StudioDataError("Provide only the required, bounded Gmail rejection evidence.")
        if evidence.get("source") != "gmail":
            raise StudioDataError("The rejection source must be Gmail.")

        limits = {"message_id": 512, "thread_id": 512, "company": 300, "title": 500,
                  "matched_role_company": 300, "matched_role_title": 500,
                  "identity_basis": 1024}
        clean: dict[str, str] = {"source": "gmail"}
        for field, maximum in limits.items():
            value = evidence.get(field)
            if (not isinstance(value, str) or not value.strip() or len(value) > maximum
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise StudioDataError(f"The Gmail rejection {field.replace('_', ' ')} is invalid.")
            clean[field] = value.strip()

        basis_path = clean["identity_basis"].replace("\\", "/")
        if (Path(basis_path).is_absolute() or basis_path.startswith("//")
                or re.match(r"^[a-z]:/", basis_path, re.I)
                or ".." in basis_path.split("/")):
            raise StudioDataError("The rejection identity basis must be a relative receipt path.")

        received_at = evidence.get("received_at")
        try:
            Applications._validate_status_timestamp(received_at)
        except ValueError as exc:
            raise StudioDataError("The Gmail rejection time must include a valid timezone.") from exc
        clean["received_at"] = received_at

        quote = evidence.get("literal_quote")
        if (not isinstance(quote, str) or not quote.strip() or len(quote) > _MAX_REJECTION_QUOTE_CHARS
                or any((ord(char) < 32 and char not in "\r\n\t") or ord(char) == 127 for char in quote)
                or _EMAIL.search(quote)):
            raise StudioDataError("The literal rejection quote must be short and contain no email address.")
        clean["literal_quote"] = quote
        return clean

    @staticmethod
    def _identity_key(value: str) -> str:
        """Compare canonical role labels while ignoring accents and separators."""
        return "".join(char for char in unicodedata.normalize("NFKD", value).casefold()
                       if char.isalnum())

    def record_email_rejection(self, role_id: str, evidence: dict, revision: str) -> dict:
        """Record a confirmed rejection email for an existing Apply role.

        Evidence is idempotent by Gmail message ID. A later application status
        update or positive/in-progress state is surfaced as a conflict instead
        of being silently changed by an older inbox message.
        """
        if not isinstance(role_id, str) or not _ID.fullmatch(role_id):
            raise StudioDataError("This role folder was not found.", "not_found", 404)
        if not isinstance(revision, str):
            raise StudioDataError("The application revision must be a string.")
        clean = self._validate_email_rejection(evidence)
        role = self.data.role(role_id)
        received = datetime.fromisoformat(clean["received_at"].replace("Z", "+00:00"))
        received_utc = received.astimezone(timezone.utc)
        if received_utc > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise StudioDataError("The Gmail rejection time cannot be in the future.")
        for evidence_field, role_field in (("matched_role_company", "company"),
                                           ("matched_role_title", "title")):
            if self._identity_key(clean[evidence_field]) != self._identity_key(str(role.get(role_field) or "")):
                raise StudioDataError("The canonical matched company and title must identify this role.")
        try:
            with self.data._write_lock():
                store, raw = self._load()
                current = self._decorate(role, store["applications"])
                if revision != current["application"]["revision"]:
                    raise StudioDataError("This application changed. Refresh the role before recording its email outcome.",
                                          "stale_revision", 409)

                prior = store["applications"].get(role_id, {})
                prior_evidence = prior.get("email_rejections", [])
                duplicate = next((item for item in prior_evidence
                                  if item["message_id"] == clean["message_id"]), None)
                if duplicate is not None:
                    if duplicate != clean:
                        raise StudioDataError("This Gmail message ID already has different saved evidence.",
                                              "email_rejection_conflict", 409)
                    return {"saved": False, "unchanged": True, "role": current,
                            "application": current["application"], "revision": current["application"]["revision"],
                            "backup": None, "scope": "local"}

                for other_role_id, other_record in store["applications"].items():
                    if (other_role_id != role_id and any(
                            item["message_id"] == clean["message_id"]
                            for item in other_record.get("email_rejections", []))):
                        raise StudioDataError("This Gmail message ID is already recorded for another role.",
                                              "email_rejection_conflict", 409)

                status = current["application"]["status"]
                if status not in {"ready", "applied", "inactive", "rejected"}:
                    raise StudioDataError("The current application state must be reviewed before recording this rejection.",
                                          "rejection_status_conflict", 409)
                # A current local state written after the message arrived may
                # represent a later application decision. Keep it intact.
                prior_updated = prior.get("updated_at")
                if status != "rejected" and prior_updated:
                    updated = datetime.fromisoformat(prior_updated.replace("Z", "+00:00"))
                    if updated.astimezone(timezone.utc) > received_utc:
                        raise StudioDataError("A newer application update exists than this rejection email.",
                                              "rejection_status_conflict", 409)

                if len(prior_evidence) >= _MAX_EMAIL_REJECTIONS:
                    raise StudioDataError("This application's rejection evidence reached its limit.",
                                          "application_evidence_full", 409)

                previous_status = prior.get("status", status)
                stamp = datetime.now(timezone.utc).isoformat()
                record = copy.deepcopy(prior)
                history = copy.deepcopy(record.get("status_history", []))
                if prior and (not history or history[-1].get("status") != prior["status"]):
                    history.append({"status": prior["status"], "at": prior["updated_at"]})
                if previous_status != "rejected":
                    history.append({"status": "rejected", "at": clean["received_at"]})
                if len(history) > _MAX_STATUS_HISTORY:
                    raise StudioDataError("This application's local status history reached its limit.",
                                          "application_history_full", 409)

                applied_at = self._status_date(prior, "applied")
                rejected_at = self._status_date(prior, "rejected")
                if applied_at is not None and record.get("applied_at") is None:
                    record["applied_at"] = applied_at
                if rejected_at is not None and record.get("rejected_at") is None:
                    record["rejected_at"] = rejected_at
                if rejected_at is None:
                    record["rejected_at"] = clean["received_at"]
                record.update({"status": "rejected", "updated_at": stamp,
                               "revision": prior.get("revision", 0) + 1,
                               "status_history": history,
                               "email_rejections": [*copy.deepcopy(prior_evidence), clean]})

                backup = self._backup(raw if raw is not None else self._encode(store))
                store["applications"][role_id] = record
                self._safe_store()
                self.data._atomic(self.path, self._encode(store))
                saved = self._decorate(role, store["applications"])
                return {"saved": True, "unchanged": False, "role": saved,
                        "application": saved["application"], "revision": saved["application"]["revision"],
                        "backup": backup, "scope": "local"}
        except OSError as exc:
            raise StudioDataError("The Gmail rejection evidence could not be saved. Existing saved data was preserved.",
                                  "application_store_unavailable", 503) from exc

    @staticmethod
    def _encode(store: dict) -> bytes:
        return (json.dumps(store, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
