from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any
from typing import TypedDict

from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse

COUNTER_SESSION_KEY = "htmx_counter"


class SampleExperience(TypedDict):
    name: str
    tag: str


SAMPLE_EXPERIENCES: list[SampleExperience] = [
    {"name": "Hospital intake", "tag": "clinical"},
    {"name": "Discharge summary", "tag": "clinical"},
    {"name": "Telehealth consult", "tag": "virtual"},
    {"name": "Pharmacy refill", "tag": "pharmacy"},
    {"name": "Lab results review", "tag": "diagnostics"},
    {"name": "Care team huddle", "tag": "operations"},
    {"name": "Patient onboarding", "tag": "operations"},
]


def _filter_experiences(query: str) -> list[SampleExperience]:
    needle = query.strip().lower()
    if not needle:
        return SAMPLE_EXPERIENCES
    return [
        experience
        for experience in SAMPLE_EXPERIENCES
        if needle in experience["name"].lower() or needle in experience["tag"].lower()
    ]


class HomeView(TemplateView):
    template_name = "pages/htmx_demo.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["counter"] = int(self.request.session.get(COUNTER_SESSION_KEY, 0))
        context["experiences"] = SAMPLE_EXPERIENCES
        return context


@require_GET
def server_time(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pages/partials/server_time.html",
        {"now": datetime.now(tz=UTC)},
    )


@require_POST
def counter(request: HttpRequest) -> HttpResponse:
    action = request.POST.get("action", "inc")
    value = int(request.session.get(COUNTER_SESSION_KEY, 0))
    if action == "inc":
        value += 1
    elif action == "dec":
        value -= 1
    elif action == "reset":
        value = 0
    request.session[COUNTER_SESSION_KEY] = value
    return render(request, "pages/partials/counter.html", {"counter": value})


@require_GET
def search(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "")
    return render(
        request,
        "pages/partials/search_results.html",
        {
            "experiences": _filter_experiences(query),
            "query": query,
        },
    )


@require_POST
def greet(request: HttpRequest) -> HttpResponse:
    name = request.POST.get("name", "").strip() or "friend"
    return render(request, "pages/partials/greeting.html", {"name": name})
