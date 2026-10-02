# ruff: noqa: E501
"""Excel downloads, written as the few XML parts a workbook needs.

Like the demo's functional report, this needs no spreadsheet library: every
cell is an inline string or a number, under a bold header that stays put.
"""

import re
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED
from zipfile import ZipFile


def _column(index):
    """A, B, … Z, AA: the letters Excel names a column by."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _xlsx_cell(reference, value, *, style=0):
    styled = f' s="{style}"' if style else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"{styled}><v>{value}</v></c>'
    # Text is stored as text, never a formula, so it needs no quote prefix.
    text = escape(_XML_ILLEGAL.sub("", "" if value is None else str(value)))
    return (
        f'<c r="{reference}" t="inlineStr"{styled}>'
        f'<is><t xml:space="preserve">{text}</t></is></c>'
    )


def workbook(header, rows):
    """A one-sheet workbook: a bold, frozen header row over the data rows.

    Inline strings keep it to the parts Excel needs, as the demo workbook does,
    without a spreadsheet library.
    """
    rows = [header, *rows]
    widths = [len(str(title)) for title in header]
    lines = []
    for number, row in enumerate(rows, start=1):
        cells = []
        for index, value in enumerate(row):
            if number > 1 and index < len(widths):
                widths[index] = max(widths[index], len(str(value or "")))
            cells.append(
                _xlsx_cell(f"{_column(index)}{number}", value, style=int(number == 1)),
            )
        lines.append(f'<row r="{number}">{"".join(cells)}</row>')
    columns = "".join(
        f'<col min="{index}" max="{index}" width="{min(width, 60) + 2}" customWidth="1"/>'
        for index, width in enumerate(widths, start=1)
    )
    last = f"{_column(len(header) - 1)}{len(rows)}"
    sheet = (
        f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{_MAIN}">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        f"<cols>{columns}</cols><sheetData>{''.join(lines)}</sheetData>"
        f'<autoFilter ref="A1:{last}"/></worksheet>'
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path, body in {**_XLSX_PARTS, "xl/worksheets/sheet1.xml": sheet}.items():
            archive.writestr(path, body)
    return output.getvalue()


_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_TYPES = "application/vnd.openxmlformats-officedocument.spreadsheetml"
# XML 1.0 has no way to write these control characters, even escaped.
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_XLSX_PARTS = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{_TYPES}.sheet.main+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{_TYPES}.worksheet+xml"/>'
        f'<Override PartName="/xl/styles.xml" ContentType="{_TYPES}.styles+xml"/>'
        "</Types>"
    ),
    "_rels/.rels": (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_RELATIONSHIPS}">'
        f'<Relationship Id="rId1" Type="{_OFFICE}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    ),
    "xl/workbook.xml": (
        f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{_MAIN}" xmlns:r="{_OFFICE}">'
        '<sheets><sheet name="Export" sheetId="1" r:id="rId1"/></sheets></workbook>'
    ),
    "xl/_rels/workbook.xml.rels": (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_RELATIONSHIPS}">'
        f'<Relationship Id="rId1" Type="{_OFFICE}/worksheet" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{_OFFICE}/styles" Target="styles.xml"/>'
        "</Relationships>"
    ),
    # Style 1 is the header's bold font.
    "xl/styles.xml": (
        f'<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{_MAIN}">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    ),
}
