"""Read-only Android/Termux memory budget for non-trading research tasks.

Use MemAvailable rather than MemFree and, where readable, the smaller cgroup
memory budget. No heavy-work approval is issued when available memory cannot
be measured. This protects OPTIONAL daily maintenance only; it does not
approve trades or claim Android will keep the app alive in the background.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

MIB = 1024 * 1024
MEMINFO = Path("/proc/meminfo")
CGROUP_V2_MAX = Path("/sys/fs/cgroup/memory.max")
CGROUP_V2_USED = Path("/sys/fs/cgroup/memory.current")
CGROUP_V1_MAX = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
CGROUP_V1_USED = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")

# These steps use independently bounded limits. No change to audit criteria.
CAPS = {
    "official_annual_pdf_source_index": (3, 6),
    "official_discovered_annual_catchup": (8, 15),
    "legacy_annual_verify": (1, 2),
    "sector_refresh": (100, 200),
    "quarterly_refresh": (150, 300),
}
ONE_PDF_STEPS = {
    "archived_pdf_section_review",
    "annual_pdf_numeric_evidence_review",
    "archived_pdf_prior_year_comparatives_research_only",
    "archived_pdf_yoy_research_only",
    "archived_pdf_vs_original_annual_xbrl_diagnostic",
}
CAP_FLAG = {
    "official_annual_pdf_source_index": "--limit",
    "official_discovered_annual_catchup": "--limit",
    "legacy_annual_verify": "--limit",
    "sector_refresh": "--limit",
    "quarterly_refresh": "--limit",
}


def _memory_kib(text: str) -> dict[str, int]:
    values = {}
    for line in text.splitlines():
        m = re.fullmatch(r"(MemTotal|MemAvailable):\s*(\d+)\s+kB\s*", line)
        if m:
            values[m.group(1)] = int(m.group(2))
    if values.get("MemTotal", 0) < 128 * 1024:
        raise ValueError("UNRELIABLE_MEMTOTAL")
    if not 0 <= values.get("MemAvailable", -1) <= values["MemTotal"]:
        raise ValueError("UNRELIABLE_MEMAVAILABLE")
    return values


def _optional_integer(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="ascii").strip()
        if raw == "max":
            return None
        if raw.isdecimal():
            return int(raw)
    except (OSError, ValueError, UnicodeError):
        pass
    return None


def memory_snapshot(*, meminfo: str | None = None,
                    cgroup_limit: int | None = None,
                    cgroup_used: int | None = None) -> dict:
    """Bound optional heavy work by available memory, never guessed RAM size."""
    try:
        if meminfo is None:
            meminfo = MEMINFO.read_text(encoding="ascii")
        values = _memory_kib(meminfo)
        total_mib = values["MemTotal"] * 1024 // MIB
        available_mib = values["MemAvailable"] * 1024 // MIB
        source = "PROC_MEMINFO"
        if cgroup_limit is None:
            cgroup_limit = _optional_integer(CGROUP_V2_MAX)
            cgroup_used = _optional_integer(CGROUP_V2_USED)
            if cgroup_limit is None or cgroup_used is None:
                cgroup_limit = _optional_integer(CGROUP_V1_MAX)
                cgroup_used = _optional_integer(CGROUP_V1_USED)
        if (isinstance(cgroup_limit, int) and isinstance(cgroup_used, int)
                and 128 * MIB <= cgroup_limit < values["MemTotal"] * 1024
                and 0 <= cgroup_used <= cgroup_limit):
            total_mib = cgroup_limit // MIB
            available_mib = min(available_mib,
                                (cgroup_limit - cgroup_used) // MIB)
            source = "PROC_MEMINFO_AND_CGROUP_LIMIT"
        tier = ("LOW" if total_mib < 4096 else
                "MEDIUM" if total_mib < 8192 else "HIGH")
        reserve = (max(320, (total_mib * 16 + 99) // 100) if tier == "LOW"
                   else max(512, (total_mib * 12 + 99) // 100)
                   if tier == "MEDIUM"
                   else max(768, (total_mib * 10 + 99) // 100))
        return {
            "state": "MEMORY_OK" if available_mib >= reserve
                     else "MEMORY_PRESSURE",
            "source": source, "tier": tier,
            "ram_total_mib": total_mib,
            "ram_available_mib": available_mib,
            "reserved_free_mib": reserve,
            "heavy_research_allowed": available_mib >= reserve,
            "orders": "DISABLED",
        }
    except (OSError, ValueError, UnicodeError, TypeError):
        return {
            "state": "MEMORY_UNMEASURABLE",
            "source": "UNAVAILABLE",
            "tier": "LOW",
            "ram_total_mib": None,
            "ram_available_mib": None,
            "reserved_free_mib": None,
            "heavy_research_allowed": False,
            "orders": "DISABLED",
        }


def adaptive_command(name: str, command: list[str], tier: str) -> list[str]:
    """Reduce only whitelisted research batch-size args. Never increase them."""
    output = list(command)
    if tier not in ("LOW", "MEDIUM"):
        return output
    if name in ONE_PDF_STEPS:
        cap, flag = 1, "--max-files"
    elif name in CAPS:
        cap = CAPS[name][0 if tier == "LOW" else 1]
        flag = CAP_FLAG[name]
    else:
        return output
    if flag not in output:
        return output
    index = output.index(flag)
    if index + 1 >= len(output):
        return output
    try:
        existing = int(output[index + 1])
    except ValueError:
        return output
    if existing > 0:
        output[index + 1] = str(min(existing, cap))
    return output


def main() -> int:
    print(json.dumps(memory_snapshot(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
