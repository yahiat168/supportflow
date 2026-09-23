"""Version comparison for the mixed formats in the corpus ("3.4", "1.0", "2026-01")."""
from __future__ import annotations

import re


def parse_version(v: str) -> tuple[str, tuple[int, ...]] | None:
    v = v.strip()
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", v):
        return "date", tuple(int(x) for x in m.groups() if x)
    if re.fullmatch(r"\d+(\.\d+)*", v):
        return "semver", tuple(int(x) for x in v.split("."))
    return None


def compare_versions(a: str, b: str) -> int | None:
    """1 if a is newer, -1 if b is newer, 0 if equal, None if not comparable (different formats)."""
    pa, pb = parse_version(a), parse_version(b)
    if not pa or not pb or pa[0] != pb[0]:
        return None
    return (pa[1] > pb[1]) - (pa[1] < pb[1])


TRUST_RANK = {"official": 3, "internal": 2, "community": 1, "unverified": 0}
