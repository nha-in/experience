# ruff: noqa: F811
import csv
import io
import re
from datetime import timedelta
from html import unescape
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db.models.functions import Lower
from django.http import QueryDict
from django.template import Context
from django.template import Template
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.events_and_activities.models import Event
from ohc_experience.experiences import tables
from ohc_experience.experiences import views
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.models import EventRegistration
from ohc_experience.experiences.queue_presentation import QUEUE_SORTS
from ohc_experience.experiences.xlsx import workbook
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

COLUMNS = {"name": Lower("name"), "joined": "date_joined"}


def rows(response):
    assert "attachment" in response["Content-Disposition"]
    if response["Content-Type"] == tables.XLSX_TYPE:
        assert response["Content-Disposition"].endswith('.xlsx"')
        return sheet(response.content)
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


def sheet(content):
    """The workbook's cells, text as text and numbers as numbers."""
    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with ZipFile(io.BytesIO(content)) as archive:
        # The workbook is our own output, not untrusted XML.
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))  # noqa: S314
    return [
        [
            "".join(cell.itertext())
            if cell.get("t") == "inlineStr"
            else int(cell.find(f"{main}v").text)
            for cell in row
        ]
        for row in root.iter(f"{main}row")
    ]


def test_sorting_reads_the_direction_and_falls_back_to_the_default(rf):
    sort, order = tables.sorting(rf.get("/", {"sort": "-name"}), COLUMNS, "joined")
    assert sort == "-name"
    assert [term.descending for term in order] == [True, True]
    assert tables.sorting(rf.get("/", {"sort": "pk"}), COLUMNS, "joined")[0] == "joined"


def test_paginate_offers_the_default_size_and_ignores_an_unknown_one(rf):
    chosen = tables.paginate(rf.get("/", {"per_page": "50"}), range(120), 20)
    unknown = tables.paginate(rf.get("/", {"per_page": "7"}), range(120), 20)
    assert chosen.paginator.per_page == tables.PAGE_SIZES[2]
    assert chosen.per_page_options == [10, 20, 25, 50, 100]
    assert unknown.paginator.per_page == unknown.per_page_options[1]


def test_pagination_offers_page_sizes_even_on_a_single_page(rf):
    request = rf.get("/portal/staff/", {"q": "a", "per_page": "25"})
    request.user = AnonymousUser()
    html = render_to_string(
        "components/pagination.html",
        {"page": tables.paginate(request, range(20)), "label": "Staff pagination"},
        request=request,
    )
    assert 'name="per_page"' in html
    assert 'value="25" selected' in " ".join(html.split())
    assert 'hx-get="/portal/staff/?q=a"' in html
    assert "ui-pagination-group" not in html


def test_a_workbook_keeps_text_as_text_past_column_z():
    header = [f"Column {index}" for index in range(28)]
    content = workbook(header, [["=SUM(A1)", "a < b & c\x07", 42, *[""] * 25]])
    assert sheet(content) == [header, ["=SUM(A1)", "a < b & c", 42, *[""] * 25]]
    with ZipFile(io.BytesIO(content)) as archive:
        assert (
            '<autoFilter ref="A1:AB2"/>'
            in archive.read(
                "xl/worksheets/sheet1.xml",
            ).decode()
        )


def test_a_cell_that_starts_like_a_formula_stays_text():
    assert tables.cell("=SUM(A1)") == "'=SUM(A1)"
    assert tables.cell(None) == ""


def test_sort_header_marks_the_column_and_toggles_its_direction(rf):
    request = rf.get("/", {"sort": "name", "page": "3", "q": "x"})
    html = Template(
        '{% load experience_ui %}{% sort_header "Name" "name" %}'
        '{% sort_header "Joined" "joined" descending_first=True %}',
    ).render(Context({"request": request, "table_sort": "name"}))
    assert 'aria-sort="ascending"' in html
    assert 'href="?sort=-name&amp;q=x"' in html
    assert 'href="?sort=-joined&amp;q=x"' in html


def test_staff_sort_and_csv_follow_the_query(client):
    admin = UserFactory(is_superuser=True, is_staff=True, name="Asha Admin")
    UserFactory(is_nha_team=True, name="Zoya Reviewer")
    client.force_login(admin)
    url = reverse("experiences:staff-list")
    listed = client.get(url, {"sort": "-member"}).context["page"]
    assert [member.name for member in listed] == ["Zoya Reviewer", "Asha Admin"]
    exported = rows(client.get(url, {"sort": "-member", "export": "csv"}))
    assert exported[0] == ["Name", "Email", "Portal access", "Status", "Last sign-in"]
    assert [row[0] for row in exported[1:]] == ["Zoya Reviewer", "Asha Admin"]
    assert rows(client.get(url, {"sort": "-member", "export": "xlsx"})) == exported
    assert exported[2][2] == "Superadmin, full portal access"


def test_the_queue_sorts_by_product_and_downloads_every_entry(environment, client):
    submit(environment, "m1")
    client.force_login(environment["reviewer"])
    url = reverse("experiences:queue")
    response = client.get(url, {"scope": "all", "sort": "title"})
    assert response.context["table_sort"] == "title"
    assert 'aria-sort="ascending"' in response.content.decode()
    exported = rows(client.get(url, {"scope": "all", "sort": "title", "export": "csv"}))
    assert exported[0][:3] == ["Reference", "Product / request", "Organisation"]
    assert len(exported) - 1 == response.context["page"].paginator.count
    workbook_rows = rows(
        client.get(url, {"scope": "all", "sort": "title", "export": "xlsx"}),
    )
    # Age is a number in Excel, so it sorts and sums there too.
    assert [row[6] for row in workbook_rows[1:]] == [
        int(row[6]) for row in exported[1:]
    ]


def test_every_queue_column_sorts_and_keeps_every_entry(environment, client):
    submit(environment, "m1")
    client.force_login(environment["reviewer"])
    url = reverse("experiences:queue")

    def listed(sort):
        response = client.get(url, {"scope": "all", "sort": sort})
        assert response.context["table_sort"] == sort
        return [entry.reference for entry in response.context["page"]]

    entries = listed("-submitted")
    for sort in QUEUE_SORTS:
        assert sorted(listed(sort)) == sorted(entries)
    # The youngest entry is the newest one.
    assert listed("age") == entries
    header = client.get(url, {"scope": "all"}).content.decode()
    header = header[header.index("<thead") : header.index("</thead>")]
    assert (
        header.count("<th ")
        == header.count('class="ui-sort"')
        == len(
            {sort.removeprefix("-") for sort in QUEUE_SORTS},
        )
    )


SORT_LINK = re.compile(r'class="ui-sort" href="([^"]*)"')


def test_every_table_sorts_by_each_heading_and_exports_its_rows(environment, client):
    item = submit(environment)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    services.decide(
        item,
        environment["reviewer"],
        action="query",
        note="Which cases cover consent expiry?",
        field_key="functional_report",
    )
    Event.objects.create(
        title="Gateway clinic",
        slug="gateway-clinic",
        starts_at=timezone.now() - timedelta(days=3),
        published_at=timezone.now(),
        created_by=environment["admin"],
    )
    organisation = reverse(
        "experiences:organization-detail",
        args=[environment["org"].slug],
    )
    tables_on_pages = [
        (reverse("experiences:organizations"), {}, "organizations"),
        (reverse("experiences:products"), {}, "products"),
        (reverse("experiences:events"), {"period": "past"}, "events"),
        (reverse("experiences:pending-queries"), {}, "items"),
        (organisation, {}, "review_requests"),
        (organisation, {"table": "products"}, "products"),
    ]
    client.force_login(environment["reviewer"])
    for url, params, key in tables_on_pages:
        # `table` picks what to export; the page itself takes no such query.
        shown = {name: value for name, value in params.items() if name != "table"}
        response = client.get(url, shown)
        listed = list(response.context[key])
        assert listed, url
        html = response.content.decode()
        # Every column of every table on the page sorts.
        assert html.count('class="ui-sort"') == html.count("<th "), url
        for href in SORT_LINK.findall(html):
            query = QueryDict(unescape(href).removeprefix("?")).dict()
            param = "product_sort" if "product_sort" in query else "sort"
            current = "product_sort" if param == "product_sort" else "table_sort"
            value = query[param]
            for sort in (value, value[1:] if value.startswith("-") else f"-{value}"):
                context = client.get(url, {**query, param: sort}).context
                assert context[current] == sort, (url, sort)
        for export in tables.EXPORT_FORMATS:
            exported = rows(client.get(url, {**params, "export": export}))
            assert len(exported) - 1 == len(listed), (url, params)


def test_events_sort_by_title_and_say_who_registered(environment, client):
    titles = ["Bridge walkthrough", "Consent clinic", "ABHA office hours"]
    for days, title in enumerate(titles, start=1):
        event = Event.objects.create(
            title=title,
            slug=f"event-{days}",
            starts_at=timezone.now() - timedelta(days=days),
            published_at=timezone.now(),
            created_by=environment["admin"],
        )
    EventRegistration.objects.create(event=event, user=environment["applicant"])
    url = reverse("experiences:events")
    client.force_login(environment["applicant"])

    def listed(sort):
        page = client.get(url, {"period": "past", "sort": sort}).context["events"]
        return [event.title for event in page]

    assert listed("") == titles  # Past events: the latest first.
    assert listed("event") == sorted(titles)
    assert listed("-event") == sorted(titles, reverse=True)
    # Registration is the viewer's own: the session they joined comes first.
    assert listed("-registration")[0] == "ABHA office hours"
    exported = rows(
        client.get(url, {"period": "past", "sort": "event", "export": "csv"}),
    )
    assert exported[0][-1] == "Registered"
    assert [(row[0], row[-1]) for row in exported[1:]] == [
        ("ABHA office hours", "Yes"),
        ("Bridge walkthrough", "No"),
        ("Consent clinic", "No"),
    ]
    client.force_login(environment["admin"])
    exported = rows(client.get(url, {"period": "past", "export": "csv"}))
    assert exported[0][-1] == "Registrations"
    assert [row[-1] for row in exported[1:]] == ["0", "0", "1"]


def test_the_team_roster_sorts_and_exports_its_members(environment, client):
    organisation = environment["org"]
    MembershipFactory(
        organisation=organisation,
        user=UserFactory(name="Zara Developer"),
        role=Role.DEVELOPER,
    )
    MembershipFactory(
        organisation=organisation,
        user=UserFactory(name="Aditi Support"),
        role=Role.SUPPORT,
    )
    client.force_login(environment["applicant"])
    url = reverse("organisations:team")

    def listed(sort):
        response = client.get(url, {"sort": sort})
        return [member.user.name for member in response.context["memberships"]]

    owner = environment["applicant"].name
    assert listed("") == [owner, "Zara Developer", "Aditi Support"]  # By role.
    assert listed("member") == sorted([owner, "Zara Developer", "Aditi Support"])
    exported = rows(client.get(url, {"sort": "member", "export": "xlsx"}))
    assert exported[0] == ["Name", "Email", "Role", "Joined on"]
    assert [row[0] for row in exported[1:]] == listed("member")


def test_an_integrators_own_products_sort_and_export(environment, client):
    client.force_login(environment["applicant"])
    url = reverse("experiences:products")
    response = client.get(url)
    html = response.content.decode()
    assert (
        html.count('class="ui-sort"')
        == html.count("<th ")
        == len(
            ("Product", "Solution type", "Registered on"),
        )
    )
    assert response.context["table_sort"] == "product"
    for sort in ("solution", "-registered"):
        assert client.get(url, {"sort": sort}).context["table_sort"] == sort
    exported = rows(client.get(url, {"export": "csv"}))
    assert exported[0] == list(views.PRODUCT_EXPORT_HEADER)
    assert [row[0] for row in exported[1:]] == [environment["product"].reference]
