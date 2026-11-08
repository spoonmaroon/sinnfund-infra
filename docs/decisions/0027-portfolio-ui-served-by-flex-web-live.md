# ADR-0027: Serve the portfolio dashboard from flex-web-live and merge the close and delayed views

- **Date:** 2026-11-08
- **Status:** Accepted — deployed to `/live/` and `/close/`; root redirect awaiting an admin

## Context

The revamped dashboard existed only as a local preview (`work/portfolio-ui-preview`) reading a bundled snapshot. Production `/live/` returned JSON only, and `/close/` already proxied to the same API. Users wanted previous-close and delayed prices in one place and the portfolio on the main site.

## Options considered

- Keep the preview as a separate static site with its own proxy.
- Rebuild a full frontend application (React or similar).
- Serve the existing page as static files from the service that already owns the data.

## Decision

Move the page into `services/flex-web-live/ui/` (`index.html`, `app.css`, `app.js`) and serve it at `/` with assets under `/static/`. The UI calls the API with relative URLs so it works under both `/live/` and `/close/`. Add `/api/v1/report` (all positions and lots) and `/api/v1/sectors`. Keep the Content Security Policy to `'self'` scripts and styles, allowing only `style=""` attributes (`style-src-attr 'unsafe-inline'`) for bar widths. Every row shows both the statement close and the delayed price; `/close/` stays a pass-through. Redirect `/` to `/live/` in Nginx (prepared in `deploy/nginx/`, needs sudo).

## Rationale

One service, one data path, no build step, no new dependencies. Relative URLs avoid duplicating the page per route.

## Consequences and limitations

- `/api/v1/report` sends every position and lot (~1.1 MB, ~107 KB gzipped) for client-side grouping. Page it if the book grows.
- `/close/` does not compress responses; `/live/` is the link to share.
- The site is public by choice; account numbers and holdings are visible (see README security notes).

## Follow-up / revisit criteria

Install the root redirect. Revisit a framework only if the dashboard becomes a multi-team, multi-page product.

Links: `services/flex-web-live/README.md`, ADR-0005, ADR-0008.
