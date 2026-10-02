"""The landing page's ABDM figures: the KPI service, the cache and the daily task."""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry
from celery.schedules import crontab
from django.core.cache import cache

from ohc_experience.pages import abdm_dashboard
from ohc_experience.pages.abdm_dashboard import CACHE_KEY
from ohc_experience.pages.abdm_dashboard import DashboardUnavailableError
from ohc_experience.pages.abdm_dashboard import cached_figures
from ohc_experience.pages.abdm_dashboard import current_figures
from ohc_experience.pages.abdm_dashboard import fetch_figures
from ohc_experience.pages.abdm_dashboard import refresh_figures
from ohc_experience.pages.tasks import RETRY_DELAYS
from ohc_experience.pages.tasks import refresh_abdm_dashboard_figures

# The service's reply on 2 October 2026.
REPLY = {
    "lastUpdated": "02-10-2026 08:33 PM",
    "HPR Count": "12,20,798",
    "HRL Count": "1,22,47,14,978",
    "ABHA Count": "98,05,82,641",
    "HFR Count": "5,85,761",
}
# What the service sends for an unknown client, still with HTTP 200.
REFUSAL = {
    "txnno": "",
    "ts": "2026-10-03T00:09:56.657+05:56",
    "status": "Please provide valid Client ID and Client Secret",
}
FIGURES = {
    "records_linked": 1224714978,
    "professionals_registered": 1220798,
    "facilities_registered": 585761,
}


@pytest.fixture(autouse=True)
def configured(settings):
    settings.PMJAY_CLIENT_ID = "test-client"
    settings.PMJAY_CLIENT_SECRET = "test-secret"  # noqa: S105
    settings.ABDM_DASHBOARD_KPI_URL = (
        "https://dashboard.example.test/abdmservice/api/dashboard/ABDM/KPI"
    )
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def service(monkeypatch):
    connection = Mock()
    reply_with(connection, REPLY)
    factory = Mock(return_value=connection)
    monkeypatch.setattr(abdm_dashboard.http.client, "HTTPSConnection", factory)
    return factory, connection


def reply_with(connection, body, status=HTTPStatus.OK):
    response = connection.getresponse.return_value
    response.status = status
    response.read.return_value = (
        body if isinstance(body, bytes) else json.dumps(body).encode()
    )


class TestFetchFigures:
    def test_reads_the_three_counts_and_sends_the_credentials(self, service):
        factory, connection = service

        assert fetch_figures() == FIGURES

        factory.assert_called_once_with(
            "dashboard.example.test",
            port=None,
            timeout=15,
        )
        assert connection.request.call_args.args == (
            "POST",
            "/abdmservice/api/dashboard/ABDM/KPI",
        )
        assert json.loads(connection.request.call_args.kwargs["body"]) == {
            "clientId": "test-client",
            "clientSecret": "test-secret",
            "grantType": "client_credentials",
        }
        connection.close.assert_called_once()

    def test_a_refusal_sent_with_200_gives_the_service_reason(self, service):
        _, connection = service
        reply_with(connection, REFUSAL)

        with pytest.raises(DashboardUnavailableError, match="valid Client ID"):
            fetch_figures()

    @pytest.mark.parametrize("setting", ["PMJAY_CLIENT_ID", "PMJAY_CLIENT_SECRET"])
    def test_missing_credentials_never_reach_the_service(
        self,
        service,
        settings,
        setting,
    ):
        factory, _ = service
        setattr(settings, setting, "  ")

        with pytest.raises(DashboardUnavailableError, match="not set"):
            fetch_figures()
        factory.assert_not_called()

    def test_the_secret_is_never_sent_over_plain_http(self, service, settings):
        factory, _ = service
        settings.ABDM_DASHBOARD_KPI_URL = "http://dashboard.example.test/KPI"

        with pytest.raises(DashboardUnavailableError, match="https"):
            fetch_figures()
        factory.assert_not_called()

    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (HTTPStatus.BAD_GATEWAY, REPLY),
            (HTTPStatus.OK, b"<html>Under maintenance</html>"),
            (HTTPStatus.OK, [REPLY]),
            (HTTPStatus.OK, {**REPLY, "HFR Count": "N/A"}),
            (HTTPStatus.OK, {**REPLY, "HRL Count": "-5"}),
            (HTTPStatus.OK, {k: v for k, v in REPLY.items() if k != "HPR Count"}),
        ],
    )
    def test_an_unusable_reply_is_an_error(self, service, status, body):
        _, connection = service
        reply_with(connection, body, status)

        with pytest.raises(DashboardUnavailableError):
            fetch_figures()

    def test_a_network_failure_is_an_error(self, service):
        _, connection = service
        connection.getresponse.side_effect = TimeoutError

        with pytest.raises(DashboardUnavailableError, match="TimeoutError"):
            fetch_figures()
        connection.close.assert_called_once()


class TestCache:
    def test_a_refresh_keeps_the_figures_for_the_page(self, service):
        refresh_figures()

        assert cached_figures() == FIGURES

    def test_a_failed_refresh_leaves_the_last_good_figures(self, service):
        refresh_figures()
        _, connection = service
        reply_with(connection, REFUSAL)

        with pytest.raises(DashboardUnavailableError):
            refresh_figures()

        assert cached_figures() == FIGURES

    def test_an_unexpected_cached_value_counts_as_nothing_cached(self):
        cache.set(CACHE_KEY, {"records_linked": 1})

        assert cached_figures() is None


class TestCurrentFigures:
    def test_the_page_reads_the_cache_without_calling_the_service(self, service):
        factory, _ = service
        cache.set(CACHE_KEY, FIGURES)

        assert current_figures() == FIGURES
        factory.assert_not_called()

    def test_before_the_first_fetch_one_visit_fetches_them(self, service):
        factory, _ = service

        assert current_figures() == FIGURES
        assert current_figures() == FIGURES
        factory.assert_called_once_with(
            "dashboard.example.test",
            port=None,
            timeout=5,
        )

    def test_a_failed_first_fetch_is_not_retried_by_every_visit(
        self,
        service,
        caplog,
    ):
        factory, connection = service
        reply_with(connection, REFUSAL)

        with caplog.at_level(logging.WARNING, logger=abdm_dashboard.__name__):
            assert current_figures() is None
            assert current_figures() is None

        factory.assert_called_once()
        assert "valid Client ID" in caplog.text


class TestDailyTask:
    def test_stores_the_figures(self, service):
        refresh_abdm_dashboard_figures.delay()

        assert cached_figures() == FIGURES

    def test_a_failure_is_tried_again_later(self, service):
        _, connection = service
        reply_with(connection, REFUSAL)

        with pytest.raises(Retry):
            refresh_abdm_dashboard_figures.delay()

    def test_after_the_last_retry_it_logs_and_keeps_the_last_good_figures(
        self,
        service,
        caplog,
    ):
        cache.set(CACHE_KEY, FIGURES)
        _, connection = service
        reply_with(connection, REFUSAL)

        with caplog.at_level(logging.ERROR, logger="ohc_experience.pages.tasks"):
            refresh_abdm_dashboard_figures.apply(retries=len(RETRY_DELAYS))

        assert "valid Client ID" in caplog.text
        assert cached_figures() == FIGURES

    def test_beat_runs_it_every_morning(self, settings):
        entry = settings.CELERY_BEAT_SCHEDULE["landing-abdm-figures"]

        assert entry["task"] == refresh_abdm_dashboard_figures.name
        assert entry["schedule"] == crontab(hour=6, minute=0)
