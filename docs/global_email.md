# Global Email Notification integration

Production email uses ABDM's internal Global Notification Service.
A Django mail backend validates and writes outgoing messages to the
existing `experiences.Notification` outbox. Celery submits those messages through
a custom Anymail HTTP backend. Organisation invitations, workflow notifications
and the notice sent when a reset is asked for an address with no account use the
same delivery job. Web requests do not contact the gateway. Local development
keeps console mail.

Email and mobile verification codes and the password reset OTP do not use this
outbox. A code has to reach the person while they wait, so the web request posts
it to notification-app's `/internal/v3/notification/message` endpoint itself,
using approved templates; see the verification codes section of
`abdm-experience-deployment-variables.md`.

Application code uses Django's `send_mail()` or `EmailMessage.send()` as usual.
The backends implement `BaseEmailBackend.send_messages()`, and allauth's standard
`render_mail()` hook supplies the account email's template ID.

This implements the **Global Email Notification API** document supplied on
10 September 2026, pages 1–3, and the legacy portal's `NotificationFClient`,
`EmailQueueRequest`, `NotificationServiceImpl` and `OtpServiceImpl` contracts.
The documented infrastructure forwards email from the EKS notification service
to the notification service on the TCL VM, then to NIC through `relay.nic.in`.
The caller does not need NIC SMTP credentials.

## Delivery contract

The configured URL is the **complete send endpoint**, including
`/internal/v3/notification/email/send`. The backend performs one JSON `POST` per
message, for one primary recipient and optional CC recipients.

| Field | Source |
| --- | --- |
| `REQUEST-ID` header | Persisted outbox UUID, reused for retries |
| `TIMESTAMP` header | Current UTC ISO 8601 time with milliseconds and `Z` |
| `requestId` | Same UUID as the header |
| `timestamp` | Same instant as the header, in integer epoch milliseconds |
| `origin` | Configured logical origin, default `abha` |
| `sender` | Configured gateway sender identifier, default `NHASMS` |
| `contentType` | `info` by default; explicit `otp` supported |
| `receiver` | Primary recipient email address |
| `templateId` | Explicitly configured approved template ID, sent as a string |
| `subject`, `content` | Already-rendered Django subject and plain-text body |
| `ccRecipients` | List of email addresses, or `null` |

There is no template lookup call. Django renders the email; the configured ID
identifies the corresponding approved gateway template. The document does not
define gateway enforcement of template/body matching. Obtain valid IDs and
approved content before enabling delivery; the legacy OTP ID `100001` is not
assumed to authorize all portal emails.

The backend requires HTTP 200 and JSON `status: "SUCCESS"`. If the response
includes request ID, receiver or template ID, those must match the request.
Numeric response template IDs are accepted, as shown in the document. A gateway
transaction ID is exposed through `message.anymail_status.message_id`, falling
back to the request UUID when the gateway returns `null`.

Anymail reports `queued`, meaning accepted by the gateway, not confirmed inbox
delivery. The outbox's existing `sent_at` field records this acceptance time.
`provider_message_id` retains the gateway/correlation identifier. No delivery
receipt or webhook is assumed.

## Configuration

Production defaults to `QueuedGlobalEmailBackend`. Configure the gateway for
**both the web process and the Celery worker** before deployment:

```dotenv
GLOBAL_EMAIL_API_URL=http://notificationapp-svc.global-services.svc.cluster.local:9102/internal/v3/notification/email/send
GLOBAL_EMAIL_ORIGIN=abha
GLOBAL_EMAIL_SENDER=NHASMS
DJANGO_EMAIL_TIMEOUT=5
```

The URL above is the document's EKS service address. It requires the appropriate
cluster DNS/network access. Use the deployment team's approved address when
running elsewhere. The document specifies no authentication header; none is
invented by this adapter. Network access and any deployment-side authentication
requirements still need confirmation in that environment.

Configure template IDs using `GLOBAL_EMAIL_TEMPLATE_IDS`, a JSON object whose
keys are message purposes and whose values are approved template ID strings:

| Key | Email |
| --- | --- |
| `notification` | General workflow, review, support and event notifications |
| `support_ticket` | Support ticket thread entries sent to the support inbox |
| `organisation_invitation` | Organisation membership invitation |
| `account/email/password_reset_key` | Password reset link |
| Other allauth template prefixes | Corresponding allauth account notices |

`GLOBAL_EMAIL_TEMPLATE_ID` is an optional fallback for unmapped purposes. Leave
it empty if each purpose requires its own approved template. A missing ID fails
validation rather than silently using a legacy sample ID. A message's explicit
Anymail `template_id` takes precedence over the mapping and fallback. Anymail
send defaults are also honored before the global fallback. Queued messages
retain their selected ID through retries.

The underlying direct transport is
`ohc_experience.core.mail.backends.GlobalEmailBackend`. The queue worker selects
it automatically, so it never puts a message back into its own queue.

The gateway settings populate Anymail's `ANYMAIL` dictionary. HTTP calls use
Django's `EMAIL_TIMEOUT` (`DJANGO_EMAIL_TIMEOUT` in the environment), unless an
explicit Anymail timeout override is supplied. The project already pins
`django-anymail`; no additional dependency is needed.

## Running the job

Apply migrations before deploying the new web and worker code:

```sh
uv run python manage.py migrate
```

Run the normal Celery worker and scheduler with the deployment's settings:

```sh
uv run celery -A config.celery_app worker --loglevel=info
uv run celery -A config.celery_app beat --loglevel=info
```

The existing `sandbox-notifications` schedule runs every 60 seconds. Each run
processes up to 20 due rows and starts no further sends after 30 seconds. Keep
the HTTP timeout below the worker's 60-second soft task limit. The default is
5 seconds per connection/read operation.

Queue inserts participate in the account/invitation request's database
transaction. A rolled-back request does not leave an email queued. The worker
locks each selected row with `skip_locked`, preventing competing workers from
submitting it concurrently.

## Failures and operations

- A backend return count of zero does not mark an email as sent.
- Missing or invalid gateway/template configuration stops the batch and leaves
  pending rows unchanged. Fix the configuration and let the next scheduled run
  resume delivery.
- Connection establishment timeouts and HTTP 429 are retried, with delays of
  60, 120, 240 and 480 seconds, up to five total attempts.
- Read timeouts, connection loss, proxy/server errors, invalid responses and
  gateway rejections are held with `failed_at` set. These outcomes may be
  ambiguous or require a configuration/content correction, so the job does
  not automatically resend them.
- For other existing mail providers, failures retain the five-attempt policy,
  now with backoff instead of immediate repeat attempts.
- `last_error` contains a controlled error code or exception class name, never
  the email body or raw gateway error. Anymail API debug dumping is disabled
  for this transport. The original email content remains in the restricted
  outbox for delivery; it must not be copied into application logs.

Superusers can inspect notifications in Django admin, including acceptance and
failure times, next attempt, request UUID and provider message ID. For uncertain
outcomes, reconcile the request UUID with the notification service before
manually requeueing. No automatic bulk resend is provided.

The stable UUID is for correlation. The supplied API does **not** promise
idempotency or deduplication. A worker crash after gateway acceptance but before
the database commit can still lead to a duplicate on a subsequent run. An
end-to-end exactly-once guarantee requires support from the gateway.

## Supported message features

The documented API supports a single primary recipient and optional CCs. BCC,
reply-to, attachments, arbitrary email headers and unsupported Anymail options
are rejected before sending. For Django/allauth multipart emails, the rendered
plain-text body is sent and the optional HTML alternative is omitted. HTML-only
messages are rejected because this API documents no MIME/content-format field.
The service determines the actual From address; Django `from_email` does not
override `NHASMS` or the configured gateway sender.

## Verification

The automated tests prepare actual HTTP request bodies through Anymail/Requests
and mock the HTTP adapter. They cover the documented JSON response, UTC/epoch
timestamps, CC, template overrides, failures, no redirects, safe errors, queue
rollback, no network access during enqueue, retry timing, stable IDs and worker
delivery without recursive enqueueing. Migration tests cover existing rows.

```sh
DATABASE_URL=postgres:///ohc_experience_port REDIS_URL=redis://localhost:6379/0 \
  uv run pytest -q ohc_experience/core/tests \
  ohc_experience/experiences/tests/test_notifications.py
```

Shell development keeps console mail and tests use Django's in-memory backend.
No live gateway request is made by these tests. Actual
network reachability, approved template acceptance and NIC delivery must be
verified from the intended deployment environment before rollout.

Anymail's [Django email integration](https://anymail.dev/en/stable/sending/)
and [template ID interface](https://anymail.dev/en/stable/sending/templates.html)
are reused; the custom adapter deliberately supplies rendered content because
that is what the Global Email API requires.
