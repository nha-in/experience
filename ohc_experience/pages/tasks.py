import logging

from celery import shared_task

from .abdm_dashboard import DashboardUnavailableError
from .abdm_dashboard import refresh_figures

logger = logging.getLogger(__name__)

# A failed daily fetch is tried again after 5 minutes, 30 minutes and 2 hours.
RETRY_DELAYS = (5 * 60, 30 * 60, 2 * 60 * 60)


@shared_task(bind=True, max_retries=len(RETRY_DELAYS))
def refresh_abdm_dashboard_figures(task):
    """Fetch the landing page's ABDM figures; a failure keeps the last good ones."""
    try:
        refresh_figures()
    except DashboardUnavailableError as error:
        if task.request.retries < len(RETRY_DELAYS):
            raise task.retry(countdown=RETRY_DELAYS[task.request.retries]) from error
        logger.exception("ABDM dashboard figures were not refreshed")
