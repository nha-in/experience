# Marketing fonts

## ABDM team design reference — 2026-10-02

The current landing page and account pages use unmodified Latin WOFF2 assets extracted from the user-provided `ABDM Agentic Sandbox, Reimagined V4.html` bundle. The CSS uses local URLs only, so the pages require no third-party font requests. All three faces use normal style and `font-display: swap`.

The variable axes below were verified from each binary’s `fvar` table with fontTools. Newsreader includes an optical-size axis of 6–72 (default 18), used with normal browser optical sizing.

### public-sans-latin.woff2

- Family: Public Sans
- Variable weight range: 100–900
- Reference bundle asset: `90841f19-d37d-45a8-a730-9909f8bcd5e2`
- SHA-256: `c1b6da516e0062e9c2f341b3a51dd2d621d946da72f06c6cfe05fd9d2dd8622d`
- License: [OFL-Public-Sans.txt](./OFL-Public-Sans.txt), copied from the [official Google Fonts repository](https://github.com/google/fonts/blob/main/ofl/publicsans/OFL.txt) on 2026-10-02.

### newsreader-latin.woff2

- Family: Newsreader
- Variable weight range: 200–800
- Reference bundle asset: `764be329-ec33-4ee9-bcf6-96a210981331`
- SHA-256: `01817351be3edfc1714fe6d60ddea6a22a169a5ebd033b50c7f9495e5d9c386a`
- License: [OFL-Newsreader.txt](./OFL-Newsreader.txt), copied from the [official Google Fonts repository](https://github.com/google/fonts/blob/main/ofl/newsreader/OFL.txt) on 2026-10-02.

### jetbrains-mono-latin.woff2

- Family: JetBrains Mono
- Variable weight range: 400–800
- Reference bundle asset: `926bd499-e3cb-4eaa-be92-b4d9befbcb60`
- SHA-256: `2c32b9b3ee358c119e210f6f5195f9bd34894d78a785ff2e95d60e718e400af4`
- License: [OFL-JetBrains-Mono.txt](./OFL-JetBrains-Mono.txt), copied from the [official Google Fonts repository](https://github.com/google/fonts/blob/main/ofl/jetbrainsmono/OFL.txt) on 2026-10-02.

## Earlier marketing fonts

The following previously used font files remain available for existing assets. They are no longer loaded by `fonts.css`.

Downloaded from the official Google Fonts CSS API on 2026-09-09. Font binaries are unmodified WOFF2 Latin subsets; only the CSS source URLs are local.

The request matches Landing v4, including the Bricolage Grotesque optical-size range 12–96.

[Exact Google Fonts request](https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600&family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,600;12..96,700&family=DM+Mono:wght@400;500&display=swap)

Albert Sans supplies weights 400, 500 and 600. Bricolage Grotesque supplies 500, 600 and 700. DM Mono supplies 400 and 500. All faces use normal style and `font-display: swap`.

## albert-sans-latin-wght-normal.woff2

- Source: [Albert Sans](https://fonts.gstatic.com/s/albertsans/v4/i7dOIFdwYjGaAMFtZd_QA1ZbYFeQGQyU.woff2)
- Weights: 400, 500, 600
- SHA-256: `acf304385d0648fd9377eff186c269f2802a5311d3ae62dca46683b05c9c2b48`

## bricolage-grotesque-latin-opsz-wght-normal.woff2

- Source: [Bricolage Grotesque](https://fonts.gstatic.com/s/bricolagegrotesque/v9/3y9K6as8bTXq_nANBjzKo3IeZx8z6up5BeSl9D4dj_x9PpZBMlGIInHWVyNJ.woff2)
- Weights: 500, 600, 700
- SHA-256: `85f55a58a31e61a2e19e8bb25fed503181bf2a6b4cab76c589992cfaac377447`

## dm-mono-latin-400-normal.woff2

- Source: [DM Mono](https://fonts.gstatic.com/s/dmmono/v16/aFTU7PB1QTsUX8KYthqQBK6PYK0.woff2)
- Weights: 400
- SHA-256: `fd7521f3531a5ccfc655b25c4f22e9871df3ec141ad79bb27fde20d0df347b6d`

## dm-mono-latin-500-normal.woff2

- Source: [DM Mono](https://fonts.gstatic.com/s/dmmono/v16/aFTR7PB1QTsUX8KYvumzEYOtbYf-Vlg.woff2)
- Weights: 500
- SHA-256: `0e263db52797086e763679c54f84ded8cc1249879bc27dca2bd5dd446f6d9f36`

## Licenses

- Albert Sans: [OFL-Albert-Sans.txt](./OFL-Albert-Sans.txt), copied from [google/fonts](https://github.com/google/fonts/blob/main/ofl/albertsans/OFL.txt).
- Bricolage Grotesque: [OFL-Bricolage-Grotesque.txt](./OFL-Bricolage-Grotesque.txt), copied from [google/fonts](https://github.com/google/fonts/blob/main/ofl/bricolagegrotesque/OFL.txt).
- DM Mono: [OFL-DM-Mono.txt](./OFL-DM-Mono.txt), copied from [google/fonts](https://github.com/google/fonts/blob/main/ofl/dmmono/OFL.txt).
