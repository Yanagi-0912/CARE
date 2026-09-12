"""把手寫表格的辨識結果組成 LINE Flex 表格卡。

上游（n8n 的影像解析）只回得出一個字串欄位，表格是以 Markdown 表格的形式
夾在那段文字裡的——webhook 的 Respond 節點只回 `$json.text`，
`mutimedia_processor._extract_user_text_via_webhook` 也只取單一字串，結構化
欄位傳不過來。所以這裡從文字反解表格，而不是接收 dict。

不用純文字直接回覆的理由：LINE 的訊息字體不是等寬的，Markdown 表格在手機上
會完全對不齊，欄一多就變成一團字。長輩取向的介面尤其不能這樣呈現。

儲存格內的「值（註記）」是上游刻意產生的格式（正字計數展開成
`正正丅（12 次）`、同上箭頭展開成 `✓（↓同上）`）。註記在這裡拆出來用小一級的
淡色字排在主值下方：主值要一眼看到，但註記是長輩核對「機器有沒有讀錯」的唯一
依據，不能丟掉。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from linebot.v3.messaging import FlexContainer, FlexMessage

from resources.flex_messages import size_guard, theme

_HEADER_DEFAULT = "表格內容"

# LINE altText 官方上限 400 字元，超過整則訊息會被拒收。
_ALT_TEXT_MAX_LEN = 400

# LINE Flex 的 text 元件不接受空字串，空字串會讓整則訊息被 400 拒收。
# 空儲存格在手寫紀錄裡是有意義的（那格就是沒寫），用淡色破折號表示「沒有紀錄」，
# 而不是補一個看起來像資料的值。
_EMPTY_CELL = "—"

# 超過這個欄數，橫式表格在手機寬度下每格只剩兩三個字，換成一列一張直式小卡。
_MAX_HORIZONTAL_COLS = 4

# 欄寬權重上限：某一欄特別長時給它多一點空間，但不讓它把其他欄擠成一個字。
_MAX_COL_FLEX = 3

# 逐步減列時至少要留下的資料列數。少於這個數量的表格已經沒有表格的意義，
# 呼叫端應該退回純文字。
_MIN_ROWS = 2

# 儲存格的「值（註記）」：全形括號是上游 prompt 指定的格式。
_CELL_NOTE = re.compile(r"^(?P<value>.*?)（(?P<note>[^（）]*)）\s*$")

_SEPARATOR_ROW = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def _split_row(line: str) -> list[str]:
    """把一行 Markdown 表格切成儲存格。前後的 | 是選用的。"""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def parse_markdown_table(
    text: str,
) -> tuple[list[str], list[str], list[list[str]]] | None:
    """從一段文字裡抽出第一個 Markdown 表格。

    回傳 (表格前的文字行, 欄名, 資料列)；找不到合法表格時回傳 None，由呼叫端
    決定退回純文字。表格前的文字行會保留——上游把標題與「（數值為推估）」這類
    警語放在表格之前，那是卡片的抬頭與提醒，不能丟。
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "|" not in line or index + 1 >= len(lines):
            continue
        if not _SEPARATOR_ROW.match(lines[index + 1]):
            continue
        columns = _split_row(line)
        if len(columns) < 2:
            continue

        rows: list[list[str]] = []
        for body_line in lines[index + 2 :]:
            if "|" not in body_line:
                break
            cells = _split_row(body_line)
            # 欄數不符的列補齊或截斷，不整張放棄：手寫表格本來就常缺格。
            cells = (cells + [""] * len(columns))[: len(columns)]
            rows.append(cells)

        if not rows:
            return None
        preamble = [ln.strip() for ln in lines[:index] if ln.strip()]
        return preamble, columns, rows
    return None


def _cell_parts(raw: str) -> tuple[str, str | None]:
    """拆出主值與括號註記。沒有註記時第二個回傳值是 None。"""
    value = raw.strip()
    if not value:
        return _EMPTY_CELL, None
    matched = _CELL_NOTE.match(value)
    if not matched:
        return value, None
    main = matched.group("value").strip()
    note = matched.group("note").strip()
    if not main:
        # 整格只有括號內容（例如「（頭暈）」），那就是這格的值本身。
        return note or _EMPTY_CELL, None
    return main, note or None


def _column_flex(columns: Sequence[str], rows: Sequence[Sequence[str]]) -> list[int]:
    """依該欄最長內容決定欄寬權重。"""
    widths: list[int] = []
    for index in range(len(columns)):
        longest = len(columns[index])
        for row in rows:
            if index < len(row):
                longest = max(longest, len(_cell_parts(row[index])[0]))
        widths.append(longest)
    shortest = max(min(widths), 1)
    return [max(1, min(_MAX_COL_FLEX, round(w / shortest))) for w in widths]


def _cell_node(
    raw: str, ft: theme.FlexTheme, flex: int, *, header: bool
) -> dict[str, Any]:
    value, note = _cell_parts(raw)
    contents: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": value,
            "size": ft.caption if header else ft.body,
            "color": theme.TEXT if value != _EMPTY_CELL else theme.TEXT_FAINT,
            "weight": "bold" if header else "regular",
            "align": "center",
            "wrap": True,
        }
    ]
    if note:
        contents.append(
            {
                "type": "text",
                "text": note,
                "size": ft.caption,
                "color": theme.TEXT_FAINT,
                "align": "center",
                "wrap": True,
            }
        )
    return {
        "type": "box",
        "layout": "vertical",
        "flex": flex,
        "paddingAll": "sm",
        "contents": contents,
    }


def _row_node(
    cells: Sequence[str],
    ft: theme.FlexTheme,
    flexes: Sequence[int],
    *,
    header: bool = False,
    striped: bool = False,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "box",
        "layout": "horizontal",
        "contents": [
            _cell_node(cell, ft, flexes[i], header=header)
            for i, cell in enumerate(cells)
        ],
    }
    if header:
        node["backgroundColor"] = theme.BRAND_TINT
    elif striped:
        node["backgroundColor"] = theme.SURFACE_ALT
    return node


def _stacked_row_node(
    columns: Sequence[str], cells: Sequence[str], ft: theme.FlexTheme
) -> dict[str, Any]:
    """欄數過多時的直式呈現：一列一張小卡，每行是「欄名　值」。"""
    lines: list[dict[str, Any]] = []
    for name, raw in zip(columns, cells):
        value, note = _cell_parts(raw)
        lines.append(
            {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                    {
                        "type": "text",
                        "text": name,
                        "size": ft.caption,
                        "color": theme.TEXT_MUTED,
                        "flex": 2,
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": value if not note else f"{value}　{note}",
                        "size": ft.body,
                        "color": theme.TEXT if value != _EMPTY_CELL else theme.TEXT_FAINT,
                        "flex": 3,
                        "wrap": True,
                    },
                ],
            }
        )
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.SURFACE_ALT,
        "cornerRadius": "md",
        "paddingAll": "md",
        "margin": "md",
        "spacing": "xs",
        "contents": lines,
    }


def _alt_text(title: str, columns: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    parts = [title, "／".join(columns)]
    for row in rows:
        parts.append("／".join(_cell_parts(cell)[0] for cell in row))
    return "　".join(parts)[:_ALT_TEXT_MAX_LEN]


def build_table_flex(
    *,
    columns: Sequence[str],
    rows: Sequence[Sequence[str]],
    ft: theme.FlexTheme,
    title: str = _HEADER_DEFAULT,
    notices: Sequence[str] = (),
) -> FlexMessage:
    """組出表格卡。

    列數過多而超過 LINE 的 bubble 上限時，從尾端逐步減列並註明還有幾列沒顯示
    ——一張少了後面幾列的表格仍然有用，使用者至少看得到前幾筆與欄位結構。
    真的減到 `_MIN_ROWS` 還放不下才拋 ValueError，由呼叫端退回純文字。
    """
    if not columns:
        raise ValueError("表格至少要有一欄")

    kept = list(rows)
    while True:
        hidden = len(rows) - len(kept)
        bubble = _build_bubble(columns, kept, ft, title, notices, hidden)
        if size_guard.fits(bubble):
            return FlexMessage(
                altText=_alt_text(title, columns, kept),
                contents=FlexContainer.from_dict(bubble),
            )
        if len(kept) <= _MIN_ROWS:
            raise ValueError("table bubble exceeds LINE size limit")
        kept = kept[:-1]


def _build_bubble(
    columns: Sequence[str],
    rows: Sequence[Sequence[str]],
    ft: theme.FlexTheme,
    title: str,
    notices: Sequence[str],
    hidden: int,
) -> dict[str, Any]:
    body: list[dict[str, Any]] = []
    for notice in notices:
        body.append(
            {
                "type": "text",
                "text": notice,
                "size": ft.caption,
                "color": theme.STATUS_PENDING,
                "wrap": True,
            }
        )

    if len(columns) > _MAX_HORIZONTAL_COLS:
        body.extend(_stacked_row_node(columns, row, ft) for row in rows)
    else:
        flexes = _column_flex(columns, rows)
        body.append(_row_node(columns, ft, flexes, header=True))
        for index, row in enumerate(rows):
            body.append(_row_node(row, ft, flexes, striped=index % 2 == 1))

    if hidden > 0:
        body.append(
            {
                "type": "text",
                "text": f"還有 {hidden} 列未顯示",
                "size": ft.caption,
                "color": theme.TEXT_FAINT,
                "align": "center",
                "wrap": True,
                "margin": "md",
            }
        )

    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme.BRAND,
            "paddingAll": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": title or _HEADER_DEFAULT,
                    "size": ft.heading,
                    "color": theme.TEXT_ON_BRAND,
                    "weight": "bold",
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "md",
            "backgroundColor": theme.SURFACE,
            "spacing": "none",
            "contents": body,
        },
    }


def build_table_flex_from_text(
    text: str, ft: theme.FlexTheme
) -> FlexMessage | None:
    """從上游那段文字直接組卡；沒有可用表格時回傳 None。"""
    parsed = parse_markdown_table(text)
    if parsed is None:
        return None
    preamble, columns, rows = parsed
    title = preamble[0] if preamble else _HEADER_DEFAULT
    notices = preamble[1:]
    try:
        return build_table_flex(
            columns=columns, rows=rows, ft=ft, title=title, notices=notices
        )
    except ValueError:
        return None
