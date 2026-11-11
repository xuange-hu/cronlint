"""The command line.

Exit codes follow the usual convention so it can gate a CI job: 0 when nothing
at or above ``--fail-on`` was found, 1 when something was, 2 when the input
could not be read at all.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Sequence
from pathlib import Path

from . import demo, render
from .__about__ import __version__
from .engine import run
from .model import Report
from .parser import looks_like_crontab, parse_file
from .rules import REGISTRY

_SEVERITIES = ("note", "low", "medium", "high", "critical")


def _configure_output() -> None:
    """Windows defaults to cp1252; the report carries offsets and box-drawing."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):  # pragma: no cover
                reconfigure(encoding="utf-8", errors="replace")


def _threshold(name: str) -> int:
    return _SEVERITIES.index(name)


def _failed(report: Report, fail_on: str) -> bool:
    if fail_on == "none":
        return False
    threshold = _threshold(fail_on)
    return any(_threshold(f.severity) >= threshold for f in report.findings)


def _discover(path: Path) -> list[Path]:
    """A directory scan picks up crontab-shaped files only."""
    if path.is_file():
        return [path]
    found: list[Path] = []
    for candidate in sorted(path.rglob("*")):
        if not candidate.is_file():
            continue
        parts = {part.lower() for part in candidate.parts}
        if "cron.d" in parts or looks_like_crontab(candidate):
            found.append(candidate)
    return found


def _scan(
    paths: Sequence[str],
    assume_user: bool | None,
    assume_seconds: bool,
    standard: bool = False,
) -> list[Report]:
    reports: list[Report] = []
    for raw in paths:
        target = Path(raw)
        if not target.exists():
            raise FileNotFoundError(raw)
        for file_path in _discover(target):
            app = parse_file(
                file_path,
                assume_user=assume_user,
                assume_seconds=assume_seconds,
                standard=standard,
            )
            reports.append(run(app, target=str(file_path)))
    return reports


def _cmd_scan(args: argparse.Namespace) -> int:
    assume_user = True if args.assume_user else None
    try:
        reports = _scan(args.paths, assume_user, args.assume_seconds, args.standard)
    except FileNotFoundError as missing:
        print(f"cronlint: no such file or directory: {missing}", file=sys.stderr)
        return 2

    if not reports:
        print("cronlint: no crontab files found", file=sys.stderr)
        return 2

    color = not args.no_color and sys.stdout.isatty()
    exit_code = 0
    for report in reports:
        if args.json:
            print(render.to_json(report))
        elif args.sarif:
            print(render.to_sarif(report))
        elif not args.quiet:
            print(render.text(report, color=color))
        if _failed(report, args.fail_on):
            exit_code = 1
    return exit_code


def _cmd_demo(args: argparse.Namespace) -> int:
    results = demo.reports()
    color = not args.no_color and sys.stdout.isatty()
    if not args.quiet:
        for name, report in results:
            print(f"=== {name} ===")
            print(render.text(report, color=color))
    fired, total = demo.coverage()
    missing = sorted(rule.id for rule in REGISTRY if rule.id not in fired)
    print(f"corpora: {len(results)}, findings: {sum(r.count for _, r in results)}")
    print(f"rules fired: {len(fired)}/{total}")
    if missing:
        print(f"NOT fired: {' '.join(missing)}")
        return 1
    print("every rule in the table has a sample line that triggers it")
    return 0


def _cmd_rules(args: argparse.Namespace) -> int:
    table = sorted(REGISTRY, key=lambda item: item.id)
    if args.json:
        import json

        print(
            json.dumps(
                [
                    {
                        "id": item.id,
                        "title": item.title,
                        "severity": item.severity,
                        "score": item.score,
                        "stage": item.stage,
                        "why": item.why,
                        "fix": item.fix,
                    }
                    for item in table
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    if args.markdown:
        from .rulebook import full_markdown

        # write(), not print(): the generated document ends with its own
        # newline, and `cronlint rules --markdown > docs/rules.md` has to
        # reproduce it byte for byte or the CI docs job diffs forever.
        sys.stdout.write(full_markdown())
        return 0

    for item in table:
        print(f"{item.id}  {item.severity:<8s} stage {item.stage}  {item.title}")
    print(f"\n{len(table)} rules")
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    wanted = args.rule.upper()
    for item in REGISTRY:
        if item.id == wanted:
            print(f"{item.id}  {item.title}")
            print(f"severity: {item.severity} (score {item.score})   stage: {item.stage}")
            print(f"\nwhy it matters\n{item.why}")
            print(f"\nfix\n{item.fix}")
            return 0
    print(f"cronlint: no rule {wanted}", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cronlint",
        description="Audit crontabs: schedules, commands and the file as a whole.",
    )
    parser.add_argument("--version", action="version", version=f"cronlint {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="audit a crontab file or a directory of them")
    scan.add_argument("paths", nargs="+", help="file or directory")
    scan.add_argument("--json", action="store_true", help="JSON report")
    scan.add_argument("--sarif", action="store_true", help="SARIF 2.1.0 report")
    scan.add_argument("--no-color", action="store_true")
    scan.add_argument("--quiet", action="store_true", help="no report, exit code only")
    scan.add_argument(
        "--fail-on",
        choices=["none", *_SEVERITIES],
        default="high",
        help="exit 1 at or above this severity (default: high)",
    )
    scan.add_argument(
        "--assume-user",
        action="store_true",
        help="read the /etc/crontab shape (a user column) whatever the path says",
    )
    scan.add_argument(
        "--standard",
        action="store_true",
        help="read six fields as 5 + command instead of a seconds field",
    )
    scan.add_argument(
        "--assume-seconds",
        action="store_true",
        help="force the six-field reading (leading seconds)",
    )
    scan.set_defaults(func=_cmd_scan)

    show = sub.add_parser("demo", help="audit three built-in crontabs that fire every rule")
    show.add_argument("--no-color", action="store_true")
    show.add_argument("--quiet", action="store_true")
    show.set_defaults(func=_cmd_demo)

    rules = sub.add_parser("rules", help="list the rule table")
    rules.add_argument("--json", action="store_true")
    rules.add_argument("--markdown", action="store_true", help="full table for docs/rules.md")
    rules.set_defaults(func=_cmd_rules)

    explain = sub.add_parser("explain", help="one rule: what it detects and how to fix it")
    explain.add_argument("rule", help="rule id, e.g. CRN014")
    explain.set_defaults(func=_cmd_explain)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_output()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
