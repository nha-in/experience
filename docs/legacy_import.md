# Legacy sandbox import

`ohc_experience.legacy_import` imports the legacy ABDM sandbox database into an empty
experience database. In production it runs once, by hand, inside an ECS task during the
cutover, writing straight to the production database and bucket. A local run against
the same dump rehearses it and produces the review lists.

## What comes across

- Accounts with an approved or still-open registration. Rejected-only accounts stay in
  the legacy dump.
- One organisation per company name under an email, merged across emails when the names
  match (exactly, or apart from legal suffixes) and something says it is one company. A
  shared login, GSTIN, website, company email domain or address says so on its own; a
  shared PIN, mobile, exit contact or product name fits thousands of companies and counts
  only alongside a second of its kind. A GSTIN outranks all of it: two registrations
  whose GSTINs share no PAN are two companies however alike they look, and stay apart
  unless one login holds both, while the same company registered in several states
  repeats its PAN and still merges. Each refusal on a GSTIN, and each organisation left
  holding several, is listed for review.
  Individuals become sole-proprietor organisations. The entity type comes from the
  legacy words; the ones with no form choice are listed for review and left blank, and
  the organisation form record keeps legacy's words in `metadata["legacy"]`. The
  description is the newest exit's summary, else the registration's intent, else its
  business description, and only then the organisation's name. The verification
  document is a GST certificate from an exit where there is one, else the certificate
  the old portal took with the registration, else another exit document. The logo is
  the newest logo link an exit gave.
- One product per registration row, recorded as registered, with the reviewer's
  remark as the registration's note, followed by each HTC member's remark as "HTC: …",
  leaving out any that only say "ok", "approved" or "N/A". Legacy's application ID goes in
  `metadata["legacy_application_id"]`. Approved rows keep their
  Keycloak client and WSO2 application: legacy named both after the client id and used it
  as the Keycloak internal id, so the ledger stores the client id for each, and the
  secret is encrypted with `EXPERIENCE_CREDENTIAL_KEY`.
  A product already in production keeps the date legacy recorded its client id. Legacy's
  bridge URL is the callback URL under another name, so it comes across as the callback;
  the import registers no bridge, because a bridge row now means the integrator gave us
  an endpoint. The sweep after the import registers one for each imported callback.
- Solution types onto the form's list. Legacy's government variants keep their type and
  add Government programme, and payers become Insurance. End user application (EUA) and
  Providers are no longer offered, so they tick Other and are named in its box, next to
  anything legacy kept under Others. Legacy asked for a solution type only from 2024, so
  an earlier product ticks Other and says the sandbox never recorded one. A registration
  holding the form's entire list of options instead of an answer reads as unanswered too:
  legacy asked the question on the exit readiness form and saved the answer back onto the
  registration, so an account that never reached that form kept whatever the screen left
  behind.
- A milestone request per milestone a product declared or exited, with one submission
  per exit and milestone, the exit's files, and legacy's decision. Legacy had one PHR
  milestone for the whole application, so it fills all three PHR phases, P1 to P3, with
  the same evidence and the same decision; its health locker becomes P4. A product whose
  account never declared milestones takes the tracks it picked at registration
  (`sd_login.field_detail`), under each era's names: M1 to M4 as named, the 2020–23
  Health Info Provider and User as M2 and M3, PHR App as P1 to P3, Health Locker as P4,
  Providers and Payers as NHCX, HSPA and EUA as UHI. Health Repository Provider has no
  track. A registration holding its era's entire option list is read as unanswered, as
  with solution types.
- ABDM and PHR cannot be applied for together, so an account that submitted work in both
  becomes two products: the ABDM one keeps legacy's client, production ID and
  registration, and the PHR one takes its name with "(PHR)" after it and is provisioned
  with the rest after the import. Where only one of the two was ever submitted, the other
  track's declarations are dropped; where neither was, the longer of the two is kept. A
  check refuses a run that leaves any product holding both.
- Where an exit covering several milestones carried a file for each, named "M1 FT
  report", "M2 FT report" and so on, each milestone takes the file named for it;
  otherwise the newest. Older uploads and the same file under another name stay out.
- WASA validity as approved WASA reviews, UHI requests as recorded participation, NHCX
  exit decisions on the NHCX request. Legacy dated WASA once per account, not per exit,
  so those dates go on the WASA review alone: each milestone request keeps the agency and
  certificate its own exit carried, with no audit or expiry date. The review takes the
  newest certificate the account uploaded, whatever date that certificate carries: legacy
  approved the record once, and NHA reviews whatever is still pending. A review with no
  certificate at all keeps its dates and grants no approval. An approved milestone's
  certificate becomes a WASA approval of its own, as
  the portal's approval of a milestone does. Every WASA runs a year, which NHA
  confirmed, and an audit comes before its
  certificate is uploaded. So a certificate uploaded over a year before the run takes the
  latest expiry it could have had, a year after that upload, and reads as expired; a
  newer one stays undated, showing "Expiry needs verification". Neither opens DHIS, and
  each record says why in `metadata["legacy_note"]`.
- Staff: legacy named its roles in `mst_role` rather than fixing them in code, so the
  import reads them by name. Super Admin becomes a superuser, HTC reviews everything, a
  name carrying a track reviews that track, a `view` name reads only, and a name the
  portal cannot place is reported in `skipped.csv` instead of guessed at.

Bcrypt passwords keep working and are rehashed on first sign-in, once production has
the `bcrypt` package and `BCryptPasswordHasher`. Accounts still on legacy MD5 hashes,
and staff without an email login, reset their password.

Legacy's payer category has no field in the portal and is dropped; the column is
near-empty anyway. Mobile numbers come across unverified, as legacy never checked them:
an imported integrator confirms the number by OTP on first sign-in, and can correct it
on that screen.

## What NHA still has to decide

The import takes the cautious reading in each of these. None of them blocks the import,
and each is counted in `legacy-import-report/` so it can be revisited.

- **Registrations holding every solution type.** Legacy asked for the solution type on
  the sandbox exit readiness form and saved the answer back onto the registration, so an
  account that never reached that form kept whatever the registration screen left behind:
  the form's own option list. The import reads an exact match for one era's full list as
  unanswered, and counts it as `solution type was the whole option list`. A row that
  unticked even one option is read as the deliberate answer it looks like. This matters
  beyond tidiness, because DHIS gates the production handoff partly on the solution types
  the registration was approved for.
- **End user application (EUA) and Providers.** Settled: the consolidated list is NHA's
  own, so both tick Other and are named in its box rather than being folded into
  HealthTech or HMIS, which would assert a type the integrator never chose. Counted as
  `solution type moved to Other: ...`.
- **Partnership firms.** Settled by adding the choice. Legacy offered it in a dropdown
  alongside Company, Proprietorship Firm, LLP, Government, Trust and Society, and it was
  the only one of those with no counterpart in the portal, so `OrganisationForm` gained a
  `partnership` choice and the import maps onto it. Society is filed under Trust. What is
  left in `review-entity-types-with-no-form-choice.csv` is the vaguer wording, such as
  "Organization" or "Institution", which names no legal form the portal could match.
- **Reject reasons.** Done, now that the dropdown is built. What legacy called a send-back
  the portal now calls a rejection, and the import files legacy's status 4 under it.
  Legacy recorded a remark, not a reason code, so the import reads each remark for the
  reason it names, in the reviewer's own order of mention, and files the rejection under
  that code. A remark naming none, such as "Please apply again", keeps its words as the
  note and imports with a blank `decision_reason`, which is how the portal says the note
  carries the reason. The note drops the reason a remark opens with, so "Incorrect
  document" alone leaves no note and "Incorrect document,Incomplete integration" leaves
  "Incomplete integration". FT and WASA share one option for a missing report and one for
  an agency that is not empaneled, as the portal does. `target_drift` also refuses a run
  whose codes the portal has since renamed.

## What each run reports

Every legacy row is either imported or named in `legacy-import-report/skipped.csv`;
nothing is silently lost. A run writes that folder next to the counts it printed:

- `counts.csv`, every measure the run kept: users, organisations, products,
  applications, submissions, attachments, credentials, and each reading it had to make,
  such as a solution type moved to Other or a registration whose tracks were its era's
  whole list.
- `mapping.csv`, legacy key to experience row, so any record can be traced back.
- `review-*.csv`, the lists a person should look at: merged organisations, organisations
  kept apart on a GSTIN or left holding several, entity types with no form choice,
  duplicate client ids, approved exits without files, WASA rows without a certificate,
  and organisations that failed to import.

Compare `counts.csv` between two runs of the same dump to see that nothing moved, and
between dumps to see what legacy added since.

## What stays behind

Dropped because the portal keeps it elsewhere or has no use for it: `sd_doc_type` and
the `mst_module` and `mst_privilege` lookups, whose meanings are written into this app
(`mst_role` is read, for the names its ids stand for); `std_data`, a table of STD telephone codes; `ci_sessions`, CodeIgniter
session rows; the `sd_login_bk*` backups, stale copies of `sd_login`; `password`, a
password history whose newest hash already matches `sd_login`; and the empty `concern`,
`security_audit_trail` and `awsdms_apply_exceptions`.

Dropped as history the portal writes for itself from now on: `notification_audit`, the
emails and texts legacy already sent, `log` and `audit_log`, and `upcoming_session`.

Also considered and rejected: **`sd_login.dhis_solution_type`**, a second solution-type
column the import does not read. It looks like the answer to the question above, but it
belongs to legacy's DHIS registration screen, which gates its own solution types on
milestones, and not to the question the registration asked. Reading it would overwrite
the registration's own answer, and it leaves the unanswered products no better off.

Three things are genuinely left behind and are worth a decision:

- **`sd_hiu`.** Legacy's HIU service kept its own client id per registration. A product
  holds one Keycloak client, so carrying a second one needs either a second product or a
  change to `ProvisionedResource`.
- **`active_integrator`.** An NHA-curated list of the integrators considered active,
  keyed on `application_id`. It is not derivable from anything imported, and the portal
  has no equivalent flag.
- **Who approved what.** Legacy recorded the decision but not the reviewer, so every
  approved review item imports with no `decided_by`. A remark, where the reviewer left
  one, becomes the note, and a rejection also carries the reason code read out of it.

## Rehearse

Run it locally against the same dump, on a database other than `ohc_experience_demo`.
Any Fernet key will do for a rehearsal.

```bash
export DATABASE_URL=postgres:///ohc_experience_import USE_DOCKER=no \
  LEGACY_DATABASE_URL=postgres:///sandbox_legacy \
  EXPERIENCE_CREDENTIAL_KEY=...
dropdb --if-exists ohc_experience_import && createdb ohc_experience_import
rm -rf ohc_experience/media/experience-attachments/legacy legacy-import-report
uv run python manage.py import_legacy
```

`import_legacy` migrates, imports and runs the consistency checks; `check_legacy_import`
runs the checks again. It exits non-zero if a check fails or an organisation failed to
import. For a trial run, `--limit` imports the first N organisations and `--no-files` leaves
the attachments out.

The import holds its own tables of legacy spellings, so a change to the portal's
milestones, solution types or entity types can leave it writing values the forms no
longer offer. Before it touches the database a run compares those tables against the
portal and refuses if the portal has moved past them, naming what to decide; after the
import a check reads back every submission for an answer under a field the forms have
since dropped. `test_checks.py` makes the same comparison, so the suite goes red as soon
as the portal changes under the import.

Files go to the default storage: the local media folder on a laptop, the bucket in
production. `legacy-import-report/` holds `counts.csv`, `skipped.csv`, `mapping.csv`
(legacy key to experience row) and `review-*.csv` lists: merged organisations,
organisations kept apart on a GSTIN or left holding several, entity types with no form
choice, duplicate client ids, approved exits without files, WASA rows with no
certificate, and organisations that failed to import (rolled back; the rest still
import).

To see the portal as it will look after steps 4 and 5 of the production run, run them
in `manage.py shell` after `from config.celery_app import app;
app.conf.task_always_eager = True`, with `REDIS_URL` on a local Redis. The local
stand-ins accept every bridge and issue every client.

## A newer legacy dump

Restore it under its own database name, point `LEGACY_DATABASE_URL` at it and run the
block above again; each run starts from an empty database. Compare
`legacy-import-report/counts.csv` with the previous run before moving on. Once
production has real sign-ins, running it again there is refused, because the database
is no longer empty.

Exits come from `sd_exit`, plus the production-approved rows of `sd_exit_live` — a
second exit table legacy's own code never names — that `sd_exit` does not hold. A run
counts what the second table contributed.

Legacy built a per-submission WASA history (ticket SDFI-7965, the "Update WASA Details"
page) on its `aws-sandbox` branch: `wasa_dhis_initiation_details` gains a row per
submission with the agency, admin status and remark, and `sd_exit_docs` gains
`wasa_initiation_id` linking each certificate to its row. A dump taken before that
release carries none of it.
A dump that has it is refused until the import maps it, since those rows would give
each certificate its real dates.

## Run it in production

1. Deploy a release with this app in it. It already carries the `bcrypt` package and
   `BCryptPasswordHasher`, which the imported passwords need.
2. Restore the legacy dump into a database the ECS task can reach, for example a
   `sandbox_legacy` database on the production Postgres instance.
3. Start a one-off task from the API task definition, or exec into a running one, and
   run the import there. It uses production's `EXPERIENCE_CREDENTIAL_KEY` from the task,
   so the secrets are encrypted under the key production reads them with, and the checks
   prove it:

   ```bash
   LEGACY_DATABASE_URL=postgres://... python manage.py import_legacy
   ```

   The counts and checks print to the task's log. The report folder stays in the
   container and goes with it; the rehearsal on the same dump gives the same lists.
4. Register a bridge for every callback that came across. The task is inside the VPC,
   where HIE-CM is reachable:

   ```bash
   python manage.py shell -c "
   from ohc_experience.experiences.models import ProductCredential
   from ohc_experience.integrations.tasks import sync_bridge
   rows = ProductCredential.objects.filter(status='active').exclude(callback_url='')
   print(rows.count(), 'to register')
   for credential in rows:
       sync_bridge.delay(credential.product_id)
   "
   ```

5. In `manage.py shell`, set the site domain, which password reset links use, and start
   provisioning for the products with no client yet: registrations still open in legacy,
   and any legacy approved without ever issuing a client:

   ```python
   from django.contrib.sites.models import Site
   from ohc_experience.experiences.models import Product
   from ohc_experience.integrations.selectors import awaiting_provisioning
   from ohc_experience.integrations.services import start_provisioning

   Site.objects.filter(pk=1).update(domain="PRODUCTION_HOST")
   for product in Product.objects.all():
       if awaiting_provisioning(product):
           start_provisioning(product, started_by=None)
   ```

6. Spot-check one imported product: reveal its secret, then obtain a token through the
   gateway.
