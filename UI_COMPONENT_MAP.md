# UI component map

This release treats the minimal UI as one full-app cutover rather than a page-by-page migration. Every visible surface now consumes the same token set and component vocabulary.

| Existing pattern / class family | New component target | Action |
|---|---|---|
| `welcome-pill`, `cycle-chip`, `todo-count-chip`, `soft-badge`, `soft-status`, decorative pills | `Badge` or plain metadata text | removed or merged |
| `btn primary`, `btn secondary`, `btn ghost`, old special-case button subclasses | `Button` | normalized to one shared look with two sizes |
| ad hoc text fields, selects, textareas, field wrappers | `Input`, `Select`, `Textarea`, `Label` | merged |
| `todo-item`, `history-item`, selected-day rows, task/event rows | `List Row` | merged |
| score cards, stat cards, quick metrics | `Score Metric` | merged |
| thick progress bars, color blocks | `Progress Bar` | reduced to a 1px track |
| tab-like pills and chip groups | `Tab` + plain metadata text | merged |
| blocking feedback dialogs | `Toast` or `Banner` | reduced where immediate action is not required |
| hydration prompt | `Modal` | kept as the main action-taking modal |
| dashboard decorative chrome | plain status line + single primary action | deleted |
| calendar heat-fill backgrounds | completion indicator bar | replaced |

## Design token groups

- Color
- Typography
- Spacing
- Radius
- Shadow
- Motion

All tokens live in `app/static/style.css`. The active release is exposed through `app_ui_defaults.ui_release` so the app shell can switch as one system instead of mixing old and new surfaces.


## Guided break components

- `.exercise-card` — reuses card/list-row spacing and button rhythm for selectable break actions.
- `.breathing-orb` — centered animated visual guide inside `.break-stage`.
- `.guide-overlay` — bottom instruction panel using the existing card surface and muted text language.
- `.alignment-score-badge` — small floating badge for optional posture alignment feedback.
