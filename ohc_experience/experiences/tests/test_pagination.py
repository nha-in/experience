import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.paginator import Paginator
from django.template.loader import render_to_string

from ohc_experience.experiences.templatetags.experience_ui import page_numbers


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (1, [(1, True), (2, True), (3, True), (4, False), (5, False)]),
        (6, [(4, False), (5, True), (6, True), (7, True), (8, False)]),
        (12, [(8, False), (9, False), (10, True), (11, True), (12, True)]),
    ],
)
def test_page_numbers_slide_around_the_current_page(number, expected):
    assert page_numbers(Paginator(range(240), 20).page(number)) == expected


def test_page_numbers_list_every_page_when_there_are_few():
    assert page_numbers(Paginator(range(50), 20).page(2)) == [
        (1, True),
        (2, True),
        (3, True),
    ]


@pytest.mark.django_db
def test_pagination_keeps_filters_and_marks_unavailable_steps(rf):
    request = rf.get("/portal/support/", {"q": "callback"})
    request.user = AnonymousUser()
    html = render_to_string(
        "components/pagination.html",
        {"page": Paginator(range(100), 20).page(1), "label": "Tickets pagination"},
        request=request,
    )
    first_and_previous = 2
    assert html.count('aria-disabled="true"') == first_and_previous
    assert 'aria-current="page"' in html
    assert 'href="?q=callback&amp;page=5"' in html


def test_pagination_renders_nothing_on_a_single_page():
    html = render_to_string(
        "components/pagination.html",
        {"page": Paginator(range(5), 20).page(1)},
    )
    assert not html.strip()
