import pytest

from app.agent.engine import AgentEngine
from app.agent.rescue import find_printed_file
from app.sessions.manager import SessionManager
from app.tools.engine import ToolEngine

HTML = "<!DOCTYPE html>\n<html>\n<head><title>Zeus</title></head>\n<body>\n" + "<p>line</p>\n" * 12 + "</body>\n</html>"
LANDING = "You are a senior front-end developer. Generate ONE complete, self-contained HTML file for a product landing page. Output ONLY the raw HTML code."


@pytest.mark.parametrize("prompt,reply,path", [
    (LANDING, f"```html\n{HTML}\n```", "index.html"),
    (LANDING, f"Here you go:\n```html\n{HTML}\n```\nEnjoy!", "index.html"),
    ("Write a Python script that renames files", "```python\n" + "print('x')\n" * 14 + "```", "main.py"),
    ("Create a file called app.js that logs hello", "```\n" + "console.log(1);\n" * 14 + "```", "app.js"),
    ("generate the website file", f"```\n{HTML}\n```", "index.html"),                   # no lang tag: content sniffed
])
def test_finds_the_printed_file(prompt, reply, path):
    r = find_printed_file(prompt, reply)
    assert r and r["path"] == path and r["content"].endswith("\n") and "```" not in r["content"]


@pytest.mark.parametrize("prompt,reply", [
    ("Explain how a mutex works", "```c\n" + "x;\n" * 20 + "```"),                          # not a file request
    ("Show me an example of a linked list in C", "```c\n" + "x;\n" * 20 + "```"),          # asks for an example
    ("Create a file hello.txt", "```\nhi\n```"),                                            # tiny snippet
    (LANDING, f"```html\n{HTML}\n```\n```css\n" + "a{}\n" * 15 + "```"),                    # two big files: ambiguous
    (LANDING, "No code here, sorry."),
    ("Write a script but don't save a file", "```python\n" + "x=1\n" * 20 + "```"),
    ("", f"```html\n{HTML}\n```"),
])
def test_does_not_guess_when_unclear(prompt, reply):
    r = find_printed_file(prompt, reply)
    assert r is None or not r["path"].startswith(("..", "/"))
    assert r is None


class Scripted:
    def __init__(self, turns): self.turns, self.seen = list(turns), []

    async def chat_stream(self, model, messages, tools, num_ctx, options=None, think=None):
        self.seen.append(tools)
        t = self.turns.pop(0)
        yield {"message": {"content": t.get("content", ""), "tool_calls": t.get("tool_calls")}}
        yield {"done": True, "prompt_eval_count": 1, "eval_count": 1, "eval_duration": 1}


async def test_engine_saves_printed_file_through_normal_approval(harness, ws_dir, db):
    h = harness("approval", ["once"])
    s = SessionManager(db).create(h.ctx.project["id"], model="m")
    await AgentEngine(db, Scripted([{"content": f"```html\n{HTML}\n```"}]), ToolEngine()).run(h.ctx, s, LANDING)
    assert (ws_dir / "index.html").read_text().startswith("<!DOCTYPE html>")
    assert [r["action"] for r in h.requests] == ["create_file"]                              # it asked for approval like any write
    assert any(e["type"] == "notice" and "instead of saving" in e["text"] for e in h.events) and h.events[-1]["type"] == "run_end"


async def test_rescue_respects_rejection_and_never_overwrites(harness, ws_dir, db):
    h = harness("approval", ["reject"])
    s = SessionManager(db).create(h.ctx.project["id"], model="m")
    await AgentEngine(db, Scripted([{"content": f"```html\n{HTML}\n```"}]), ToolEngine()).run(h.ctx, s, LANDING)
    assert not (ws_dir / "index.html").exists()                                            # rejected -> nothing written
    (ws_dir / "index.html").write_text("MINE")
    h.answers.append("once")                                                                # even if approved...
    s2 = SessionManager(db).create(h.ctx.project["id"], model="m")
    await AgentEngine(db, Scripted([{"content": f"```html\n{HTML}\n```"}]), ToolEngine()).run(h.ctx, s2, LANDING)
    assert (ws_dir / "index.html").read_text() == "MINE"                                   # ...an existing file is never replaced


async def test_no_rescue_when_model_already_saved_a_file(harness, ws_dir, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="m")
    call = {"function": {"name": "create_file", "arguments": {"path": "a.html", "content": "<p>x</p>", "reason": "r"}}}
    prov = Scripted([{"tool_calls": [call]}, {"content": f"Saved. Here it is:\n```html\n{HTML}\n```"}])
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, LANDING)
    assert (ws_dir / "a.html").exists() and not (ws_dir / "index.html").exists()


def test_traversal_in_prompt_never_becomes_a_path():
    r = find_printed_file("Create the page in ../../etc/passwd.html", f"```html\n{HTML}\n```")
    assert r and ".." not in r["path"] and not r["path"].startswith("/")


def test_long_spec_mentioning_example_later_still_counts_as_a_file_request():
    spec = LANDING + "\n\n" + "Requirements: be responsive.\n" * 30 + "Example commands: zeus --help. For example, show a copy button.\nHow to verify: open it in a browser."
    r = find_printed_file(spec, f"```html\n{HTML}\n```")
    assert r and r["path"] == "index.html"
    assert find_printed_file("Show me an example HTML page for a landing page", f"```html\n{HTML}\n```") is None


def test_named_file_in_a_long_spec_does_not_rename_unrelated_code():
    """Regression (real run): the spec showed `zeus run --config zeus.yaml` as a CLI example, so the HTML page was saved as zeus.yaml."""
    spec = LANDING + "\n\n" + "Requirements.\n" * 20 + "Example commands: `zeus --help`, `zeus run --config zeus.yaml`."
    assert find_printed_file(spec, f"```html\n{HTML}\n```")["path"] == "index.html"
    # a name in the request itself is honoured when its extension fits the code...
    assert find_printed_file("Generate a landing page file called site.html", f"```html\n{HTML}\n```")["path"] == "site.html"
    # ...but not when it contradicts it
    assert find_printed_file("Create a file called notes.yaml with a landing page", f"```html\n{HTML}\n```")["path"] == "index.html"
    assert find_printed_file("Write a script called tool.py", "```python\n" + "x = 1\n" * 14 + "```")["path"] == "tool.py"


from app.agent.rescue import find_printed_files


def test_labelled_blocks_are_found_and_header_comments_stripped():
    reply = ("```python\n# todo.py\n\nimport json\nprint('x')\n```\n\n```python\n# todos.json\n\n[]\n```\n\n"
             "Now the README:\n**README.md**:\n```markdown\n# Todo\nUse it.\n```\n\n### test_todo.py\n```python\nimport unittest\n```\n"
             "```html\n<!-- index.html -->\n<p>hi</p>\n```")
    got = {f["path"]: f["content"] for f in find_printed_files(reply)}
    assert set(got) == {"todo.py", "todos.json", "README.md", "test_todo.py", "index.html"}
    assert got["todos.json"] == "[]\n" and not got["todo.py"].startswith("#") and got["index.html"] == "<p>hi</p>\n"      # header comment removed (a '#' line would break JSON)
    assert got["README.md"].startswith("# Todo")                                                                       # a real heading inside the file is kept


def test_unlabelled_or_suspicious_blocks_are_not_guessed():
    assert find_printed_files("Here is an example:\n```python\nprint('hi')\n```") == []
    assert find_printed_files("```python\n# ../../etc/passwd.py\nx\n```") == []
    assert find_printed_files("```python\n# /abs/path.py\nx\n```") == []
    assert find_printed_files("```python\n# not a file name\nx\n```") == []
    assert find_printed_files("") == [] and find_printed_files("no code") == []
    twice = find_printed_files("```python\n# a.py\nx\n```\n```python\n# a.py\ny\n```")
    assert len(twice) == 1                                                                                            # duplicates collapse
    assert len(find_printed_files("".join(f"```python\n# f{i}.py\nx\n```\n" for i in range(20)))) == 6              # capped
