"""A very small test framework: ordered cases that share one stack, per-case evidence on failure, and JSON / Markdown / JUnit reports."""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

CASES: list["Case"] = []


@dataclass
class Case:
    id: str
    layer: str            # "gui" or "cli"
    title: str
    fn: object
    status: str = "pending"
    seconds: float = 0.0
    error: str = ""
    evidence: list[str] = field(default_factory=list)


def case(layer: str, title: str):
    def deco(fn):
        CASES.append(Case(f"{layer}.{fn.__name__}", layer, title, fn))
        return fn
    return deco


class Skip(Exception):
    pass


def expect(cond, message: str):
    if not cond:
        raise AssertionError(message)


def write_reports(cases: list[Case], out: Path, meta: dict) -> None:
    import json
    out.mkdir(parents=True, exist_ok=True)
    passed = sum(c.status == "passed" for c in cases)
    failed = sum(c.status == "failed" for c in cases)
    skipped = sum(c.status == "skipped" for c in cases)
    (out / "report.json").write_text(json.dumps({"meta": meta, "passed": passed, "failed": failed, "skipped": skipped,
                                                 "cases": [{k: getattr(c, k) for k in ("id", "layer", "title", "status", "seconds", "error", "evidence")} for c in cases]}, indent=1))
    lines = [f"# Zeus-Agent end-to-end report", "", f"{meta.get('when', '')} · version {meta.get('version', '?')} · **{passed} passed, {failed} failed, {skipped} skipped**", "",
             "| Layer | Case | Result | Seconds |", "|---|---|---|---|"]
    for c in cases:
        lines.append(f"| {c.layer} | {c.title} | {c.status} | {c.seconds:.1f} |")
    for c in cases:
        if c.status == "failed":
            lines += ["", f"## FAILED: {c.title}", "```", c.error.strip(), "```"] + [f"- evidence: `{e}`" for e in c.evidence]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    xml = [f'<testsuite name="zeus-e2e" tests="{len(cases)}" failures="{failed}" skipped="{skipped}">']
    for c in cases:
        xml.append(f'<testcase classname="{c.layer}" name="{escape(c.title)}" time="{c.seconds:.2f}">')
        if c.status == "failed":
            xml.append(f'<failure message="{escape(c.error.splitlines()[-1] if c.error else "failed")}">{escape(c.error)}</failure>')
        elif c.status == "skipped":
            xml.append("<skipped/>")
        xml.append("</testcase>")
    xml.append("</testsuite>")
    (out / "junit.xml").write_text("\n".join(xml))


def run_cases(cases: list[Case], ctx, out: Path, fail_fast=False) -> None:
    for c in cases:
        t = time.time()
        print(f"  … {c.layer:3} {c.title}", flush=True)
        try:
            c.fn(ctx)
            c.status = "passed"
        except Skip as s:
            c.status, c.error = "skipped", str(s)
        except Exception:  # noqa: BLE001
            c.status, c.error = "failed", traceback.format_exc()
            try:
                c.evidence = ctx.collect_evidence(c, out)
            except Exception as e:  # noqa: BLE001
                c.evidence = [f"(could not collect evidence: {e})"]
            try:
                getattr(ctx, "recover", lambda: None)()                  # close open dialogs so one failure does not hide the cases after it
            except Exception:  # noqa: BLE001
                pass
        c.seconds = time.time() - t
        mark = {"passed": "✓", "failed": "✗", "skipped": "-"}[c.status]
        print(f"  {mark} {c.layer:3} {c.title} ({c.seconds:.1f}s)" + (f"\n{c.error}" if c.status == "failed" else ""), flush=True)
        if c.status == "failed" and fail_fast:
            break
