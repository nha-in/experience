from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.http import require_safe


@transaction.non_atomic_requests
@require_safe
def ping(request):
    return HttpResponse("OK", content_type="text/plain")


class PingMiddleware:
    """Answer ALB probes before host checks, HTTPS redirects, or sessions."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info == "/ping/":
            return ping(request)
        return self.get_response(request)
