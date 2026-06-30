# Frontend refactor & robustness plan — viewer / display

Review of [static/viewer.html](static/viewer.html), [static/display.html](static/display.html),
and the shared [static/captions-client.js](static/captions-client.js).

## Summary

The hard part is already done well. All protocol, reconnect, session-keying, and
rendering logic lives in `captions-client.js`; both pages consume it through
callbacks. There is essentially **no duplicated JavaScript** — the per-page
scripts are genuinely page-specific:

- **viewer**: venue tabs, view modes, embedded stream, wake-lock, smooth scroll
- **display**: perspective Star-Wars-crawl transform, QR code

That boundary is correct and should be kept. The duplication that remains is
almost entirely **CSS**, plus two deployment-robustness issues that matter more
than the refactor for the EMF Camp deployment.

## Worth doing

### 1. Extract a shared `static/captions.css` (the real win)

The following are duplicated verbatim (or near-verbatim) between `viewer.html`
and `display.html`; the palette is shared by all four pages (also `admin.html`,
`status.html`):

- the `* { margin / padding / box-sizing }` reset
- the colour palette (`#2AE28C` / `#F9E200` / `#F5515E` / `#F77F02`, `Raleway`
  font stack) — viewer uses CSS vars, display hardcodes the *same* values in
  `#status.live` etc.
- `@keyframes pulse` (viewer 0.4 / display 0.5 — trivially reconcilable)
- `body::before` background-image helper (identical bar the image file)
- **`.caption-line` / `.caption-segment` / `.caption-tentative`** — the most
  important. These classes are the contract with the DOM that
  `captions-client.js` produces. The contract is currently defined twice, in two
  files, and can silently drift. It should live in one place, next to the client.

**Action:** move `:root` tokens + caption classes + reset + `pulse` into
`static/captions.css`, linked from all pages. Removes ~60–80 lines of
duplication and makes the palette single-source.

**Leave alone:** the status-indicator styling differs structurally (viewer =
dot, display = badge). Forcing it together isn't worth it.

### 2. Self-host the two external runtime dependencies (festival robustness)

Matters more than the refactor for this deployment — EMF Camp network is flaky,
and both are runtime fetches over the internet:

- **qrcodejs** — [display.html:207](static/display.html#L207) loads it from
  the cloudflare CDN at runtime. If the CDN is unreachable when a display boots,
  the QR code silently never renders. ~5KB — vendor it into `static/`.
- **Google Fonts (Raleway)** — loaded from `fonts.googleapis.com` on all pages.
  Good fallbacks exist, but on a Pi-driven display the font may never swap in.
  Self-host the woff2 for deterministic displays. Note:
  [captions-client.js:488](static/captions-client.js#L488) already waits on
  `document.fonts.ready` before baking line-mode wrap points — so a slow/failed
  font load directly affects layout correctness on the display, not just
  aesthetics.

## Probably not worth it

- **Pulling per-page inline `<script>` / `<style>` into external files for
  caching.** The display runs one long-lived session (caching irrelevant); the
  viewer's first paint benefits from inline CSS (no extra round-trip). Only the
  *shared* `captions.css` from #1 is worth externalizing.
- **Moving the inline logo SVG** (viewer, ~25 lines) to a file — single-use,
  trades inline weight for a request, no net win.

## Recommendation

Do #1 and #2 — both low-risk; #2 has real operational payoff for the event. Skip
the broader "split everything into files for performance" framing; the current
inline-per-page + shared-JS split is already the right one.
