# OHC Experience

Experience Builder

The ABDM Developer Sandbox portal is documented in
[the sandbox setup and architecture guide](docs/abdm_sandbox.md).

[![Built with Cookiecutter Django](https://img.shields.io/badge/built%20with-Cookiecutter%20Django-ff69b4.svg?logo=cookiecutter)](https://github.com/cookiecutter/cookiecutter-django/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

License: MIT

## Settings

Moved to [settings](https://cookiecutter-django.readthedocs.io/en/latest/1-getting-started/settings.html).

## Basic Commands

### Setting Up Your Users

- To create a **normal user account**, just go to Sign Up and fill out the form. Once you submit it, you'll see a "Confirm your email address" page. The local notification gateway logs the 6-digit code to your console instead of sending it; enter it on that page. A mobile number, if you gave one, gets its own code the same way.

- To create a **superuser account**, use this command:

      uv run python manage.py createsuperuser

For convenience, you can keep your normal user logged in on Chrome and your superuser logged in on Firefox (or similar), so that you can see how the site behaves for both kinds of users.

### Type checks

Running type checks with mypy:

    uv run mypy ohc_experience

### Test coverage

To run the tests, check your test coverage, and generate an HTML coverage report:

    uv run coverage run -m pytest
    uv run coverage html
    uv run open htmlcov/index.html

#### Running tests with pytest

    uv run pytest

### Live reloading and Sass CSS compilation

Moved to [Live reloading and SASS compilation](https://cookiecutter-django.readthedocs.io/en/latest/2-local-development/developing-locally.html#using-webpack-or-gulp).

### Celery

This app comes with Celery.

To run a celery worker:

```bash
cd ohc_experience
uv run celery -A config.celery_app worker -l info
```

Please note: For Celery's import magic to work, it is important _where_ the celery commands are run. If you are in the same folder with _manage.py_, you should be right.

To run [periodic tasks](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html), you'll need to start the celery beat scheduler service. You can start it as a standalone process:

```bash
cd ohc_experience
uv run celery -A config.celery_app beat
```

or you can embed the beat service inside a worker with the `-B` option (not recommended for production use):

```bash
cd ohc_experience
uv run celery -A config.celery_app worker -B -l info
```

### Email Server

In development, it is often nice to be able to see emails that are being sent from your application. For that reason local SMTP server [Mailtrap Local](https://github.com/mailtrap/mailtrap-local) with a web interface is available as docker container.

Container mailtrap-local will start automatically when you will run all docker containers.
Please check [cookiecutter-django Docker documentation](https://cookiecutter-django.readthedocs.io/en/latest/2-local-development/developing-locally-docker.html) for more details how to start all containers.

With Mailtrap Local running, to view messages that are sent by your application, open your browser and go to `http://127.0.0.1:3550`

### Sentry

Sentry is an error logging aggregator service. You can sign up for a free account at <https://sentry.io/signup/?code=cookiecutter> or download and host it yourself.
The system is set up with reasonable defaults, including 404 logging and integration with the WSGI application.

You must set the DSN url in production.

## Deployment

The following details how to deploy this application.

### Docker

See detailed [cookiecutter-django Docker documentation](https://cookiecutter-django.readthedocs.io/en/latest/3-deployment/deployment-with-docker.html).

### Production deploys

[`deploy-prod.yml`](.github/workflows/deploy-prod.yml) builds
`compose/production/django/Dockerfile`, pushes it to Amazon ECR, and rolls the
new image out to the production ECS services. It runs on pushes of `v*` tags, or
manually from **Actions → Deploy production → Run workflow**.

Both jobs run in the `production` GitHub Environment, so environment protection
rules (required reviewers, wait timers, branch restrictions) gate the image push
as well as the rollout. In-flight runs are never cancelled by a newer one.

The image is `linux/arm64` only, built natively on an `ubuntu-24.04-arm` runner,
so the production ECS task definitions must use the `ARM64` runtime platform. The
Dockerfile builds Tailwind, collects static assets, and verifies the generated
CSS, fonts, and manifests before publishing, so no separate asset job is needed.

Published tags:

- The Git tag (for example, `v1.2.3`) and its semantic version (`1.2.3`).
- `sha-<full-commit-sha>` on every run.
- `production-latest`, only for release tags without a hyphen — a prerelease such
  as `v1.2.3-rc.1` does not move that alias.

The rollout itself pins the image by **digest** (`<repo>@sha256:…`) rather than by
tag, so the exact artifact that was built is the one that ships, regardless of
any later tag reassignment.

#### Rollback

No task definition ever references a moving tag. A mutable tag like `latest` or
`production-latest` would make earlier revisions meaningless — every revision
would resolve to whatever that tag points at *now*, so rolling back would
redeploy the broken image. Pinning by digest means each ECS task definition
revision is a permanent, exact record of what shipped.

A commit-SHA tag such as `sha-<commit>` is not sufficient on its own either: a
re-run of the workflow on the same commit re-pushes that tag to a newly built
image, silently changing what older revisions point to. The digest cannot be
reassigned.

To roll back, redeploy the previous revision — no rebuild required:

```bash
aws ecs list-task-definitions --family-prefix "$ECS_PREFIX-api" \
  --sort DESC --max-items 5

aws ecs update-service --cluster "$ECS_CLUSTER" --service "$ECS_PREFIX-api" \
  --task-definition "$ECS_PREFIX-api:<revision>" --force-new-deployment
```

Repeat per service (`-api`, `-celeryworker`, `-celerybeat`). The `sha-<commit>`
and semver tags are still published for traceability — they map a running digest
back to source — they are just not what the services resolve at deploy time.

#### Configuration

Nothing environment-specific is hard-coded. Set these on the `production`
environment under **Settings → Environments → production**.

Variables:

| Variable | Purpose | Default |
|----------|---------|---------|
| `AWS_REGION` | Region for the ECR push and the ECS deploy | `ap-south-1` |
| `ECR_REPOSITORY` | ECR repository **name**, not the full URI | none — must be set |
| `ECS_CLUSTER` | Production ECS cluster name | none — must be set |
| `ECS_PREFIX` | Prefix for the `-api`, `-celeryworker`, `-celerybeat` services | none — must be set |

Secrets:

| Secret | Used by | Purpose |
|--------|---------|---------|
| `ECR_AWS_ACCESS_KEY_ID` | `build` | Push to ECR |
| `ECR_AWS_SECRET_ACCESS_KEY` | `build` | Push to ECR |
| `AWS_ACCESS_KEY_ID` | `deploy` | Register and roll out ECS task definitions |
| `AWS_SECRET_ACCESS_KEY` | `deploy` | Register and roll out ECS task definitions |

Push and deploy credentials are deliberately separate, so the ECR key can be
scoped to the registry alone. The registry host is resolved at runtime from
`aws-actions/amazon-ecr-login`, so the AWS account ID never appears in the repo.

The ECR credentials need `ecr:GetAuthorizationToken` plus the usual push actions
(`ecr:BatchCheckLayerAvailability`, `ecr:InitiateLayerUpload`,
`ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:PutImage`,
`ecr:BatchGetImage`) on the repository. Two registry settings matter:

- The repository must already exist; ECR does not create it on push.
- Tag mutability must stay **mutable**, because `production-latest` is reassigned
  on every release.

ECS pulls through its task execution role, which needs ECR read access. Make sure
the production task definitions carry no `repositoryCredentials` block — when one
is present the agent uses those static credentials instead of the execution role
and the ECR pull fails.

Pull a published image with:

```bash
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"
docker pull "$REGISTRY/$ECR_REPOSITORY:production-latest"
```

where `REGISTRY` is `<account-id>.dkr.ecr.$AWS_REGION.amazonaws.com`.

The same image supports the Django app (`/start`), Celery worker
(`/start-celeryworker`), Celery beat (`/start-celerybeat`), and Flower
(`/start-flower`), using the runtime environment from the production Compose
configuration.
