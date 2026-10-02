"""Sorting, page sizes and downloads shared by the portal's tables.

A table names its sortable columns as `{key: expressions}`, the `order_by`
terms that sort it ascending. `?sort=key` sorts by a column ascending and
`?sort=-key` descending; `?per_page=` picks how many rows a page shows; and
`?export=csv` or `?export=xlsx` downloads every row the filters match, in the
order on screen. A second table on a page sorts by a query key of its own.
"""

import csv

from django.core.paginator import Paginator
from django.db.models import Case
from django.db.models import CharField
from django.db.models import F
from django.db.models import IntegerField
from django.db.models import Value
from django.db.models import When
from django.db.models.functions import Coalesce
from django.db.models.functions import NullIf
from django.db.models.lookups import Exact
from django.http import HttpResponse
from django.utils import timezone

from .xlsx import workbook

PAGE_SIZES = (10, 25, 50, 100)
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
EXPORT_FORMATS = ("csv", "xlsx")
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sorting(request, columns, default, param="sort"):
    """The `sort` value in force, and the `order_by` terms it stands for.

    Empty values sort last whichever way the column runs, and `pk` breaks ties
    so a row never shifts between pages.
    """
    value = request.GET.get(param, "")
    if value.removeprefix("-") not in columns:
        value = default
    descending = value.startswith("-")
    terms = columns[value.removeprefix("-")]
    if not isinstance(terms, (list, tuple)):
        terms = (terms,)
    terms = [F(term) if isinstance(term, str) else term for term in (*terms, "pk")]
    order = [
        term.desc(nulls_last=True) if descending else term.asc(nulls_last=True)
        for term in terms
    ]
    return value, order


def organisation_name(path=""):
    """The name an organisation goes by, its legal name when it has one."""
    return Coalesce(NullIf(f"{path}legal_name", Value("")), f"{path}name")


def labelled(expression, choices):
    """A column sorted by the labels its values show as, rather than the values.

    A value without a label, a blank one among them, sorts last.
    """
    if isinstance(expression, str):
        expression = F(expression)
    return Case(
        *(
            When(Exact(expression, Value(value)), then=Value(str(label)))
            for value, label in choices
        ),
        default=Value(None),
        output_field=CharField(),
    )


def ranked(field, values):
    """A column sorted in the given order of its values, not alphabetically."""
    return Case(
        *(When(**{field: value}, then=Value(n)) for n, value in enumerate(values)),
        default=Value(len(values)),
        output_field=IntegerField(),
    )


def paginate(request, items, default=25):
    """A page of `items`, sized by `?per_page=` when it names an offered size."""
    sizes = sorted({*PAGE_SIZES, default})
    size = request.GET.get("per_page", "")
    size = int(size) if size.isdigit() and int(size) in sizes else default
    page = Paginator(items, size).get_page(request.GET.get("page"))
    page.per_page_options = sizes
    return page


def export_format(request):
    """ "csv" or "xlsx" when the request asks for a download, else None."""
    value = request.GET.get("export")
    return value if value in EXPORT_FORMATS else None


def cell(value):
    """Spreadsheets run a cell that starts with a formula character."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def day(value):
    """A day as the portal prints it, from a timestamp or a date."""
    if not value:
        return ""
    if hasattr(value, "tzinfo"):
        value = timezone.localtime(value)
    return f"{value:%d/%m/%Y}"


def export_response(export, name, header, rows):
    """Every row as a download, in the format `export_format` read."""
    filename = f"{name}-{timezone.localdate():%Y-%m-%d}.{export}"
    if export == "xlsx":
        response = HttpResponse(workbook(header, rows), content_type=XLSX_TYPE)
    else:
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        # Lets spreadsheet software pick UTF-8 for names.
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(header)
        writer.writerows([cell(value) for value in row] for row in rows)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
