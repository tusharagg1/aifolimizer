"""
Auto skills builder.

Inspect MCP tools, list existing skills, scaffold new skill files.

Usage:
  python backend/scripts/build_skills.py
  python backend/scripts/build_skills.py --scaffold get_crypto_data
"""

import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
_MCP = Path(__file__).parent.parent / "mcp_server.py"
_SKILLS_DIR = _ROOT / ".claude" / "skills"

_SKILL_TEMPLATE = """\
---
name: {skill_name}
description: |
  Run a {skill_title} analysis. Update this description — it controls
  when Claude auto-triggers this skill.
---

# {skill_title} Analysis

## How to run

1. Call `mcp__aifolimizer__get_profile` — account types and capital context
2. Call `mcp__aifolimizer__get_portfolio` for current holdings
3. Call `mcp__aifolimizer__{tool_name}` — {docstring_first_line}

## Investor profile

- Age: 32, Canadian resident
- Account types and capital: always read from `get_profile` — never hardcode

## Output structure

[Define the expected output format here]

## Rules

- Always call `get_profile` first — never hardcode account types or capital
- Never reference PII — MCP filters it before returning data
- Under 500 words
"""


def extract_tools(filepath: Path) -> list[dict]:
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source)
    tools = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            is_tool = (
                (isinstance(dec, ast.Attribute) and dec.attr == "tool")
                or (isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "tool")
            )
            if is_tool:
                doc = ast.get_docstring(node) or ""
                args = [a.arg for a in node.args.args if a.arg != "self"]
                tools.append({
                    "name": node.name,
                    "doc": doc,
                    "args": args,
                })
    return tools


def main() -> None:
    tools = extract_tools(_MCP)

    if "--scaffold" in sys.argv:
        idx = sys.argv.index("--scaffold")
        tool_name = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        if not tool_name:
            print("Usage: build_skills.py --scaffold <tool_name>")
            return

        tool = next((t for t in tools if t["name"] == tool_name), None)
        if not tool:
            print(f"Tool '{tool_name}' not found in {_MCP.name}")
            print(f"Available: {', '.join(t['name'] for t in tools)}")
            return

        skill_name = tool_name.replace("get_", "").replace("_", "-")
        skill_title = skill_name.replace("-", " ").title()
        skill_dir = _SKILLS_DIR / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"

        if skill_file.exists():
            print(f"Already exists: {skill_file}")
            return

        doc_line = tool["doc"].split("\n")[0].strip() if tool["doc"] else ""
        skill_file.write_text(
            _SKILL_TEMPLATE.format(
                skill_name=skill_name,
                skill_title=skill_title,
                tool_name=tool_name,
                docstring_first_line=doc_line,
            ),
            encoding="utf-8",
        )
        print(f"Scaffolded: {skill_file}")
        return

    print(f"\n{'--- MCP Tools ---':}")
    for t in tools:
        args_str = ", ".join(t["args"])
        doc = t["doc"]
        first_line = doc.split("\n")[0].strip() if doc else "(no docstring)"
        print(f"  mcp__aifolimizer__{t['name']}({args_str})")
        print(f"    {first_line}")

    print("\n--- Skills ---")
    if _SKILLS_DIR.exists():
        for skill_dir in sorted(_SKILLS_DIR.iterdir()):
            if skill_dir.is_dir():
                skill_file = skill_dir / "SKILL.md"
                has_profile = False
                if skill_file.exists():
                    content = skill_file.read_text(encoding="utf-8")
                    has_profile = "get_profile" in content
                flag = "[OK]" if has_profile else "[MISSING get_profile]"
                print(f"  {skill_dir.name:30s}  {flag}")
    else:
        print("  .claude/skills/ not found")

    print("\n  To scaffold a new skill:")
    print("  python backend/scripts/build_skills.py --scaffold <tool_name>")


if __name__ == "__main__":
    main()
