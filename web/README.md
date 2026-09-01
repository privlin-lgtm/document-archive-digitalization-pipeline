# Document Archive — Review UI

A React + TypeScript annotation/review UI for human reviewers, consuming
the `review_api` FastAPI backend (see the repo root README for that side).

## Stack

- **Vite + React 19 + TypeScript** — build tooling and framework
- **React Router** — routing
- **TanStack Query** — server-state fetching/caching/mutations
- **Zustand** — the one piece of genuinely shared UI state (which
  region/entity is selected), so the image canvas and entity panel can
  stay in sync without prop-drilling through every layer between them
- **Mantine** — component library (chosen for dense-data UI primitives:
  tables, badges, forms — appropriate for a reviewer working through many
  documents, per the stage 8 spec)

## Structure

```
src/
  api/          typed client (client.ts) + types mirroring review_api/schemas.py (types.ts)
  state/        selection.ts — shared selected-region/entity store (zustand)
  hooks/        one React Query hook module per API resource
  components/   ImageCanvas, EntityPanel, ConfidenceBadge, FlagBadge, SnippetHighlight
  routes/       DashboardPage, DocumentViewPage, ReviewQueuePage, SearchPage
  App.tsx       nav shell + route definitions
  main.tsx      provider setup (Mantine, React Query, Router)
```

## Running it

```bash
cp .env.example .env   # set VITE_API_TOKEN to match the backend's REVIEW_API_TOKEN
npm install
npm run dev
```

Needs the backend running and reachable at `VITE_API_BASE_URL` (default
`http://localhost:8000`) — see the repo root README's Docker Compose
section. The backend's `CORS_ALLOWED_ORIGINS` must include this dev
server's origin (`http://localhost:5173` by default — already the
backend's default).

```bash
npm run build      # type-checks (tsc -b) then builds to dist/
npm run lint        # oxlint
```

## What each view does

- **Dashboard** (`/`) — stats summary, recent documents table, upload
  (drag/pick files → `POST /documents`).
- **Document view** (`/documents/:id`) — split pane: pan/zoom scan image
  with clickable region overlays on the left, region/entity list with an
  inline correction form on the right. Clicking a region or an entity
  selects both (via the shared selection store); confidence is
  color-coded; a distinct purple badge marks entities a human has
  corrected. The image itself is fetched as an authenticated blob (see
  `api.getDocumentImageUrl`) since `<img src>` can't send a bearer header —
  `GET /documents/{id}/image` also supports a server-rendered
  `?annotate=true` variant, useful for a static/printable view, but the
  interactive document view draws its own overlays so they can be
  clickable.
- **Review queue** (`/review`) — open flags sorted by severity (high
  first), with resolve/dismiss actions and keyboard shortcuts (`j`/`k` or
  arrow keys to move, `r` resolve, `x` dismiss, `s` skip).
- **Search** (`/search`) — full-text query with date range / entity type /
  location / min-confidence filters, ranked results with highlighted
  snippets. Snippets come from Postgres `ts_headline()` and embed the
  *document's own OCR'd text* — `SnippetHighlight` parses out just the
  `<b>` spans it wraps matched terms in rather than
  `dangerouslySetInnerHTML`-ing the raw string, so nothing in a scanned
  document's OCR output can inject arbitrary HTML into the page.

## Scope note

Per this project's own stage-by-stage notes (see the prompt series doc at
the repo root), stage 8 splits into two passes: an autonomous agent
generates the initial scaffold — component structure, the typed API
client, routing — in one shot, since that part is boilerplate; the actual
annotation *feel* (pan/zoom responsiveness, exact confidence color
thresholds, click-to-highlight snappiness, keyboard shortcut tuning) is
explicitly handed to an interactive "look at the screen, nudge it, look
again" pass, which an agent working unattended can't do well.

This is that first pass. Everything listed above is real and functionally
wired end-to-end against a live backend (verified manually: upload, the
split-pane view with real regions/entities, click-to-select sync, entity
correction with the audit-trail badge, review-queue resolve/dismiss, and
search with all four filters) — not a mockup. What it hasn't had is the
second pass's polish.
