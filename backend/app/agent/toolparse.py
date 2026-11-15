"""Fallback parser for models that print tool calls as JSON text instead of native tool_calls."""
import json
import re

from ..tools.engine import SCHEMA_BY_NAME

_PATTERNS = [
    re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S),
    re.compile(r"```(?:json|tool_call)?\s*(\{.*?\})\s*```", re.S),
]


def _normalize(obj):
    if isinstance(obj, dict) and isinstance(obj.get("function"), dict):
        obj = obj["function"]
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    args = obj.get("arguments", obj.get("parameters", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return None
    if name in SCHEMA_BY_NAME and isinstance(args, dict):
        return {"function": {"name": name, "arguments": args}}
    return None


_XML_FUNC = re.compile(r"<function=([A-Za-z_][\w]*)\s*>(.*?)(?:</function>|\Z)", re.S)
_XML_PARAM = re.compile(r"<parameter=([A-Za-z_][\w]*)\s*>(.*?)(?:</parameter>|(?=<parameter=)|\Z)", re.S)
_XML_TAIL = re.compile(r"\s*(?:</tool_call>)+")


def _coerce(name: str, key: str, val: str):
    typ = SCHEMA_BY_NAME[name]["parameters"]["properties"].get(key, {}).get("type")
    v = val.strip()
    if typ == "boolean":
        return v.lower() in ("true", "1", "yes")
    if typ in ("integer", "number"):
        try:
            return int(v) if typ == "integer" else float(v)
        except ValueError:
            return v
    if key in ("content", "old_str", "new_str"):
        # Models wrap values in newlines/spaces; drop one wrapper of each, keep inner whitespace.
        v = re.sub(r"^[ \t]*\r?\n?", "", val, count=1)
        v = re.sub(r"\r?\n?[ \t]*$", "", v, count=1)
        if key == "content" and v and not v.endswith("\n"):
            v += "\n"
        return v
    return v


def _xml_calls(text: str) -> tuple[list[dict], list[tuple[int, int]]]:
    """Qwen-style `<function=name><parameter=key>value</parameter></function>` calls."""
    calls, spans = [], []
    for m in _XML_FUNC.finditer(text):
        name = m.group(1)
        if name not in SCHEMA_BY_NAME:
            continue
        args = {pm.group(1): _coerce(name, pm.group(1), pm.group(2)) for pm in _XML_PARAM.finditer(m.group(2))}
        end = m.end()
        tail = _XML_TAIL.match(text, end)
        if tail:
            end = tail.end()
        calls.append({"function": {"name": name, "arguments": args}})
        spans.append((m.start(), end))
    return calls, spans


_JSON_ESC = re.compile(r'\\(["\\/bfnrt]|u[0-9a-fA-F]{4})')
_ESC_MAP = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "/": "/"}


def _unescape(raw: str) -> str:
    """Decode JSON string escapes leniently: unknown backslashes and bare quotes are kept as they are."""
    return _JSON_ESC.sub(lambda m: chr(int(m.group(1)[1:], 16)) if m.group(1)[0] == "u" else _ESC_MAP[m.group(1)], raw)


_LEN_TOOLS = ("create_file", "edit_file")


def lenient_file_call(text: str) -> dict | None:
    """Recover a create_file call whose JSON is *almost* valid (typically one unescaped quote inside a large HTML/JS
    string, which makes strict json.loads reject a 16 KB file). Extracts path/content by position instead of parsing."""
    if not re.search(r'"name"\s*:\s*"create_file"', text):
        return None
    pm = re.search(r'"path"\s*:\s*"([^"\n]+)"', text)
    cm = re.search(r'"content"\s*:\s*"', text)
    if not pm or not cm:
        return None
    start = cm.end()
    # the string ends at the last quote that is followed only by an optional "reason" field and closing braces/fence
    end_re = re.compile(r'"\s*(?:,\s*"reason"\s*:\s*"(?:[^"\\]|\\.)*"\s*)?\}\s*\}?\s*(?:`{3}\s*)?\Z|"\s*(?:,\s*"reason"\s*:\s*"(?:[^"\\]|\\.)*"\s*)?\}\s*\}?\s*`{3}', re.S)
    ends = [m for m in end_re.finditer(text, start)]
    if not ends:
        return None
    content = _unescape(text[start:ends[-1].start()])
    reason = re.search(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"', text[ends[-1].start():])
    if not content.strip():
        return None
    args = {"path": _unescape(pm.group(1)), "content": content}
    if reason:
        args["reason"] = _unescape(reason.group(1))
    return {"function": {"name": "create_file", "arguments": args}}


def looks_like_tool_attempt(text: str) -> bool:
    """True if the text appears to be an (unparseable) tool call: the model tried but the format was broken."""
    names = "|".join(SCHEMA_BY_NAME)
    return bool(re.search(rf'"name"\s*:\s*"(?:{names})"', text) or re.search(rf"<function=(?:{names})\b", text))


def extract_tool_calls(text: str) -> tuple[list[dict], str]:
    """Returns (calls, text with the tool-call JSON removed). Only known tools are accepted."""
    found, spans = _xml_calls(text)
    for pat in ([] if found else _PATTERNS):
        for m in pat.finditer(text):
            try:
                c = _normalize(json.loads(m.group(1)))
            except Exception:
                c = lenient_file_call(m.group(1))        # one bad escape must not lose a whole file
            if c:
                found.append(c)
                spans.append(m.span())
        if found:
            break
    if not found:
        # Last resort: any JSON object embedded in prose that validates as a known tool call.
        dec, pos = json.JSONDecoder(), 0
        while (pos := text.find("{", pos)) != -1:
            try:
                obj, end = dec.raw_decode(text, pos)
            except ValueError:
                pos += 1
                continue
            c = _normalize(obj)
            if c:
                found.append(c)
                spans.append((pos, end))
            pos = end
    cleaned = text
    for a, b in sorted(spans, reverse=True):
        cleaned = cleaned[:a] + cleaned[b:]
    cleaned = re.sub(r"</?tool_call>", "", cleaned)
    return found, cleaned.strip()


def parse_text_tool_calls(text: str) -> list[dict]:
    return extract_tool_calls(text)[0]


_FABRICATED = re.compile(r"</?tool_(?:response|result|output)>|^\s*(?:Created|Edited|Deleted|Wrote) [\w./-]+ \(\d+ chars?\)\s*$|^\s*exit code: -?\d+\s*$", re.I | re.M)


def looks_fabricated_tool_output(text: str) -> bool:
    """True if the model typed what looks like a tool RESULT itself (a classic small-model failure: it narrates that the tool
    ran, with invented output, instead of calling it). Only the system produces tool results."""
    return bool(text and _FABRICATED.search(text))
