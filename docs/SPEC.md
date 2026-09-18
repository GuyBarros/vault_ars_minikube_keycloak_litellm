# architecture-site/index.html — working spec

Living spec for the single-page IBM Carbon-styled architecture guide at
`architecture-site/index.html`. Captures intent, scope, content rules, and
interactive/animation expectations so any future session can continue
without re-deriving them.

## 1. Goal

Teach the end-to-end agentic-IAM + runtime-security solution to three
personas (developer, platform engineer, security) on a single highly
animated, highly interactive page. Concepts are presented in the actual
request lifecycle order: identity → delegation → **authorization (scope-aware)** →
transport → gates → policy → observability → replay → reference → start.

## 2. Chapters (current numbering)

| #  | id                | label              |
|----|-------------------|--------------------|
| 01 | `problem`         | Problem            |
| 02 | `foundations`     | Foundations        |
| 03 | `workload-identity` | Identity         |
| 04 | `delegation`      | Delegation         |
| 05 | `authorization`   | **Authorize** (scope-aware delegation) |
| 06 | `transport`       | Transport (Consul mesh, Envoy sidecars, mTLS) |
| 07 | `gates`           | Gates              |
| 08 | `policy`          | Policy             |
| 09 | `observability`   | Observe            |
| 10 | `replay`          | Replay             |
| 11 | `reference`       | Reference          |
| 12 | `start`           | Start              |

## 3. Per-chapter current shape

### 3.1 Chapter 4 · Delegation

Continuous SMIL streak animation — **no Play/Step UI**. A prior stepper
version was rejected; the current shape is a single 6-second loop with
seven `<animateMotion>` particles (lead + tail) visualizing the three-token
flow:

- t=0.04→0.42 — subject token + actor token stream **into** the Identity
  broker (purple `#8a3ffc` lead, lighter `#a78bfa` tail).
- t=0.46→0.55 — back-channel ping to IBM Verify (grey `#525252`).
- t=0.58→0.92 — OBO token streams **out** of the broker.
- t=0.92→1.0 — idle beat.

The IBM Verify back-channel arrow uses a dashed grey stroke with marker,
and starts at the broker's **top edge** (not inside the broker rect).
Particles use `class="dl-flow-particle"` so the reduced-motion CSS hides
them automatically.

### 3.2 Chapter 5 · Authorize — current shape

Lede: delegation gave a token bound to a user; this chapter answers
*for which scopes is the token valid?* (the **tool** answers, via MCP `_meta`)
and *is the user even entitled to those scopes?* (`token-exchange` answers,
via a small policy table mapping each scope to allow-listed `groups`). HTTP
**403** fires inline before any OBO is minted. The LLM never picks scopes.

Five subsections:

1. **Tool registry inspector** — 5 tool cards
   (`list_all_users`, `search_users_by_first_name`, `create_user`,
   `update_user_by_email`, `delete_user_by_email`) each with a scope chip
   (`users.read` green / `users.write` red). Clicking a card highlights
   matching rows in a 6-row redacted user table. Driven by `MCP_TOOLS` JS
   array + delegated click handler.
2. **Vault dynamic credentials, scope-bound** — single-lane animated SVG
   (user-mcp → Vault `login_with_jwt(obo)` → user repository). A scope
   toggle (`users.read` ⇄ `users.write`) morphs the role label
   (`users-read` ⇄ `users-write`). No static-credential / "direct" mode.
3. **Intent → scope** — three-row stage with Step ▶ button. (1) LLM picks
   tool, (2) wrapper looks up `_meta.required_scopes` from the cached
   registry, (3) wrapper builds the OBO request with the deterministic
   scope. Includes a `.callout.risk` reading "Scopes come from `_meta`,
   never inferred by the LLM."
4. **Authz sandbox** — left column has a `<select>` for user groups
   (`{readonly}`, `{admin}`, `{readonly, finance}`, `{finance}`, `{}`)
   plus checkboxes for requested scopes. Right column shows a live decision
   card: green `200 OBO minted` or red `403 VerifyAuthorizationError` with
   a "groups=… do not satisfy scope=…" detail. Pure JS over a 6-line
   `SCOPE_REQUIREMENTS` table that mirrors `token-exchange`'s policy.
5. **End-to-end mini-sequence** — top row (dim) is the `tools/list`
   discovery that happens once at startup. Bottom row (bright orange) is
   the per-call flow: **ai-agent → token-exchange → user-mcp → Vault →
   user repo**. Two looping `<animateMotion>` particles. mTLS is an
   *implicit* arrow label (`obo · mTLS`) — no separate Envoy box.

### 3.3 Chapter 6 · Transport — current shape

Three subsections only. A previous "How the sidecar gets there" subsection
(Connect-inject annotation prose + Deployment YAML) was removed as
implementation detail.

1. **The mesh, as one picture** — single SVG (viewBox `0 0 920 460`).
   Top row: `web-app`, `ai-agent`, `opa-gov-api`, `opa-server` connected
   by horizontal mTLS. Bottom row: `token-exchange`, `user-mcp`,
   `wx-gov-api` reached from `ai-agent` via diagonal mTLS branches with
   their own animated particles (2.4s loops, staggered 0s / 0.6s / 1.2s).
   The horizontal handshake highlight (`tp-handshake`) and the middle
   line (`tp-mtls-mid`, `tp-mtls-mid-label`) are JS-pinned — see §6.
2. **Intentions · who may call whom** — six rows: `web→ai-agent`,
   `ai-agent→token-exchange`, `ai-agent→user-mcp`, `ai-agent→wx-gov-api`,
   `ai-agent→opa-gov-api`, `opa-gov-api→opa-service · ns:opa`. There is
   **no** `ai-agent→opa-service` row. Anything not listed is denied.
3. **Why intent-based, not L3-firewall-based** — single callout
   contrasting NetworkPolicy label-matching with SPIFFE-cert-based
   intentions enforced at the mTLS handshake.

Closes with a forward pointer to Chapter 7 (`#gates`).

### 3.4 Chapter 7 · Gates — current shape

**Two gates, not three.** A previous version had a separate Gate 02
for outbound tool-call inspection; that gate is removed and its
unsafe-pattern check is folded into Gate 01 (the inbound prompt
already contains the shell-like text the rule matches, so a single
`/evaluate` call returns both `is_injection` and `is_unsafe`).
What was Gate 03 (PII masking) is renumbered to Gate 02. Chapter
title is **"Two gates, one request — prompt and response"**.

**Subsections are ordered as a temporal walk** (six h3 subsections):
(1) "The runtime pipeline, as one picture" — overview SVG + caption,
no inline gate copy; (2) "Gate 01 · Pre-flight · injection + unsafe
patterns" — paragraph + curl + helper; (3) "Authorization · between
Gate 01 and the tool call" — paragraph + authz SVG + caption;
(4) "Gate 02 · Post-flight · PII masking" — paragraph + curl +
helper; (5) "Fail-open by default" — single callout; (6) "Run a
request through the gates" — gate-runner widget. Gate 01 / Gate 02
are h3 (not h4 nested under the pipeline subsection) so the
authorization subsection slots between them at peer level — preserving
the "authz is a different system, not a numbered gate" framing while
giving the chapter a strict temporal read.

- **Authz SVG** uses viewBox `0 0 920 140`. Layout (left → right):
  user circle, small grey **Gate 01 · pre-flight** context box,
  **agent** box, orange **AUTHZ · token-exchange** focus rect
  (x=280, y=20, w=280, h=70), then a single dashed-orange OBO arrow
  forward labelled "tool call → Gate 02 (response masking)". The
  deny curve starts at the bottom of the authz rect (x=420, y=90),
  control points reach y≈130, terminates at the user circle (origin
  near `30, 55`); the "403 — no OBO minted" label sits at y=120.
  The chapter has **two gates** — Gate 01 (pre-flight) and Gate 02
  (post-flight). The authz check is intentionally *not* numbered:
  it fires after Gate 01, in a different process (token-exchange),
  and a "Gate 00" label would imply temporal precedence it doesn't
  have.
- **Authz placement is after Gate 01, before the tool call.**
  Authz fires when the agent calls `token-exchange` for an OBO with
  the tool's required scopes; the prompt has already cleared Gate
  01's injection + unsafe-pattern check by then. Gate 02 (response
  masking) only sees a response if the authz check minted an OBO
  and the tool returned data.
- **Pipeline diagram** (viewBox `0 0 920 320`, two gates):
  - Gate 01 (pre-flight, two stamps `is_injection?` + `is_unsafe?`):
    x=110, y=80, w=240, h=160. Two stamp animations stagger 0.4s /
    0.9s. Footer counter label uses the literal
    `opa_violations_total {rule=injection|unsafe_code}`.
  - `agent` rect sits **between Gate 01 and Gate 02** (x=408, y=140,
    w=68, h=40) with a "+ tool call" subscript at y=194. Through-line
    arrows are segmented around it so the agent rect paints on top.
  - Gate 02 (post-flight, single stamp `has_pii?`): x=510, y=80,
    w=240, h=160.
  - Particle path:
    `M 30 → 108 → 352 → 408 → 510 → 752 → 880` over 7.5s.
  - Block-exit deny curve starts at (180, 240), reaches y≈280, and
    terminates near the user (38, 185); "400 blocked" label at y=278.
- **Gate semantics**: Gate 01 calls `/evaluate` and enforces both
  `is_injection` and `is_unsafe` from the same response payload.
  Gate 02 calls `/mask`. Both Lua-filter calls fire once per
  request: one inbound (`/evaluate`), one outbound (`/mask`). There
  is no in-flight check.
- **Gate-runner widget** has two tiles (`data-gate="1"`, `data-gate="2"`).
  `movePacketTo` is `1..2`. The JS `run()` performs a single Gate 01
  pass that blocks if either `injHits` or `unsafeHits` matches, then
  a Gate 02 PII masking pass. `POLICY_RULES` keeps `kind: 'is_unsafe'`
  rules but their `gate` field is `1` (same gate as `is_injection`).

Closes with a forward pointer to Chapter 8 (`#policy`).

### 3.5 Chapter 8 · Policy — current shape

One subsection only: **"Same contract, every risk — masking and blocking"**
— the OPA-vs-watsonx.governance engine split (Rego snippet + Python
detector snippet + crosswalk table). A previous **"Policy sandbox — feel
it"** subsection (browser-side regex evaluator with `is_injection` /
`is_unsafe` flags and a 6-rule list) was removed because Chapter 7's
gate-runner widget already drives the same `POLICY_RULES` set against
both gates end-to-end; a second standalone sandbox in Ch 8 was redundant.
Do **not** re-add it. The `Next.` helper points readers back to Ch 7's
gate-runner if they want to feel the rules fire.

Closes with a forward pointer to Chapter 9 (`#observability`).

## 4. Content rules (must follow when adding/editing)

1. **No raw tech-stack / implementation labels.** Do *not* mention FastMCP,
   AWS NLB, Kubernetes StatefulSet, streamable-HTTP, `.env`, raw library
   names, deployment topology details, etc. These are implementation
   details that distract from the architectural story.
2. **HashiCorp & IBM product details ARE kept.** Vault, Consul, Envoy
   (as Consul sidecar), IBM Verify, watsonx.governance, Open Policy Agent
   are part of the solution narrative — name them, brand them, link to
   their `data-node` entries.
3. **No static-credential / direct-mode content.** Static creds were a
   connectivity-test crutch; they are not part of the story. Every database
   path is **Vault dynamic credentials** with a per-call short-lived lease,
   and the role is selected by scope (`users.read` → read role,
   `users.write` → write role).
4. **The LLM never picks scopes.** Always frame scope determination as
   deterministic lookup from MCP `_meta.required_scopes`. The LLM's only
   contribution is choosing a tool.
5. **Authorization is inline in `token-exchange`.** No callback to ai-agent;
   no external authz service. `VerifyAuthorizationError` → HTTP 403.
6. **Postgres-as-product is genericized.** The data store is referred to
   as the "user repository" / "user repo" / `users` (the table name) —
   not "Postgres". Schema specifics (email PK, PII columns) may be shown
   to illustrate the data model, but the product label is not foregrounded.
7. **No mesh-implementation YAML in copy.** Connect-inject annotations,
   Vault Agent inject annotations, `ServiceDefaults` / `ServiceIntentions`
   resource files, and "how the sidecar gets injected" prose are
   implementation detail. The transport story is told via the topology
   picture and the intentions list — never via embedded Deployment YAML.
   This is rule #1 applied to the transport layer.
8. **SPIFFE language belongs to the mesh, not to agent identity.** Use
   SPIFFE only when describing Envoy↔Envoy mTLS (Consul-CA-signed certs
   presented at the handshake). Agent identity issued by Vault is a
   **Vault-issued OIDC JWT** trusted by IBM Verify — never call it a
   "SPIFFE token". Mixing the two layers is a recurring mistake.

## 5. Styling

- **Design language:** IBM Carbon Design tokens. IBM Plex Sans / IBM Plex
  Mono fonts. Light theme. Reuse existing CSS custom properties
  (`--layer-*`, `--bg`, `--text`, `--s-0n`, etc.) — do not introduce new
  color tokens.
- **Chapter 5 accent:** `--layer-risk` (orange `#ff832b`). Subsection
  callouts use `.callout` (neutral) and `.callout.risk` (orange) — both
  already defined.
- **Scope chips:** `.scope-chip.scope-read` (green) and
  `.scope-chip.scope-write` (red).
- **Inline product/component refs** use `<span class="node" data-node="…">`
  so the reference-catalog drawer (Ch11) opens on click.
- **No frameworks.** Vanilla HTML + CSS + JS. One inline `<script>` block
  near the bottom contains all behavior.
- **No emojis** in copy unless explicitly requested.
- Respect `prefers-reduced-motion: reduce` — particles pause, transitions
  drop. CSS overrides already in place; preserve them when adding new
  animated elements. New `<animateMotion>` particles should use
  `class="dl-flow-particle"` so the existing rule covers them.

## 6. Animation & interactivity

- **SVG `<animateMotion>` particles** drive the per-flow animations
  (db-flipper Vault lane, end-to-end mini-sequence — discovery + per-call,
  replay-scrubber edges, etc.). Particles are orange (`#ff832b`) for the
  per-call path, grey/dim for startup discovery.
- **Tool registry inspector** (5.1): clicking a tool card highlights
  rows in the user table. Keyboard-accessible (`role="tab"`,
  `aria-pressed`).
- **Vault flipper** (5.2): a `users.read` ⇄ `users.write` segmented
  toggle. The role label and status caption update live.
- **Intent translator** (5.3): Step ▶ / ↻ Reset buttons advance through
  the 3-row stage. Three preset tools selectable.
- **Authz sandbox** (5.4): `<select>` + checkboxes feed a live decision
  card with status pill, detail line, and an inline group∩policy flow.
- **End-to-end** (5.5): two looping particles (one dim discovery, one
  bright runtime).
- **Replay scrubber** (Ch10): 10-state stepper with arrow-key navigation;
  state indicator reads `n / 10`. New states `tools/list cache`,
  `scope authz`, `postgres query` integrated.
- **Reference catalog** (Ch11): searchable; each `data-node` opens a
  detail drawer. Includes `user-mcp`, `users-pg`, `mcp-tools-list`,
  `scope-requirements`, `vault-db-roles`, `obo-scope-cache`.

### 6.1 Cross-cutting animation rules

- **Continuous loops are preferred over Play/Step controls** for
  sequence visualizations. Phase windows on a single `<animateMotion>`
  loop (e.g. Ch4's 6s streak) keep the page legible without gating
  comprehension behind clicks.
- **JS-pinned SVG ids must be preserved.** When restructuring SVGs,
  do not rename or remove ids that other scripts bind to. Currently:
  `tp-handshake`, `tp-mtls-mid`, `tp-mtls-mid-label` (Ch6 intent-toggle).
- **viewBox must contain all geometry.** Deny curves, "403" labels,
  and below-row annotations are easy to clip. Grow the viewBox before
  adding content below the main shape; verify by reading endpoint
  coordinates against the declared height.
- **SVG arrows must terminate inside their target shapes.** A back-channel
  arrow to "IBM Verify" must land inside the IBM Verify rect, not at
  empty coordinates near it. Compute end-point against the destination's
  edge midpoint.

## 7. JS structure

All Chapter-5 widgets are self-contained IIFEs added before `</script>`:

- Tool registry (5.1) — `MCP_TOOLS` array + `paint(toolName)`.
- Vault flipper (5.2) — single `scope` state; updates role label and
  status. (No `mode` axis after the static-cred removal.)
- Intent translator (5.3) — step counter + tool preset.
- Authz sandbox (5.4) — `SCOPE_REQUIREMENTS` const replicated client-side.

## 8. Verification

The page is a single static file. Verification = browser smoke test:

```
cd architecture-site && python3 -m http.server 8765
# open http://localhost:8765/
```

Walk top-to-bottom, with extra attention to Ch5 widgets, the authz tile
in Ch7, the 10-state replay scrubber (Ch10), the new NODES entries (Ch11),
and the `kubectl apply` blocks in Ch12.

JS sanity check:

```
node -e "const fs=require('fs');const h=fs.readFileSync('architecture-site/index.html','utf8');new Function(h.match(/<script>([\\s\\S]*?)<\\/script>/)[1]);console.log('ok');"
```

Section balance: 13 `<section ` opens, 13 `</section>` closes.

## 9. Conventions for future edits

- **Only `architecture-site/index.html` is edited** for content/UX work.
  Source files in `user-mcp/`, `ai-agent/`, `token-exchange/`,
  `deploy-k8s/` are read for accuracy but not modified.
- Match existing patterns when adding widgets — re-use `.callout`,
  `.subsection`, `.chapter`, `.helper`, `.caption`, `.lede`, the
  Carbon spacing scale, and the `data-node` reference linkage. Prefer
  continuous SMIL loops over Play/Step idioms (see §6.1).
- Each chapter ends with a `<p class="helper"><strong>Next.</strong>…</p>`
  forward pointer linking to the next chapter's anchor.
- Reviews proceed **chapter-by-chapter**. The user typically asks for
  recommendations grouped into Tier A (correctness/clarity defects)
  and Tier B (polish), then says "apply both tiers" or just "Tier A".
- Validate one piece end-to-end before producing the rest of a
  multi-piece deliverable; wait for review.
- When editing repeated structures (intentions table rows, gate
  descriptions), include enough surrounding context in `old_string` to
  make it unique — Edit fails on ambiguity.
- Don't commit without explicit user review — even for auto-generated
  spec updates like this one.
