# CSS / HTML Class Convergence Map

This file tracks the shared component names that templates and CSS now target directly.

## Final component names

| Legacy class family | Final shared class | Notes |
|---|---|---|
| `profile-note-box`, `goal-note-box`, `field-picker-box`, `selected-day-journal`, `selected-day-card`, `sleep-reminder-card`, `auth-card`, `care-chat-card`, `care-summary-card`, `timeline-card`, `todo-card`, `primary-action-card`, `log-main-card`, `log-side-card`, `profile-section-card`, `history-entry-card`, `ai-feedback-box` | `card` | Base container primitive |
| compact card variants | `card card--compact` | Smaller internal padding |
| muted support surfaces | `card card--muted` | Softer background for secondary blocks |
| warning card variants | `card card--warning` | Reserved for caution states |
| `habit-modal-card`, `eye-exercise-card`, `ai-suggestion-followup-card` | `modal` | Blocking dialogs only |
| `wellness-feedback-card`, `ai-suggestion-added-card` | `card card--compact` | Toast and banner surfaces |
| `score-metric`, `mini-stat-card` | `metric` | Shared number + label block |

## Temporary compatibility aliases

A short alias section still exists in `app/static/style.css` so older markup does not hard-break during the cleanup window. It is marked with:

- `TODO: remove after 2026-06-30`

## Rule going forward

Any new template should use the shared names above directly. Do not add page-specific visual container names back into HTML.
