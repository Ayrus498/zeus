"""Real-browser check: you can scroll away from a streaming answer and the view stays where you put it.

Needs a running backend (scripts/run-backend.sh), the UI (cd frontend && npm run dev), Ollama with the model below, one
project already added, and Playwright with Chromium (pip install playwright && playwright install chromium).

    python scripts/e2e_scroll_check.py [http://localhost:1420] [model-name-fragment]

It sends a prompt that produces a long answer, scrolls up with the mouse wheel while the text is still arriving and
reports whether the scroll position held, whether the "Jump to latest" button appeared and whether it returns to the end.
Exit code 0 means the position held. Use a throw-away ZEUS_HOME/ZEUS_DATA_DIR for the backend so your own data is untouched."""
import sys
import time

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:1420"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "qwen3.5:4b"
PROMPT = "Write a very long, detailed tutorial about how HTTP works, with at least 15 numbered sections, examples in each section and a summary at the end. Do not use any tools."
STATE = """(() => { const r = document.querySelector('.mx-auto.max-w-3xl'); if (!r) return null; const s = r.parentElement;
  return {top: Math.round(s.scrollTop), h: s.scrollHeight, c: s.clientHeight, gap: Math.round(s.scrollHeight - s.scrollTop - s.clientHeight),
          jump: !!document.querySelector('[data-testid=jump-latest]')}; })()"""


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.set_default_timeout(15000)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(URL)
        page.wait_for_timeout(1500)
        if page.get_by_text("Select model…").count():
            page.get_by_text("Select model…").first.click()
            page.wait_for_timeout(400)
            page.get_by_text(MODEL, exact=False).first.click()
        box = page.locator("textarea").first
        box.fill(PROMPT)
        box.press("Enter")
        base = (page.evaluate(STATE) or {"h": 0})["h"]
        start = time.time()
        while time.time() - start < 180:                                  # wait until new text has streamed in
            s = page.evaluate(STATE)
            if s and s["h"] > s["c"] + 700 and s["h"] > base + 500:
                break
            page.wait_for_timeout(500)
        page.wait_for_timeout(1500)
        print("following the stream, distance from the end:", page.evaluate(STATE)["gap"])
        page.mouse.move(700, 400)
        for _ in range(4):
            page.mouse.wheel(0, -300)
            page.wait_for_timeout(120)
        page.wait_for_timeout(300)
        samples = []
        for _ in range(10):
            page.wait_for_timeout(500)
            samples.append(page.evaluate(STATE))
        tops = [x["top"] for x in samples]
        held = max(tops) - min(tops) <= 2 and samples[-1]["gap"] > 100
        print("scroll position over 5 s:", tops)
        print("content grew while generating:", samples[0]["h"], "->", samples[-1]["h"])
        print("'Jump to latest' shown:", samples[-1]["jump"])
        if samples[-1]["jump"]:
            page.click("[data-testid=jump-latest]")
            page.wait_for_timeout(1500)
            print("after Jump to latest, distance from the end:", page.evaluate(STATE)["gap"])
        print("page errors:", errors)
        browser.close()
        print("PASS: the position held" if held and not errors else "FAIL: the view was pulled back or errors occurred")
        return 0 if held and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
