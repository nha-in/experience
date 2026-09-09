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

- To create a **normal user account**, just go to Sign Up and fill out the form. Once you submit it, you'll see a "Verify Your E-mail Address" page. Go to your console to see a simulated email verification message. Copy the link into your browser. Now the user's email should be verified and ready to go.

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

### GitHub Container Registry

The [Publish sandbox image workflow](.github/workflows/publish-image.yml) builds
`compose/production/django/Dockerfile` for `linux/amd64` and `linux/arm64`, then
publishes one multi-platform image to `ghcr.io/nha-in/sandbox`. It runs on
pushes to `testing_new` (the current default branch), pushes of `v*` tags, or
manually from **Actions → Publish sandbox image → Run workflow**. Branch pushes
that only change `docs/**` are skipped.

This is the only production image build-and-push workflow. Its Dockerfile builds
Tailwind, collects static assets, and verifies the generated CSS, fonts, and
manifests before publishing. No separate Tailwind or production-assets job is needed.

Published tags include:

- Default branch: `testing_new`, `latest`, and `latest-<run-number>`.
- Releases: the Git tag (for example, `v1.2.3`) and its semantic version (`1.2.3`).
  Tags without a hyphen also update `production-latest`; prerelease tags such as
  `v1.2.3-rc.1` do not update that alias.
- Every build: `sha-<full-commit-sha>`. Manual runs on other branches also publish
  a branch-name tag without updating `latest`.

The workflow uses the automatic `GITHUB_TOKEN` with `contents: read` and
`packages: write`; no additional build secrets are required. The repository must
be allowed to publish packages. If the package already exists, grant this
repository Actions access in the package settings. See the
[GitHub Container Registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
for authentication and package access.

After a successful default-branch build, pull the image with:

```bash
docker pull ghcr.io/nha-in/sandbox:latest
```

Authenticate to `ghcr.io` first if the package is private. The same image supports
the Django app (`/start`), Celery worker (`/start-celeryworker`), Celery beat
(`/start-celerybeat`), and Flower (`/start-flower`), using the runtime environment
from the production Compose configuration. This workflow publishes the app image;
deployment remains a separate step.
