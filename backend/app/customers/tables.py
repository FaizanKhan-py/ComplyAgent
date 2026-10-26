"""Reading the UCI dataset's shape from CSV, .xls or .xlsx: the dataset prediction upload and the
customer import both read files this way.
"""

from __future__ import annotations

import csv
import io

MAX_ROWS = 500_000


class TableError(Exception):
    """The file cannot be read as a table. `status_code` is the HTTP status to return."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


_XLS_MAGIC = bytes.fromhex("d0cf11e0")  # the old Excel format (an OLE2 file)
_ZIP_MAGIC = bytes.fromhex("504b0304")  # .xlsx is a zip archive


def _cell(value) -> str:
    """A spreadsheet cell as the text a CSV would hold: 20000.0 becomes "20000"."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _read_rows(data: bytes) -> list[list[str]]:
    """The first sheet or the CSV as rows of text. The format comes from the bytes, not the
    file name, so a spreadsheet renamed to .csv (or the other way round) still reads."""
    if data.startswith(_XLS_MAGIC):
        try:
            import xlrd

            sheet = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
        except ImportError as exc:
            raise TableError("Reading .xls files needs xlrd: pip install xlrd", 503) from exc
        except Exception as exc:
            raise TableError(f"The .xls file cannot be read ({exc}).") from None
        return [[_cell(v) for v in sheet.row_values(i)] for i in range(sheet.nrows)]
    if data.startswith(_ZIP_MAGIC):
        try:
            import openpyxl

            book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except ImportError as exc:
            raise TableError(
                "Reading .xlsx files needs openpyxl: pip install openpyxl", 503
            ) from exc
        except Exception as exc:
            raise TableError(f"The .xlsx file cannot be read ({exc}).") from None
        try:
            return [
                [_cell(v) for v in row] for row in book.worksheets[0].iter_rows(values_only=True)
            ]
        finally:
            book.close()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return list(csv.reader(io.StringIO(text)))


def read_table(data: bytes) -> tuple[list[str], list[list[str]]]:
    rows = [r for r in _read_rows(data) if any(cell.strip() for cell in r)]
    if not rows:
        raise TableError("The file is empty.")
    header = [h.strip() for h in rows[0]]
    body = rows[1:]
    # The UCI spreadsheet has two header rows: X1..X23 and then LIMIT_BAL, ...
    if body and "LIMIT_BAL" in [c.strip() for c in body[0]] and "LIMIT_BAL" not in header:
        header = [(second.strip() or first) for first, second in zip(header, body[0], strict=False)]
        body = body[1:]
    if not body:
        raise TableError("The file has no data rows.")
    if len(body) > MAX_ROWS:
        raise TableError(f"The file has more than {MAX_ROWS:,} rows.", 413)
    return header, body
