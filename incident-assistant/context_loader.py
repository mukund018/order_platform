"""Reads Phase 3 RCA and runbook files into the text block the Gemini prompt is
grounded on. No caching: every function re-reads from disk on every call, since
these files only change when an incident is closed and re-reading is cheap at
this scale."""

from pathlib import Path
from typing import TypedDict

import yaml

from common.logging import get_logger

log = get_logger(__name__)


class RCA(TypedDict):
    id: str
    title: str
    severity: str
    services: list[str]
    category: str
    body: str


class Runbook(TypedDict):
    name: str
    content: str


def load_rcas(incidents_dir: Path) -> list[RCA]:
    rcas: list[RCA] = []
    for rca_file in sorted(incidents_dir.glob("INC-*/rca.md")):
        try:
            text = rca_file.read_text(encoding="utf-8")
            parts = text.split("---", 2)
            if len(parts) < 3:
                raise ValueError(f"{rca_file} is missing the closing '---' frontmatter delimiter")
            frontmatter = yaml.safe_load(parts[1])
            if not isinstance(frontmatter, dict):
                kind = type(frontmatter).__name__
                raise ValueError(f"{rca_file} frontmatter parsed to {kind}, expected dict")
            body = parts[2].strip()
            rcas.append(
                RCA(
                    id=frontmatter["id"],
                    title=frontmatter["title"],
                    severity=frontmatter["severity"],
                    services=frontmatter["services"],
                    category=frontmatter["category"],
                    body=body,
                )
            )
        except (IndexError, KeyError, TypeError, yaml.YAMLError, ValueError) as e:
            log.warning("skipping malformed rca", file=str(rca_file), error=str(e))
    return rcas


def load_runbooks(runbooks_dir: Path) -> list[Runbook]:
    runbooks: list[Runbook] = []
    for runbook_file in sorted(runbooks_dir.glob("*.md")):
        try:
            content = runbook_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as e:
            log.warning("skipping unreadable runbook", file=str(runbook_file), error=str(e))
            continue
        runbooks.append(Runbook(name=runbook_file.stem, content=content))
    return runbooks


def build_context(incidents_dir: Path, runbooks_dir: Path) -> str:
    """Formats every closed RCA and every runbook into one text block for the
    Gemini system prompt. Re-read from disk on every call — see the module docstring."""
    rcas = load_rcas(incidents_dir)
    runbooks = load_runbooks(runbooks_dir)

    sections = ["## Incident history"]
    if not rcas:
        sections.append("(no incidents have been closed with a written RCA yet)")
    for rca in rcas:
        services = ", ".join(rca["services"])
        sections.append(
            f"### {rca['id']} — {rca['title']} ({rca['severity']}, {rca['category']}, "
            f"services: {services})\n{rca['body']}"
        )

    sections.append("## Runbooks")
    if not runbooks:
        sections.append("(no runbooks yet)")
    for runbook in runbooks:
        sections.append(f"### {runbook['name']}\n{runbook['content']}")

    return "\n\n".join(sections)
