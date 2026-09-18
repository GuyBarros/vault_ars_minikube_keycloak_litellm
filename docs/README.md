# Architecture Site

A single-page, zero-dependency teaching SPA for the **Agentic Runtime Security Architecture**. Modeled on the IBM AdvArch `tfai` reference. One HTML file. No build step.

## Serve locally

```bash
python3 -m http.server --directory architecture-site 8080
# open http://localhost:8080
```

Or just open `index.html` directly in a browser.

## Scope

Iteration 1 ships the spine (nav, progress, search, detail panel) with the hero and three deep chapters fully built: workload identity, user-to-agent delegation (OBO), and the three guardrail gates. Remaining chapters are wired into the navigation but hold intro-only placeholders for later iterations.

## Editing

Everything is inline in `index.html` — CSS in `<style>`, SVG in markup, JS in `<script>`. No bundler. No fonts bundled; IBM Plex is pulled from Google Fonts.
