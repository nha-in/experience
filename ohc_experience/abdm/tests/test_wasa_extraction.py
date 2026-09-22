"""The certificate reader proposes values; nothing it returns is trusted."""
# ruff: noqa: F811

import base64
import json
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

import litellm
import pypdfium2
import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm import wasa_extraction
from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.abdm.forms import WasaReviewForm
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.wasa import WASA_FIELDS
from ohc_experience.abdm.wasa_extraction import WasaExtractionError
from ohc_experience.abdm.wasa_extraction import extract_certificate
from ohc_experience.experiences.models import CertificationAgency
from ohc_experience.experiences.models import FormAttachment

pytestmark = pytest.mark.django_db

# Held before any test replaces it, so the provider wrapper itself can be tested.
REAL_COMPLETION = wasa_extraction._completion  # noqa: SLF001

AGENCY = "M/s A3S Tech & Company"
OK = 200
BAD_REQUEST = 400
FORBIDDEN = 403
NOT_FOUND = 404
TOO_MANY = 429
UNREADABLE = 422
UNAVAILABLE = 503


def certificate(marker: str = "one", name: str = "wasa.pdf") -> SimpleUploadedFile:
    """Distinct bytes per marker, so the reader's cache is not in the way."""
    return SimpleUploadedFile(
        name,
        f"%PDF-1.4\n% {marker}\n%%EOF".encode(),
        content_type="application/pdf",
    )


def stated(**overrides) -> str:
    today = timezone.localdate()
    return json.dumps(
        {
            "is_certificate": True,
            "agency": AGENCY,
            "audit_date": (today - timedelta(days=20)).isoformat(),
            "valid_until": (today + timedelta(days=345)).isoformat(),
        }
        | overrides,
    )


@pytest.fixture(autouse=True)
def reader(settings, monkeypatch):
    """A configured hook whose model replies with whatever a test sets."""
    settings.WASA_EXTRACTION_MODEL = "bedrock/test-model"
    wasa_extraction._extraction_cache.clear()  # noqa: SLF001
    cache.clear()
    replies = {"reply": "{}", "calls": []}

    def completion(model, content, timeout, max_tokens):
        replies["calls"].append(content)
        return replies["reply"]

    monkeypatch.setattr(wasa_extraction, "_completion", completion)
    return replies


def test_stated_fields_are_returned(reader):
    today = timezone.localdate()
    reader["reply"] = stated()
    assert extract_certificate(certificate()) == {
        "wasa_agency": AGENCY,
        "wasa_date": (today - timedelta(days=20)).isoformat(),
        "wasa_valid_until": (today + timedelta(days=345)).isoformat(),
    }


def test_a_fenced_reply_is_still_read(reader):
    reader["reply"] = f"Here you go:\n```json\n{stated()}\n```"
    assert extract_certificate(certificate())["wasa_agency"] == AGENCY


@pytest.mark.parametrize(
    "agency",
    ["Unlisted new agency", "", None, 7, "A3S Technologies"],
)
def test_only_a_published_agency_survives(reader, agency):
    reader["reply"] = stated(agency=agency)
    assert extract_certificate(certificate())["wasa_agency"] == ""


def test_agency_matching_ignores_case_and_spacing(reader):
    reader["reply"] = stated(agency="m/s  a3s   TECH & company ")
    assert extract_certificate(certificate())["wasa_agency"] == AGENCY


@pytest.mark.parametrize(
    "printed",
    ["M/s A3S Tech & Company Pvt", "A3S Tech and Company Private Limited"],
    ids=["extra legal form", "spelt out"],
)
def test_a_legal_form_is_how_a_company_registers_not_what_it_is_called(
    reader,
    printed,
):
    """The list says 'M/s A3S Tech & Company'; a letterhead adds Pvt Ltd."""
    reader["reply"] = stated(agency=printed)

    assert extract_certificate(certificate())["wasa_agency"] == AGENCY


@pytest.mark.parametrize(
    "printed",
    [
        "CertCube Labs Pvt Ltd",
        "Certcube Labs Private Limited",
        "CERTCUBE LABS PVT. LTD.",
        "M/s Certcube Labs Pvt Ltd (CLPL)",
    ],
    ids=["no honorific", "spelt out", "shouted", "with initials"],
)
def test_an_agency_is_matched_however_its_letterhead_spells_it(reader, printed):
    """The list says 'M/s Certcube Labs Pvt. Ltd.'; no certificate says that."""
    reader["reply"] = stated(agency=printed)

    assert extract_certificate(certificate())["wasa_agency"] == (
        "M/s Certcube Labs Pvt. Ltd."
    )


def test_two_agencies_that_reduce_alike_are_a_coin_toss_nobody_wins(reader):
    CertificationAgency.objects.create(
        program="abdm",
        name="A3S Tech and Company",
        is_active=True,
    )
    reader["reply"] = stated(agency="A3S Tech & Company")

    assert extract_certificate(certificate())["wasa_agency"] == ""


def test_an_inactive_agency_is_not_proposed(reader):
    CertificationAgency.objects.filter(name=AGENCY).update(is_active=False)
    reader["reply"] = stated()
    assert extract_certificate(certificate())["wasa_agency"] == ""


@pytest.mark.parametrize("denial", [False, "false", "No", " FALSE "])
def test_a_document_that_is_not_a_certificate_is_refused(reader, denial):
    """Uploading the functional report by mistake should say so, not stay blank."""
    reader["reply"] = stated(
        is_certificate=denial,
        agency="",
        audit_date="",
        valid_until="",
    )

    with pytest.raises(WasaExtractionError) as failure:
        extract_certificate(certificate())

    assert not failure.value.retryable
    assert "does not look like a WASA certificate" in str(failure.value)


def test_a_refusal_does_not_override_the_certificate_it_was_asked_about(reader):
    """Only a denial refuses; a reply without the flag is still read."""
    reader["reply"] = json.dumps(
        {
            "agency": AGENCY,
            "audit_date": timezone.localdate().isoformat(),
            "valid_until": "",
        },
    )

    assert extract_certificate(certificate())["wasa_agency"] == AGENCY


def test_the_model_is_asked_to_check_the_document_first(sent):
    extract_certificate(certificate())
    instruction = sent["messages"][0]["content"][0]["text"]

    assert "is_certificate" in instruction
    assert "First decide whether it really is one" in instruction
    # The same certificate goes by either name in the wild.
    assert "Safe to Host" in instruction
    # An audit runs over days; the certificate is dated by the day it ended.
    assert "give the last day of that range" in instruction


@pytest.mark.parametrize(
    "audit_date",
    ["", "yesterday", "12/03/2024", "2024-13-01", None],
)
def test_only_iso_dates_are_kept(reader, audit_date):
    reader["reply"] = stated(audit_date=audit_date)
    assert extract_certificate(certificate())["wasa_date"] == ""


def test_an_audit_spanning_days_is_dated_by_the_day_it_ended(reader):
    """Real certificates print 'Audit Date: 30th March 2026 to 3rd April 2026'."""
    reader["reply"] = stated(audit_date="2026-03-30 to 2026-04-03")

    assert extract_certificate(certificate())["wasa_date"] == "2026-04-03"


def test_a_certificate_that_states_no_expiry_leaves_it_for_the_form(reader):
    """The form derives a year from the audit date; an empty reply lets it."""
    reader["reply"] = stated(valid_until="")

    details = extract_certificate(certificate())

    assert details["wasa_valid_until"] == ""
    assert details["wasa_date"]


def test_a_future_audit_date_is_dropped(reader):
    tomorrow = timezone.localdate() + timedelta(days=1)
    reader["reply"] = stated(audit_date=tomorrow.isoformat())
    assert extract_certificate(certificate())["wasa_date"] == ""


def test_an_expiry_before_the_audit_is_dropped(reader):
    today = timezone.localdate()
    reader["reply"] = stated(
        audit_date=(today - timedelta(days=20)).isoformat(),
        valid_until=(today - timedelta(days=40)).isoformat(),
    )
    details = extract_certificate(certificate())
    assert details["wasa_valid_until"] == ""
    assert details["wasa_date"] == (today - timedelta(days=20)).isoformat()


@pytest.mark.parametrize("reply", ["not json at all", "[]", '{"agency": ', '"text"'])
def test_an_unreadable_reply_is_an_error(reader, reply):
    reader["reply"] = reply
    with pytest.raises(WasaExtractionError):
        extract_certificate(certificate())


def test_the_same_file_is_only_read_once(reader):
    reader["reply"] = stated()
    first = extract_certificate(certificate())
    assert extract_certificate(certificate(name="renamed.pdf")) == first
    assert len(reader["calls"]) == 1


def test_a_reading_that_proposed_nothing_is_not_kept(reader):
    """Nothing useful was learned, so the next attempt deserves its own look."""
    reader["reply"] = stated(agency="Unlisted", audit_date="", valid_until="")
    assert not any(extract_certificate(certificate()).values())

    reader["reply"] = stated()
    assert extract_certificate(certificate())["wasa_agency"] == AGENCY
    assert len(reader["calls"]) == 2  # noqa: PLR2004


def test_a_refusal_is_not_kept_either(reader):
    reader["reply"] = stated(is_certificate=False)
    for _ in range(2):
        with pytest.raises(WasaExtractionError):
            extract_certificate(certificate())
    assert len(reader["calls"]) == 2  # noqa: PLR2004


def test_publishing_a_missing_agency_corrects_a_reading_already_made(reader):
    """What is remembered is the reply, so the published list is applied anew."""
    reader["reply"] = stated(agency="Brand New Labs Pvt Ltd")
    assert extract_certificate(certificate())["wasa_agency"] == ""

    CertificationAgency.objects.create(
        program="abdm",
        name="Brand New Labs Pvt Ltd",
    )

    assert extract_certificate(certificate())["wasa_agency"] == "Brand New Labs Pvt Ltd"
    assert len(reader["calls"]) == 1


def test_choosing_the_same_file_again_reads_it_again(reader):
    """A second reading is the only thing a second look can offer."""
    reader["reply"] = stated(agency="Unlisted")
    first = extract_certificate(certificate())
    assert first["wasa_agency"] == ""

    reader["reply"] = stated()
    again = extract_certificate(certificate(), refresh=True)

    assert again["wasa_agency"] == AGENCY
    assert len(reader["calls"]) == 2  # noqa: PLR2004


def test_what_a_second_reading_found_is_what_is_remembered(reader):
    reader["reply"] = stated(agency="Unlisted")
    extract_certificate(certificate())
    reader["reply"] = stated()
    extract_certificate(certificate(), refresh=True)

    assert extract_certificate(certificate())["wasa_agency"] == AGENCY
    assert len(reader["calls"]) == 2  # noqa: PLR2004


def test_the_hook_is_off_without_a_model(settings):
    settings.WASA_EXTRACTION_MODEL = ""
    assert not wasa_extraction.is_enabled()
    with pytest.raises(WasaExtractionError):
        extract_certificate(certificate())


def test_the_field_only_offers_the_hook_when_it_is_configured(settings):
    html = render_to_string(
        "experiences/partials/form.html",
        {"form": ExitEvidenceForm()},
    )
    assert 'data-read-document="wasa_certificate"' in html
    assert "document-reader.js" in html
    settings.WASA_EXTRACTION_MODEL = ""
    silent = render_to_string(
        "experiences/partials/form.html",
        {"form": ExitEvidenceForm()},
    )
    assert "data-read-document=" not in silent


def test_the_hook_covers_the_section_while_it_reads(settings):
    html = render_to_string(
        "experiences/partials/form.html",
        {"form": ExitEvidenceForm()},
    )
    assert "data-read-document-overlay" in html
    assert "animate-spin" in html
    settings.WASA_EXTRACTION_MODEL = ""
    silent = render_to_string(
        "experiences/partials/form.html",
        {"form": ExitEvidenceForm()},
    )
    # The overlay is inert markup; only the script that shows it goes away.
    assert "data-read-document=" not in silent


@pytest.mark.parametrize("form_class", [WasaReviewForm, ExitEvidenceForm])
def test_the_certificate_is_asked_for_before_the_fields_it_fills(form_class):
    """Upload first, so the values appearing below it read as a consequence."""
    section = next(
        fields for title, fields in form_class.sections if title == "WASA audit"
    )
    upload = section.index("wasa_certificate")

    assert all(upload < section.index(name) for name in WASA_FIELDS)
    # Spanning the row keeps those fields underneath it rather than beside it.
    assert "wasa_certificate" in form_class.full_width_fields


def read_request(client, url, **extra):
    return client.post(
        url,
        {
            "intent": "read",
            "field": "wasa_certificate",
            "wasa_certificate": certificate(),
        }
        | extra,
    )


def test_the_certification_page_reads_a_chosen_certificate(
    reader,
    environment,
    client,
):
    reader["reply"] = stated()
    today = timezone.localdate()
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )

    response = read_request(client, url)

    assert response.status_code == OK
    assert response.json() == {
        "name": "wasa.pdf",
        "fields": {
            "wasa_agency": AGENCY,
            "wasa_date": (today - timedelta(days=20)).isoformat(),
            "wasa_valid_until": (today + timedelta(days=345)).isoformat(),
        },
    }


def test_the_browser_can_ask_for_the_certificate_to_be_read_again(
    reader,
    environment,
    client,
):
    reader["reply"] = stated(agency="Unlisted")
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )
    assert read_request(client, url).json()["fields"]["wasa_agency"] == ""

    reader["reply"] = stated()
    answered = read_request(client, url, refresh="1")

    assert answered.json()["fields"]["wasa_agency"] == AGENCY
    assert len(reader["calls"]) == 2  # noqa: PLR2004


def test_the_same_certificate_is_not_read_twice_unasked(reader, environment, client):
    reader["reply"] = stated()
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )

    assert read_request(client, url).json() == read_request(client, url).json()
    assert len(reader["calls"]) == 1


def test_a_milestone_page_reads_a_chosen_certificate(reader, environment, client):
    reader["reply"] = stated()
    assert milestone(environment, "m1")
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:track",
        args=[environment["workspace"].reference, "HIE-CM"],
    )

    response = read_request(client, url, milestone="m1")

    assert response.status_code == OK
    assert response.json()["fields"]["wasa_agency"] == AGENCY


def test_reading_needs_the_same_access_as_the_form(reader, environment, client):
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )
    assert read_request(client, url).status_code != OK
    client.force_login(environment["outsider"])
    assert read_request(client, url).status_code in {FORBIDDEN, NOT_FOUND}


@pytest.mark.parametrize(
    ("field", "upload"),
    [
        ("wasa_certificate", None),
        ("wasa_certificate", SimpleUploadedFile("a.pdf", b"not a pdf")),
        ("wasa_agency", SimpleUploadedFile("a.pdf", b"%PDF-1.4\n%%EOF")),
    ],
)
def test_reading_refuses_anything_the_form_would_not_accept(
    reader,
    environment,
    client,
    field,
    upload,
):
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )
    payload = {"intent": "read", "field": field}
    if upload is not None:
        payload[field] = upload

    assert client.post(url, payload).status_code == BAD_REQUEST


def test_reading_stops_paying_for_an_account_that_never_stops(
    settings,
    reader,
    environment,
    client,
):
    settings.EXPERIENCE_DOCUMENT_READ_HOURLY_LIMIT = 2
    reader["reply"] = stated()
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )

    codes = [
        client.post(
            url,
            {
                "intent": "read",
                "field": "wasa_certificate",
                "wasa_certificate": certificate(marker=str(index)),
            },
        ).status_code
        for index in range(3)
    ]

    assert codes == [OK, OK, TOO_MANY]
    assert len(reader["calls"]) == 2  # noqa: PLR2004


def test_reading_never_starts_a_review_or_stores_the_file(reader, environment, client):
    reader["reply"] = stated()
    product = environment["workspace"].product
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )
    before = product.review_items.count()

    assert read_request(client, url).status_code == OK

    assert product.review_items.count() == before
    assert not FormAttachment.objects.filter(field_key="wasa_certificate").exists()


@pytest.fixture
def provider(settings, monkeypatch):
    """The real provider wrapper, with LiteLLM itself standing in for Bedrock."""
    settings.WASA_EXTRACTION_MODEL = "bedrock/test-model"
    settings.BEDROCK_REGION_NAME = ""
    settings.BEDROCK_ACCESS_KEY_ID = ""
    settings.BEDROCK_SECRET_ACCESS_KEY = ""
    wasa_extraction._extraction_cache.clear()  # noqa: SLF001
    monkeypatch.setattr(wasa_extraction, "_completion", REAL_COMPLETION)
    monkeypatch.setattr(wasa_extraction, "_page_images", lambda content: [b"page-png"])
    return monkeypatch


def blank_pdf(page_count: int = 1) -> bytes:
    """A real PDF, so the renderer itself is exercised rather than mocked."""
    document = pypdfium2.PdfDocument.new()
    for _ in range(page_count):
        document.new_page(595, 842)
    buffer = BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


@pytest.fixture
def sent(provider):
    """The keyword arguments the wrapper hands to LiteLLM."""
    recorded = {}

    def completion(**kwargs):
        recorded.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=stated()))],
        )

    provider.setattr(litellm, "completion", completion)
    return recorded


def test_the_pages_are_sent_as_images_beside_the_instruction(sent):
    assert extract_certificate(certificate())["wasa_agency"] == AGENCY
    blocks = sent["messages"][0]["content"]
    assert sent["model"] == "bedrock/test-model"
    assert blocks[0]["type"] == "text"
    assert "JSON object" in blocks[0]["text"]
    assert [block["type"] for block in blocks[1:]] == ["image_url"]
    assert blocks[1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(b"page-png").decode()
    )


def test_every_page_up_to_the_cap_is_sent(provider, sent):
    provider.setattr(
        wasa_extraction,
        "_page_images",
        lambda content: [b"one", b"two", b"three"],
    )

    extract_certificate(certificate())

    assert len(sent["messages"][0]["content"]) == 4  # noqa: PLR2004


def test_no_temperature_is_offered_because_providers_disagree(sent):
    extract_certificate(certificate())

    assert "temperature" not in sent
    assert sent["drop_params"] is True


def test_pages_really_render_to_png(settings):
    settings.WASA_EXTRACTION_DPI = 150

    images = wasa_extraction._page_images(blank_pdf(2))  # noqa: SLF001

    assert len(images) == 2  # noqa: PLR2004
    assert all(image.startswith(b"\x89PNG") for image in images)


def test_only_the_first_pages_are_rendered(settings):
    settings.WASA_EXTRACTION_DPI = 72

    images = wasa_extraction._page_images(  # noqa: SLF001
        blank_pdf(wasa_extraction.MAX_DOCUMENT_PAGES + 3),
    )

    assert len(images) == wasa_extraction.MAX_DOCUMENT_PAGES


def test_the_render_resolution_is_configurable(settings):
    settings.WASA_EXTRACTION_DPI = 72
    coarse = wasa_extraction._page_images(blank_pdf())  # noqa: SLF001
    settings.WASA_EXTRACTION_DPI = 300
    fine = wasa_extraction._page_images(blank_pdf())  # noqa: SLF001

    assert len(fine[0]) > len(coarse[0])


@pytest.mark.parametrize("dpi", [0, 71, 401])
def test_an_implausible_resolution_switches_the_hook_off(settings, dpi):
    settings.WASA_EXTRACTION_DPI = dpi

    assert not wasa_extraction.is_enabled()


def test_the_page_quality_is_configurable(settings):
    settings.WASA_EXTRACTION_DPI = 150
    settings.WASA_EXTRACTION_JPEG_QUALITY = 20
    coarse = wasa_extraction._page_images(blank_pdf())  # noqa: SLF001
    settings.WASA_EXTRACTION_JPEG_QUALITY = 95
    fine = wasa_extraction._page_images(blank_pdf())  # noqa: SLF001

    assert len(fine[0]) > len(coarse[0])


@pytest.mark.parametrize("quality", [0, 96])
def test_an_implausible_page_quality_switches_the_hook_off(settings, quality):
    settings.WASA_EXTRACTION_JPEG_QUALITY = quality

    assert not wasa_extraction.is_enabled()


def test_a_page_too_large_to_send_is_refused(settings, monkeypatch):
    settings.WASA_EXTRACTION_DPI = 150
    monkeypatch.setattr(wasa_extraction, "MAX_IMAGE_BYTES", 10)

    with pytest.raises(WasaExtractionError) as failure:
        wasa_extraction._page_images(blank_pdf())  # noqa: SLF001

    assert not failure.value.retryable
    assert "too large" in str(failure.value)


def test_a_pdf_that_will_not_open_is_refused_as_permanently_unreadable(monkeypatch):
    def broken(_content):
        msg = "damaged file"
        raise ValueError(msg)

    monkeypatch.setattr("pypdfium2.PdfDocument", broken)

    with pytest.raises(WasaExtractionError) as failure:
        wasa_extraction._page_images(b"%PDF-1.4 broken")  # noqa: SLF001

    assert not failure.value.retryable
    assert "could not be opened" in str(failure.value)


def test_an_unreadable_document_answers_differently_from_an_outage(
    provider,
    environment,
    client,
):
    def unreadable(_content):
        msg = "This PDF could not be opened."
        raise WasaExtractionError(msg, retryable=False)

    provider.setattr(wasa_extraction, "_page_images", unreadable)
    client.force_login(environment["applicant"])
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )

    response = read_request(client, url)

    assert response.status_code == UNREADABLE
    assert response.json()["retryable"] is False
    assert "could not be opened" in response.json()["error"]


def test_bedrock_is_told_which_principal_to_use(settings, sent):
    settings.BEDROCK_REGION_NAME = " ap-south-1 "
    settings.BEDROCK_ACCESS_KEY_ID = "AKIAREADER"
    settings.BEDROCK_SECRET_ACCESS_KEY = "reader-secret"  # noqa: S105

    extract_certificate(certificate())

    assert sent["aws_region_name"] == "ap-south-1"
    assert sent["aws_access_key_id"] == "AKIAREADER"
    assert sent["aws_secret_access_key"] == "reader-secret"  # noqa: S105


def test_an_unset_credential_is_left_to_the_instance_role(settings, sent):
    settings.BEDROCK_REGION_NAME = "ap-south-1"

    extract_certificate(certificate())

    assert sent["aws_region_name"] == "ap-south-1"
    assert not [
        key for key in sent if key.startswith("aws_") and key != "aws_region_name"
    ]


def test_the_media_buckets_keys_are_never_offered_to_bedrock(settings, sent):
    """AWS_* belongs to storage; borrowing it would widen what the reader can do."""
    settings.AWS_ACCESS_KEY_ID = "AKIASTORAGE"
    settings.AWS_SECRET_ACCESS_KEY = "storage-secret"  # noqa: S105

    extract_certificate(certificate())

    assert "AKIASTORAGE" not in str(sent)
    assert not [key for key in sent if key.startswith("aws_")]


def test_a_model_outside_bedrock_is_sent_no_aws_arguments(settings, sent):
    settings.WASA_EXTRACTION_MODEL = "openai/gpt-5.6-luna"
    settings.BEDROCK_ACCESS_KEY_ID = "AKIAREADER"

    extract_certificate(certificate())

    assert not [key for key in sent if key.startswith("aws_")]


def test_endpoint_hides_the_provider_detail_of_a_failed_reading(
    provider,
    environment,
    client,
):
    def explode(**kwargs):
        msg = "bedrock said: <the whole certificate>"
        raise RuntimeError(msg)

    provider.setattr(litellm, "completion", explode)
    client.force_login(environment["applicant"])
    response = read_request(
        client,
        reverse(
            "experiences:product-certification",
            args=[environment["workspace"].reference],
        ),
    )
    assert response.status_code == UNAVAILABLE
    assert "bedrock" not in response.json()["error"]
