"""One row per added document, as an Excel file the bot attaches to the thread ("문서로 추가").

After the bot registers what was shared, people usually want a list they can keep or forward:
which files became documents, what each one is about in one line, and which files were
skipped and why. ``build_sheet`` makes that list as .xlsx, ``one_line_summaries`` writes the
one-line content from each document's own text (the model, retrieval off), and
``post_sheet`` uploads it to the thread (``files:write``).
"""

from __future__ import annotations

import io
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

logger = logging.getLogger("baryon.slack.intake")

SUMMARY_CHARS = 4000       # document text the model reads for its one line
SUMMARY_MAX = 120          # characters kept from the model's line
SHEET_NOTE = ":bar_chart: 추가한 문서 목록(문서마다 한 줄 내용)을 엑셀로 첨부했습니다."
SCOPE_NOTE = ":information_source: 엑셀 목록을 첨부하려면 봇에 `files:write` 권한이 필요합니다. 앱 설정에서 범위를 추가하고 다시 설치해 주세요."


def sheet_enabled() -> bool:
    return os.getenv("SLACK_INTAKE_SHEET", "true").strip().lower() in {"1", "true", "yes", "on"}


def first_line(text: str) -> str:
    """Fallback one-liner: the first line with real content (not a heading marker, table rule or metadata)."""
    for line in str(text or "").splitlines():
        line = re.sub(r"^[#>*\-\s|]+", "", line).strip(" |")
        if len(line) >= 8 and not re.fullmatch(r"[-:| ]+", line) and not re.match(r"^[^:\n]{1,20}:\s", line):
            return line[:SUMMARY_MAX]
    return ""


def _clean(line: str) -> str:
    line = re.sub(r"<think>[\s\S]*?</think>", "", line or "")
    line = next((part for part in line.splitlines() if part.strip()), "")
    line = re.sub(r"^\s*(?:[-*•]|\d+[.)]|한\s*줄\s*(?:요약|내용)?\s*[:：])\s*", "", line).strip().strip('"“”')
    line = line.replace("**", "")
    return line if len(line) <= SUMMARY_MAX else line[: SUMMARY_MAX - 1] + "…"


def one_line_summaries(intake, added, slack_user: str, thread_id: str, workers: int = 4) -> dict[str, str]:
    """document_id → one line of what the document is about, from its own text (retrieval off)."""
    def summarize(item) -> tuple[str, str]:
        try:
            text = intake.document_text(item.document_id, slack_user)
        except Exception:
            logger.warning("could not read document %s for its summary", item.document_id, exc_info=True)
            return item.document_id, ""
        if not text.strip():
            return item.document_id, ""
        prompt = ("아래 문서가 무엇에 관한 문서인지 한국어 한 문장(80자 이내)으로만 써 주세요. "
                  "머리말·따옴표·목록 기호 없이 문장 하나만 씁니다.\n\n"
                  f"문서 이름: {item.title}\n\n{text[:SUMMARY_CHARS]}")
        try:
            answer = intake.baryon.ask([{"role": "user", "content": prompt}], slack_user,
                                       thread_id=thread_id, use_retrieval=False)
            line = _clean(answer.text)
        except Exception:
            logger.warning("summary for %s failed; using the first line", item.document_id, exc_info=True)
            line = ""
        return item.document_id, line or first_line(text)

    unique = list({item.document_id: item for item in added}.values())
    if not unique:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(unique)))) as pool:
        return dict(pool.map(summarize, unique))


def build_sheet(result, summaries: dict[str, str], requested_by: str = "", source: str = "") -> bytes:
    """.xlsx bytes: sheet 1 = added documents (one row each), sheet 2 = skipped files (if any)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = "추가된 문서"
    headers = ["번호", "파일", "문서 제목", "한 줄 내용", "조각 수", "파서", "상태", "문서 ID"]
    sheet.append(headers)
    for index, item in enumerate(result.added, 1):
        sheet.append([index, item.name, item.title, summaries.get(item.document_id, ""), item.chunks or None,
                      item.parser, "이전에 등록됨" if item.reused else "새로 등록", item.document_id])
    _style(sheet, headers, widths=[6, 42, 36, 70, 8, 10, 14, 38], Font=Font, PatternFill=PatternFill,
           Alignment=Alignment, get_column_letter=get_column_letter, wrap_column=4)

    if result.skipped:
        skipped = book.create_sheet("건너뛴 파일")
        skipped.append(["번호", "파일", "사유"])
        for index, item in enumerate(result.skipped, 1):
            skipped.append([index, item.name, item.reason])
        _style(skipped, ["번호", "파일", "사유"], widths=[6, 50, 50], Font=Font, PatternFill=PatternFill,
               Alignment=Alignment, get_column_letter=get_column_letter, wrap_column=3)

    info = book.create_sheet("정보")
    for key, value in (("만든 시각", datetime.now().strftime("%Y-%m-%d %H:%M")), ("요청한 사람", requested_by),
                       ("출처", source), ("추가된 문서", len(result.added)), ("건너뛴 파일", len(result.skipped)),
                       ("한 줄 내용", "각 문서의 원문 앞부분을 읽고 모델이 쓴 요약(검색 결과 아님)")):
        info.append([key, value])
    info.column_dimensions["A"].width = 14
    info.column_dimensions["B"].width = 70

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _style(sheet, headers, widths, Font, PatternFill, Alignment, get_column_letter, wrap_column):
    for column, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1E293B")
    for row in sheet.iter_rows(min_row=2, min_col=wrap_column, max_col=wrap_column):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(1, sheet.max_row)}"


def sheet_filename(ts: str) -> str:
    stamp = datetime.fromtimestamp(float(ts)).strftime("%Y%m%d-%H%M") if ts else datetime.now().strftime("%Y%m%d-%H%M")
    return f"추가된_문서_{stamp}.xlsx"


def post_sheet(web, channel: str, thread_ts: str, content: bytes, filename: str, comment: str = SHEET_NOTE) -> bool:
    """Attach the sheet to the thread. Without files:write, say what to add instead of failing silently."""
    try:
        web.files_upload_v2(channel=channel, thread_ts=thread_ts, content=content, filename=filename,
                            title=filename.removesuffix(".xlsx"), initial_comment=comment)
        return True
    except Exception as error:
        logger.warning("could not attach %s: %s", filename, error)
        if "missing_scope" in str(error) or "not_allowed_token_type" in str(error):
            web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=SCOPE_NOTE)
        else:
            web.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                 text=f":warning: 엑셀 목록을 첨부하지 못했습니다 ({type(error).__name__}).")
        return False
