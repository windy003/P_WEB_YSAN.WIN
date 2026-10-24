"""Offline IP geolocation backed by the ip2region xdb database."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ip2region.searcher as xdb
import ip2region.util as util

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR_ENV = "IP2REGION_DATA_DIR"
COUNTRY_NAMES_FILE = PROJECT_ROOT / "data" / "country_names_zh.json"

DATA_FILES = {
    4: "ip2region_v4.xdb",
    6: "ip2region_v6.xdb",
}
IP_VERSIONS = {
    4: util.IPv4,
    6: util.IPv6,
}
VERSION_LABELS = {
    4: "IPv4",
    6: "IPv6",
}
EMPTY_VALUE = "0"


def _load_country_names() -> dict[str, str]:
    try:
        with COUNTRY_NAMES_FILE.open(encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, ValueError):
        logger.warning("Could not load country name table: %s", COUNTRY_NAMES_FILE)
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(code).upper(): str(name)
        for code, name in payload.items()
        if str(code).strip()
    }


COUNTRY_NAMES_ZH = _load_country_names()


@dataclass(frozen=True)
class IpRegion:
    """One parsed ip2region record: country|province|city|isp|iso."""

    country: str = ""
    province: str = ""
    city: str = ""
    isp: str = ""
    iso: str = ""


def data_directory() -> Path:
    """Return the configured xdb directory, defaulting to ./data."""
    configured = os.environ.get(DATA_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_DATA_DIR


def _clean(value: str) -> str:
    return (value or "").strip()


def _is_meaningful(value: str) -> bool:
    value = _clean(value)
    return bool(value) and value != EMPTY_VALUE and value.lower() != "reserved"


def normalize_region_name(value: str) -> str:
    """Normalize a province/city name for loose comparisons.

    浙江省 -> 浙江, 杭州市 -> 杭州, 香港特别行政区 -> 香港.
    """
    value = _clean(value)
    if not _is_meaningful(value):
        return ""
    for suffix in ("特别行政区", "自治区", "省", "市", "地区"):
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def parse_region(raw: str) -> Optional[IpRegion]:
    """Split a raw ip2region record into its five fields."""
    if not raw:
        return None
    fields = [_clean(part) for part in str(raw).split("|")]
    fields.extend([""] * (5 - len(fields)))
    region = IpRegion(*fields[:5])
    return region if region.country else None


def format_region(raw: str) -> str:
    """Render a raw ip2region record as a Chinese location string."""
    region = parse_region(raw)
    if region is None:
        return "归属地未知"
    if region.country.lower() == "reserved":
        return "保留地址"
    if not _is_meaningful(region.country):
        return "归属地未知"

    if region.country == "中国":
        country = "中国"
    else:
        country = COUNTRY_NAMES_ZH.get(_clean(region.iso).upper(), region.country)

    pieces = [country]
    province = _clean(region.province) if _is_meaningful(region.province) else ""
    city = _clean(region.city) if _is_meaningful(region.city) else ""
    if province:
        pieces.append(province)
    if city and city != province:
        pieces.append(city)

    location = " · ".join(pieces)
    if _is_meaningful(region.isp):
        location = f"{location}（{region.isp}）"
    return location or "归属地未知"


def _parse_address(ip_address: str):
    try:
        address = ipaddress.ip_address(_clean(ip_address))
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _is_non_public(address) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def _read_build_date(path: Path) -> str:
    """Read the xdb createdAt field (uint32 LE at offset 4)."""
    try:
        with path.open("rb") as file:
            header = file.read(8)
        if len(header) < 8:
            return ""
        timestamp = int.from_bytes(header[4:8], "little")
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class IpLocator:
    """Lazily loads xdb files into memory and answers IP queries."""

    def __init__(self, directory: Optional[os.PathLike | str] = None) -> None:
        self.directory = Path(directory) if directory else data_directory()
        self._searchers: dict[int, object] = {}
        self._file_info: dict[int, dict] = {}
        self._unavailable: set[int] = set()
        self._lock = threading.Lock()

    def _path(self, version: int) -> Path:
        return self.directory / DATA_FILES[version]

    def _load(self, version: int):
        path = self._path(version)
        if version in self._searchers:
            return self._searchers[version]

        with self._lock:
            if version in self._searchers:
                return self._searchers[version]
            if version in self._unavailable:
                return None

            if not path.is_file():
                self._unavailable.add(version)
                logger.warning("IP2REGION database not found: %s", path)
                return None

            try:
                util.verify_from_file(str(path))
                content = util.load_content_from_file(str(path))
                searcher = xdb.new_with_buffer(IP_VERSIONS[version], content)
            except Exception:
                self._unavailable.add(version)
                logger.exception("Failed to load IP2REGION database: %s", path)
                return None

            info = {
                "version": version,
                "label": VERSION_LABELS[version],
                "build_date": _read_build_date(path),
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
            self._searchers[version] = searcher
            self._file_info[version] = info
            logger.info(
                "Loaded IP2REGION database: %s (built %s, SHA-256 %s)",
                path,
                info["build_date"],
                info["sha256"][:12],
            )
            return searcher

    def searcher_for(self, version: int):
        return self._load(version)

    def _search_raw(self, address) -> Optional[str]:
        searcher = self.searcher_for(address.version)
        if searcher is None:
            return None
        try:
            return searcher.search(str(address))
        except Exception:
            logger.warning("IP location lookup failed: %s", address, exc_info=True)
            return None

    def lookup(self, ip_address: str) -> str:
        address = _parse_address(ip_address)
        if address is None:
            return "未知 IP"
        if _is_non_public(address):
            return "本地或内网地址"

        raw = self._search_raw(address)
        if raw is None:
            if address.version == 6:
                return "IPv6 地址（暂无归属地数据）"
            return "归属地未知"
        return format_region(raw)

    def parts(self, ip_address: str) -> Optional[IpRegion]:
        address = _parse_address(ip_address)
        if address is None or _is_non_public(address):
            return None
        raw = self._search_raw(address)
        region = parse_region(raw) if raw else None
        if region is None or region.country.lower() == "reserved":
            return None
        return region

    def file_info(self) -> list[dict]:
        for version in DATA_FILES:
            if self._path(version).is_file():
                self._load(version)
        return [
            dict(info)
            for _, info in sorted(self._file_info.items())
        ]


_locator: Optional[IpLocator] = None
_locator_lock = threading.Lock()


def get_locator() -> IpLocator:
    global _locator
    if _locator is None:
        with _locator_lock:
            if _locator is None:
                _locator = IpLocator()
    return _locator


def reset_locator() -> None:
    """Drop the cached locator (useful for tests)."""
    global _locator
    with _locator_lock:
        _locator = None


def lookup_ip_location(ip_address: str) -> str:
    return get_locator().lookup(ip_address)


def lookup_ip_parts(ip_address: str) -> Optional[IpRegion]:
    return get_locator().parts(ip_address)


def database_file_info() -> list[dict]:
    return get_locator().file_info()
