"""What the secretary shows, as opposed to what it says.

The voice answer stays short; anything worth looking at (a list, a table, numbers to compare, a trend)
goes to the app's screen as a card through the `show` tool. The app draws it as a list, a table, a bar or
line chart, or number tiles, under the spoken sentence.

    {"type": "card", "card": {"kind", "title", ...}}   one event per card (session events)

Kinds and their fields:
    list   items [{label, detail?, badge?}]
    table  columns [..], rows [[..], ..]
    bar    items [{label, value}], unit?          compare amounts
    line   items [{label, value}], unit?          a trend over time, items in time order
    stats  items [{label, value? | detail}], unit? a few headline numbers
    text   body: lines; "- " starts a bullet       a longer version to read
Every card may carry a note (a source, a time).
"""

from __future__ import annotations

from typing import Any, Callable

KINDS = ("list", "table", "bar", "line", "stats", "text")
MAX_ITEMS = 30
MAX_COLUMNS = 6


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def card(raw: dict[str, Any]) -> dict[str, Any]:
    """A clean card from the model's tool input, or ValueError saying what is wrong."""
    kind = str(raw.get("kind", "")).strip()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    out: dict[str, Any] = {"kind": kind, "title": _text(raw.get("title"), 80)}
    if raw.get("note"):
        out["note"] = _text(raw["note"], 160)
    if kind in {"list", "bar", "line", "stats"}:
        items = []
        for item in (raw.get("items") or [])[:MAX_ITEMS]:
            if not isinstance(item, dict):
                item = {"label": item}
            entry: dict[str, Any] = {"label": _text(item.get("label"), 80)}
            value = _number(item.get("value"))
            if value is not None:
                entry["value"] = value
            for key, limit in (("detail", 200), ("badge", 20)):
                if item.get(key):
                    entry[key] = _text(item[key], limit)
            if entry["label"] or len(entry) > 1:
                items.append(entry)
        if not items:
            raise ValueError("items is empty")
        if kind in {"bar", "line"}:
            if any("value" not in item for item in items):
                raise ValueError(f"every {kind} item needs a numeric value")
            if kind == "line" and len(items) < 2:
                raise ValueError("a line needs two or more points")
        out["items"] = items
        if raw.get("unit"):
            out["unit"] = _text(raw["unit"], 12)
    elif kind == "table":
        columns = [_text(column, 30) for column in (raw.get("columns") or [])[:MAX_COLUMNS]]
        rows = [[_text(cell, 80) for cell in list(row)[:len(columns) or MAX_COLUMNS]]
                for row in (raw.get("rows") or [])[:MAX_ITEMS] if isinstance(row, (list, tuple))]
        if not rows:
            raise ValueError("rows is empty")
        out["columns"], out["rows"] = columns, rows
    else:
        body = "\n".join(line.rstrip() for line in str(raw.get("body", "")).splitlines())[:4000].strip()
        if not body:
            raise ValueError("body is empty")
        out["body"] = body
    return out


SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS),
                 "description": "list: things to pick from or check; table: rows with several fields; bar: compare "
                                "amounts; line: a trend over time (items in time order); stats: two to four headline "
                                "numbers; text: a longer version to read"},
        "title": {"type": "string", "description": "a short heading"},
        "items": {"type": "array", "description": "for list, bar, line and stats",
                  "items": {"type": "object", "properties": {
                      "label": {"type": "string"},
                      "value": {"type": "number", "description": "the number, for bar, line and stats"},
                      "detail": {"type": "string", "description": "a second line (list) or a value as text (stats)"},
                      "badge": {"type": "string", "description": "a short tag: 긴급, 오늘, 완료"}},
                      "required": ["label"]}},
        "columns": {"type": "array", "items": {"type": "string"}, "description": "for table"},
        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "for table"},
        "unit": {"type": "string", "description": "unit of the values: 원, 건, %, 분"},
        "body": {"type": "string", "description": "for text: lines, '- ' starts a bullet"},
        "note": {"type": "string", "description": "a source or time, shown small"},
    },
    "required": ["kind", "title"],
}


def screen_tools(show: Callable[[dict[str, Any]], None]):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("show", "Put a card on the owner's phone screen: a list, a table, a bar or line chart, number tiles, or a "
          "longer text. Say only the gist aloud and show the details here. Several cards may be shown in one answer.",
          SCHEMA)
    async def show_card(args: dict) -> dict:
        try:
            shown = card(args)
        except ValueError as error:
            return {"content": [{"type": "text", "text": f"카드를 만들 수 없어요: {error}"}], "is_error": True}
        show(shown)
        return {"content": [{"type": "text", "text": "화면에 띄웠어요. 말로는 요점만 짧게 하세요."}]}

    return create_sdk_mcp_server("screen", tools=[show_card])
