"""Every link into the ABDM documentation, built from one setting.

`ABDM_DOCS_URL` names the site a deployment reads its documentation from, so a
staging portal can send people to a staging site. A link resolves when it is
rendered rather than when this module is imported, which is what lets the
setting be changed and every link in the portal follow it.
"""

from django.conf import settings
from django.utils.functional import lazy


def _docs_page(path=""):
    return f"{settings.ABDM_DOCS_URL.rstrip('/')}{path}"


#: A page on the documentation site, from a path that starts with a slash.
#: Called with nothing, the site itself.
docs_page = lazy(_docs_page, str)
