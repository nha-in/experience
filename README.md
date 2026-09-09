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

- To create a **normal user account**, just go to Sign Up and fill out the form. Once you submit it, you'll see a "Verify Your E-mail Address" page. Native development prints the verification email in your console by default; if you enable SMTP, open the [Mailtrap Local inbox](#email-server) instead. Copy the verification link into your browser to verify the user's email.

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

[Mailtrap Local](https://github.com/mailtrap/mailtrap-local) captures development email in a local web inbox. It starts with the Docker development stack, or you can start just the mail service:

```bash
docker compose -f docker-compose.local.yml up -d mailtrap-local
```

For Django running inside Docker, local settings use SMTP at `mailtrap-local:3535`. A native Django process (`USE_DOCKER=no`) prints email to the console by default. To send its email to Mailtrap Local instead, start or restart the process with an explicit SMTP backend and the host address:

```bash
USE_DOCKER=no \
EMAIL_HOST=127.0.0.1 \
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend \
uv run python manage.py runserver
```

Local settings already use SMTP port `3535`. These email settings retain native development's disk uploads. Open [the Mailtrap Local inbox](http://127.0.0.1:3550) to read verification and other development emails. If you run a separate Celery worker for email tasks, restart it with the same email environment variables.

### Sentry

Sentry is an error logging aggregator service. You can sign up for a free account at <https://sentry.io/signup/?code=cookiecutter> or download and host it yourself.
The system is set up with reasonable defaults, including 404 logging and integration with the WSGI application.

You must set the DSN url in production.

## Deployment

The following details how to deploy this application.

### Docker

See detailed [cookiecutter-django Docker documentation](https://cookiecutter-django.readthedocs.io/en/latest/3-deployment/deployment-with-docker.html).
