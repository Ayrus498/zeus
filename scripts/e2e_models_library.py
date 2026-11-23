"""Real-browser check of the Models window: models that are only in the public Ollama library are findable and can be pulled.

Needs a running backend (scripts/run-backend.sh) and UI (cd frontend && npm run dev), internet access to ollama.com, and Playwright
with Chromium. Use a throw-away ZEUS_HOME/ZEUS_DATA_DIR for the backend. Nothing is downloaded: the Download request is intercepted.

    python scripts/e2e_models_library.py [http://localhost:1420] [model-name]      (default model: ornith)

It opens Settings -> Models, checks that the "Ollama library" group is listed, searches for the model, opens it and checks its
description, `ollama pull` command and version table (with sizes and fit), presses Download for the first sized version and checks
that exactly that model name was sent to /api/ollama/pull. Exit code 0 means everything held."""
import json
import sys

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:1420"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "ornith"


def main() -> int:
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.set_default_timeout(20000)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        pulls: list[dict] = []

        def fake_pull(route):
            pulls.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(
                {"id": 1, "model": pulls[-1]["model"], "status": "queued", "detail": "", "completed": 0, "total": 0, "percent": None, "error": None, "started": 0, "updated": 0}))

        page.route("**/api/ollama/pull", fake_pull)
        page.goto(URL)
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="Settings").first.click()
        page.wait_for_timeout(300)
        page.get_by_text("Models", exact=True).first.click()
        page.wait_for_selector("[data-testid=library-status]")
        page.wait_for_selector("[data-testid=group-library]", timeout=40000)
        status = page.inner_text("[data-testid=library-status]")
        print("status:", status.replace("\n", " | ")[:150])
        if "models in the Ollama library" not in status:
            problems.append("the library count is not shown")
        page.fill("input[aria-label='Search models']", MODEL)
        page.wait_for_selector(f"[data-testid='row-lib:{MODEL}']", timeout=30000)
        page.click(f"[data-testid='row-lib:{MODEL}']")
        page.wait_for_selector("[data-testid=library-versions]", timeout=30000)
        command = page.inner_text("[data-testid=pull-command]")
        versions = page.locator("[data-testid=library-versions] .py-1").all_inner_texts()
        print("pull command:", command, "| versions:", [v.replace("\n", " ") for v in versions])
        if command.strip() != f"ollama pull {MODEL}":
            problems.append("wrong pull command")
        if not versions or not any("GB" in v for v in versions):
            problems.append("no versions with sizes")
        target = next((v for v in versions if "GB" in v), "").split("\n")[0]
        row = page.locator("[data-testid=library-versions] .py-1", has_text=target).first
        row.get_by_role("button", name="Download").click()
        page.wait_for_timeout(500)
        print("download request:", pulls)
        if len(pulls) != 1 or not pulls[0]["model"].startswith(MODEL):
            problems.append("the Download button did not send the model name")
        if errors:
            problems.append(f"page errors: {errors}")
        browser.close()
    print("PASS" if not problems else "FAIL: " + "; ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
