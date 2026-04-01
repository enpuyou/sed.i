# Project Conventions

These conventions apply to all skills. Follow them exactly.

## Error Feedback
- **No toasts.** All error feedback uses `InlineError` component — inline, contextual, near the action.
- **Error message tone.** "Couldn't [action]. Try again." — concise, no jargon, no "Failed to...".
- **Error component.** `InlineError` supports `message`, `onDismiss`, `onRetry`, `className` props.

## Empty States
- **Shared component.** All empty data states use `EmptyState` component.
- **EmptyState props.** `message`, `description`, `actionLabel`, `onAction`, `className`, `variant` ("inline" | "bordered").
- **Copy style.** Sentence case, no emoji.

## State Rendering
- **Exclusive states.** Loading > Error > Empty > Data. Never show two at once.
- **Optimistic updates.** Update UI immediately, revert on failure, show InlineError.

## API
- **`fetchWithAuth`** is the single API path. All methods (including deletes) route through it.
- **Backend error shape.** All responses use `{detail: string}`. Global exception handlers sanitize 422/500.

## Frontend Stack
- Next.js 14 (App Router), React 19, TypeScript, Tailwind CSS v4
- `next/image` for all images (not `<img>`)
- CSS: `rounded-none`, `var(--color-*)`, `font-serif` for headings

## Backend Stack
- FastAPI, SQLAlchemy, PostgreSQL, Celery, Redis
- Python 3.11.7, Poetry for dependency management

## Commands
```bash
# Frontend
cd frontend && npx tsc --noEmit          # type check
cd frontend && npx eslint . --max-warnings=0  # lint
cd frontend && npx jest --ci --passWithNoTests # tests
cd frontend && npx next build            # build

# Backend
cd content-queue-backend && PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run ruff check app/  # lint
cd content-queue-backend && PYENV_VERSION=3.11.7 /usr/local/opt/pyenv/bin/pyenv exec poetry run pytest tests/ -x -q  # tests
```

## Documentation
- **ARCHITECTURE.md** must be updated in the same commit as any feature change.
- Plans go in `docs/plans/`, retros go in `docs/retros/`.
