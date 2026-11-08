# ADR-0027: Serve the portfolio dashboard from flex-web-live and merge the close and delayed views

- **Date:** 2026-11-08
- **Status:** Accepted — deployed to `/live/`. Amended 2026-11-08: `/close/` restored as a separate close-only view; no root redirect

## Context

The revamped dashboard existed only as a local preview (`work/portfolio-ui-preview`) reading a bundled snapshot. Production `/live/` returned JSON only, and `/close/` already proxied to the same API. Users wanted previous-close and delayed prices in one place and the portfolio on the main site.

## Options considered

- Keep the preview as a separate static site with its own proxy.
- Rebuild a full frontend application (React or similar).
- Serve the existing page as static files from the service that already owns the data.

## Decision

Move the page into `services/flex-web-live/ui/` (`index.html`, `app.css`, `app.js`) and serve it at `/` with assets under `/static/`. The UI calls the API with relative URLs so it works under both `/live/` and `/close/`. Add `/api/v1/report` (all positions and lots) and `/api/v1/sectors`. Keep the Content Security Policy to `'self'` scripts and styles, allowing only `style=""` attributes (`style-src-attr 'unsafe-inline'`) for bar widths. Every row shows both the statement close and the delayed price.

## Rationale

One service, one data path, no build step, no new dependencies. Relative URLs avoid duplicating the page per route.

## Consequences and limitations

- `/api/v1/report` sends every position and lot (~1.1 MB, ~107 KB gzipped) for client-side grouping. Page it if the book grows.
- **Amendment (2026-11-08):** `/close/` is again the separate statement-only dashboard (`services/flex-web`). It does not depend on LSEG or flex-web-live, so it stays as the view for debugging and the fallback when either is down. The landing page at `/` stays (it shows what the fund has built and is building) and links only to `/live/`; `/close/` remains reachable by URL.
- The site is public by choice; account numbers and holdings are visible (see README security notes).

## Follow-up / revisit criteria

Revisit a framework only if the dashboard becomes a multi-team, multi-page product.

Links: `services/flex-web-live/README.md`, ADR-0005, ADR-0008.
