# Dates read day first, as the legacy portal wrote them: 14/09/2026. These are
# Django's defaults for English, used by the admin and by any date rendered
# without a format. Templates spell out the same formats, since LocaleMiddleware
# can serve another language whose defaults differ.
DATE_FORMAT = "d/m/Y"
SHORT_DATE_FORMAT = "d/m/Y"
DATETIME_FORMAT = "d/m/Y, H:i"
SHORT_DATETIME_FORMAT = "d/m/Y, H:i"
