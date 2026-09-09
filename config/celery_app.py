import os

from celery import Celery
from celery.signals import before_task_publish
from celery.signals import setup_logging
from celery.signals import task_prerun

from ohc_experience.integrations.correlation import get_correlation_id
from ohc_experience.integrations.correlation import set_correlation_id

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("ohc_experience")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")


@setup_logging.connect
def config_loggers(*args, **kwargs):
    from logging.config import dictConfig  # noqa: PLC0415

    from django.conf import settings  # noqa: PLC0415

    dictConfig(settings.LOGGING)


#: Header carrying our correlation id across the broker.
#:
#: Deliberately not `correlation_id`: Celery's own `Context.correlation_id` is
#: the task id, and `Context._get_custom_headers` drops every name it already
#: owns — so a header called that would be swallowed and then overwritten.
CORRELATION_HEADER = "experience_correlation_id"


@before_task_publish.connect
def attach_correlation_id(headers=None, **_kwargs) -> None:
    """Stamp the publishing context's id onto the message.

    A `ContextVar` does not cross the broker, so the id has to travel as data.
    Doing it here rather than as a task argument is what makes it automatic:
    every task carries it, including the ones nobody remembered to thread it
    through.
    """
    if headers is not None:
        headers.setdefault(CORRELATION_HEADER, get_correlation_id())


@task_prerun.connect
def bind_correlation_id(task=None, **_kwargs) -> None:
    """Rebind the id before the task body runs.

    A worker is long-lived, so the `ContextVar` arrives holding whatever the
    previous task left in it — this is an overwrite, not an initialisation.

    No header means the message was never published: eager mode calls the task
    inline, where the ambient context is already the caller's. Leave it.
    """
    value = getattr(getattr(task, "request", None), CORRELATION_HEADER, None)
    if value:
        set_correlation_id(value)


# Load task modules from all registered Django app configs.
app.autodiscover_tasks()
