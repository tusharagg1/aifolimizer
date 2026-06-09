"""Guard against doc/code drift on MCP tool count and skill count.

Counts the source of truth (@mcp.tool() decorators in backend/mcp_server.py
and skill folders containing SKILL.md under .claude/skills/) and asserts the
numbers cited in the top-level docs match.

Scope:
  - CLAUDE.md, README.md, AGENTS.md (live, externally consumed)
  - .claude/context/architecture.md (live architecture reference)
  - backend/mcp_server.py docstrings (so a stale "13 skills" docstring fails)

Excluded by design:
  - .claude/context/changes.md — historical change log; numbers describe
    state at a particular date and SHOULD NOT be retroactively edited.

Run locally:    python backend/scripts/check_doc_counts.py
Run in CI:      added as a step in .github/workflows/ci.yml after lint.

Exits 0 when everything matches, 1 with a diff report otherwise.

Why: an external code review caught these counts drifting (CLAUDE.md said
14/32 tools and 13/16 skills while the code already had 80 tools and 21
skills). A second adversarial review caught even more drift forms — lowercase
headings, code-comment counts, 'All N skills' phrasings — that the first
guard missed. Regexes below have since been broadened to cover those.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
_MCP = _ROOT / "backend" / "mcp_server.py"
_SKILLS_DIR = _ROOT / ".claude" / "skills"
_ADAPTERS_DIR = _ROOT / "backend" / "app" / "services" / "data_sources"

# Each entry: (filename, regex, kind). The regex MUST capture the count in
# group 1. Kinds: "tools" or "skills". Patterns are matched case-insensitive
# and across the whole file. To allow legitimate references to a smaller
# subset (e.g. "core 13 of 21"), preface those with the literal word "core".
# Patterns include a (?<!core\s) negative lookbehind where ambiguity matters.

_TOOL_PATTERNS: list[str] = [
    # bold: **80 MCP tools**
    r"\*\*(\d+)\s+MCP\s+tools?\*\*",
    # FastMCP — 80 tools / FastMCP, 80 tools / (FastMCP, 80 tools)
    r"FastMCP[^\d\n]{0,12}(\d+)\s+tools?",
    # heading or bare: '## MCP tools (80)' / '## MCP Tools (80 total ...)'
    r"##\s*MCP\s+tools?\s*\((\d+)",
    # comment form: '# 80 MCP tools' or '│   # 80 MCP tools'
    r"#\s*(\d+)\s+MCP\s+tools?",
    # 'MCP server (80 tools)' (AGENTS.md form) and 'MCP tools (80 total)'
    r"MCP\s+(?:server|tools?)\s*\((\d+)\s+(?:tools?|total)",
    # docstring form: '"""... 80 institutional analysis frameworks'
    # NOT a tool count by itself — but mcp_server.py used "13 institutional
    # analysis frameworks" when it should match skills, handled below.
]

_ADAPTER_PATTERNS: list[str] = [
    # README + CLAUDE styles
    r"\*\*(\d+)\s+(?:data|market[- ]data)\s+adapters?\*\*",
    r"\((\d+)\s+(?:data|market[- ]data)\s+adapters?\)",
    r"(\d+)\s+(?:data|market[- ]data)\s+adapters?\b",
]

_SKILL_PATTERNS: list[str] = [
    # bold: **21 institutional analysis skills**
    r"\*\*(\d+)\s+institutional\s+analysis\s+skills?\*\*",
    # parens: (21 institutional analysis skills)
    r"\((\d+)\s+institutional\s+analysis\s+skills?\)",
    # comment: # 21 institutional analysis skills
    r"#\s*(\d+)\s+institutional\s+analysis\s+skills?",
    # heading: '## Analysis Skills (21 ...)' — but tolerates "(21 in ...)"
    r"##\s*Analysis\s+Skills\s*\((\d+)\s",
    # AGENTS.md: '`.claude/skills/` — 21 analysis skills'
    r"\.claude/skills/`?\s*[—\-]\s*(\d+)\s+analysis\s+skills?",
    # 'All N skills' (table cell, README/CLAUDE) — must NOT be 'core' / 'core N'
    r"(?<!core\s)\bAll\s+(\d+)\s+skills?\b",
    # architecture.md file-index row '.claude/skills/*/SKILL.md | 21 skills'
    r"SKILL\.md`?\s*\|\s*(\d+)\s+skills?",
    # static list (21 skills) / list of all 21 skills
    r"static\s+list\s*\((\d+)\s+skills?",
    # docstring in mcp_server.py: '13 institutional analysis frameworks' /
    # '13 institutional analysis skills'
    r"(\d+)\s+institutional\s+analysis\s+(?:frameworks|skills?)",
]

# Map filename -> (patterns, kind) pairs for the scanner.
_TARGETS: list[tuple[Path, str, str]] = []
for filename in (
    "CLAUDE.md",
    "README.md",
    "AGENTS.md",
    ".claude/context/architecture.md",
    "backend/mcp_server.py",
):
    full = _ROOT / filename
    for pat in _TOOL_PATTERNS:
        _TARGETS.append((full, pat, "tools"))
    for pat in _SKILL_PATTERNS:
        _TARGETS.append((full, pat, "skills"))
    for pat in _ADAPTER_PATTERNS:
        _TARGETS.append((full, pat, "adapters"))


# Allow-list: prefixes that, when they appear immediately before THIS captured
# number, mean the number is intentionally NOT the live total. We check the
# 24 chars preceding the match — earlier "core 13" mentions on the same line
# don't shadow a fresh drift later in the line. Examples that pass:
#   "core 13 of 21"            — explicit subset disclosure
#   "highlights core 13"        — table-curation disclosure
#   "Backtest 13 codified-rule" — describes the rule subset
_ALLOWLIST_PREFIX_TOKENS = (
    "core ",
    "codified-rule ",
    "codified rule ",
    "subset of ",
    "highlights core ",
    "of 21 ",
    "of 80 ",
)


def count_mcp_tools() -> int:
    # Match bare @mcp.tool() AND parameterized @mcp.tool(...) on a single
    # line, plus imperative mcp.add_tool(...). Prevents a future
    # @mcp.tool(name="foo") from silently dropping the count.
    text = _MCP.read_text(encoding="utf-8")
    decorator = len(re.findall(r"@mcp\.tool\s*\(", text))
    add_tool = len(re.findall(r"\bmcp\.add_tool\s*\(", text))
    return decorator + add_tool


def count_skills() -> int:
    if not _SKILLS_DIR.is_dir():
        return 0
    return sum(1 for child in _SKILLS_DIR.iterdir() if child.is_dir() and (child / "SKILL.md").is_file())


def count_adapters() -> int:
    """Files matching `*_src.py` under `data_sources/` are the adapters.

    Excludes base.py / circuit_breaker.py / symbol_classifier.py / __init__.py
    by construction. README and CLAUDE.md cite this number; drift breaks user
    trust in the rest of the doc.
    """
    if not _ADAPTERS_DIR.is_dir():
        return 0
    return sum(1 for child in _ADAPTERS_DIR.iterdir() if child.is_file() and child.name.endswith("_src.py"))


def _line_for(text: str, start: int) -> tuple[int, str]:
    line_no = text.count("\n", 0, start) + 1
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    if line_end < 0:
        line_end = len(text)
    return line_no, text[line_start:line_end]


def main() -> int:
    # Windows consoles default to cp1252; failure lines quote source text that
    # can contain em-dashes/arrows. Force UTF-8 so local runs don't crash on
    # the very drift they are meant to report.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    actual = {
        "tools": count_mcp_tools(),
        "skills": count_skills(),
        "adapters": count_adapters(),
    }
    print(f"actual: tools={actual['tools']}  skills={actual['skills']}  adapters={actual['adapters']}")

    failures: list[str] = []
    seen: set[tuple[str, int, int]] = set()  # dedupe overlapping regex hits
    for path, pattern, kind in _TARGETS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            try:
                claimed = int(m.group(1))
            except (ValueError, IndexError):
                continue
            if claimed == actual[kind]:
                continue
            line_no, line_text = _line_for(text, m.start())
            # Prefix-scoped allowlist: only skip if THIS captured number is the
            # one preceded by a subset-disclosure marker.
            number_start = m.start(1)
            window_lower = text[max(0, number_start - 24) : number_start].lower()
            if any(window_lower.endswith(t) for t in _ALLOWLIST_PREFIX_TOKENS):
                continue
            key = (str(path), line_no, claimed)
            if key in seen:
                continue
            seen.add(key)
            rel = path.relative_to(_ROOT).as_posix()
            failures.append(
                f"  {rel}:{line_no}  claims {claimed} {kind}, actual {actual[kind]}  ({line_text.strip()[:80]!r})"
            )

    if failures:
        print("DRIFT DETECTED — doc counts do not match source of truth:")
        for f in failures:
            print(f)
        print(
            "\nFix: update each line above to the actual count, "
            "or — if the number is an intentional subset — preface it "
            "with 'core' or 'codified-rule' so the guard skips it. "
            "Re-run `python backend/scripts/check_doc_counts.py`."
        )
        return 1

    print("OK — all doc tool/skill counts match source of truth.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
