# ruff: noqa: INP001
"""Smoke-test the baked assets without a database or a running web server."""

import gzip
import re
import sys
from html.parser import HTMLParser
from http import HTTPStatus
from urllib.parse import urljoin
from urllib.parse import urlsplit

import django
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory
from whitenoise.middleware import WhiteNoiseMiddleware


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "link":
            url = attributes.get("href", "")
        elif tag in {"script", "img"}:
            url = attributes.get("src", "")
        else:
            return
        if url.startswith(settings.STATIC_URL):
            self.urls.add(url)


def verify_assets():
    assert not settings.DEBUG, "Verify assets with production static settings."
    middleware = WhiteNoiseMiddleware(lambda request: HttpResponse(status=404))
    assert not middleware.use_finders, "Assets must be served from STATIC_ROOT."
    assert not middleware.autorefresh, "Production must not use development refresh."
    parser = AssetParser()
    for template in ("base.html", "layouts/marketing.html"):
        parser.feed(render_to_string(template))

    css_url = staticfiles_storage.url(settings.TAILWIND_CSS_PATH)
    assert css_url in parser.urls, "The page is missing its Tailwind stylesheet."
    assert css_url != urljoin(settings.STATIC_URL, settings.TAILWIND_CSS_PATH), (
        "Tailwind must use a content-hashed manifest URL."
    )
    if settings.COMPRESS_ENABLED:
        assert any("/CACHE/css/" in url for url in parser.urls)
        assert any("/CACHE/js/" in url for url in parser.urls)

    factory = RequestFactory()
    pending = set(parser.urls)
    checked = set()
    fonts = set()
    while pending:
        url = pending.pop()
        checked.add(url)
        response = middleware(factory.get(urlsplit(url).path))
        try:
            assert response.status_code == HTTPStatus.OK, f"Missing asset: {url}"
            content = b"".join(response.streaming_content)
            assert content, f"Empty asset: {url}"
            if urlsplit(url).path.endswith(".woff2"):
                fonts.add(url)
            if "text/css" not in response["Content-Type"]:
                continue
            css = content.decode()
            if url == css_url:
                assert "immutable" in response["Cache-Control"]
                for selector in (
                    ".ui-btn",
                    ".ui-nav-link",
                    ".ui-checkbox",
                    ".bg-soft-background",
                    ".text-foreground",
                ):
                    assert selector in css, f"Tailwind is missing {selector}"
            for match in re.finditer(r"url\(([^)]+)\)", css):
                dependency = urljoin(url, match[1].strip("\"' "))
                if dependency.startswith(settings.STATIC_URL):
                    pending.add(dependency)
            pending.difference_update(checked)
        finally:
            response.close()

    assert fonts, "The generated stylesheet must include the bundled fonts."
    response = middleware(factory.get(css_url, HTTP_ACCEPT_ENCODING="gzip"))
    try:
        assert response.status_code == HTTPStatus.OK
        assert response["Content-Encoding"] == "gzip"
        assert b".ui-btn" in gzip.decompress(b"".join(response.streaming_content))
    finally:
        response.close()
    sys.stdout.write(
        f"Verified {len(checked)} static assets, including Tailwind, "
        f"{len(fonts)} fonts, template links, and gzip delivery.\n",
    )


if __name__ == "__main__":
    django.setup()
    verify_assets()
