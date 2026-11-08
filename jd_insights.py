"""Deterministic, read-only keyword evidence from saved JDs and full captures.

The catalog is a small projection for CV Studio. It reads canonical role folders
through :class:`StudioData` and full-detail captures from bounded run/lane
folders. It never dispatches work or writes pipeline state.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from data import StudioData, StudioDataError


_MAX_RUNS = 160
_MAX_ROLE_DOCUMENTS = 2_000
_MAX_CAPTURE_DOCUMENTS = 4_000
_MAX_SOURCE_BYTES = 2_000_000
_MAX_CAPTURE_BODY_BYTES = 3_000_000
_MAX_TOTAL_TEXT_BYTES = 128_000_000
_MAX_CAPTURE_JSON_BYTES = 1_000_000
_MAX_AD_JSON_BYTES = 1_000_000
_MAX_TOTAL_METADATA_BYTES = 16_000_000
_MAX_HITS = 30_000
_MAX_TERMS = 8_000
_MAX_QUOTE_CHARS = 360
_SITE_IDS = ("hellowork", "france_travail", "linkedin", "apec", "wttj", "indeed")
_RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-|$)")
_ROLE_COLLECTIONS = {"_Applied", "_No longer active", "Already applied"}
_CATEGORIES = (
    ("it", "IT skills"),
    ("soft_skills", "Soft skills"),
    ("languages", "Languages"),
    ("tasks", "Tasks"),
    ("other", "Other"),
    ("experience_years", "Experience years"),
)


@dataclass(frozen=True)
class _Skill:
    category: str
    term: str
    pattern: re.Pattern[str]


def _alternation(*values: str) -> str:
    return "(?:" + "|".join(re.escape(value) for value in sorted(values, key=len, reverse=True)) + ")"


def _skill(category: str, term: str, *variants: str) -> _Skill:
    values = variants or (term,)
    return _Skill(category, term, re.compile(r"(?<!\w)" + _alternation(*values) + r"(?!\w)", re.I))


_SKILLS = (
    _skill("it", "Excel", "Excel", "Microsoft Excel"),
    _skill("it", "Power BI", "Power BI", "PowerBI"),
    _skill("it", "Power Query", "Power Query"),
    _skill("it", "Tableau"),
    _skill("it", "Python"),
    _skill("it", "SQL"),
    _skill("it", "R", "R", "RStudio"),
    _skill("it", "VBA"),
    _skill("it", "JavaScript"),
    _skill("it", "TypeScript"),
    _skill("it", "Java"),
    _skill("it", "C++"),
    _skill("it", "SAS"),
    _skill("it", "SPSS"),
    _skill("it", "Dataiku"),
    _skill("it", "Alteryx"),
    _skill("it", "SAP"),
    _skill("it", "Salesforce"),
    _skill("it", "Snowflake"),
    _skill("it", "Databricks"),
    _skill("it", "AWS", "AWS", "Amazon Web Services"),
    _skill("it", "Azure", "Azure", "Microsoft Azure"),
    _skill("it", "Google Cloud", "Google Cloud", "GCP"),
    _skill("it", "ETL"),
    _skill("it", "Jira"),
    _skill("it", "SharePoint"),
    _skill("it", "PowerPoint"),
    _skill("it", "Microsoft Office", "Microsoft Office", "MS Office"),
    _skill("soft_skills", "Communication", "communication", "communication skills", "communication orale", "communication écrite"),
    _skill("soft_skills", "Teamwork", "teamwork", "team work", "team spirit", "travail d'équipe", "travail en équipe", "esprit d'équipe"),
    _skill("soft_skills", "Collaboration", "collaboration", "collaborative", "collaborer"),
    _skill("soft_skills", "Leadership"),
    _skill("soft_skills", "Problem solving", "problem solving", "problem-solving", "résolution de problèmes"),
    _skill("soft_skills", "Analytical thinking", "analytical thinking", "analytical skills", "esprit d'analyse", "sens de l'analyse"),
    _skill("soft_skills", "Attention to detail", "attention to detail", "attention aux détails", "sens du détail"),
    _skill("soft_skills", "Adaptability", "adaptability", "adaptable", "adaptabilité"),
    _skill("soft_skills", "Autonomy", "autonomy", "autonomous", "autonomie", "autonome"),
    _skill("soft_skills", "Organization", "organization", "organisation", "organized", "organisé", "organisée"),
    _skill("soft_skills", "Rigor", "rigor", "rigour", "rigueur"),
    _skill("soft_skills", "Initiative", "initiative", "proactive", "proactivité"),
    _skill("soft_skills", "Negotiation", "negotiation", "négociation"),
    _skill("soft_skills", "Stakeholder management", "stakeholder management", "stakeholder engagement", "gestion des parties prenantes"),
    _skill("soft_skills", "Interpersonal skills", "interpersonal skills", "aisance relationnelle", "sens relationnel"),
    _skill("languages", "French", "French", "Français", "Française"),
    _skill("languages", "English", "English", "Anglais", "Anglaise"),
    _skill("languages", "Spanish", "Spanish", "Espagnol", "Espagnole"),
    _skill("languages", "German", "German", "Allemand", "Allemande"),
    _skill("languages", "Italian", "Italian", "Italien", "Italienne"),
    _skill("languages", "Portuguese", "Portuguese", "Portugais", "Portugaise"),
    _skill("languages", "Dutch", "Dutch", "Néerlandais", "Néerlandaise"),
    _skill("languages", "Arabic", "Arabic", "Arabe"),
    _skill("languages", "Chinese", "Chinese", "Mandarin", "Chinois", "Chinoise"),
    _skill("languages", "Japanese", "Japanese", "Japonais", "Japonaise"),
    _skill("languages", "Korean", "Korean", "Coréen", "Coréenne"),
    _skill("languages", "Russian", "Russian", "Russe"),
    _skill("languages", "Hindi"),
    _skill("other", "ESG", "ESG", "CSRD"),
    _skill("other", "Sustainability", "sustainability", "sustainable development", "durabilité", "développement durable"),
    _skill("other", "CSR / RSE", "CSR", "RSE", "corporate social responsibility", "responsabilité sociétale"),
    _skill("other", "Climate", "climate", "climate change", "climat", "changement climatique"),
    _skill("other", "Decarbonization", "decarbonization", "decarbonisation", "décarbonation", "décarbonisation"),
    _skill("other", "Carbon footprint", "carbon footprint", "empreinte carbone"),
    _skill("other", "Scope 3", "Scope 3", "scope three"),
    _skill("other", "Biodiversity", "biodiversity", "biodiversité"),
    _skill("other", "Circular economy", "circular economy", "économie circulaire"),
    _skill("other", "Supply chain", "supply chain", "chaîne d'approvisionnement", "chaine d'approvisionnement"),
    _skill("other", "Responsible procurement", "responsible procurement", "responsible purchasing", "achats responsables", "approvisionnement responsable"),
    _skill("other", "Sustainability reporting", "sustainability reporting", "ESG reporting", "reporting de durabilité", "reporting extra-financier"),
    _skill("other", "GHG Protocol", "GHG Protocol", "Greenhouse Gas Protocol"),
    _skill("other", "Life cycle assessment", "life cycle assessment", "LCA", "analyse du cycle de vie", "ACV"),
    _skill("other", "SFDR"),
    _skill("other", "EU Taxonomy", "EU Taxonomy", "Taxonomie européenne"),
    _skill("other", "ISO 14001"),
)

_EXPERIENCE = re.compile(
    r"(?<!\d)(\d{1,2}(?:\s*(?:-|–|—|to|à)\s*\d{1,2})?\s*\+?\s*"
    r"(?:years?|yrs?|ans)(?:\s*(?:of\s+|d['’]\s*)?(?:professional\s+)?(?:experience|expérience))?)",
    re.I,
)
_TASK_HEADINGS = re.compile(
    r"^(?:your\s+)?(?:key\s+)?(?:missions?|responsabilit[eé]s?|activit[eé]s?|t[âa]ches|"
    r"duties|responsibilities|what\s+you\s+will\s+do|what\s+you['’]ll\s+do|main\s+activities|"
    r"principales\s+t[âa]ches|vos\s+missions?)(?:\s*:)?$",
    re.I,
)
_OTHER_SECTION = re.compile(
    r"^(?:profile|profil|qualifications?|requirements?|comp[eé]tences?|skills?|education|formation|"
    r"about\s+you|your\s+profile|votre\s+profil|pr[eé]requis|what\s+we\s+are\s+looking\s+for)(?:\s*:)?$",
    re.I,
)
_TASK_VERB = re.compile(
    r"^(?:manage|lead|develop|support|analy[sz]e|collect|contribute|build|design|create|implement|"
    r"monitor|coordinate|prepare|ensure|conduct|report|deliver|transform|maintain|collaborate|"
    r"identify|write|produce|drive|oversee|participate|assist|pilot|piloter|assurer|[eé]laborer|"
    r"contribuer|analyser|participer|accompagner|r[eé]aliser|suivre|g[eé]rer|coordonn?er|animer|"
    r"mettre\s+en\s+(?:œuvre|place)|identifier|r[eé]diger|produire|soutenir|construire|garantir|appuyer|"
    r"d[eé]velopper|assurer|contribuer|[eé]valuer|conseiller|[eé]tablir|pr[eé]parer|optimiser|"
    r"faciliter|encadrer|analyser|am[eé]liorer|r[eé]aliser)\b",
    re.I,
)
_BULLET = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)])\s*")
_SOURCE_FIELDS = ("source url", "source", "original advert", "job url", "url", "lien")
_TRACKING_KEYS = {"fbclid", "gclid", "trk", "trackingid", "refid", "source", "src"}


def _normal(value: str) -> str:
    return " ".join(value.split())


def _safe_http_url(value: Any, *, canonicalize: bool = True) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    markdown = re.fullmatch(r"\[[^\]\r\n]*\]\((https?://[^\s<>\)]+)\)", raw)
    if markdown:
        raw = markdown.group(1)
    if not re.fullmatch(r"https?://[^\s<>]+", raw, re.I) or re.search(r"[\x00-\x1f\\]", raw):
        return None
    try:
        parts = urlsplit(raw)
        if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return None
        host = parts.hostname.casefold()
        port = parts.port
    except ValueError:
        return None
    if not canonicalize:
        return raw
    netloc = host
    if port and not ((parts.scheme.casefold() == "http" and port == 80) or
                     (parts.scheme.casefold() == "https" and port == 443)):
        netloc = f"{host}:{port}"
    query = [(key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
             if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.casefold(), netloc, path, urlencode(sorted(query)), ""))


def _captured_at(value: Any) -> str | None:
    """Normalize only a timestamp explicitly saved by the capture receipt."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _document_id(kind: str, source: str, source_url: str | None, captured_at: str | None) -> str:
    raw = "\0".join((kind, source, source_url or "", captured_at or ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _quote(text: str, start: int, end: int, limit: int = _MAX_QUOTE_CHARS) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    left = line_start
    right = line_end
    if right - left > limit:
        room = max(0, limit - (end - start))
        before = min(start - left, room // 2)
        left = start - before
        right = min(line_end, left + limit)
        if right < end:
            right = end
            left = max(line_start, right - limit)
    return text[left:right].strip()


def _no_symlink_read(path: Path, base: Path, limit: int) -> tuple[bytes | None, str | None]:
    """Read one bounded regular file beneath ``base`` without following links."""
    base = Path(os.path.abspath(base))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        return None, "outside_root"
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        return None, "unsafe_path"
    current = base
    try:
        if stat.S_ISLNK(current.lstat().st_mode):
            return None, "symlink"
        for part in relative.parts:
            current = current / part
            details = current.lstat()
            if stat.S_ISLNK(details.st_mode):
                return None, "symlink"
        if not stat.S_ISREG(details.st_mode):
            return None, "not_file"
        if details.st_size < 0 or details.st_size > limit:
            return None, "oversize"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or opened.st_size > limit:
                    return None, "oversize"
                data = stream.read(limit + 1)
                final = os.fstat(stream.fileno())
                if len(data) > limit or (opened.st_dev, opened.st_ino, opened.st_size) != (
                        final.st_dev, final.st_ino, final.st_size):
                    return None, "changed_during_read"
                return data, None
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError):
        return None, "unreadable"


class JDInsights:
    """Build a deterministic catalog from one canonical Resources directory."""

    def __init__(self, root: Path | str):
        self.data = StudioData(Path(root))
        self.root = self.data.root
        self.evidence = self.data.evidence
        self._coverage = defaultdict(int)
        self._truncation_reasons: set[str] = set()
        self._bytes_read = 0
        self._metadata_bytes_read = 0
        self._document_count = 0

    def _limited_read(self, path: Path, base: Path, per_file_limit: int) -> bytes | None:
        if self._bytes_read >= _MAX_TOTAL_TEXT_BYTES:
            self._truncation_reasons.add("total_text_bytes")
            return None
        remaining = _MAX_TOTAL_TEXT_BYTES - self._bytes_read
        data, reason = _no_symlink_read(path, base, min(per_file_limit, remaining))
        if reason:
            self._coverage[f"omitted_{reason}"] += 1
            if reason == "oversize":
                self._truncation_reasons.add("source_size_limit")
            return None
        if data is None:
            self._coverage["omitted_unreadable"] += 1
            return None
        self._bytes_read += len(data)
        return data

    def _source_path(self, path: Path) -> str:
        if path.is_relative_to(self.data.role_root):
            return (Path("Tailored CVs") / path.relative_to(self.data.role_root)).as_posix()
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            # StudioData permits exactly its established visible Tailored CVs alias.
            return self.data._relative(path)

    def _role_folders(self) -> tuple[list[Path], bool]:
        try:
            root = self.data._safe(self.data.role_root)
            if not root.is_dir():
                return [], False
        except (OSError, StudioDataError):
            return [], False
        result: list[Path] = []
        visited = 0
        truncated = False
        for current, dirs, _files in os.walk(root, followlinks=False):
            visited += 1
            if visited > _MAX_ROLE_DOCUMENTS * 4:
                truncated = True
                break
            path = Path(current)
            dirs[:] = sorted(d for d in dirs if not (path / d).is_symlink()
                             and (not d.startswith(("_", ".")) or d in _ROLE_COLLECTIONS))
            if path != root and path.name not in _ROLE_COLLECTIONS:
                result.append(path)
                dirs[:] = []
                if len(result) >= _MAX_ROLE_DOCUMENTS:
                    # We stop at the cap. Even if this happened to be the final
                    # folder, report the boundary conservatively as truncated.
                    truncated = True
                    break
        return result, truncated

    def _role_documents(self) -> Iterable[tuple[dict[str, Any], str]]:
        folders, truncated = self._role_folders()
        if truncated:
            self._truncation_reasons.add("role_folder_limit")
        for folder in folders:
            if self._document_count >= _MAX_ROLE_DOCUMENTS or self._bytes_read >= _MAX_TOTAL_TEXT_BYTES:
                self._truncation_reasons.add("total_text_bytes" if self._bytes_read >= _MAX_TOTAL_TEXT_BYTES
                                             else "role_document_limit")
                break
            try:
                jd_path = self.data._role_jd_target(folder)
            except (OSError, StudioDataError):
                jd_path = None
            if jd_path is None:
                continue
            raw = self._limited_read(jd_path, folder, _MAX_SOURCE_BYTES)
            if raw is None:
                self._coverage["omitted_role_jd"] += 1
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                self._coverage["omitted_invalid_utf8"] += 1
                self._coverage["omitted_role_jd"] += 1
                continue
            source = self._source_path(folder / "JD.md")
            fields = self.data._fields(text)
            title = next((line.lstrip()[2:].strip() for line in text.splitlines()
                          if line.lstrip().startswith("# ") and line.lstrip()[2:].strip()), None)
            company = next((fields.get(key) for key in ("company", "entreprise") if fields.get(key)), None)
            source_value = next((fields.get(key) for key in _SOURCE_FIELDS if fields.get(key)), None)
            source_url = _safe_http_url(source_value)
            doc = self._document("role_jd", source, text, source_url=source_url,
                                 captured_at=None, title=title, company=company,
                                 full_capture=False)
            self._coverage["role_jds"] += 1
            self._document_count += 1
            yield doc, text

    def _run_dirs(self) -> tuple[list[Path], bool]:
        try:
            safe_evidence = self.data._safe(self.evidence)
            if not safe_evidence.is_dir():
                return [], False
            candidates = []
            for path in safe_evidence.iterdir():
                if path.is_symlink() or not path.is_dir() or not _RUN_NAME.match(path.name):
                    continue
                candidates.append(path)
        except (OSError, StudioDataError):
            return [], False
        candidates.sort(key=lambda path: path.name, reverse=True)
        return candidates[:_MAX_RUNS], len(candidates) > _MAX_RUNS

    @staticmethod
    def _capture_roots(run: Path) -> list[Path]:
        roots: list[Path] = []
        seen: set[str] = set()
        for parent in (run / "hunt", run):
            for site in _SITE_IDS:
                lane = parent / site
                if lane.is_symlink() or not lane.is_dir():
                    continue
                captures = lane / "captures"
                if captures.is_symlink() or not captures.is_dir():
                    continue
                identity = os.path.abspath(captures)
                if identity not in seen:
                    roots.append(captures)
                    seen.add(identity)
        return roots

    def _capture_documents(self) -> Iterable[tuple[dict[str, Any], str]]:
        runs, run_truncated = self._run_dirs()
        if run_truncated:
            self._truncation_reasons.add("run_folder_limit")
        seen_capture_dirs: set[str] = set()
        total_capture_dirs = 0
        for run in runs:
            for capture_root in self._capture_roots(run):
                try:
                    boards = sorted(p for p in capture_root.iterdir()
                                    if p.name in _SITE_IDS and p.is_dir() and not p.is_symlink())
                except OSError:
                    self._coverage["omitted_unreadable_capture_roots"] += 1
                    continue
                for board in boards:
                    try:
                        capture_dirs = sorted(p for p in board.iterdir() if p.is_dir() and not p.is_symlink())
                    except OSError:
                        self._coverage["omitted_unreadable_capture_roots"] += 1
                        continue
                    for capture_dir in capture_dirs:
                        if self._bytes_read >= _MAX_TOTAL_TEXT_BYTES:
                            self._truncation_reasons.add("total_text_bytes")
                            return
                        identity = os.path.abspath(capture_dir)
                        if identity in seen_capture_dirs:
                            continue
                        seen_capture_dirs.add(identity)
                        total_capture_dirs += 1
                        if total_capture_dirs > _MAX_CAPTURE_DOCUMENTS:
                            self._truncation_reasons.add("capture_document_limit")
                            return
                        if self._document_count >= _MAX_ROLE_DOCUMENTS + _MAX_CAPTURE_DOCUMENTS:
                            self._truncation_reasons.add("document_limit")
                            return
                        capture_path = capture_dir / "capture.json"
                        capture_bytes = self._limited_read(capture_path, self.evidence, _MAX_CAPTURE_JSON_BYTES)
                        if capture_bytes is None:
                            self._coverage["omitted_capture_record"] += 1
                            continue
                        try:
                            capture = json.loads(capture_bytes.decode("utf-8"))
                        except (UnicodeError, ValueError, TypeError):
                            self._coverage["omitted_invalid_capture"] += 1
                            continue
                        if not isinstance(capture, dict) or capture.get("status") != "captured_full_detail":
                            self._coverage["omitted_non_full_capture"] += 1
                            continue
                        paths = capture.get("paths") if isinstance(capture.get("paths"), dict) else {}
                        body_name = paths.get("body_text") or "body.txt"
                        if not isinstance(body_name, str) or "\\" in body_name or Path(body_name).is_absolute() or any(
                                part in ("", ".", "..") for part in Path(body_name).parts):
                            self._coverage["omitted_unsafe_path"] += 1
                            continue
                        body_path = capture_dir / body_name
                        body_bytes = self._limited_read(body_path, self.evidence, _MAX_CAPTURE_BODY_BYTES)
                        if body_bytes is None:
                            self._coverage["omitted_capture_body"] += 1
                            continue
                        expected_hash = capture.get("body_sha256")
                        if (not isinstance(expected_hash, str) or
                                not re.fullmatch(r"[a-fA-F0-9]{64}", expected_hash) or
                                hashlib.sha256(body_bytes).hexdigest() != expected_hash.casefold()):
                            self._coverage["omitted_body_hash_mismatch"] += 1
                            continue
                        try:
                            text = body_bytes.decode("utf-8")
                        except UnicodeError:
                            self._coverage["omitted_invalid_utf8"] += 1
                            self._coverage["omitted_capture_body"] += 1
                            continue
                        source = self._source_path(body_path)
                        source_url = _safe_http_url(capture.get("canonical_url"), canonicalize=False)
                        captured_at = _captured_at(capture.get("captured_at_utc"))
                        ad_path = capture_dir / "ad.json"
                        title, company = self._capture_labels(ad_path)
                        doc = self._document("full_capture", source, text, source_url=source_url,
                                             captured_at=captured_at, title=title, company=company,
                                             full_capture=True, site=capture.get("site"),
                                             capture_status=capture.get("status"))
                        self._coverage["full_captures"] += 1
                        self._document_count += 1
                        yield doc, text

    def _capture_labels(self, path: Path) -> tuple[str | None, str | None]:
        remaining = _MAX_TOTAL_METADATA_BYTES - self._metadata_bytes_read
        if remaining <= 0:
            self._coverage["omitted_ad_metadata_budget"] += 1
            self._truncation_reasons.add("metadata_byte_limit")
            return None, None
        raw, reason = _no_symlink_read(path, self.evidence, min(_MAX_AD_JSON_BYTES, remaining))
        if raw is None:
            if reason not in {"missing", "not_file"}:
                self._coverage[f"omitted_ad_metadata_{reason or 'unreadable'}"] += 1
            return None, None
        self._metadata_bytes_read += len(raw)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError):
            self._coverage["omitted_ad_metadata_invalid_json"] += 1
            return None, None
        if not isinstance(payload, dict):
            return None, None
        def literal(*keys: str) -> str | None:
            for key in keys:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return _normal(value)
            return None
        return literal("title", "job_title"), literal("company", "organization", "hiring_organization")

    @staticmethod
    def _document(kind: str, source: str, text: str, *, source_url: str | None,
                  captured_at: str | None, title: str | None, company: str | None,
                  full_capture: bool, site: Any = None,
                  capture_status: Any = None) -> dict[str, Any]:
        return {
            "id": _document_id(kind, source, source_url, captured_at),
            "source": source,
            "kind": kind,
            "full_capture": full_capture,
            "source_url": source_url,
            "captured_at": captured_at,
            "date_status": "known" if captured_at else "unknown",
            "title": title,
            "company": company,
            "site": site if isinstance(site, str) and site else None,
            "capture_status": capture_status if isinstance(capture_status, str) else None,
        }

    @staticmethod
    def _task_hits(text: str) -> Iterable[tuple[str, str, int]]:
        active = False
        cursor = 0
        for line in text.splitlines(keepends=False):
            line_start = text.find(line, cursor)
            if line_start < 0:
                line_start = cursor
            cursor = line_start + len(line) + 1
            trimmed = line.strip()
            if not trimmed:
                continue
            heading = re.sub(r"^\s{0,3}#{1,6}\s*", "", trimmed).strip().strip("*: ")
            if _TASK_HEADINGS.fullmatch(heading):
                active = True
                continue
            if trimmed.startswith("#") and not trimmed.startswith("##"):
                active = False
                continue
            if trimmed.startswith("#") and not _TASK_HEADINGS.fullmatch(heading):
                active = bool(_TASK_HEADINGS.fullmatch(heading))
                continue
            inline = re.match(r"^\s*(?:missions?|responsabilit[eé]s?|duties|responsibilities|activit[eé]s?)\s*:\s*(\S.*)$", line, re.I)
            if inline:
                active = True
                task = inline.group(1).strip()
                if task:
                    start = line_start + line.find(task)
                    yield _quote(text, start, start + len(task)), task[:_MAX_QUOTE_CHARS], start
                continue
            if _OTHER_SECTION.fullmatch(heading):
                active = False
                continue
            if not active:
                continue
            content = _BULLET.sub("", trimmed)
            if not content:
                continue
            if _BULLET.match(trimmed) or _TASK_VERB.match(content):
                start = line_start + line.find(trimmed)
                quote = _quote(text, start, start + len(trimmed))
                term = _normal(_BULLET.sub("", quote))[:_MAX_QUOTE_CHARS]
                if term:
                    yield quote, term, start

    @staticmethod
    def _skill_hits(text: str, doc: dict[str, Any]) -> Iterable[tuple[str, str, str, int]]:
        for skill in _SKILLS:
            match = skill.pattern.search(text)
            if match:
                yield skill.category, skill.term, _quote(text, *match.span()), match.start()
        for match in _EXPERIENCE.finditer(text):
            phrase = _normal(match.group(1))
            if phrase:
                yield "experience_years", phrase, _quote(text, *match.span()), match.start()
        for quote, term, start in JDInsights._task_hits(text):
            yield "tasks", term, quote, start

    def catalog(self) -> dict[str, Any]:
        """Return source rows plus literal-quote term groups for dashboard filters."""
        # Each instance is intended for one projection. Reset counters so callers
        # can safely request the same deterministic snapshot more than once.
        self._coverage = defaultdict(int)
        self._truncation_reasons = set()
        self._bytes_read = 0
        self._metadata_bytes_read = 0
        self._document_count = 0
        docs_and_text = list(self._role_documents())
        docs_and_text.extend(self._capture_documents())
        documents = [row for row, _text in docs_and_text]
        hits_by_category: dict[str, dict[str, list[dict[str, Any]]]] = {
            key: defaultdict(list) for key, _label in _CATEGORIES
        }
        hit_count = 0
        term_count = 0
        for doc, text in docs_and_text:
            per_document: set[tuple[str, str, int]] = set()
            for category, term, quote, position in self._skill_hits(text, doc):
                canonical_term = _normal(term)
                identity = (category, canonical_term.casefold(), position)
                if identity in per_document:
                    continue
                per_document.add(identity)
                normalized_key = canonical_term.casefold()
                if normalized_key not in hits_by_category[category] and term_count >= _MAX_TERMS:
                    self._coverage["omitted_terms"] += 1
                    self._truncation_reasons.add("term_limit")
                    continue
                if hit_count >= _MAX_HITS:
                    self._coverage["omitted_hits"] += 1
                    self._truncation_reasons.add("hit_limit")
                    continue
                if normalized_key not in hits_by_category[category]:
                    term_count += 1
                hit = {
                    "document_id": doc["id"],
                    "term": canonical_term,
                    "category": category,
                    "quote": quote,
                    "source": doc["source"],
                    "kind": doc["kind"],
                    "full_capture": doc["full_capture"],
                    "source_url": doc["source_url"],
                    "captured_at": doc["captured_at"],
                    "date_status": doc["date_status"],
                }
                hits_by_category[category][normalized_key].append(hit)
                hit_count += 1
        categories = []
        for category_id, label in _CATEGORIES:
            terms = []
            for term_key, hits in sorted(hits_by_category[category_id].items(), key=lambda row: row[0]):
                display = min((hit["term"] for hit in hits), key=lambda value: (value.casefold(), value))
                ordered_hits = sorted(hits, key=lambda hit: (hit["source"], hit["quote"], hit["document_id"]))
                terms.append({"term": display, "count": len(ordered_hits), "hits": ordered_hits})
            categories.append({"id": category_id, "label": label, "terms": terms})
        captures = [doc for doc in documents if doc["full_capture"]]
        unique_urls = {doc["source_url"] for doc in captures if doc["source_url"]}
        document_omission_keys = {
            "omitted_role_jd", "omitted_capture_record", "omitted_non_full_capture",
            "omitted_unsafe_path", "omitted_capture_body", "omitted_body_hash_mismatch",
            "omitted_invalid_capture",
        }
        omitted_count = sum(self._coverage[key] for key in document_omission_keys)
        document_scan_caps = {
            "role_folder_limit", "role_document_limit", "run_folder_limit",
            "capture_document_limit", "document_limit", "total_text_bytes",
        }
        coverage = {
            "documents": len(documents),
            "role_jds": self._coverage["role_jds"],
            "full_captures": self._coverage["full_captures"],
            "unique_full_capture_urls": len(unique_urls),
            "undated_documents": sum(doc["captured_at"] is None for doc in documents),
            "omitted_documents": omitted_count,
            "omitted_hits": self._coverage["omitted_hits"],
            "omitted_terms": self._coverage["omitted_terms"],
            "bytes_read": self._bytes_read,
            "metadata_bytes_read": self._metadata_bytes_read,
            "truncated": bool(self._truncation_reasons),
            "truncation_reasons": sorted(self._truncation_reasons),
            "omission_counts": dict(sorted(self._coverage.items())),
            "omitted_documents_exact": not bool(document_scan_caps & self._truncation_reasons),
        }
        return {
            "schema_version": 1,
            "categories": categories,
            "documents": documents,
            "coverage": coverage,
        }
