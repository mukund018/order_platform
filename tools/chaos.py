"""Phase 3 incident harness: inject a fault, hand over a ticket, grade the answer.

The point of Phase 3 is that the engineer investigating an incident does not already
know what broke. That is easy to say and hard to keep true when the same person wrote
the faults, so this tool is built around keeping the two apart:

* Every fault lives in `incidents/faults/INC-0XX.json`. The half you are allowed to read
  - the ticket, the category, the load profile needed to reproduce it - is plain text.
  The half that gives it away is base64 in the `spoiler` field, so `cat` and `grep`
  cannot spill it by accident and decoding it has to be a deliberate act.
* Code faults are committed to an `incident/INC-0XX` branch under one neutral message,
  and several faults touch no code at all - configuration, a compose override, or rows
  in the database - so reaching for `git diff` would not help even if you cheated.
* `reveal` refuses to print the root cause until `rca.md` has actually been written.

Usage:

    python tools/chaos.py list
    python tools/chaos.py start INC-001
    python tools/chaos.py ticket INC-001
    python tools/chaos.py hint INC-001          # one tier at a time, and it is counted
    python tools/chaos.py status
    python tools/chaos.py revert INC-001        # undo the fault: this is the mitigation
    python tools/chaos.py reveal INC-001        # needs a written rca.md
    python tools/chaos.py close INC-001 --sev SEV2 --tta 4 --ttm 21 --score 8

Run it from the repository root with the project venv.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FAULTS_DIR = ROOT / "incidents" / "faults"
INCIDENTS_DIR = ROOT / "incidents"
TEMPLATES_DIR = INCIDENTS_DIR / "templates"
STATE_FILE = ROOT / ".chaos" / "state.json"
ENV_FILE = ROOT / ".env"
OVERRIDE_FILE = ROOT / "docker-compose.override.yml"

NEUTRAL_COMMIT = "chore: {incident} environment setup"
BRANCH = "incident/{incident}"

# A key that was absent before the fault and a key set to the empty string are different
# things to pydantic-settings, so the backup has to be able to say "there was none".
ABSENT = "\x00absent"

# psql runs inside the postgres container, so the three databases are reachable by name
# with no client installed on the host.
PSQL = ["docker", "compose", "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U"]

# Enough prose under the headings to be a real answer rather than an untouched template.
RCA_MIN_CHARS = 200

WIDTH = 76


class ChaosError(RuntimeError):
    """Something the operator has to fix before the command can run."""


# --------------------------------------------------------------------------- faults


@dataclass(frozen=True)
class Fault:
    """One incident: the public half, and the sealed half until it is opened."""

    id: str
    title: str
    category: str
    difficulty: str
    hint_cap: int | None
    ticket: str
    traffic: dict[str, Any]
    setup_notes: str
    spoiler: str

    @classmethod
    def load(cls, incident: str) -> Fault:
        path = FAULTS_DIR / f"{incident}.json"
        if not path.exists():
            raise ChaosError(f"no fault definition at {path.relative_to(ROOT)}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            id=raw["id"],
            title=raw["title"],
            category=raw["category"],
            difficulty=raw["difficulty"],
            hint_cap=raw.get("hint_cap"),
            ticket=raw["ticket"],
            traffic=raw.get("traffic", {}),
            setup_notes=raw.get("setup_notes", ""),
            spoiler=raw["spoiler"],
        )

    def open(self) -> dict[str, Any]:
        """Decode the sealed half - everything that would give the incident away."""
        return json.loads(base64.b64decode(self.spoiler).decode("utf-8"))


def all_faults() -> list[Fault]:
    return [Fault.load(path.stem) for path in sorted(FAULTS_DIR.glob("INC-*.json"))]


# --------------------------------------------------------------------------- state


@dataclass
class State:
    """What the active incident changed, so `revert` can put all of it back.

    Kept outside git deliberately: it names the files the fault touched, which for a
    code fault is most of the answer.
    """

    active: str | None = None
    started_at: str | None = None
    branch: str | None = None
    base_branch: str | None = None
    env_backup: dict[str, str] = field(default_factory=dict)
    patched: list[str] = field(default_factory=list)
    override_written: bool = False
    hints_used: int = 0
    revealed: bool = False

    @classmethod
    def load(cls) -> State:
        if not STATE_FILE.exists():
            return cls()
        return cls(**json.loads(STATE_FILE.read_text(encoding="utf-8")))

    def save(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.__dict__, indent=2) + "\n", encoding="utf-8")

    @property
    def fault_is_live(self) -> bool:
        return bool(self.patched or self.env_backup or self.override_written)


# --------------------------------------------------------------------------- helpers


def run(command: list[str], *, check: bool = True) -> str:
    result = subprocess.run(command, cwd=ROOT, check=False, text=True, capture_output=True)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ChaosError(f"`{' '.join(command)}` failed: {detail}")
    return (result.stdout or "").strip()


def git(*args: str, check: bool = True) -> str:
    return run(["git", *args], check=check)


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD")


def working_tree_is_clean() -> bool:
    return git("status", "--porcelain") == ""


def docker_is_up() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "compose", "ps", "-q"], cwd=ROOT, check=False, capture_output=True
    )
    return probe.returncode == 0 and bool(probe.stdout.strip())


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def banner(text: str) -> str:
    line = "=" * WIDTH
    return f"\n{line}\n{text}\n{line}"


def wrap(text: str, prefix: str = "") -> str:
    return textwrap.fill(f"{prefix}{text}", width=WIDTH, subsequent_indent=" " * len(prefix))


# --------------------------------------------------------------------------- applying


def set_env_values(values: dict[str, str]) -> dict[str, str]:
    """Rewrite `.env` in place. Returns the previous values so revert can restore them."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    previous: dict[str, str] = {}
    for key, value in values.items():
        for index, line in enumerate(lines):
            if line.startswith(f"{key}="):
                previous[key] = line.split("=", 1)[1]
                lines[index] = f"{key}={value}"
                break
        else:
            previous[key] = ABSENT
            lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return previous


def restore_env_values(previous: dict[str, str]) -> None:
    kept: list[str] = []
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        if key not in previous:
            kept.append(line)
        elif previous[key] != ABSENT:
            kept.append(f"{key}={previous[key]}")
    ENV_FILE.write_text("\n".join(kept) + "\n", encoding="utf-8")


def apply_patches(patches: list[dict[str, str]], *, reverse: bool = False) -> list[str]:
    """Anchored find/replace rather than a diff, so a patch either lands exactly or
    refuses - a fault that applied halfway would be an incident nobody designed."""
    touched: list[str] = []
    for patch in patches:
        path = ROOT / patch["file"]
        find, replace = patch["find"], patch["replace"]
        if reverse:
            find, replace = replace, find
        source = path.read_text(encoding="utf-8")
        found = source.count(find)
        if found != 1:
            raise ChaosError(
                f"{patch['file']}: expected exactly one match for the patch anchor, found "
                f"{found}. The file has moved on since this fault was written."
            )
        path.write_text(source.replace(find, replace), encoding="utf-8")
        touched.append(patch["file"])
    return touched


def run_sql(statements: list[dict[str, str]]) -> None:
    if not statements:
        return
    if not docker_is_up():
        raise ChaosError(
            "this incident changes the database, so the stack has to be running first: "
            "docker compose up -d"
        )
    for item in statements:
        run([*PSQL, "app", "-d", item["db"], "-c", item["sql"]])


# --------------------------------------------------------------------------- scaffold


def scaffold(fault: Fault) -> Path:
    """Create the incident folder. The ticket is written for you; the rest is yours."""
    folder = INCIDENTS_DIR / fault.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ticket.md").write_text(fault.ticket.rstrip() + "\n", encoding="utf-8")

    for name in ("investigation.md", "escalation.md", "rca.md"):
        target = folder / name
        if target.exists():
            # Never overwrite work in progress: `start` can be re-run after a failed
            # docker step, and that must not wipe an investigation already under way.
            continue
        body = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
        target.write_text(body.replace("INC-00X", fault.id), encoding="utf-8")
    return folder


def traffic_command(fault: Fault) -> str:
    traffic = fault.traffic
    if not traffic:
        return "(no load needed - this one is visible without traffic)"
    parts = ["python tools/traffic.py"]
    for flag in ("rps", "duration", "mix"):
        if flag in traffic:
            parts.append(f"--{flag} {traffic[flag]}")
    if traffic.get("hot_sku"):
        parts.append(f"--hot-sku {traffic['hot_sku']}")
    return " ".join(parts)


# --------------------------------------------------------------------------- commands


def cmd_list(_: argparse.Namespace) -> int:
    state = State.load()
    closed = closed_incidents()
    print(f"{'ID':<9} {'STATE':<9} {'DIFFICULTY':<10} {'CATEGORY':<30} TITLE")
    for fault in all_faults():
        if fault.id in closed:
            marker = "closed"
        elif fault.id == state.active:
            marker = "ACTIVE"
        else:
            marker = "-"
        print(
            f"{fault.id:<9} {marker:<9} {fault.difficulty:<10} {fault.category:<30} {fault.title}"
        )
    print(f"\n{len(closed)}/{len(all_faults())} closed.")
    print("Hint budget: guided and hinted are unlimited, capped allows 2, unguided none.")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    state = State.load()
    if state.active:
        raise ChaosError(f"{state.active} is still active. Revert it before starting another.")
    if not working_tree_is_clean():
        raise ChaosError("commit or stash your work first - the fault needs a clean tree.")

    fault = Fault.load(args.incident)
    spec = fault.open()
    base = current_branch()
    branch = BRANCH.format(incident=fault.id)

    git("checkout", "-b", branch)
    state = State(active=fault.id, started_at=utcnow(), branch=branch, base_branch=base)

    try:
        state.patched = apply_patches(spec.get("patches", []))
        if spec.get("env"):
            state.env_backup = set_env_values(spec["env"])
        if spec.get("compose_override"):
            OVERRIDE_FILE.write_text(spec["compose_override"], encoding="utf-8")
            state.override_written = True
        run_sql(spec.get("sql", []))
    except Exception:
        # Leave nothing half-applied: an incident that is only partly there is not the
        # incident that was designed, and debugging it teaches the wrong lesson.
        git("checkout", "--", ".", check=False)
        git("checkout", base, check=False)
        git("branch", "-D", branch, check=False)
        raise

    scaffold(fault)
    git("add", "-A")
    git("commit", "-q", "-m", NEUTRAL_COMMIT.format(incident=fault.id))
    state.save()

    print(banner(f"{fault.id} is live"))
    print(fault.ticket.rstrip())
    print(banner("Bring it up"))
    print(
        "  docker compose up -d --build"
        if spec.get("needs_rebuild", True)
        else "  docker compose up -d"
    )
    if fault.setup_notes:
        print(textwrap.indent(fault.setup_notes.rstrip(), "  "))
    print(f"  {traffic_command(fault)}")
    print(banner("Your move"))
    print("  Triage first. Severity, impact and the acknowledged-at timestamp go into")
    print(f"  incidents/{fault.id}/investigation.md before you touch anything else.")
    print(f"  Stuck? python tools/chaos.py hint {fault.id}  (it is counted against you)")
    return 0


def cmd_ticket(args: argparse.Namespace) -> int:
    print(Fault.load(args.incident).ticket.rstrip())
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    state = State.load()
    if not state.active:
        print("No incident active.")
        return 0
    fault = Fault.load(state.active)
    cap = "" if fault.hint_cap is None else f" / {fault.hint_cap} allowed"
    print(f"Active    : {fault.id} - {fault.title}")
    print(f"Category  : {fault.category}    Difficulty: {fault.difficulty}")
    print(f"Branch    : {state.branch}   (cut from {state.base_branch})")
    print(f"Started   : {state.started_at}  ({elapsed_minutes(state.started_at)} min ago)")
    print(f"Hints used: {state.hints_used}{cap}")
    print(f"Fault     : {'still injected' if state.fault_is_live else 'reverted'}")
    return 0


def cmd_hint(args: argparse.Namespace) -> int:
    state = State.load()
    fault = Fault.load(args.incident)
    if state.active != fault.id:
        raise ChaosError(f"{fault.id} is not the active incident.")

    hints = fault.open().get("hints", [])
    allowed = min(fault.hint_cap if fault.hint_cap is not None else len(hints), len(hints))
    if state.hints_used >= allowed:
        raise ChaosError(
            f"you have used {state.hints_used} of the {allowed} hints {fault.id} allows. "
            "Write down what you have ruled out and keep going - that list is the "
            "investigation, and it is the part an interviewer will ask about."
        )

    print(banner(f"Hint {state.hints_used + 1} of {allowed}"))
    print(wrap(hints[state.hints_used]))
    state.hints_used += 1
    state.save()
    return 0


def cmd_revert(args: argparse.Namespace) -> int:
    """Undo the injected fault. This is the mitigation step, not the permanent fix."""
    state = State.load()
    fault = Fault.load(args.incident)
    if state.active != fault.id:
        raise ChaosError(f"{fault.id} is not the active incident.")

    spec = fault.open()
    if state.patched:
        apply_patches(spec.get("patches", []), reverse=True)
        state.patched = []
    if state.env_backup:
        restore_env_values(state.env_backup)
        state.env_backup = {}
    if state.override_written and OVERRIDE_FILE.exists():
        OVERRIDE_FILE.unlink()
        state.override_written = False
    if spec.get("undo_sql"):
        run_sql(spec["undo_sql"])
    state.save()

    print(f"{fault.id} mitigated - the injected change is out of the working tree.")
    print("Restart the stack to pick it up:  docker compose up -d --build")
    print(f"Now write the real fix and a regression test on {state.branch}, then run")
    print(f"  python tools/chaos.py reveal {fault.id}   (once rca.md is written)")
    return 0


def cmd_reveal(args: argparse.Namespace) -> int:
    fault = Fault.load(args.incident)
    rca = INCIDENTS_DIR / fault.id / "rca.md"
    if not args.force and not rca_is_written(rca):
        raise ChaosError(
            f"incidents/{fault.id}/rca.md still reads like the blank template. Submit "
            "your answer first - grading yourself after seeing the answer is not the "
            "exercise. Pass --force if you genuinely want to give up on this one."
        )

    spec = fault.open()
    print(banner(f"{fault.id} - what was actually injected"))
    print(f"Root cause : {spec['root_cause']}")
    print(f"Severity   : {spec['expected_severity']}")
    print()
    print(wrap(spec["explanation"]))
    print(banner("How it should have been caught"))
    print(wrap(spec["detection"]))
    print(banner("The fix worth writing"))
    print(wrap(spec["fix"]))
    print(banner("Talking points for the interview"))
    for point in spec.get("teaching_points", []):
        print(wrap(point, prefix="- "))

    state = State.load()
    if state.active == fault.id:
        state.revealed = True
        state.save()
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    fault = Fault.load(args.incident)
    folder = INCIDENTS_DIR / fault.id
    missing = [n for n in ("ticket.md", "investigation.md", "rca.md") if not (folder / n).exists()]
    if missing:
        raise ChaosError(f"{fault.id} is missing {', '.join(missing)}")
    if not rca_is_written(folder / "rca.md"):
        raise ChaosError(f"{fault.id}/rca.md is still the blank template.")
    if fault.id in closed_incidents():
        raise ChaosError(f"{fault.id} already has a row in incidents/INDEX.md.")

    state = State.load()
    hints = args.hints
    if hints is None:
        hints = state.hints_used if state.active == fault.id else 0
    append_index_row(fault, args.sev, args.tta, args.ttm, hints, args.score)

    base = state.base_branch or "main"
    if state.active == fault.id:
        STATE_FILE.unlink(missing_ok=True)
    print(f"{fault.id} closed and written into incidents/INDEX.md.")
    print("Merge the branch once the regression test passes:")
    print(f"  git checkout {base} && git merge --no-ff {BRANCH.format(incident=fault.id)}")
    return 0


def cmd_verify(_: argparse.Namespace) -> int:
    """Which incidents have a complete paper trail and which are half done."""
    incomplete = 0
    for fault in all_faults():
        folder = INCIDENTS_DIR / fault.id
        if not folder.exists():
            print(f"{fault.id}  not started")
            continue
        gaps = [n for n in ("ticket.md", "investigation.md", "rca.md") if not (folder / n).exists()]
        if "rca.md" not in gaps and not rca_is_written(folder / "rca.md"):
            gaps.append("rca.md (still the template)")
        if gaps:
            incomplete += 1
            print(f"{fault.id}  INCOMPLETE: {', '.join(gaps)}")
        else:
            print(f"{fault.id}  complete")
    return 1 if incomplete else 0


# --------------------------------------------------------------------------- support


def rca_is_written(path: Path) -> bool:
    """An RCA whose headings are all still empty does not count as an answer."""
    if not path.exists():
        return False
    prose = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith(("#", "-", "|", "`"))
    ]
    return len("".join(prose)) > RCA_MIN_CHARS


def closed_incidents() -> set[str]:
    index = INCIDENTS_DIR / "INDEX.md"
    if not index.exists():
        return set()
    return {
        line.split("|")[1].strip()
        for line in index.read_text(encoding="utf-8").splitlines()
        if line.startswith("| INC-")
    }


def elapsed_minutes(started_at: str | None) -> int:
    if not started_at:
        return 0
    started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return int((datetime.now(UTC) - started).total_seconds() // 60)


def append_index_row(fault: Fault, sev: str, tta: int, ttm: int, hints: int, score: int) -> None:
    """Slot the row in under the existing ones so the table stays in incident order."""
    index = INCIDENTS_DIR / "INDEX.md"
    lines = index.read_text(encoding="utf-8").splitlines()
    row = (
        f"| {fault.id} | {fault.title} | {sev} | {fault.category} | "
        f"{tta} | {ttm} | {hints} | {score}/10 |"
    )
    header = next(i for i, line in enumerate(lines) if line.startswith("|---"))
    end = header + 1
    while end < len(lines) and lines[end].startswith("| INC-"):
        end += 1
    lines.insert(end, row)
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chaos", description=(__doc__ or "").split("\n")[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="every incident and its state").set_defaults(fn=cmd_list)
    subparsers.add_parser("status", help="what is live right now").set_defaults(fn=cmd_status)
    subparsers.add_parser("verify", help="which incidents are documented").set_defaults(
        fn=cmd_verify
    )

    start = subparsers.add_parser("start", help="inject a fault and open the ticket")
    start.add_argument("incident")
    start.set_defaults(fn=cmd_start)

    ticket = subparsers.add_parser("ticket", help="reprint the ticket")
    ticket.add_argument("incident")
    ticket.set_defaults(fn=cmd_ticket)

    hint = subparsers.add_parser("hint", help="one hint tier, counted against you")
    hint.add_argument("incident")
    hint.set_defaults(fn=cmd_hint)

    revert = subparsers.add_parser("revert", help="undo the fault - the mitigation step")
    revert.add_argument("incident")
    revert.set_defaults(fn=cmd_revert)

    reveal = subparsers.add_parser("reveal", help="the answer, once your RCA is written")
    reveal.add_argument("incident")
    reveal.add_argument("--force", action="store_true", help="give up and show it anyway")
    reveal.set_defaults(fn=cmd_reveal)

    close = subparsers.add_parser("close", help="score the incident into INDEX.md")
    close.add_argument("incident")
    close.add_argument("--sev", required=True, help="SEV1..SEV4, as you assigned it")
    close.add_argument("--tta", type=int, required=True, help="time to acknowledge, minutes")
    close.add_argument("--ttm", type=int, required=True, help="time to mitigate, minutes")
    close.add_argument("--score", type=int, required=True, help="RCA score out of 10")
    close.add_argument("--hints", type=int, default=None, help="override the counted hints")
    close.set_defaults(fn=cmd_close)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except ChaosError as exc:
        print(f"chaos: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
