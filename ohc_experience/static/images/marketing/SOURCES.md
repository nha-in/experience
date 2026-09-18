# Marketing image sources

Retrieved on 2026-09-17. These files are hosted locally so the page does not depend
on third-party image requests. SVG artwork is preserved as published by the source.

| Local file | Source page | Original asset |
| --- | --- | --- |
| `mohfw-logo.png` | [Kerala State Health Agency IEC guidebook, page 12](https://sha.kerala.gov.in/wp-content/uploads/2020/07/IEC-Guidebook-110119.pdf#page=12) | Native 857 × 429 image extracted losslessly from PDF image object 95 using `pdfimages`; no cropping, redrawing, or upscaling |
| `meity-logo.svg` | [Digital India](https://www.digitalindia.gov.in/) | [Official header SVG](https://www.digitalindia.gov.in/wp-content/themes/di-child/assets/images/anthem2.svg) |
| `digital-india-logo.svg` | [Digital India](https://www.digitalindia.gov.in/) | [Official SVG](https://www.digitalindia.gov.in/wp-content/themes/di-child/assets/images/digital-india.svg) |
| `india-gov-logo.svg` | [National Informatics Centre](https://www.nic.in/) | [Official SVG](https://cdnbbsr.s3waas.gov.in/s3dcf6070a4ab7f3afbfd2809173e0824b/uploads/2024/09/20241009722649360.svg) |

The India.gov.in asset uses the updated navy wordmark with a tricolor underline.
Its original canvas includes clear space; the institution strip accounts for that
in CSS without modifying the artwork.

The existing `nha-logo-trim.png` (540 × 236) and `abdm-logo-trim.png` (391 × 362)
already exceed 3× their rendered sizes in the institutional strip and footer, so
they are retained without upscaling.

## App download badges

The English badges are unchanged official assets, retrieved on 2026-09-17:

- `app-store-badge.svg`: [Apple's official App Store badge](https://developer.apple.com/assets/elements/badges/download-on-the-app-store.svg), vector artwork with a 119.66407 × 40 viewBox.
- `google-play-badge.png`: [Google's official Google Play badge](https://play.google.com/intl/en_us/badges/static/images/badges/en_badge_web_generic.png), 646 × 250 PNG. The visible badge is 564 × 168; CSS accounts for the built-in transparent padding to match the App Store badge's visual height.
