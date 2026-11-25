#!/usr/bin/env python3
"""Zeus-Agent end-to-end test agent: builds an isolated stack, drives the real GUI (Chromium) and the real terminal app (pty), reports.

    python scripts/e2e/run.py                 # everything
    python scripts/e2e/run.py --layer cli     # only the terminal app
    python scripts/e2e/run.py --keep          # keep the temp dir (logs, screenshots)
Exit code: 0 all passed, 1 a case failed, 2 the suite could not run (missing Playwright/Chromium/frontend). ZEUS_E2E=skip exits 0 without running
(for machines that cannot run a browser); a skip is printed loudly and written to the report.
Reports: <out>/report.json, report.md, junit.xml and, for failures, screenshots / terminal screens. Default <out>: <repo>/e2e-report.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from framework import CASES, run_cases, write_reports  # noqa: E402
import cases  # noqa: E402,F401  (registers the cases)
from harness import ROOT, Stack  # noqa: E402


class Ctx:
    def __init__(self, stack, page=None):
        self.stack, self.page, self.term, self.page_errors = stack, page, None, []

    def recover(self):
        if self.page is not None:
            for _ in range(3):
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(150)

    def collect_evidence(self, case, out: Path) -> list[str]:
        out.mkdir(parents=True, exist_ok=True)
        ev = []
        if self.page is not None and case.layer in ("gui", "final"):
            p = out / f"{case.id}.png"
            self.page.screenshot(path=str(p))
            ev.append(str(p))
        if self.term is not None and case.layer == "cli":
            p = out / f"{case.id}.screen.txt"
            p.write_text(self.term.screen(8000))
            ev.append(str(p))
        t = out / f"{case.id}.backend.log"
        t.write_text(self.stack.log_tail("backend", 80))
        ev.append(str(t))
        return ev


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layer", choices=["gui", "cli", "all"], default="all")        # gui includes the final destructive GUI cases
    ap.add_argument("--out", default=str(ROOT / "e2e-report"))
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--fail-fast", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    if os.environ.get("ZEUS_E2E") == "skip":
        print("!! ZEUS_E2E=skip: end-to-end tests NOT run")
        write_reports([], out, {"when": time.strftime("%F %T"), "note": "skipped by ZEUS_E2E=skip"})
        return 0
    sel = [c for c in CASES if a.layer == "all" or a.layer == c.layer or (a.layer == "gui" and c.layer == "final")]
    need_gui = any(c.layer in ("gui", "final") for c in sel)
    pw = None
    if need_gui:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("Playwright is not installed. Run: backend/.venv/bin/pip install -r backend/requirements-dev.txt && backend/.venv/bin/playwright install chromium", file=sys.stderr)
            return 2
    from app_version import version  # noqa: E402
    stack = Stack(build_ui=need_gui)
    t0 = time.time()
    try:
        print(f"Starting the isolated stack in {stack.tmp} …", flush=True)
        stack.start()
        ctx = Ctx(stack)
        gui = [c for c in sel if c.layer == "gui"]
        cli = [c for c in sel if c.layer == "cli"]
        final = [c for c in sel if c.layer == "final"]           # destructive GUI cases (reset): they run after everything else, in the same browser
        if need_gui:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=not a.headed)
                except Exception as e:  # noqa: BLE001
                    print(f"Chromium cannot start: {e}\nRun: backend/.venv/bin/playwright install chromium", file=sys.stderr)
                    return 2
                page = browser.new_page(viewport={"width": 1500, "height": 950})
                page.set_default_timeout(20000)
                page.on("pageerror", lambda e: ctx.page_errors.append(str(e)))
                ctx.page = page
                run_cases(gui, ctx, out, a.fail_fast)
                run_cases(cli, ctx, out, a.fail_fast)
                run_cases(final, ctx, out, a.fail_fast)
                browser.close()
        else:
            run_cases(cli, ctx, out, a.fail_fast)
    except RuntimeError as e:
        print(f"Could not run the suite: {e}", file=sys.stderr)
        return 2
    finally:
        stack.stop(keep=a.keep)
    write_reports(sel, out, {"when": time.strftime("%F %T"), "version": version(), "seconds": round(time.time() - t0, 1)})
    failed = [c for c in sel if c.status == "failed"]
    print(f"\n{sum(c.status == 'passed' for c in sel)} passed, {len(failed)} failed, {sum(c.status == 'skipped' for c in sel)} skipped · report: {out}/report.md")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
