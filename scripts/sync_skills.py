"""Generate the standalone skills (for Claude Code without plugin support) from the plugin skills.

    python scripts/sync_skills.py          # write standalone-skill/*/SKILL.md
    python scripts/sync_skills.py --check  # exit 1 if they are out of date (used by the tests)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# plugin skill -> (standalone name, replacements). Plugin skills are called /coinflip:<name>,
# standalone skills are plain /coinflip, /coinflip-progress and /coinflip-quality.
RULES = {
    "review": ("coinflip", [
        ("name: review\n", "name: coinflip\n"),
        ("Use when the user runs /coinflip:review,", "Use when the user runs /coinflip,"),
        ("`/coinflip:progress`", "`/coinflip-progress`"),
        ("`/coinflip:quality`", "`/coinflip-quality`"),
    ]),
    "progress": ("coinflip-progress", [
        ("name: progress\n", "name: coinflip-progress\n"),
        ("/coinflip:progress", "/coinflip-progress"),
        ("/coinflip:review", "/coinflip"),
    ]),
    "quality": ("coinflip-quality", [
        ("name: quality\n", "name: coinflip-quality\n"),
        ("/coinflip:quality", "/coinflip-quality"),
        ("/coinflip:review", "/coinflip"),
    ]),
}


def render(skill: str) -> tuple[Path, str]:
    """Turn the plugin skill into the standalone variant (rules from RULES)."""
    name, replacements = RULES[skill]
    text = (ROOT / "plugins/coinflip/skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    for old, new in replacements:
        text = text.replace(old, new)
    return ROOT / "standalone-skill" / name / "SKILL.md", text


def main(check: bool) -> int:
    """Write the standalone skills, or with --check fail if they are out of date."""
    stale = []
    for skill in RULES:
        target, text = render(skill)
        if check:
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                stale.append(str(target.relative_to(ROOT)))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        print("out of date - run python scripts/sync_skills.py:", *stale, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
