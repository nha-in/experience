import re
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import OTHER_TYPE
from ohc_experience.events_and_activities.models import Event
from ohc_experience.experiences import workflows
from ohc_experience.experiences.definitions import SupportCategoryDefinition
from ohc_experience.experiences.forms import SupportForm
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import EventRegistration
from ohc_experience.experiences.models import Notification
from ohc_experience.experiences.tests.test_tables import rows
from ohc_experience.experiences.uploads import TICKET_ATTACHMENT_SIGNATURES
from ohc_experience.experiences.uploads import validate_ticket_attachment
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.support.models import Ticket
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal_products(owner_membership):
    result = []
    for name, milestones in (
        ("Alpha HMIS", ["HIE-CM:m1"]),
        ("Zeta Locker", ["PHR:p1", "PHR:p2", "PHR:p3", "PHR:p4"]),
    ):
        data = product_data(name) | OTHER_TYPE
        data["applied_milestones"] = milestones
        product, form = workflows.register_product(
            owner_membership.organisation,
            owner_membership.user,
            data=data,
        )
        assert product, form.errors
        product.refresh_from_db()
        result.append(product)
    return result


@pytest.fixture
def portal_client(client, owner_membership, portal_products):
    client.force_login(owner_membership.user)
    return client


@pytest.fixture
def support_url(portal_products):
    """Support for the product the tests here work on."""
    return reverse("experiences:product-support", args=[portal_products[1].reference])


def test_ticket_defaults_and_applied_category_choices(portal_products):
    """A product on HIE-CM is offered that track's categories, and the catch-all."""
    form = SupportForm(product=portal_products[0])
    assert form["priority"].value() == "medium"
    assert [value for value, _label in form.fields["category"].choices] == [
        "",
        "abdm-m1",
        "abdm-m2",
        "abdm-m3",
        "abdm-m4",
        "abdm-review",
        "abdm-scan-share",
        "others",
    ]
    assert form.fields["category"].choices[-1][1] == "Others"
    # Nothing is chosen for the integrator, Others included.
    assert form.fields["category"].choices[0][1] == "Select a category"
    assert not form["category"].value()


@pytest.mark.parametrize("category", ["phr-app", "nhcx-auth", "HIE-CM", "unknown"])
def test_ticket_refuses_a_category_this_product_cannot_file_under(
    portal_products,
    category,
):
    """Another track's categories, a retired track code, and nonsense alike."""
    form = SupportForm(
        product=portal_products[0],
        data={
            "subject": "Help",
            "priority": "medium",
            "body": "Details",
            "category": category,
        },
    )
    assert not form.is_valid()
    assert "category" in form.errors


def test_a_category_takes_an_issue_type_from_its_own_sub_menu(portal_products):
    def submit(**extra):
        return SupportForm(
            product=portal_products[0],
            data={
                "subject": "Help",
                "priority": "medium",
                "environment": "sandbox",
                "body": "Details",
                **extra,
            },
        )

    assert not submit(category="abdm-m2").is_valid()
    # An issue type from another category is no better than none at all.
    assert "issue_type" in submit(category="abdm-m2", issue_type="ABHA Creation").errors
    form = submit(category="abdm-m2", issue_type="Data Transfer")
    assert form.is_valid(), form.errors
    assert form.cleaned_data["issue_type"] == "Data Transfer"


def test_a_category_with_no_sub_menu_records_no_issue_type():
    """A category that asks nothing further drops an issue type posted anyway.

    Every ABDM category has a sub-menu now, so this uses a program that keeps a
    bare one: the rule belongs to the form, not to one program's menu.
    """

    class BareMenu:
        @staticmethod
        def support_category_map():
            return {
                "bare": SupportCategoryDefinition("bare", "Bare"),
                "full": SupportCategoryDefinition(
                    "full",
                    "Full",
                    issue_types=("Data Transfer",),
                ),
            }

    form = SupportForm(
        program=BareMenu,
        data={
            "subject": "Help",
            "priority": "medium",
            "body": "Details",
            "category": "bare",
            "issue_type": "Data Transfer",
            "environment": "sandbox",
        },
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["issue_type"] == ""


def test_others_asks_which_kind_of_general_question_it_is(portal_products):
    """The catch-all carries a sub-menu of its own, so it is answered like one."""

    def submit(**extra):
        return SupportForm(
            product=portal_products[0],
            data={
                "subject": "Help",
                "priority": "medium",
                "body": "Details",
                "category": "others",
                "environment": "sandbox",
                **extra,
            },
        )

    assert not submit().is_valid()
    assert "issue_type" in submit(issue_type="Data Transfer").errors
    form = submit(issue_type="Access / General Inquiry / Concerns")
    assert form.is_valid(), form.errors
    assert form.cleaned_data["issue_type"] == "Access / General Inquiry / Concerns"


def field_label(response, name):
    """A field's label on the page, whitespace collapsed, its required asterisk
    reduced to a trailing *."""
    html = " ".join(response.content.decode().split())
    label = html.split(f'for="id_{name}">', 1)[1].split("</label>", 1)[0]
    return re.sub(r'<span class="ui-required"[^>]*>(\*)</span>', r"\1", label).strip()


def test_a_ticket_is_always_filed_under_a_category(portal_client, support_url):
    """Others has a code of its own, so no ticket is filed under a blank one. The
    field that picks it is not optional, and starts on no category at all, so a
    ticket is never left in one by default."""
    url = support_url
    data = {
        "subject": "Help",
        "issue_type": "Access / General Inquiry / Concerns",
        "priority": "medium",
        "environment": "sandbox",
        "body": "Details",
    }

    page = portal_client.get(url, {"new": "1"})
    assert field_label(page, "category") == "Category *"
    html = " ".join(page.content.decode().split())
    assert '<option value="" selected>Select a category</option>' in html
    assert " required " in re.search(r'<select name="category"[^>]*>', html).group()
    response = portal_client.post(url, {**data, "category": ""})
    assert response.context["form"].errors["category"] == [
        "Choose the category this ticket is about.",
    ]
    assert not Ticket.objects.exists()
    portal_client.post(url, {**data, "category": "others"})
    ticket = Ticket.objects.get()
    assert ticket.category == "others"
    assert ticket.category_label == "Others"


def test_new_ticket_asks_for_an_issue_type_without_calling_it_optional(
    portal_client,
    support_url,
):
    """Every ABDM category has a sub-menu, Others included, so every ticket
    names an issue type, and a missing one is reported once."""
    url = support_url

    page = portal_client.get(url, {"new": "1"})
    assert field_label(page, "issue_type") == "Issue type *"
    html = " ".join(page.content.decode().split())
    assert " required " in re.search(r'<select name="issue_type"[^>]*>', html).group()
    response = portal_client.post(
        url,
        {
            "subject": "Help",
            "category": "abdm-m2",
            "priority": "medium",
            "body": "Details",
        },
    )
    assert response.context["form"].errors["issue_type"] == [
        "Choose the issue type this ticket is about.",
    ]
    assert field_label(response, "issue_type") == "Issue type *"


def test_issue_type_is_required_wherever_it_shows():
    """The field shows only for a category with a sub-menu, so only there is it
    required, and a menu with no sub-menu at all never asks for one."""
    bare = SupportCategoryDefinition("bare", "Bare")
    full = SupportCategoryDefinition("full", "Full", issue_types=("Data Transfer",))

    def required(categories, posted=None):
        class Menu:
            @staticmethod
            def support_category_map():
                return {category.code: category for category in categories}

        data = (
            None
            if posted is None
            else {
                "subject": "Help",
                "priority": "medium",
                "body": "Details",
                "category": posted,
            }
        )
        return SupportForm(program=Menu, data=data)["issue_type"].field.required

    # Before a category is posted, any sub-menu the script could show counts.
    assert required([bare, full]) is True
    assert required([bare]) is False
    # Once one is, the category posted decides for itself.
    assert required([bare, full], posted="full") is True
    assert required([bare, full], posted="bare") is False


def test_issue_type_menu_is_flat_and_tags_each_option_with_its_category(
    portal_products,
):
    """The sub-menu lists issue types only: no category heading, but each option
    still carries the category it belongs to so the script can narrow it."""
    html = str(SupportForm(product=portal_products[0])["issue_type"])
    assert "<optgroup" not in html
    # Category names all read "ABDM - …"; no issue type does, so none leaked in.
    assert "ABDM - " not in html
    assert 'value="Data Transfer" data-category="abdm-m2"' in html


@pytest.mark.parametrize(
    "route",
    [
        "experiences:product-queries",
        "experiences:product-support",
        "experiences:product-events",
    ],
)
def test_programme_pages_keep_current_product(portal_client, portal_products, route):
    product = portal_products[1]
    link = reverse(route, args=[product.reference])
    assert f'href="{link}"' in portal_client.get(product.get_absolute_url()).text
    response = portal_client.get(link)
    assert response.status_code == HTTPStatus.OK
    assert response.context["product"] == product
    assert f'href="{link}"' in response.text


def test_support_ignores_another_organisations_product(
    portal_client,
    portal_products,
):
    membership = MembershipFactory()
    other, form = workflows.register_product(
        membership.organisation,
        membership.user,
        data=product_data("Private product"),
    )
    assert other, form.errors
    response = portal_client.get(
        reverse("experiences:support"),
        {"product": other.reference},
    )
    assert response.context["product"] is None
    assert b"Private product" not in response.content


def test_ticket_create_saves_category_and_scopes_product(
    portal_client,
    portal_products,
    support_url,
):
    response = portal_client.post(
        support_url,
        {
            "subject": "Callback rejects the request",
            "category": "phr-app-p2",
            "issue_type": "Consent Flow",
            "priority": "medium",
            "environment": "sandbox",
            "body": "The callback returns an unexpected status.",
        },
    )
    ticket = Ticket.objects.get()
    assert response.status_code == HTTPStatus.FOUND
    assert [str(message) for message in get_messages(response.wsgi_request)] == [
        f"Ticket {ticket.reference} created.",
    ]
    assert ticket.category == "phr-app-p2"
    assert ticket.issue_type == "Consent Flow"
    assert ticket.category_label == "PHR App - P2"
    assert ticket.product == portal_products[1]
    assert ticket.messages.get().body == "The callback returns an unexpected status."


def test_a_ticket_says_which_environment_it_is_about(portal_client, support_url):
    """Sandbox or Production and nothing else, as NHA asked, and never chosen for
    the integrator: a default would send a production problem to sandbox logs."""
    data = {
        "subject": "Callback rejects the request",
        "category": "phr-app-p2",
        "issue_type": "Consent Flow",
        "priority": "medium",
        "body": "The callback returns an unexpected status.",
    }
    page = portal_client.get(support_url, {"new": "1"})
    assert field_label(page, "environment") == "Environment *"
    assert page.context["form"].fields["environment"].choices == [
        ("", "Select an environment"),
        ("sandbox", "Sandbox"),
        ("production", "Production"),
    ]
    assert '<option value="" selected>Select an environment</option>' in compact(page)

    response = portal_client.post(support_url, data)
    assert response.context["form"].errors["environment"] == [
        "Choose the environment this ticket is about.",
    ]
    response = portal_client.post(support_url, {**data, "environment": "staging"})
    assert "environment" in response.context["form"].errors
    assert not Ticket.objects.exists()
    portal_client.post(support_url, {**data, "environment": "production"})
    ticket = Ticket.objects.get()
    assert ticket.environment == "production"
    assert (
        '<dt class="text-soft-foreground">Environment</dt> <dd> Production </dd>'
        in compact(portal_client.get(ticket.get_absolute_url()))
    )


def test_a_ticket_filed_before_environments_shows_none(
    portal_client,
    portal_products,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[1],
        subject="Callback rejects the request",
        created_by=owner_membership.user,
        category="phr-app-p2",
    )
    page = portal_client.get(ticket.get_absolute_url()).text
    assert ">Environment</dt>" not in page
    assert 'id="api-details-title"' not in page


def test_api_details_are_asked_only_for_integration_categories(
    portal_client,
    portal_products,
):
    """The legacy help desk asked an integration issue for the call that failed.
    A review demo and Others have no call, so the page shows the fields only once
    an integration category is chosen, and never requires them."""
    form = SupportForm(product=portal_products[0])
    assert form.api_detail_categories == [
        "abdm-m1",
        "abdm-m2",
        "abdm-m3",
        "abdm-m4",
        "abdm-scan-share",
    ]
    assert not any(
        form.fields[name].required
        for name in ("api_url", "api_request_id", "api_request", "api_response")
    )
    page = compact(
        portal_client.get(
            reverse("experiences:product-support", args=[portal_products[0].reference]),
            {"new": "1"},
        ),
    )
    assert (
        'data-show-when-field="category" data-show-when-value="abdm-m1 abdm-m2 '
        'abdm-m3 abdm-m4 abdm-scan-share"'
    ) in page
    # The thread's opening message stays first; the call follows it.
    assert page.index('name="body"') < page.index('name="api_url"')


def test_api_details_start_folded_and_reopen_with_what_was_typed(
    portal_client,
    support_url,
):
    """They are optional and long, so the section starts closed. A form that comes
    back with any of them typed opens it again rather than hiding them."""

    def opens(response):
        page = compact(response)
        tag = re.search(r'<details[^>]*data-show-when-field="category"[^>]*>', page)[0]
        return tag.endswith(" open>")

    assert not opens(portal_client.get(support_url, {"new": "1"}))
    # Without a subject, each form comes back to be fixed.
    untyped = {
        "category": "phr-app-p2",
        "issue_type": "Consent Flow",
        "priority": "medium",
        "environment": "sandbox",
        "body": "The consent request stays REQUESTED past its expiry.",
    }
    assert not opens(portal_client.post(support_url, untyped))
    typed = {**untyped, "api_request_id": "5f0c6a2e-7d1b-4a39-9c51-2b9e1d7f4a10"}
    assert opens(portal_client.post(support_url, typed))


def test_an_integration_ticket_records_the_call_that_failed(
    portal_client,
    support_url,
):
    """Shown ahead of the thread, each long field in a box that scrolls rather
    than stretching the page."""
    details = {
        "api_url": "POST /api/hiecm/consent/v3/request/init",
        "api_request_id": "5f0c6a2e-7d1b-4a39-9c51-2b9e1d7f4a10",
        "api_request": (
            "curl -X POST https://dev.abdm.gov.in/api/hiecm/consent/v3/request/init"
            " -H 'REQUEST-ID: 5f0c6a2e'"
        ),
        "api_response": '{"error": {"code": "ABDM-1017", "message": "Invalid"}}',
    }
    response = portal_client.post(
        support_url,
        {
            "subject": "Consent request not moving to EXPIRED",
            "category": "phr-app-p2",
            "issue_type": "Consent Flow",
            "priority": "medium",
            "environment": "sandbox",
            "body": "The consent request stays REQUESTED past its expiry.",
            **details,
        },
    )
    assert response.status_code == HTTPStatus.FOUND
    ticket = Ticket.objects.get()
    assert {name: getattr(ticket, name) for name in details} == details
    page = compact(portal_client.get(ticket.get_absolute_url()))
    assert page.index('id="api-details-title"') < page.index('id="ticket-thread"')
    assert "POST /api/hiecm/consent/v3/request/init" in page
    assert "5f0c6a2e-7d1b-4a39-9c51-2b9e1d7f4a10" in page
    assert 'tabindex="0" aria-labelledby="api-request-label">curl -X POST' in page
    assert "{&quot;error&quot;: {&quot;code&quot;: &quot;ABDM-1017&quot;" in page


def test_a_question_with_no_call_keeps_no_api_details(portal_client, support_url):
    """Hidden fields still post, so details typed before switching to Others are
    dropped rather than filed against a question with no call."""
    portal_client.post(
        support_url,
        {
            "subject": "How do I add a colleague?",
            "category": "others",
            "issue_type": "Access / General Inquiry / Concerns",
            "priority": "low",
            "environment": "sandbox",
            "body": "We need a second integrator on the product.",
            "api_url": "POST /api/hiecm/consent/v3/request/init",
            "api_response": "HTTP 500",
        },
    )
    ticket = Ticket.objects.get()
    assert not ticket.has_api_details
    assert (
        'id="api-details-title"'
        not in portal_client.get(
            ticket.get_absolute_url(),
        ).text
    )


def test_a_ticket_takes_documents_spreadsheets_text_and_screenshots(
    portal_client,
    support_url,
):
    """Each file is labelled with its own type in the thread. Screenshots open in
    the browser as PDFs do; Office files and text download."""
    files = {
        "screenshot.png": b"\x89PNG\r\n\x1a\nimage",
        "photo.jpeg": b"\xff\xd8\xff\xe0photo",
        "steps.docx": b"PK\x03\x04document",
        "results.xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1workbook",
        "response.txt": b'{"error": "INVALID_CONSENT"}',
    }
    response = portal_client.post(
        support_url,
        {
            "subject": "Callback rejects the request",
            "category": "phr-app-p2",
            "issue_type": "Consent Flow",
            "priority": "medium",
            "environment": "sandbox",
            "body": "The callback returns an unexpected status.",
            "attachments": [
                SimpleUploadedFile(name, content) for name, content in files.items()
            ],
        },
    )
    assert response.status_code == HTTPStatus.FOUND
    ticket = Ticket.objects.get()
    attachments = ticket.messages.get().attachments.order_by("pk")
    assert [file.original_name for file in attachments] == list(files)
    page = portal_client.get(ticket.get_absolute_url()).content.decode()
    for label in ("PNG", "JPEG", "DOCX", "XLS", "TXT"):
        assert f'uppercase">{label}</span>' in page
    for file, disposition in zip(
        attachments,
        ["inline", "inline", "attachment", "attachment", "attachment"],
        strict=True,
    ):
        preview = portal_client.get(
            reverse(
                "experiences:ticket-attachment-preview",
                args=[file.pk, file.original_name],
            ),
        )
        assert preview["Content-Disposition"].startswith(f"{disposition};")
        assert b"".join(preview.streaming_content) == files[file.original_name]


def test_ticket_attachments_say_what_they_take(
    portal_client,
    portal_products,
    support_url,
    owner_membership,
):
    """The picker and the hint name what the validator takes, on the new ticket
    and on a reply. The hint says it in words, so the drop area adds no list."""
    field = SupportForm().fields["attachments"]
    assert field.widget.attrs["accept"] == ",".join(TICKET_ATTACHMENT_SIGNATURES)
    assert field.help_text == (
        "PDF, Word, Excel, CSV, text, PNG or JPG, up to 5 files, 10 MB each."
    )
    creating = portal_client.get(support_url, {"new": "1"})
    assert b"PDF, Word, Excel, CSV, text, PNG or JPG, 10 MB each." in creating.content
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[1],
        subject="Callback rejects the request",
        created_by=owner_membership.user,
        category="phr-app-p2",
    )
    replying = portal_client.get(ticket.get_absolute_url())
    assert field.help_text.encode() in replying.content
    for page in (creating, replying):
        assert b'<span class="font-mono">.pdf' not in page.content


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("report.pdf", b"%PDF-1.7"),
        ("steps.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ("results.xlsx", b"PK\x03\x04"),
        ("export.csv", b"request_id,status\n1,FAILED\n"),
        ("response.TXT", b"HTTP/1.1 400 Bad Request"),
        ("screenshot.jpg", b"\xff\xd8\xff\xe0"),
        # Saved under a sibling's extension, each still opens.
        ("screenshot.png", b"\xff\xd8\xff\xe0"),
        ("results.xls", b"PK\x03\x04"),
    ],
)
def test_a_ticket_attachment_opens_like_its_kind(name, content):
    validate_ticket_attachment(SimpleUploadedFile(name, content))


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("installer.exe", b"MZ\x90\x00"),
        ("archive.zip", b"PK\x03\x04"),
        ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
        ("photo.heic", b"\x00\x00\x00\x18ftypheic"),
        ("no-extension", b"%PDF-1.7"),
        # A saved error page, and a PDF renamed to an image.
        ("report.pdf", b"<html>Session expired</html>"),
        ("screenshot.png", b"%PDF-1.7"),
    ],
)
def test_a_ticket_refuses_a_file_it_cannot_open(name, content):
    with pytest.raises(ValidationError) as refused:
        validate_ticket_attachment(SimpleUploadedFile(name, content))
    assert refused.value.messages == [
        f"{name} is not a PDF, Word, Excel, CSV, text, PNG or JPG file.",
    ]


def test_ticket_list_shows_category_and_sub_category_columns(
    portal_client,
    portal_products,
    owner_membership,
):
    Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[1],
        subject="Callback rejects the request",
        created_by=owner_membership.user,
        category="abdm-m2",
        issue_type="Bridge Service",
    )
    response = portal_client.get(reverse("experiences:support"))
    assert response.status_code == HTTPStatus.OK
    body = response.content.decode()
    assert 'href="?sort=category"' in body
    assert 'href="?sort=subcategory"' in body
    assert "ABDM - Milestone 2" in body
    assert "Bridge Service" in body


def test_ticket_creation_error_keeps_form_and_does_not_write(
    portal_client,
    support_url,
):
    response = portal_client.post(
        support_url,
        {
            "subject": "Help",
            "category": "bad",
            "priority": "medium",
            "body": "Details",
        },
    )
    assert response.status_code == HTTPStatus.OK
    assert "category" in response.context["form"].errors
    assert b"New support ticket" in response.content
    assert not Ticket.objects.exists()


@pytest.mark.parametrize(
    ("actor", "message"),
    [
        ("reviewer", "Only an organisation's integrators can open a ticket."),
        ("no_product", "Register a product before opening a ticket."),
    ],
)
def test_support_hides_the_new_ticket_form_from_those_who_cannot_use_it(
    client,
    owner_membership,
    portal_products,
    actor,
    message,
):
    """A reviewer, and an integrator with no product yet, have nothing to raise
    a ticket against. They must not be offered the form, and posting one must
    explain itself rather than fail the request."""
    if actor == "reviewer":
        client.force_login(ReviewerFactory(is_nha_team=True))
    else:
        membership = MembershipFactory.create(role="owner")
        client.force_login(membership.user)

    creating = client.get(reverse("experiences:support"), {"new": "1"})

    assert creating.status_code == HTTPStatus.OK
    assert not creating.context["creating"]
    assert b"New support ticket" not in creating.content

    response = client.post(
        reverse("experiences:support"),
        {
            "subject": "Callback rejects the request",
            "priority": "medium",
            "body": "The callback returns an unexpected status.",
        },
        follow=True,
    )

    assert response.status_code == HTTPStatus.OK
    assert not Ticket.objects.exists()
    assert message in [str(item) for item in response.context["messages"]]


def test_ticket_reply_needs_no_category_and_keeps_downloads(
    portal_client,
    portal_products,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[0],
        subject="Existing ticket",
        created_by=owner_membership.user,
        category="abdm-m1",
        issue_type="ABHA Creation",
    )
    upload = SimpleUploadedFile(
        "diagnostic.pdf",
        b"%PDF-1.4\n%%EOF",
        content_type="application/pdf",
    )
    response = portal_client.post(
        ticket.get_absolute_url(),
        {"body": "More details", "attachments": upload},
    )
    assert response.status_code == HTTPStatus.FOUND
    assert [str(message) for message in get_messages(response.wsgi_request)] == [
        "Reply sent.",
    ]
    message = ticket.messages.get()
    assert message.body == "More details"
    assert message.attachments.get().original_name == "diagnostic.pdf"
    response = portal_client.get(ticket.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert b"diagnostic.pdf" in response.content
    assert response.context["selected_product"] == portal_products[0]
    assert set(response.context["form"].fields) == {"body", "attachments"}
    ticket.refresh_from_db()
    assert ticket.category == "abdm-m1"
    assert ticket.issue_type == "ABHA Creation"


def test_support_search_keeps_product_and_status(
    portal_client,
    portal_products,
    owner_membership,
    support_url,
):
    for product, subject, status in (
        (portal_products[0], "Callback on another product", "open"),
        (portal_products[1], "Callback investigation", "open"),
        (portal_products[1], "Callback fixed", "closed"),
        (portal_products[1], "Other issue", "open"),
    ):
        Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=product,
            subject=subject,
            status=status,
        )
    response = portal_client.get(support_url, {"q": "callback", "status": "open"})
    assert response.status_code == HTTPStatus.OK
    assert [ticket.subject for ticket in response.context["tickets"]] == [
        "Callback investigation",
    ]


def test_support_counts_keep_filters_and_product_before_status(
    portal_client,
    portal_products,
    owner_membership,
    support_url,
):
    for product_index, subject, category, priority, status in (
        (1, "Callback investigation", "phr-app-p2", "high", "open"),
        (1, "Callback fixed", "phr-app-p2", "high", "closed"),
        (1, "Unrelated issue", "phr-app-p2", "high", "open"),
        (1, "Callback medium priority", "phr-app-p2", "medium", "open"),
        (1, "Callback sandbox issue", "phr-app-p1", "high", "open"),
        (0, "Callback on another product", "abdm-m2", "high", "open"),
    ):
        Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=portal_products[product_index],
            subject=subject,
            category=category,
            priority=priority,
            status=status,
        )
    response = portal_client.get(
        support_url,
        {
            "q": "callback",
            "category": "phr-app-p2",
            "priority": "high",
            "status": "open",
        },
    )
    assert response.status_code == HTTPStatus.OK
    assert [ticket.subject for ticket in response.context["tickets"]] == [
        "Callback investigation",
    ]
    expected_product_tickets = 5
    assert response.context["ticket_total"] == expected_product_tickets
    assert {tab["value"]: tab["count"] for tab in response.context["status_tabs"]} == {
        "": 2,
        "open": 1,
        "awaiting_integrator": 0,
        "closed": 1,
    }
    assert response.context["has_ticket_filters"]
    assert b">Clear filters</a>" in response.content
    cleared = portal_client.get(support_url)
    assert not cleared.context["has_ticket_filters"]
    assert cleared.context["ticket_filters"]["status"] == "open"
    open_product_tickets = 4
    assert len(cleared.context["tickets"]) == open_product_tickets
    assert cleared.context["product"] == portal_products[1]


def test_filters_stay_on_all_tickets(
    portal_client,
    portal_products,
    owner_membership,
):
    """The filter form sends the tab it sits under, All tickets included.

    All tickets is the blank status, and a request with no status at all opens
    the default tab instead.
    """
    Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[1],
        subject="Callback fixed",
        status="closed",
    )
    response = portal_client.get(reverse("experiences:support"), {"status": ""})
    assert b'<input type="hidden" name="status" value="" />' in response.content
    response = portal_client.get(
        reverse("experiences:support"),
        {"status": "", "q": "callback"},
    )
    assert [ticket.subject for ticket in response.context["tickets"]] == [
        "Callback fixed",
    ]


def test_header_counts_every_ticket_until_something_is_filtered(
    portal_client,
    portal_products,
    owner_membership,
):
    for status in ("open", "closed"):
        Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=portal_products[1],
            subject=f"An {status} ticket",
            status=status,
        )
    # The default tab lists the open ticket; the header still counts both.
    response = portal_client.get(reverse("experiences:support"))
    assert len(response.context["tickets"]) == 1
    compact = " ".join(response.content.decode().split())
    assert (
        '<span class="ui-kicker block">Tickets</span> '
        '<span class="ui-figure mt-0.5 block">2</span>'
    ) in compact


def test_others_filters_to_the_catch_all_category(
    portal_client,
    portal_products,
    owner_membership,
):
    for subject, category in (("General question", "others"), ("Callback", "abdm-m1")):
        Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=portal_products[1],
            subject=subject,
            category=category,
            status="open",
        )
    url = reverse("experiences:support")
    response = portal_client.get(url, {"category": "others"})
    assert [ticket.subject for ticket in response.context["tickets"]] == [
        "General question",
    ]
    compact = " ".join(response.content.decode().split())
    assert '<option value="others" selected>Others</option>' in compact
    # The new ticket form's placeholder is not a category to filter by.
    assert "Select a category" not in compact
    # All categories is the blank one, and lists both.
    response = portal_client.get(url, {"category": ""})
    assert len(response.context["tickets"]) == 2  # noqa: PLR2004
    assert "selected>Others</option>" not in " ".join(response.content.decode().split())


def test_invalid_support_filters_do_not_create_a_false_active_state(portal_client):
    response = portal_client.get(
        reverse("experiences:support"),
        {"category": "unknown", "priority": "urgent", "status": "invalid", "q": "  "},
    )
    assert response.status_code == HTTPStatus.OK
    assert response.context["ticket_filters"] == {
        "category": "",
        "priority": "",
        "assignee": "",
        "status": "",
        "q": "",
    }
    assert not response.context["has_ticket_filters"]


@pytest.mark.parametrize(
    ("as_reviewer", "labels"),
    [
        (False, ["All tickets", "With NHA team", "Awaiting your reply", "Resolved"]),
        (True, ["All tickets", "Needs a reply", "Awaiting integrator", "Resolved"]),
    ],
)
def test_status_tabs_say_whose_turn_it_is(portal_client, as_reviewer, labels):
    if as_reviewer:
        portal_client.force_login(ReviewerFactory(is_nha_team=True))
    response = portal_client.get(reverse("experiences:support"))
    assert response.status_code == HTTPStatus.OK
    assert [tab["label"] for tab in response.context["status_tabs"]] == labels


@pytest.mark.parametrize(
    ("as_reviewer", "message"),
    [
        (False, b"None of your tickets are with the NHA team right now."),
        (True, b"No tickets need your reply right now."),
    ],
)
def test_an_empty_first_tab_does_not_claim_there_are_no_tickets(
    portal_client,
    portal_products,
    owner_membership,
    as_reviewer,
    message,
):
    Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[0],
        subject="Waiting on the integrator",
        status="awaiting_integrator",
    )
    if as_reviewer:
        portal_client.force_login(ReviewerFactory(is_nha_team=True))
    response = portal_client.get(
        reverse("experiences:product-support", args=[portal_products[0].reference]),
    )
    assert response.status_code == HTTPStatus.OK
    assert b"No tickets yet" not in response.content
    assert message in response.content


def test_event_counts_filter_by_kind_and_registrations_stay_private(
    portal_client,
    owner_membership,
):
    events = {}
    for title, kind, days, published in (
        ("Next webinar", "webinar", 2, True),
        ("Next workshop", "workshop", 1, True),
        ("Past webinar", "webinar", -2, True),
        ("Unpublished webinar", "webinar", 1, False),
    ):
        events[title] = Event.objects.create(
            title=title,
            kind=kind,
            starts_at=timezone.now() + timedelta(days=days),
            published_at=timezone.now() if published else None,
        )
    for title in ("Next webinar", "Past webinar", "Unpublished webinar"):
        EventRegistration.objects.create(
            event=events[title],
            user=owner_membership.user,
        )
    EventRegistration.objects.create(
        event=events["Next workshop"],
        user=UserFactory(),
    )
    response = portal_client.get(
        reverse("experiences:events"),
        {"kind": "webinar", "period": "past"},
    )
    assert response.status_code == HTTPStatus.OK
    assert list(response.context["events"]) == [events["Past webinar"]]
    assert response.context["upcoming_count"] == 1
    assert response.context["past_count"] == 1
    assert response.context["next_event"] == events["Next webinar"]
    assert response.context["registered_upcoming_count"] == 1
    assert events["Next workshop"].pk not in response.context["registered"]
    assert events["Next webinar"].title.encode() not in response.content


def test_registered_event_explains_missing_joining_details(
    portal_client,
    owner_membership,
):
    event = Event.objects.create(
        title="Office hours awaiting a link",
        starts_at=timezone.now() + timedelta(days=2),
        published_at=timezone.now(),
    )
    EventRegistration.objects.create(event=event, user=owner_membership.user)
    response = portal_client.get(event.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert b"Joining details have not been added yet." in response.content
    assert b"Cancel registration" in response.content
    assert b"Join session" not in response.content


def test_support_pages_keep_filters_in_links(
    portal_client,
    portal_products,
    owner_membership,
):
    url = reverse("experiences:support")
    per_page = portal_client.get(url).context["tickets"].paginator.per_page
    for number in range(per_page + 1):
        Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=portal_products[1],
            subject=f"Callback {number}",
            status="open",
        )
    params = {"q": "callback", "status": "open"}
    first = portal_client.get(url, params)
    assert len(first.context["tickets"]) == per_page
    assert b'aria-label="Tickets pagination"' in first.content
    assert b'href="?q=callback&amp;status=open&amp;page=2"' in first.content
    last = portal_client.get(url, {**params, "page": 2})
    assert len(last.context["tickets"]) == 1


def test_events_paginate_in_date_order(portal_client):
    url = reverse("experiences:events")
    per_page = portal_client.get(url).context["events"].paginator.per_page
    for day in range(1, per_page + 2):
        Event.objects.create(
            title=f"Office hours {day}",
            starts_at=timezone.now() + timedelta(days=day),
            published_at=timezone.now(),
        )
    first = portal_client.get(url, {"period": "upcoming"})
    assert b'href="?period=upcoming&amp;page=2"' in first.content
    last = portal_client.get(url, {"period": "upcoming", "page": 2})
    assert [event.title for event in last.context["events"]] == [
        f"Office hours {per_page + 1}",
    ]


def test_event_register_and_cancel_on_the_event_page(
    portal_client,
    portal_products,
):
    event = Event.objects.create(
        title="Integration office hours",
        kind="event",
        starts_at=timezone.now() + timedelta(days=2),
        published_at=timezone.now(),
        join_url="https://example.org/event",
        description="Bring your integration questions.",
    )
    reference = portal_products[1].reference
    url = reverse("experiences:product-event", args=[reference, event.pk])
    listing = (
        f"{reverse('experiences:product-events', args=[reference])}"
        "?kind=event&period=upcoming"
    )
    # The listing no longer registers anyone; it sends them to the event instead.
    assert url.encode() in portal_client.get(listing).content
    response = portal_client.post(url, {"intent": "register"}, follow=True)
    assert response.status_code == HTTPStatus.OK
    # The product the integrator came from stays selected on the event's page.
    assert response.context["product"] == portal_products[1]
    assert response.redirect_chain == [(url, HTTPStatus.FOUND)]
    assert [str(message) for message in response.context["messages"]] == [
        f"You are registered for {event.title}.",
    ]
    assert EventRegistration.objects.filter(event=event).exists()
    assert Notification.objects.filter(subject__contains=event.title).count() == 1
    assert b"Cancel registration" in response.content
    assert b"Join session" in response.content
    filtered = portal_client.get(listing, {"kind": "webinar"})
    assert event not in filtered.context["events"]
    response = portal_client.post(url, {"intent": "cancel"}, follow=True)
    assert response.status_code == HTTPStatus.OK
    assert not EventRegistration.objects.filter(event=event).exists()
    assert response.redirect_chain == [(url, HTTPStatus.FOUND)]
    assert [str(message) for message in response.context["messages"]] == [
        f"Registration cancelled for {event.title}.",
    ]
    # Registering is the event page's job now; the listing only reads.
    assert (
        portal_client.post(reverse("experiences:events")).status_code
        == HTTPStatus.METHOD_NOT_ALLOWED
    )


def test_an_event_that_has_ended_takes_no_registration(portal_client):
    event = Event.objects.create(
        title="Last month's workshop",
        starts_at=timezone.now() - timedelta(days=30),
        published_at=timezone.now() - timedelta(days=40),
    )
    url = event.get_absolute_url()
    page = portal_client.get(url)
    assert b"This session has ended." in page.content
    assert b'value="register"' not in page.content
    assert (
        portal_client.post(url, {"intent": "register"}).status_code
        == HTTPStatus.FORBIDDEN
    )
    assert not EventRegistration.objects.exists()


def test_reviewer_can_reply_and_resolve(
    portal_client,
    portal_products,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[0],
        subject="Needs review",
    )
    portal_client.force_login(ReviewerFactory(is_nha_team=True))
    response = portal_client.get(ticket.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert b"Mark as resolved" in response.content
    response = portal_client.post(
        ticket.get_absolute_url(),
        {"intent": "close", "body": "Callback acknowledged on our side."},
        follow=True,
    )
    assert response.status_code == HTTPStatus.OK
    ticket.refresh_from_db()
    assert ticket.status == "closed"


def test_staff_who_reply_can_correct_a_priority(
    portal_client,
    portal_products,
    owner_membership,
):
    """An integrator can file a ticket as High without cause. The NHA team puts
    it right, and the thread tells both sides who changed it from what."""
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[0],
        subject="Where is the sandbox guide?",
        priority="high",
        created_by=owner_membership.user,
    )
    url = ticket.get_absolute_url()
    portal_client.force_login(ReviewerFactory(name="Anand S"))
    page = " ".join(portal_client.get(url).content.decode().split())
    assert '<input type="hidden" name="intent" value="priority" />' in page
    assert '<option value="high" selected>High</option>' in page

    response = portal_client.post(
        url,
        {"intent": "priority", "priority": "low"},
        follow=True,
    )

    assert response.redirect_chain == [(url, HTTPStatus.FOUND)]
    assert [str(message) for message in response.context["messages"]] == [
        "Priority changed to Low.",
    ]
    ticket.refresh_from_db()
    assert ticket.priority == "low"
    assert list(ticket.messages.values_list("kind", "body")) == [
        ("priority", "High → Low"),
    ]
    entry = (
        "Anand S changed the priority from <strong>High</strong> to "
        "<strong>Low</strong>"
    )
    assert entry in " ".join(response.content.decode().split())
    # Saving the priority it already has says and records nothing.
    response = portal_client.post(
        url,
        {"intent": "priority", "priority": "low"},
        follow=True,
    )
    assert not list(response.context["messages"])
    assert ticket.messages.count() == 1

    portal_client.force_login(owner_membership.user)
    page = " ".join(portal_client.get(url).content.decode().split())
    assert entry in page
    assert 'value="priority"' not in page


@pytest.mark.parametrize("actor", ["integrator", "read_only_staff"])
def test_only_staff_who_reply_can_change_a_priority(
    client,
    owner_membership,
    portal_products,
    actor,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[0],
        subject="Where is the sandbox guide?",
        priority="high",
    )
    user = owner_membership.user
    if actor == "read_only_staff":
        user = UserFactory(is_nha_team=True)
        AccessGrant.objects.create(
            user=user,
            program=portal_products[0].experience_type,
            area="support",
            category="*",
            can_read=True,
        )
    client.force_login(user)
    url = ticket.get_absolute_url()

    page = client.get(url)
    assert page.status_code == HTTPStatus.OK
    assert b'value="priority"' not in page.content
    response = client.post(url, {"intent": "priority", "priority": "low"})

    assert response.status_code == HTTPStatus.FORBIDDEN
    ticket.refresh_from_db()
    assert ticket.priority == "high"
    assert not ticket.messages.exists()


def test_a_priority_off_the_list_is_refused(
    portal_client,
    portal_products,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[0],
        subject="Where is the sandbox guide?",
        priority="high",
    )
    portal_client.force_login(ReviewerFactory())

    response = portal_client.post(
        ticket.get_absolute_url(),
        {"intent": "priority", "priority": "urgent"},
        follow=True,
    )

    assert [str(message) for message in response.context["messages"]] == [
        "Choose High, Medium or Low.",
    ]
    ticket.refresh_from_db()
    assert ticket.priority == "high"
    assert not ticket.messages.exists()


@pytest.fixture
def m1_ticket(owner_membership, portal_products):
    return Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[0],
        subject="Where is the sandbox guide?",
        category="abdm-m1",
        created_by=owner_membership.user,
    )


def staff_with_grant(ticket, category, name="Divya R", **actions):
    """Someone on the team with one support grant, and no other way in."""
    user = UserFactory(is_nha_team=True, name=name)
    AccessGrant.objects.create(
        user=user,
        program=ticket.product.experience_type,
        area="support",
        category=category,
        **actions,
    )
    return user


def compact(response):
    return " ".join(response.content.decode().split())


def toasts(response):
    return [str(message) for message in response.context["messages"]]


def test_staff_can_assign_a_ticket_and_the_thread_tells_both_sides(
    portal_client,
    owner_membership,
    m1_ticket,
):
    ticket = m1_ticket
    colleague = staff_with_grant(ticket, "abdm-m1", can_write=True)
    url = ticket.get_absolute_url()
    portal_client.force_login(ReviewerFactory(name="Anand S"))
    page = compact(portal_client.get(url))
    assert '<input type="hidden" name="intent" value="assign" />' in page
    assert f'<option value="{colleague.pk}" >Divya R</option>' in page
    assert '<span class="block break-words">Unassigned</span>' in page

    response = portal_client.post(
        url,
        {"intent": "assign", "assignee": colleague.pk},
        follow=True,
    )

    assert response.redirect_chain == [(url, HTTPStatus.FOUND)]
    assert toasts(response) == [f"Ticket {ticket.reference} assigned to Divya R."]
    ticket.refresh_from_db()
    assert ticket.assignee == colleague
    assert list(ticket.messages.values_list("kind", "body")) == [
        ("assignment", "Divya R"),
    ]
    page = compact(response)
    entry = "Anand S assigned this to <strong>Divya R</strong>"
    assert entry in page
    assert '<span class="block break-words">Divya R</span>' in page
    assert f'<option value="{colleague.pk}" selected>Divya R</option>' in page
    # Saving the assignee it already has says and records nothing.
    response = portal_client.post(
        url,
        {"intent": "assign", "assignee": colleague.pk},
        follow=True,
    )
    assert not toasts(response)
    assert ticket.messages.count() == 1

    portal_client.force_login(owner_membership.user)
    page = compact(portal_client.get(url))
    assert entry in page
    assert 'value="assign"' not in page


def test_staff_can_take_a_ticket_and_let_it_go(
    portal_client,
    owner_membership,
    m1_ticket,
):
    ticket = m1_ticket
    url = ticket.get_absolute_url()
    reviewer = ReviewerFactory(name="Anand S")
    portal_client.force_login(reviewer)

    taken = portal_client.post(
        url,
        {"intent": "assign", "assignee": reviewer.pk},
        follow=True,
    )

    assert toasts(taken) == [f"Ticket {ticket.reference} assigned to you."]
    assert "Anand S assigned this to themselves" in compact(taken)
    assert ">Reassign" in compact(taken)

    released = portal_client.post(
        url,
        {"intent": "assign", "assignee": ""},
        follow=True,
    )

    assert toasts(released) == [f"Ticket {ticket.reference} unassigned."]
    assert "Anand S unassigned this" in compact(released)
    ticket.refresh_from_db()
    assert ticket.assignee is None
    # An integrator's ticket with no one on it is still with the NHA team.
    portal_client.force_login(owner_membership.user)
    assert 'class="break-words"> NHA team </dd>' in compact(portal_client.get(url))


def test_a_ticket_is_only_offered_to_staff_who_can_act_on_its_category(
    portal_client,
    m1_ticket,
):
    ticket = m1_ticket
    writer = staff_with_grant(ticket, "abdm-m1", "Writer", can_write=True)
    approver = staff_with_grant(ticket, "abdm-m1", "Approver", can_approve=True)
    all_categories = staff_with_grant(ticket, "*", "All", can_write=True)
    staff_with_grant(ticket, "abdm-m1", "Reader")
    staff_with_grant(ticket, "abdm-m2", "Another category", can_write=True)
    archived = staff_with_grant(ticket, "abdm-m1", "Archived", can_write=True)
    archived.is_active = False
    archived.save(update_fields=["is_active"])
    portal_client.force_login(writer)

    response = portal_client.get(ticket.get_absolute_url())

    assert list(response.context["assignees"]) == [all_categories, approver, writer]


def test_someone_off_the_list_cannot_be_assigned_a_ticket(portal_client, m1_ticket):
    """A form left open can name someone who has since lost the category."""
    ticket = m1_ticket
    outsider = staff_with_grant(ticket, "abdm-m2", can_write=True)
    portal_client.force_login(ReviewerFactory())

    response = portal_client.post(
        ticket.get_absolute_url(),
        {"intent": "assign", "assignee": outsider.pk},
        follow=True,
    )

    assert toasts(response) == [
        "Choose an active team member with write or approve access to this category.",
    ]
    ticket.refresh_from_db()
    assert ticket.assignee is None
    assert not ticket.messages.exists()


def test_an_assignee_who_does_not_exist_is_not_found(portal_client, m1_ticket):
    portal_client.force_login(ReviewerFactory())

    response = portal_client.post(
        m1_ticket.get_absolute_url(),
        {"intent": "assign", "assignee": "0"},
    )

    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize("actor", ["integrator", "read_only_staff"])
def test_only_staff_who_can_act_on_a_ticket_can_assign_it(
    client,
    owner_membership,
    m1_ticket,
    actor,
):
    ticket = m1_ticket
    user = owner_membership.user
    assigned_shows = "NHA team"
    if actor == "read_only_staff":
        user = staff_with_grant(ticket, "abdm-m1")
        assigned_shows = "Unassigned"
    client.force_login(user)
    url = ticket.get_absolute_url()

    page = client.get(url)
    assert page.status_code == HTTPStatus.OK
    assert b'value="assign"' not in page.content
    assert f'class="break-words"> {assigned_shows} </dd>' in compact(page)
    response = client.post(url, {"intent": "assign", "assignee": user.pk})

    assert response.status_code == HTTPStatus.FORBIDDEN
    ticket.refresh_from_db()
    assert ticket.assignee is None
    assert not ticket.messages.exists()


def test_a_ticket_keeps_its_assignee_through_a_reply_and_a_resolution(
    portal_client,
    m1_ticket,
):
    ticket = m1_ticket
    reviewer = ReviewerFactory()
    portal_client.force_login(reviewer)
    url = ticket.get_absolute_url()
    portal_client.post(url, {"intent": "assign", "assignee": reviewer.pk})

    portal_client.post(url, {"body": "Looking into it."})
    portal_client.post(url, {"intent": "close", "body": "Guide shared."})

    ticket.refresh_from_db()
    assert ticket.status == "closed"
    assert ticket.assignee == reviewer


@pytest.fixture
def assigned_tickets(owner_membership, portal_products):
    """One reviewer's tickets among a colleague's and some with no one on them."""
    reviewer = ReviewerFactory(name="Anand S")
    colleague = ReviewerFactory(name="Divya R")
    for subject, assignee, status in (
        ("Mine and open", reviewer, "open"),
        ("Mine and resolved", reviewer, "closed"),
        ("Theirs", colleague, "open"),
        ("No one's", None, "open"),
    ):
        Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=portal_products[0],
            subject=subject,
            category="abdm-m1",
            status=status,
            assignee=assignee,
        )
    return reviewer


def test_assigned_to_me_narrows_the_queue_and_its_tabs(client, assigned_tickets):
    client.force_login(assigned_tickets)
    url = reverse("experiences:support")

    response = client.get(url, {"assignee": "me", "status": ""})

    assert {ticket.subject for ticket in response.context["tickets"]} == {
        "Mine and open",
        "Mine and resolved",
    }
    assert {tab["value"]: tab["count"] for tab in response.context["status_tabs"]} == {
        "": 2,
        "open": 1,
        "awaiting_integrator": 0,
        "closed": 1,
    }
    assert response.context["has_ticket_filters"]
    assert response.context["has_ticket_search_filters"]
    page = compact(response)
    assert '<option value="me" selected>Assigned to me</option>' in page
    assert page.count("&amp;assignee=me") == len(response.context["status_tabs"])
    # Sorting reads the query, so a column heading keeps the filter as well.
    assert "?assignee=me&amp;status=&amp;sort=subject" in page


def test_the_queue_shows_everyone_s_tickets_unless_asked(client, assigned_tickets):
    client.force_login(assigned_tickets)

    response = client.get(reverse("experiences:support"), {"status": ""})

    assert len(response.context["tickets"]) == 4  # noqa: PLR2004
    assert '<option value="me" >Assigned to me</option>' in compact(response)
    assert not response.context["has_ticket_search_filters"]


def test_a_status_with_nothing_assigned_to_me_points_at_the_filter(
    client,
    assigned_tickets,
):
    client.force_login(assigned_tickets)

    response = client.get(
        reverse("experiences:support"),
        {"assignee": "me", "status": "awaiting_integrator"},
    )

    assert not response.context["tickets"]
    assert b"Try another status, category, priority or assignee" in response.content


@pytest.mark.parametrize("assignee", ["me", "unassigned"])
def test_integrators_have_no_assignee_filter_or_column(
    portal_client,
    assigned_tickets,
    assignee,
):
    """They are never assigned a ticket, so the filter would hide all of theirs."""
    response = portal_client.get(
        reverse("experiences:support"),
        {"assignee": assignee, "status": "", "sort": "assignee"},
    )

    assert len(response.context["tickets"]) == 4  # noqa: PLR2004
    assert response.context["ticket_filters"]["assignee"] == ""
    assert not response.context["has_ticket_search_filters"]
    assert response.context["table_sort"] == "-updated"
    assert b'name="assignee"' not in response.content
    assert b"Assigned to" not in response.content


def test_the_queue_says_who_holds_each_ticket(client, assigned_tickets):
    client.force_login(assigned_tickets)

    page = compact(client.get(reverse("experiences:support"), {"status": ""}))

    assert '<td class="align-top text-muted-foreground">Divya R</td>' in page
    assert '<span class="text-soft-foreground">Unassigned</span>' in page
    # The phone's cards say it in words, with no heading above them.
    assert ">Assigned to Divya R</p>" in page


def test_the_queue_sorts_by_assignee_with_the_unassigned_last(
    client,
    owner_membership,
    portal_products,
    assigned_tickets,
):
    """Someone with no name sorts where their email would, as it shows."""
    Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[0],
        subject="Held by someone with no name",
        category="abdm-m1",
        assignee=ReviewerFactory(name="", email="beena@ohc.network"),
    )
    client.force_login(assigned_tickets)

    def holders(sort):
        response = client.get(
            reverse("experiences:support"),
            {"status": "", "sort": sort},
        )
        assert response.context["table_sort"] == sort
        return [
            ticket.assignee.display_name if ticket.assignee else None
            for ticket in response.context["tickets"]
        ]

    holding = ["Anand S", "Anand S", "beena", "Divya R"]
    assert holders("assignee") == [*holding, None]
    assert holders("-assignee") == [*reversed(holding), None]


def test_the_assignee_filter_lists_everyone_holding_a_ticket(client, assigned_tickets):
    """But not the viewer, who has Assigned to me, or anyone with nothing to show."""
    colleague = Ticket.objects.get(subject="Theirs").assignee
    ReviewerFactory(name="Nothing Assigned")
    client.force_login(assigned_tickets)

    response = client.get(reverse("experiences:support"))

    assert response.context["assignee_choices"] == [
        ("me", "Assigned to me"),
        ("unassigned", "Unassigned"),
        (str(colleague.pk), "Divya R"),
    ]


def test_the_queue_narrows_to_one_persons_tickets(client, assigned_tickets):
    colleague = Ticket.objects.get(subject="Theirs").assignee
    client.force_login(assigned_tickets)

    response = client.get(
        reverse("experiences:support"),
        {"assignee": colleague.pk, "status": ""},
    )

    assert [ticket.subject for ticket in response.context["tickets"]] == ["Theirs"]
    assert response.context["has_ticket_search_filters"]
    page = compact(response)
    assert f'<option value="{colleague.pk}" selected>Divya R</option>' in page
    assert f"&amp;assignee={colleague.pk}" in page


def test_the_queue_narrows_to_tickets_no_one_holds(client, assigned_tickets):
    client.force_login(assigned_tickets)

    response = client.get(
        reverse("experiences:support"),
        {"assignee": "unassigned", "status": ""},
    )

    assert [ticket.subject for ticket in response.context["tickets"]] == ["No one's"]
    assert '<option value="unassigned" selected>Unassigned</option>' in compact(
        response,
    )


def test_the_assignee_filter_stops_at_the_tickets_a_reviewer_can_see(
    client,
    owner_membership,
    m1_ticket,
):
    """Whoever holds only another category's tickets is not offered, or honoured."""
    m1_holder = staff_with_grant(m1_ticket, "abdm-m1", "M1 Holder", can_write=True)
    Ticket.objects.filter(pk=m1_ticket.pk).update(assignee=m1_holder)
    m2_holder = staff_with_grant(m1_ticket, "abdm-m2", "M2 Holder", can_write=True)
    Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=m1_ticket.product,
        subject="Another category",
        category="abdm-m2",
        assignee=m2_holder,
    )
    client.force_login(staff_with_grant(m1_ticket, "abdm-m1", can_write=True))
    url = reverse("experiences:support")

    response = client.get(url)

    assert [label for _value, label in response.context["assignee_choices"]] == [
        "Assigned to me",
        "Unassigned",
        "M1 Holder",
    ]
    response = client.get(url, {"assignee": m2_holder.pk, "status": ""})
    assert response.context["ticket_filters"]["assignee"] == ""
    assert not response.context["has_ticket_search_filters"]
    assert [ticket.subject for ticket in response.context["tickets"]] == [
        m1_ticket.subject,
    ]


@pytest.mark.parametrize("assignee", ["nobody", "0", "999999"])
def test_an_assignee_off_the_filter_narrows_nothing(
    client,
    assigned_tickets,
    assignee,
):
    client.force_login(assigned_tickets)

    response = client.get(
        reverse("experiences:support"),
        {"assignee": assignee, "status": ""},
    )

    assert len(response.context["tickets"]) == 4  # noqa: PLR2004
    assert response.context["ticket_filters"]["assignee"] == ""
    assert not response.context["has_ticket_search_filters"]


def test_the_export_says_who_holds_each_ticket_for_staff_only(
    client,
    owner_membership,
    assigned_tickets,
):
    url = reverse("experiences:support")
    client.force_login(assigned_tickets)

    exported = rows(
        client.get(url, {"status": "", "sort": "assignee", "export": "xlsx"}),
    )

    column = exported[0].index("Assigned to")
    assert [row[column] for row in exported[1:]] == [
        "Anand S",
        "Anand S",
        "Divya R",
        "Unassigned",
    ]
    client.force_login(owner_membership.user)
    assert "Assigned to" not in rows(client.get(url, {"export": "xlsx"}))[0]


@pytest.mark.parametrize("route", ["experiences:events", "experiences:support"])
def test_reviewer_pages_do_not_select_a_product(
    portal_client,
    portal_products,
    route,
):
    portal_client.force_login(ReviewerFactory(is_nha_team=True))
    response = portal_client.get(reverse(route))

    assert response.status_code == HTTPStatus.OK
    assert response.context["product"] is None


def test_integrator_can_resolve_their_own_ticket(
    portal_client,
    portal_products,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[0],
        subject="Sorted on our side",
    )
    url = ticket.get_absolute_url()
    assert b"Mark as resolved" in portal_client.get(url).content

    response = portal_client.post(url, {"intent": "close", "body": "Fixed now."})

    assert response.status_code == HTTPStatus.FOUND
    assert [str(message) for message in get_messages(response.wsgi_request)] == [
        "Ticket marked as resolved.",
    ]
    ticket.refresh_from_db()
    assert ticket.status == "closed"
    assert list(ticket.messages.values_list("kind", "body")) == [
        ("reply", "Fixed now."),
        ("event", "Resolved"),
    ]
    assert b"Mark as resolved" not in portal_client.get(url).content


def test_resolving_takes_a_comment_of_at_least_ten_characters(
    portal_client,
    portal_products,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_products[0],
        subject="Sorted on our side",
    )
    url = ticket.get_absolute_url()
    for body in ("", "Fixed now", "   Fixed now   "):
        response = portal_client.post(url, {"intent": "close", "body": body})
        assert response.status_code == HTTPStatus.OK
        assert response.context["form"].errors["body"] == [
            "Add a comment of at least 10 characters to resolve this ticket.",
        ]
        assert b"This ticket has not been resolved." in response.content
    ticket.refresh_from_db()
    assert ticket.status == "open"
    assert not ticket.messages.exists()
    # A plain reply has no minimum length.
    response = portal_client.post(url, {"body": "Fixed now"})
    assert response.status_code == HTTPStatus.FOUND


def test_staff_see_who_opened_a_ticket_and_how_to_reach_them(
    portal_client,
    owner_membership,
    m1_ticket,
):
    """The legacy help desk showed agents the requester's email. Integrators
    already know their colleagues, so they get the name alone."""
    owner = owner_membership.user
    page = compact(portal_client.get(m1_ticket.get_absolute_url()))
    assert (
        f'<dt class="text-soft-foreground">Opened by</dt> <dd class="break-words"> '
        f"{owner.display_name} </dd>"
    ) in page
    assert f"mailto:{owner.email}" not in page

    portal_client.force_login(staff_with_grant(m1_ticket, "abdm-m1", can_write=True))
    page = compact(portal_client.get(m1_ticket.get_absolute_url()))
    assert f'href="mailto:{owner.email}">{owner.email}</a>' in page


def test_the_export_says_which_environment_each_ticket_is_about(
    portal_client,
    owner_membership,
    portal_products,
):
    for subject, environment in (
        ("Filed before environments", ""),
        ("Live consent fails", "production"),
    ):
        Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=portal_products[0],
            subject=subject,
            category="abdm-m1",
            environment=environment,
            created_by=owner_membership.user,
        )

    exported = rows(
        portal_client.get(
            reverse("experiences:support"),
            {"status": "", "sort": "subject", "export": "xlsx"},
        ),
    )

    column = exported[0].index("Environment")
    assert [row[column] for row in exported[1:]] == ["", "Production"]
