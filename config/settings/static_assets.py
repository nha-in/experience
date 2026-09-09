"""Asset settings shared by the production runtime and the image build."""

from .base import STATIC_URL
from .base import env

STATICFILES_STORAGE_BACKEND = "whitenoise.storage.CompressedManifestStaticFilesStorage"

COMPRESS_ENABLED = env.bool("COMPRESS_ENABLED", default=True)
COMPRESS_URL = STATIC_URL
COMPRESS_OFFLINE = True
COMPRESS_FILTERS = {
    "css": [
        "compressor.filters.css_default.CssAbsoluteFilter",
        "compressor.filters.cssmin.rCSSMinFilter",
    ],
    "js": ["compressor.filters.jsmin.JSMinFilter"],
}
