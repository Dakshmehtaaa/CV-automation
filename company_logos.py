"""Safe, offline-only access to the reviewed company-logo library."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import threading
import xml.etree.ElementTree as ET


MAX_MANIFEST_BYTES = 1_000_000
MAX_LOGO_BYTES = 1_000_000
_SVG_STATUS = "verified_safe_transparent_svg"
_PNG_STATUS = "verified_alpha_transparent_png"
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_LOGO_ID = re.compile(r"^[a-f0-9]{20}$")


class CompanyLogoError(ValueError):
    def __init__(self, message: str, status: int = 404, code: str = "not_found"):
        super().__init__(message)
        self.status = status
        self.code = code


class CompanyLogoLibrary:
    """Expose only manifest-listed verified SVG/PNG files under one local folder."""

    def __init__(self, asset_root: Path):
        self.asset_root = Path(asset_root).absolute()
        self.manifest_path = self.asset_root / "manifest.json"
        self._lock = threading.RLock()
        self._signature = None
        self._entries: list[dict] = []
        self._by_id: dict[str, dict] = {}

    @staticmethod
    def _safe_relative(value: object) -> PurePosixPath | None:
        if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
            return None
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            return None
        return path

    def _safe_file(self, path: Path, *, maximum: int) -> tuple[Path, os.stat_result] | None:
        try:
            if self.asset_root.is_symlink() or path.is_symlink():
                return None
            root = self.asset_root.resolve(strict=True)
            current = self.asset_root
            relative = path.relative_to(self.asset_root)
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    return None
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            info = resolved.stat()
            if not resolved.is_file() or info.st_size <= 0 or info.st_size > maximum:
                return None
            return resolved, info
        except (OSError, ValueError):
            return None

    @staticmethod
    def _safe_metadata(row: object) -> dict | None:
        if not isinstance(row, dict):
            return None
        company = row.get("company")
        path_value = row.get("path")
        safe_path = CompanyLogoLibrary._safe_relative(path_value)
        fmt = row.get("format")
        verification = row.get("verification_status")
        expected_size = row.get("bytes")
        expected_hash = row.get("sha256")
        if (not isinstance(company, str) or not company.strip() or safe_path is None or
                fmt not in {"svg", "png"} or safe_path.suffix.lower() != f".{fmt}" or
                (fmt == "svg" and verification != _SVG_STATUS) or
                (fmt == "png" and verification != _PNG_STATUS) or
                isinstance(expected_size, bool) or not isinstance(expected_size, int) or
                expected_size <= 0 or expected_size > MAX_LOGO_BYTES or
                not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash)):
            return None
        role_ids_set = set()
        role_id = row.get("role_id")
        if isinstance(role_id, str) and re.fullmatch(r"[a-fA-F0-9]{24}", role_id):
            role_ids_set.add(role_id)
        raw_role_ids = row.get("role_ids")
        if isinstance(raw_role_ids, list):
            for rid in raw_role_ids:
                if isinstance(rid, str) and re.fullmatch(r"[a-fA-F0-9]{24}", rid):
                    role_ids_set.add(rid)
        role_ids = sorted(role_ids_set)

        cleaned_company = company.strip()
        raw_aliases = row.get("aliases")
        alias_list = [cleaned_company]
        if isinstance(raw_aliases, list):
            for alias in raw_aliases:
                if isinstance(alias, str) and alias.strip():
                    alias_list.append(alias.strip())
        seen_aliases = set()
        aliases = []
        for a in alias_list:
            if a not in seen_aliases:
                seen_aliases.add(a)
                aliases.append(a)

        categories = row.get("categories") if isinstance(row.get("categories"), list) else []
        return {"id": hashlib.sha256(str(safe_path).encode("utf-8")).hexdigest()[:20],
                "company": cleaned_company, "aliases": aliases, "role_ids": role_ids,
                "path": str(safe_path), "format": fmt, "bytes": expected_size,
                "sha256": expected_hash.lower(), "categories": [c for c in categories if isinstance(c, str)],
                "render_hint": row.get("render_hint") if isinstance(row.get("render_hint"), str) else None}

    def _load(self) -> tuple[list[dict], dict[str, dict]]:
        safe = self._safe_file(self.manifest_path, maximum=MAX_MANIFEST_BYTES)
        if safe is None:
            return [], {}
        path, info = safe
        signature = (info.st_ino, info.st_mtime_ns, info.st_size)
        with self._lock:
            if signature == self._signature:
                return self._entries, self._by_id
            try:
                raw = path.read_bytes()
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise ValueError("manifest too large")
                document = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeError, ValueError):
                self._signature, self._entries, self._by_id = signature, [], {}
                return self._entries, self._by_id
            grouped: dict[str, dict] = {}
            for source in document if isinstance(document, list) else []:
                item = self._safe_metadata(source)
                if item is None:
                    continue
                file_info = self._safe_file(self.asset_root / item["path"], maximum=MAX_LOGO_BYTES)
                if file_info is None or file_info[1].st_size != item["bytes"]:
                    continue
                key = self._brand_key(item["company"])
                if not key:
                    continue
                prior = grouped.get(key)
                if prior is None:
                    grouped[key] = item
                else:
                    prior["role_ids"] = sorted(set(prior["role_ids"] + item["role_ids"]))
                    seen = set(prior["aliases"])
                    for a in item["aliases"]:
                        if a not in seen:
                            seen.add(a)
                            prior["aliases"].append(a)
                    # Prefer the shortlist capture when the same exact company is
                    # present in the broader reference collection too.
                    if "shortlist" in item["categories"] and "shortlist" not in prior["categories"]:
                        item["role_ids"] = prior["role_ids"]
                        item["aliases"] = prior["aliases"]
                        grouped[key] = item
            entries = sorted(grouped.values(), key=lambda item: item["company"].casefold())
            by_id = {item["id"]: item for item in entries}
            self._signature, self._entries, self._by_id = signature, entries, by_id
            return self._entries, self._by_id

    @staticmethod
    def _brand_key(value: str) -> str:
        import unicodedata
        decomposed = unicodedata.normalize("NFKD", value).casefold()
        return "".join(char for char in decomposed if char.isalnum() and not unicodedata.combining(char))

    def catalog(self) -> dict:
        entries, _by_id = self._load()
        return {"items": [{"id": item["id"], "company": item["company"],
                           "aliases": item["aliases"], "role_ids": item["role_ids"],
                           "format": item["format"], "render_hint": item["render_hint"],
                           "url": f"/api/company-logos/{item['id']}"}
                          for item in entries], "total": len(entries)}

    def asset(self, logo_id: str) -> tuple[bytes, str, str]:
        if not isinstance(logo_id, str) or not _LOGO_ID.fullmatch(logo_id):
            raise CompanyLogoError("This company logo was not found.")
        _entries, by_id = self._load()
        item = by_id.get(logo_id)
        if item is None:
            raise CompanyLogoError("This company logo was not found.")
        safe = self._safe_file(self.asset_root / item["path"], maximum=MAX_LOGO_BYTES)
        if safe is None:
            raise CompanyLogoError("This company logo is no longer available.")
        path, info = safe
        try:
            content = path.read_bytes()
        except OSError as error:
            raise CompanyLogoError("This company logo is no longer available.") from error
        if len(content) != item["bytes"] or len(content) > MAX_LOGO_BYTES or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise CompanyLogoError("This company logo did not match its saved integrity check.", 404, "invalid_asset")
        if item["format"] == "svg":
            try:
                svg = content.decode("utf-8")
            except UnicodeError as error:
                raise CompanyLogoError("This company logo is not safe to display.", 404, "invalid_asset") from error
            unsafe_svg = bool(re.search(r"<!DOCTYPE|<!ENTITY|<\s*(?:script|foreignObject|iframe|object|embed)\b|@import\b", svg, re.I))
            try:
                root = ET.fromstring(svg) if not unsafe_svg else None
            except ET.ParseError:
                root = None
                unsafe_svg = True
            if root is not None and root.tag.rsplit("}", 1)[-1].casefold() != "svg":
                unsafe_svg = True
            if root is not None:
                for element in root.iter():
                    if element.tag.rsplit("}", 1)[-1].casefold() in {"script", "foreignobject", "iframe", "object", "embed"}:
                        unsafe_svg = True
                    for name, value in element.attrib.items():
                        local = name.rsplit("}", 1)[-1].casefold()
                        if local.startswith("on") or (local in {"href", "src"} and not value.strip().startswith("#")):
                            unsafe_svg = True
                        for uri in re.findall(r"url\(\s*(['\"]?)(.*?)\1\s*\)", value, re.I):
                            if not uri[1].strip().startswith("#"):
                                unsafe_svg = True
                    for text in (element.text, element.tail):
                        if text and ("@import" in text.casefold() or
                                     any(not match[1].strip().startswith("#") for match in
                                         re.findall(r"url\(\s*(['\"]?)(.*?)\1\s*\)", text, re.I))):
                            unsafe_svg = True
            if unsafe_svg or root is None:
                raise CompanyLogoError("This company logo is not safe to display.", 404, "invalid_asset")
            mime = "image/svg+xml; charset=utf-8"
        else:
            if not self._safe_transparent_png(content):
                raise CompanyLogoError("This company logo is not a verified transparent PNG.", 404, "invalid_asset")
            mime = "image/png"
        return content, mime, '"' + item["sha256"] + '"'

    @staticmethod
    def _safe_transparent_png(content: bytes) -> bool:
        if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) < 33:
            return False
        bit_depth, color_type = content[24], content[25]
        if bit_depth != 8 or color_type not in {3, 4, 6}:
            return False
        palette_seen = transparency_seen = end_seen = False
        offset = 8
        while offset + 12 <= len(content):
            length = int.from_bytes(content[offset:offset + 4], "big")
            chunk_end = offset + 12 + length
            if chunk_end > len(content):
                return False
            kind = content[offset + 4:offset + 8]
            payload = content[offset + 8:offset + 8 + length]
            if kind == b"PLTE":
                palette_seen = bool(length and length % 3 == 0)
            elif kind == b"tRNS":
                transparency_seen = bool(length and any(alpha < 255 for alpha in payload))
            elif kind == b"IEND":
                end_seen = length == 0 and chunk_end == len(content)
                break
            offset = chunk_end
        if not end_seen:
            return False
        if color_type == 3:
            return palette_seen and transparency_seen
        return True
