import asyncio
import io
import json
import subprocess
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("markitdown")

from app.api.main import create_app                      # noqa: E402
from app.convert import service                          # noqa: E402
from app.convert.service import ConvertError, convert_bytes, info   # noqa: E402


# ------------------------------------------------------------------ fixtures built in code (no binary files in the repo)
def make_pdf(text: str | None) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"]
    stream = (f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET" if text else "").encode()
    objs += [b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, x)


def make_docx() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Doc title</w:t></w:r></w:p><w:p><w:r><w:t>Some paragraph text.</w:t></w:r></w:p></w:body></w:document>')
    return b.getvalue()


def make_xlsx() -> bytes:
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Plan"; ws.append(["task", "owner"]); ws.append(["ship", "ana"])
    b = io.BytesIO(); wb.save(b); return b.getvalue()


def make_xls() -> bytes:
    xlwt = pytest.importorskip("xlwt")
    wb = xlwt.Workbook(); ws = wb.add_sheet("Old"); ws.write(0, 0, "col1"); ws.write(0, 1, "col2")
    b = io.BytesIO(); wb.save(b); return b.getvalue()


def make_pptx() -> bytes:
    from pptx import Presentation
    p = Presentation(); s = p.slides.add_slide(p.slide_layouts[1]); s.shapes.title.text = "Roadmap"; s.placeholders[1].text = "Q1 goals"
    b = io.BytesIO(); p.save(b); return b.getvalue()


def make_zip() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("inner.html", "<h1>Inner page</h1>"); z.writestr("n.csv", "a,b\n1,2\n")
    return b.getvalue()


def make_epub() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="c.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("c.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Book</dc:title><dc:identifier id="i">x</dc:identifier><dc:language>en</dc:language></metadata><manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("c1.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter one</h1><p>Text.</p></body></html>')
    return b.getvalue()


NOTEBOOK = json.dumps({"cells": [{"cell_type": "markdown", "source": ["# Analysis"], "metadata": {}},
                                 {"cell_type": "code", "source": ["print(1)"], "metadata": {}, "outputs": [], "execution_count": None}],
                       "metadata": {}, "nbformat": 4, "nbformat_minor": 5}).encode()

CASES = [
    ("page.html", b"<html><body><h1>Hello</h1><p>World <b>bold</b></p><table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table></body></html>", ["# Hello", "**bold**", "| a | b |"]),
    ("data.csv", b"name,qty\napple,3\n", ["| name | qty |", "| apple | 3 |"]),
    ("conf.json", b'{"a": 1}', ['"a": 1']),
    ("feed.xml", b"<root><item>x</item></root>", ["<item>x</item>"]),
    ("notes.txt", "naïve café".encode(), ["naïve café"]),
    ("spec.md", b"# Already markdown\n", ["# Already markdown"]),
    ("nb.ipynb", NOTEBOOK, ["# Analysis", "print(1)"]),
    ("plan.xlsx", make_xlsx(), ["## Plan", "| task | owner |", "| ship | ana |"]),
    ("old.xls", None, ["## Old", "col1"]),
    ("deck.pptx", make_pptx(), ["# Roadmap", "Q1 goals"]),
    ("report.docx", make_docx(), ["# Doc title", "Some paragraph text."]),
    ("paper.pdf", make_pdf("Hello PDF world"), ["Hello PDF world"]),
    ("bundle.zip", make_zip(), ["inner.html", "Inner page", "| a | b |"]),
    ("book.epub", make_epub(), ["Chapter one"]),
]


@pytest.mark.parametrize("name,data,expect", CASES, ids=[c[0] for c in CASES])
async def test_each_supported_format_converts(name, data, expect):
    if data is None:
        data = make_xls()
    r = await convert_bytes(data, name)
    assert r["name"] == (name if name.endswith(".md") else name + ".md") and r["source"] == name and not r["truncated"]
    for needle in expect:
        assert needle in r["markdown"], (name, needle, r["markdown"][:200])


def test_every_advertised_format_has_a_test_case():
    tested = {c[0].rsplit(".", 1)[1] for c in CASES}
    assert set(service.FORMATS) - {"markdown", "htm"} <= tested                        # nothing is advertised without being verified
    i = info()
    assert i["installed"] and {"pdf", "docx", "pptx", "xlsx", "md"} <= {f["ext"] for f in i["formats"]}
    assert all(not f["needs_markitdown"] for f in i["formats"] if f["ext"] in ("md", "txt"))


@pytest.mark.parametrize("name", ["song.mp3", "voice.wav", "clip.mp4", "photo.png", "photo.jpg", "tool.exe", "noextension", "archive.tar.gz", "doc.docm", ".hidden"])
async def test_unsupported_types_are_rejected_before_any_conversion(name):
    with pytest.raises(ConvertError) as e:
        await convert_bytes(b"data", name)
    assert e.value.status == 415 and "not supported" in e.value.message and "Images and audio" in e.value.message


async def test_empty_corrupt_and_textless_files_give_clear_errors():
    with pytest.raises(ConvertError) as e:
        await convert_bytes(b"", "a.pdf")
    assert e.value.status == 422 and "empty" in e.value.message
    with pytest.raises(ConvertError) as e:
        await convert_bytes(b"%PDF-1.4 this is not really a pdf", "broken.pdf")           # right signature, unparseable: no raw-bytes fallback
    assert e.value.status == 422 and "Could not convert broken.pdf" in e.value.message
    with pytest.raises(ConvertError) as e:
        await convert_bytes(b"hello this is just text", "mislabelled.pdf")                # wrong signature
    assert e.value.status == 422 and "not a valid .pdf" in e.value.message
    with pytest.raises(ConvertError) as e:
        await convert_bytes(b"\x00\x01\x02random bytes", "garbage.pdf")
    assert e.value.status == 422
    with pytest.raises(ConvertError) as e:
        await convert_bytes(make_pdf(None), "scan.pdf")                                  # valid PDF without any text (like a scan)
    assert e.value.status == 422 and "No text could be extracted" in e.value.message and "OCR" in e.value.message
    with pytest.raises(ConvertError) as e:
        await convert_bytes(b"PK\x03\x04 not a zip", "bad.docx")                          # right signature, not an Office file: no mojibake fallback
    assert e.value.status == 422 and "Could not convert bad.docx" in e.value.message


async def test_long_documents_are_cut_with_a_visible_note():
    big = ("col,val\n" + "".join(f"row{i},{'x' * 40}\n" for i in range(8000))).encode()
    r = await convert_bytes(big, "big.csv")
    assert r["truncated"] and r["omitted_chars"] > 0 and len(r["markdown"].encode()) < 205_000
    assert "the converted document was cut at 200 KB" in r["markdown"] and r["markdown"].rstrip().endswith("]")
    assert r["markdown"].count("| row0 ") == 1                                          # content up to the cut is intact


async def test_filenames_are_sanitised():
    r = await convert_bytes(b"<p>x</p>", "../../etc/pass wd<>.html")
    assert "/" not in r["name"] and ".." not in r["name"] and r["name"].endswith(".html.md")


async def test_missing_markitdown_is_reported_with_install_hint(monkeypatch):
    monkeypatch.setattr(service, "markitdown_version", lambda: None)
    with pytest.raises(ConvertError) as e:
        await convert_bytes(b"<p>x</p>", "a.html")
    assert e.value.status == 503 and "pip install" in e.value.message
    assert (await convert_bytes(b"# plain", "a.md"))["markdown"] == "# plain"           # md/txt never need it


async def test_timeout_is_enforced_and_reported(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(service.subprocess, "run", boom)
    with pytest.raises(ConvertError) as e:
        await convert_bytes(b"<p>x</p>", "a.html", timeout=1)
    assert e.value.status == 408 and "longer than 1 seconds" in e.value.message


async def test_a_hung_converter_process_is_really_killed(monkeypatch, tmp_path):
    """The real subprocess.run path: a worker that never finishes must be stopped by the timeout."""
    slow = tmp_path / "slow.py"
    slow.write_text("import time; time.sleep(60)")
    real_run = subprocess.run
    monkeypatch.setattr(service.subprocess, "run", lambda cmd, **k: real_run([sys.executable, str(slow)], **k))
    import time
    t = time.time()
    with pytest.raises(ConvertError) as e:
        await convert_bytes(b"<p>x</p>", "a.html", timeout=2)
    assert e.value.status == 408 and time.time() - t < 15


# ------------------------------------------------------------------ isolation guarantees of the worker
def _py(code: str):
    return subprocess.run([sys.executable, "-c", code], cwd=service.BACKEND_DIR, capture_output=True, text=True, timeout=60)


def test_worker_blocks_every_kind_of_network_access():
    code = ("import socket\nfrom app.convert.worker import _block_network\n_block_network()\n"
            "for f in (lambda: socket.create_connection(('127.0.0.1', 9)), lambda: socket.getaddrinfo('example.com', 80),\n"
            "          lambda: socket.gethostbyname('example.com'), lambda: socket.socket().connect(('127.0.0.1', 9)),\n"
            "          lambda: socket.socket().connect_ex(('127.0.0.1', 9))):\n"
            "    try: f(); print('ALLOWED')\n"
            "    except OSError as e: print('blocked:', e)\n")
    out = _py(code).stdout
    assert "ALLOWED" not in out and out.count("blocked: network access is disabled") == 5


def test_worker_applies_cpu_memory_and_file_size_limits():
    out = _py("import resource\nfrom app.convert.worker import _limits\n_limits()\n"
              "print(resource.getrlimit(resource.RLIMIT_CPU)[0], resource.getrlimit(resource.RLIMIT_AS)[0], resource.getrlimit(resource.RLIMIT_FSIZE)[0])").stdout.split()
    assert out == ["60", str(3 * 2**30), str(8 * 2**20)]


async def test_remote_references_in_documents_are_never_fetched():
    html = b'<html><body><img src="http://203.0.113.9/x.png"><a href="http://203.0.113.9/l">link</a><p>text</p></body></html>'
    r = await convert_bytes(html, "remote.html")                                          # would hang/fail if it tried to fetch
    assert "text" in r["markdown"]


# ------------------------------------------------------------------ HTTP API
@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "c.db")))


def test_convert_endpoint_roundtrip_and_formats(client):
    f = client.get("/api/convert/formats").json()
    assert f["installed"] and any(x["ext"] == "pdf" for x in f["formats"])
    r = client.post("/api/convert", params={"filename": "report.docx"}, content=make_docx(), headers={"content-type": "application/octet-stream"})
    assert r.status_code == 200 and r.json()["name"] == "report.docx.md" and "# Doc title" in r.json()["markdown"] and r.json()["converter"] == "markitdown"


def test_convert_endpoint_errors(client, monkeypatch):
    assert client.post("/api/convert", params={"filename": "a.mp3"}, content=b"x").status_code == 415
    assert client.post("/api/convert", params={"filename": "a.pdf"}, content=b"").status_code == 422
    assert client.post("/api/convert", content=b"x").status_code == 422                  # filename is required
    monkeypatch.setattr(service, "MAX_INPUT", 1000)
    big = client.post("/api/convert", params={"filename": "a.html"}, content=b"<p>" + b"x" * 5000)
    assert big.status_code == 413 and "larger than" in big.json()["detail"]


def test_converted_document_works_as_a_mission_document(client):
    from app.agent.mission import build_task, parse_mission_md
    md = "# Plan\n\n## Acceptance criteria\n- [ ] ships\n- [ ] documented\n"
    r = client.post("/api/convert", params={"filename": "plan.html"}, content=b"<h1>Plan</h1><h2>Acceptance criteria</h2><ul><li>ships</li><li>documented</li></ul>").json()
    parsed = parse_mission_md(r["markdown"], r["name"])
    assert parsed.criteria == ["ships", "documented"] and r["name"].endswith(".md")      # passes the WebSocket's ".md" check
    assert "plan.html.md" in build_task(1, r["name"], r["markdown"], parsed.criteria, "")


def test_worker_only_loads_the_allowed_converters():
    """No audio / image / YouTube / Bing / Wikipedia / RSS / Outlook / Azure converter exists inside the worker."""
    out = _py("import json\nfrom app.convert.worker import build_converter\n"
              "names = lambda e: sorted({type(c.converter).__name__ for c in build_converter(e)._converters})\n"
              "print(json.dumps({'pdf': names('pdf'), 'txt': names('txt')}))").stdout
    got = json.loads(out)
    banned = {"AudioConverter", "ImageConverter", "YouTubeConverter", "BingSerpConverter", "WikipediaConverter", "RssConverter",
              "OutlookMsgConverter", "DocumentIntelligenceConverter", "ContentUnderstandingConverter"}
    assert not banned & set(got["pdf"]) and not banned & set(got["txt"])
    assert "PlainTextConverter" not in got["pdf"] and "PlainTextConverter" in got["txt"]        # binary formats get no raw-bytes fallback
