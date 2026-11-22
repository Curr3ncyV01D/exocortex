# Project Instructions: Exocortex (`AGENTS.md`)

Extends the global `~/.codex/AGENTS.md` with repository-specific architecture, commands, and constraints. This file contains agent instructions and is maintained in English; user-facing communication and project documentation follow the global language rules.

## 1. Architecture & Layer Boundaries
- **Backend (`src/`):** Python 3.12 + FastAPI + SQLAlchemy 2.x Async (`aiosqlite`) + Alembic + SQLite FTS5.
  - `src/api/`: HTTP routers (`/api/v1`) and DI dependencies only. Validate DTOs (`src/schemas.py`) and delegate to `src/services.py`; keep SQL out of routers.
  - `src/main.py`: FastAPI app, lifespan, and domain-error-to-HTTP exception mapping. Domain logic MUST NOT depend on HTTP status codes.
  - `src/services.py`: Application use cases (chats, messages, generation, regeneration, merges) and transaction boundaries. *(Note: legacy `select`/`BEGIN IMMEDIATE` calls exist here and in `src/agent/loop.py`; avoid spreading new raw SQL outside `src/storage/` unless transaction control requires it.)*
  - `src/storage/`: Models, async engine/session, CRUD (`flush()` without premature commit), FTS5, migration helpers.
  - `src/agent/`: `client.py` isolates `google-genai` SDK; `tools.py` accesses storage; `loop.py` runs the agent loop.
- **Frontend (`frontend/`):** React + TypeScript + Vite + Tailwind CSS + Zustand (`src/store/`) + HTTP client (`src/api/`). Communicates strictly via HTTP API.
- **API Contract Sync:** There is NO OpenAPI-to-TS codegen. Any change to `src/schemas.py` or `src/api/` MUST update `frontend/src/api/types.ts` (and API client/tests) in the same logical unit.

## 2. Repository Commit Scopes
Use Conventional Commits per global rules. Prefer existing repository scopes:
`agent`, `api`, `branches`, `config`, `core`, `data`, `deploy`, `docs`, `explorer`, `frontend`, `memory`, `migration`, `mvp`, `release`, `storage`, `test` (and `ci` as commit type).

## 3. Verification Commands & Environment (Windows-safe)
Use the project `.venv` and `.cmd` wrappers in PowerShell. On POSIX, use `.venv/bin/python`, `npm`, and `npx`. Python dependencies are constrained by `constraints.txt`; frontend dependencies are locked in `frontend/package-lock.json`.

- **Unified checks (run from the repository root):**
  - Base: `.\.venv\Scripts\python.exe scripts/check.py` runs pytest, `alembic check`, `pip check`, frontend tests, and the production build.
  - With browser E2E: `.\.venv\Scripts\python.exe scripts/check.py --e2e` runs the base checks followed by Playwright.
  - Staged: `.\.venv\Scripts\python.exe scripts/check.py --staged` selects checks from `git diff --cached`: documentation-only changes skip code checks; backend paths run pytest and Alembic; frontend paths run `npx tsc -b` and frontend tests. Changes to the checker itself or unclassified files run the full base checks. Checks stop at the first failure.
- **Backend tests:**
  - Full suite: `.\.venv\Scripts\python.exe -m pytest`
  - API contracts & flows: `.\.venv\Scripts\python.exe -m pytest tests/test_api_contracts.py tests/test_api_flows.py`
  - Storage & migrations: `.\.venv\Scripts\python.exe -m pytest tests/test_database.py tests/test_graph.py`
  - Verification tooling: `.\.venv\Scripts\python.exe -m pytest tests/test_check.py tests/test_install_hooks.py`
  - Migrations check: `.\.venv\Scripts\python.exe -m alembic check`
  - Dependency sanity: `.\.venv\Scripts\python.exe -m pip check`
- **Prepared database requirement:** `alembic check` inspects the database selected by `DATABASE_URL` or application configuration and requires it to be at the current migration head. For a new or already versioned database, first run `.\.venv\Scripts\python.exe -m alembic upgrade head`. For a legacy database without Alembic versioning, follow `docs/how-to/migrate-sqlite.md` and `scripts.adopt_legacy_db` first. The checker never applies migrations or recreates the database automatically. Use a separate temporary SQLite database when verification must not touch local application data.
- **Frontend (run from `frontend/`):**
  - Unit/UI tests: `npm.cmd test` (order independence: `npm.cmd test -- --sequence.shuffle --sequence.seed=17`)
  - Typecheck only: `npx.cmd tsc -b`
  - Typecheck + production build: `npm.cmd run build` (runs `tsc -b && vite build`)
- **Browser E2E (Playwright, run from `frontend/`, requires root `.venv`):**
  - `npm.cmd run test:e2e`
  - Install Chromium first if needed: `npx.cmd playwright install chromium`.
  - Playwright starts its own `webServer` pair (`127.0.0.1:8017` FastAPI via `tests/e2e_app.py`, `127.0.0.1:5178` Vite).
  - **E2E State Caveat:** `tests/e2e_app.py` uses a single shared `:memory:` SQLite DB for the whole test server lifetime (not reset between tests). Write E2E tests with isolated chats/unique titles and do not rely on an empty DB.
- **Linters note:** `ruff`, `mypy`, and `eslint` are NOT configured in this repository. `tsc -b` checks TypeScript and enables `noUnusedLocals` and `noUnusedParameters`; these checks do NOT imply full strict mode, and `strict` is not explicitly enabled in the current tsconfig files. Do NOT install new linters autonomously without spec approval.
- **Local pre-commit hook:** Install per clone with `.\.venv\Scripts\python.exe scripts/install_hooks.py`. The LF-only POSIX shell hook invokes `scripts/check.py --staged` through `./.venv/Scripts/python.exe` on Windows or `./.venv/bin/python` on POSIX. Installation is idempotent and refuses to overwrite a different hook or override `core.hooksPath`; no extra packages are required.
- **Partial staging caveat:** Checks are selected from the Git index but execute against the working tree, not an isolated index snapshot. Review both `git diff` and `git diff --cached` when staging partially; a passing hook alone does not prove that the staged snapshot works. The hook does not stash files or replace the full relevant verification and self-review required by the global protocol. See `docs/how-to/checks.md`.

## 4. Storage, External APIs & Docker
- **Alembic Migrations:** FastAPI startup verifies schema version and does NOT auto-migrate. Any schema change MUST include an Alembic revision in `migrations/` (remember FTS5/triggers if touching indexed tables) and pass `alembic check` + `tests/test_database.py`.
- **Gemini Mocking:** `GeminiClient` wraps `google-genai`. Never hit live Gemini API in automated tests. Keep `TestGemini` / mock overrides aligned with real `GenerateContentResponse` fields; prefer recorded JSON fixtures in `tests/fixtures/gemini/` for new response shapes or edge cases. This is a recommended location for new fixtures; the directory does not currently exist. Never record API keys or other secrets in fixtures.
- **Docker:** Uses `compose.yaml` (not `docker-compose.yml`), `./data` bind mount (`create_host_path: false` — ensure `./data` exists before `docker compose up`). Keep `.env.example` updated if adding config vars.

## 5. Specs & Roadmap Alignment
- Existing `docs/SPEC.md`, `docs/spec_*.md`, and `ROADMAP.md` remain valid project documentation.
- Active feature execution uses `docs/specs/<nnn>-<feature>.md` (4-block tracker per global `AGENTS.md`), referencing detailed technical specs (e.g., `docs/spec_chat_explorer.md`) without duplicating them.
