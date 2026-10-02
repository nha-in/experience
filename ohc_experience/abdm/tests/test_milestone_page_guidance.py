"""What a milestone page says around the form: its documentation, which saved
version is on screen, how long its history runs, and where to go once it is
approved."""

import re

import pytest
from django.urls import reverse

from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AuditEvent

pytestmark = pytest.mark.django_db


def page(client, environment, key="m1"):  # noqa: F811
    url = reverse(
        "experiences:track",
        args=[environment["product"].reference, "ABDM"],
    )
    return client.get(url, {"milestone": key}).content.decode()


def text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_the_track_offers_its_documentation_as_a_button(environment, client):  # noqa: F811
    client.force_login(environment["applicant"])

    html = page(client, environment)

    docs = str(TRACK_MAP["ABDM"].docs_url)
    assert re.search(
        rf'<a class="ui-btn ui-btn--outline [^"]*"\s+href="{re.escape(docs)}"',
        html,
    )


def test_saved_evidence_names_its_version_and_says_when_nothing_is_uploaded(
    environment,  # noqa: F811
    client,
):
    item = milestone(environment)
    workflows.save_review_form(item, environment["applicant"], data=evidence_data())
    client.force_login(environment["applicant"])

    html = page(client, environment)

    saved = text(re.search(r"Saved evidence.*?</div>", html, re.S).group(0))
    assert re.search(r"Version 1 · \d{2}/\d{2}/\d{4}, \d{2}:\d{2} IST", saved)
    assert "No evidence uploaded yet." in saved
    assert "Revision" not in saved
    assert "Submission 1" not in saved

    item.refresh_from_db()
    workflows.save_review_form(
        item,
        environment["applicant"],
        data=evidence_data(),
        files=files(),
    )
    html = page(client, environment)

    saved = text(re.search(r"Saved evidence.*?</div>", html, re.S).group(0))
    assert saved.startswith("Saved evidence Version 2 ·")
    assert "No evidence uploaded yet." not in saved
    # The file it now holds offers a labelled download.
    assert re.search(
        r'aria-label="Download [^"]+">\s*<svg.*?</svg>\s*Download\s*</a>',
        html,
        re.S,
    )


def test_a_saved_upload_keeps_a_small_button_instead_of_a_drop_area(
    environment,  # noqa: F811
    client,
):
    """A milestone form's uploads take room only while they are empty."""
    item = milestone(environment)
    workflows.save_review_form(
        item,
        environment["applicant"],
        data=evidence_data(),
        files=files(),
    )
    client.force_login(environment["applicant"])

    html = page(client, environment)

    def picker(field):
        found = re.search(
            rf'<label class="([^"]*)"\s+for="id_{field}"\s+data-file-dropzone>'
            r"(.*?)</label>",
            html,
            re.S,
        )
        return found.group(1), text(found.group(2))

    assert picker("functional_certificate") == (
        "ui-btn ui-btn--outline ui-btn--size-xs mt-2 w-fit cursor-pointer",
        "Replace file",
    )
    assert picker("functional_report")[1] == "Add files"
    classes, words = picker("supporting_evidence")
    assert "min-h-16" in classes
    assert words == "Add files Choose several at once or drop them here · .pdf"


def test_the_demo_date_explains_what_it_is_for(environment, client):  # noqa: F811
    client.force_login(environment["applicant"])

    html = page(client, environment)

    # Behind the (i) beside the label, not printed under the field.
    panel = re.search(
        r'<div class="ui-info-panel"\s+id="info-field-id_tentative_demo_date".*?</div>',
        html,
        re.S,
    )
    assert "When you expect to demonstrate this milestone to NHA." in panel.group(0)
    assert 'aria-label="About Tentative demo date"' in html
    assert "id_tentative_demo_date_helptext" not in html


def test_a_long_history_shows_its_newest_events_first_and_the_rest_on_request(
    environment,  # noqa: F811
    client,
):
    item = milestone(environment)
    for number in range(1, 8):
        AuditEvent.objects.create(
            organisation=item.organisation,
            product=item.product,
            item=item,
            action=f"Checked item {number}",
        )
    client.force_login(environment["applicant"])

    html = page(client, environment)

    history = html[html.index('id="history-title"') :]
    shown, _, earlier = history.partition("<details")
    assert f"Show {item.history.count() - 5} earlier events" in earlier
    assert [f"Checked item {number}" in shown for number in (7, 6, 5, 4, 3)] == [
        True,
    ] * 5
    assert "Checked item 2" in earlier
    assert "Checked item 1" in earlier
    assert "Checked item 2" not in shown


def test_an_approved_milestone_recommends_what_to_do_next(environment, client):  # noqa: F811
    client.force_login(environment["applicant"])
    assert "Recommended next step" not in page(client, environment)

    approve(environment)
    html = page(client, environment)

    banner = re.search(
        r'<section class="flex flex-col gap-4 rounded-xl border.*?</section>',
        html,
        re.S,
    ).group(0)
    assert text(banner).startswith("Recommended next step Continue with M2")
    track = reverse(
        "experiences:track",
        args=[environment["product"].reference, "ABDM"],
    )
    assert f'href="{track}?milestone=m2"' in banner


def test_the_dashboard_says_what_each_chart_counts(environment, client):  # noqa: F811
    client.force_login(environment["reviewer"])

    html = client.get(reverse("experiences:assess-dashboard")).content.decode()

    for title in (
        "Ready for review, by track",
        "Approved this month, by milestone",
        "Approvals and rejections per week",
        "Ready for review, by type",
        "How long ready requests have waited",
        "Ready for review, by assignee",
    ):
        assert title in html
