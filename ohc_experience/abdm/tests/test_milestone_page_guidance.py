"""What a milestone page says around the form: its documentation, which saved
version is on screen, how long its history runs, and where to go once it is
approved."""

import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AuditEvent

pytestmark = pytest.mark.django_db


def page(client, environment, key="m1"):  # noqa: F811
    url = reverse(
        "experiences:track",
        args=[environment["product"].reference, "HIE-CM"],
    )
    return client.get(url, {"milestone": key}).content.decode()


def text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_the_track_offers_its_documentation_as_a_button(environment, client):  # noqa: F811
    client.force_login(environment["applicant"])

    html = page(client, environment)

    docs = str(TRACK_MAP["HIE-CM"].docs_url)
    assert re.search(
        rf'<a class="ui-btn ui-btn--outline [^"]*"\s+href="{re.escape(docs)}"',
        html,
    )


def test_every_documentation_link_says_it_opens_a_new_tab(environment, client):  # noqa: F811
    """The same mark ends each one: an external-link icon sized to the text,
    the words for screen readers, and a tooltip on hover or keyboard focus."""
    mark = " ".join(render_to_string("components/new_tab_mark.html").split())
    product = environment["product"]
    client.force_login(environment["applicant"])

    def compact(url):
        return " ".join(client.get(url).content.decode().split())

    track = compact(reverse("experiences:track", args=[product.reference, "HIE-CM"]))
    assert f"Milestone documentation {mark}" in track
    # The (i) beside each milestone links to its documentation too.
    assert f"Open documentation {mark}" in track
    unapplied = compact(reverse("experiences:track", args=[product.reference, "NHCX"]))
    assert f"Track documentation {mark}" in unapplied
    for url in (
        product.get_absolute_url(),
        reverse("experiences:reference-environment", args=[product.reference]),
    ):
        assert f"Milestone documentation {mark}" in compact(url)
    for html in (track, unapplied):
        assert "documentation ↗" not in html


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
    assert words == "Add files Choose several at once or drop them here"


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
        args=[environment["product"].reference, "HIE-CM"],
    )
    assert f'href="{track}?milestone=m2"' in banner


def test_a_track_with_every_milestone_approved_recommends_nothing_else(
    environment,  # noqa: F811
    client,
):
    """The next step stays on the track on screen; the overview has the rest.

    UHI is done once M1, M2 and its own milestone are, though HIE-CM's M3 is not.
    """
    approve(environment)
    approve(environment, "m2")
    submit(environment, "uhi1")
    client.force_login(environment["applicant"])
    uhi = reverse(
        "experiences:track",
        args=[environment["product"].reference, "UHI"],
    )

    html = client.get(uhi, {"milestone": "uhi1"}).content.decode()

    assert "All required milestones for UHI onboarding are approved." in html
    assert "Recommended next step" not in html
    assert "M3" not in text(html[html.index('id="main-content"') :])
    overview = client.get(environment["product"].get_absolute_url()).content.decode()
    assert "Continue with M3" in overview


def test_reusing_the_approved_certificate_says_what_it_does(environment, client):  # noqa: F811
    approve(environment)
    client.force_login(environment["applicant"])

    html = page(client, environment, "m2")

    # Behind the (i) beside the checkbox's label, not printed under it.
    assert re.search(
        r'<div class="min-w-0">\s*<label [^>]*for="id_use_product_wasa">'
        r"Reuse Previously Approved Certificate</label>\s*"
        r'<button class="ui-info-button[^"]*"\s+type="button"\s+'
        r'popovertarget="info-field-id_use_product_wasa"\s+'
        r'aria-label="About Reuse Previously Approved Certificate"',
        html,
    )
    panel = re.search(
        r'<div class="ui-info-panel"\s+id="info-field-id_use_product_wasa".*?</div>',
        html,
        re.S,
    )
    assert text(panel.group(0)) == (
        "Uses the WASA certificate already approved for this product instead of "
        "uploading it again. An approved certificate stays available to every "
        "milestone of the product until it expires. Untick to upload a new "
        "certificate, which NHA reviews with this milestone."
    )
    assert "id_use_product_wasa_helptext" not in html


def test_functional_testing_comes_before_the_wasa_audit(environment, client):  # noqa: F811
    """On the form, and in the reviewer's copy of what was sent."""
    client.force_login(environment["applicant"])

    html = page(client, environment)

    assert re.findall(
        r'<legend class="ui-field-legend[^"]*">([^<]+)</legend>',
        html,
    ) == [
        "Milestone dates",
        "Functional testing",
        "WASA audit",
        "Undertaking",
    ]
    item = submit(environment)
    assert [field["key"] for field in item.selected_submission.field_schema] == [
        "start_date",
        "end_date",
        "tentative_demo_date",
        "functional_certificate",
        "functional_report",
        "use_product_wasa",
        "wasa_certificate",
        "wasa_agency",
        "wasa_date",
        "wasa_valid_until",
        "undertaking_form",
        "supporting_evidence",
    ]
    client.force_login(environment["reviewer"])
    review = client.get(item.get_absolute_url()).content.decode()
    labels = re.findall(
        r'<dt class="ui-kicker pt-1 tracking-\[0.04em\]">([^<]+)</dt>',
        review,
    )
    assert labels.index("Functional testing certificate") < labels.index(
        "WASA certificate",
    )


def test_the_dashboard_says_what_each_chart_counts(environment, client):  # noqa: F811
    client.force_login(environment["reviewer"])

    html = client.get(reverse("experiences:assess-dashboard")).content.decode()

    for title in (
        "Sandbox access",
        "Organisation verification",
        "Decisions per month",
        "so far:",
    ):
        assert title in html


def test_the_dashboard_is_named_for_who_reads_it(environment, client):  # noqa: F811
    """An administrator's dashboard is not a reviewer's."""
    url = reverse("experiences:assess-dashboard")

    def names(user):
        client.force_login(user)
        html = client.get(url).content.decode()
        title = text(re.search(r"<title>(.*?)</title>", html, re.S).group(1))
        crumb = re.search(
            r'<span class="truncate font-medium text-foreground">\s*([^<]+?)\s*<',
            html,
        ).group(1)
        return title.split(" | ")[0], crumb, "NHA assessment" in html

    assert names(environment["admin"]) == (
        "Administrator dashboard",
        "Administrator dashboard",
        False,
    )
    assert names(environment["reviewer"]) == (
        "Reviewer dashboard",
        "Reviewer dashboard",
        False,
    )


def test_no_staff_page_says_nha_assessment_above_its_title(environment, client):  # noqa: F811
    client.force_login(environment["admin"])

    for name in (
        "assess-dashboard",
        "queue",
        "organizations",
        "products",
        "production-list",
        "pending-queries",
    ):
        html = client.get(reverse(f"experiences:{name}")).content.decode()
        assert "NHA assessment" not in html, name
        assert 'class="ui-hero-eyebrow"' not in html, name

    # An integrator's pending queries still say what they are for.
    client.force_login(environment["applicant"])
    html = client.get(reverse("experiences:pending-queries")).content.decode()
    assert '<p class="ui-hero-eyebrow">Action needed</p>' in html
