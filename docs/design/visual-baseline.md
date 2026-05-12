# Visual design baseline

grackle's visual language: minimal chrome, content-forward, dark-mode-first,
one signature accent (iridescent purple — grackle plumage).

## Design tokens

Defined in `packages/frontend/src/styles/tokens.css`. All colors use **OKLCH**
(perceptually uniform; wide-gamut P3 ready; Baseline-supported since 2023).

| Group | Token prefix | Notes |
|---|---|---|
| Surface | `--color-bg`, `--color-surface`, `--color-surface-2` | Layered elevation |
| Border | `--color-border`, `--color-border-strong` | |
| Text | `--color-text`, `--color-text-muted`, `--color-text-subtle` | |
| Accent | `--color-accent`, `--color-accent-bright`, `--color-accent-glow` | Iridescent purple |
| Status | `--color-success`, `--color-warning`, `--color-error` | System feedback only |
| Typography | `--font-sans`, `--font-mono`, `--text-*` | System stack; web fonts in phase 3 |
| Spacing | `--space-1` … `--space-16` | 4px base |
| Radius | `--radius-sm` … `--radius-full` | |
| Motion | `--duration-fast/normal/slow`, `--ease` | |
| Z-index | `--z-base` … `--z-toast` | |

## Theming

- `<html data-theme="dark">` set in `index.html` to prevent FOUC.
- `useTheme` hook reads `localStorage["grackle:theme"]`, falls back to
  `prefers-color-scheme`. Writes `data-theme` attribute on `<html>`.
- `prefers-reduced-motion: reduce` disables all transitions and animations.

## Phase 0 deliverables

| Component | Location | Notes |
|---|---|---|
| `ConnectionBadge` | `src/components/ConnectionBadge.tsx` | Status dot + label + 2s pulse when connected |
| `ThemeToggle` | `src/components/ThemeToggle.tsx` | Icon-only, `aria-pressed`, persists choice |
| `App` layout | `src/App.tsx` | Header + dotted-grid graph placeholder |

## Forward: graph visual language (phase 2+)

**Nodes**
- Shape encodes kind: file = rounded square, class = circle, function = diamond,
  method = small dot
- Color encodes folder by default; toggle for community / language
- Size from in-degree (degree centrality)

**Edges**
- Line style encodes kind: solid = import, dashed = call, double = inheritance
- Color matches source node, dimmed

**Runtime overlay (phase 7)**
- Nodes pulse on `call` (radius +15%, accent glow for ~800 ms decay)
- Edges glow during traversal

**Web fonts (phase 3)**: Inter + JetBrains Mono via `@fontsource` for
cross-platform metric consistency.

**Inspiration**: Obsidian graph view, Sigma.js demos, Linear (motion polish),
Datadog APM service map (live aesthetic).
