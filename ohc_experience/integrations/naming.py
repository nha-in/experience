"""Names we derive rather than receive, shared by an adapter and its local stand-in."""

from __future__ import annotations

#: What a product is called in WSO2. Create-or-lookup matches on this name, so a
#: re-run finds the application it made last time. WSO2 rejects slashes and spaces.
APP_NAME_TEMPLATE = "sbx-{reference}"
