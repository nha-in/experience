from django.conf import settings
from django.shortcuts import redirect

from .views import FrontView


class FrontHostMiddleware:
    """Serve NHA's front page on NHA's hostname, and only that.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.FRONT_HOST:
            return self.get_response(request)
        if request.get_host().partition(":")[0] != settings.FRONT_HOST:
            return self.get_response(request)
        if request.path == "/":
            # Short-circuiting the chain skips the render a view's response gets.
            return FrontView.as_view()(request).render()
        return redirect(
            f"{settings.SITE_BASE_URL.rstrip('/')}{request.get_full_path()}",
        )
