# Financer V2 (Multi-Agent, Dynamic Taxonomy)

Terminal-first finance assistant with dynamic taxonomies, semantic roles, and a 4-agent orchestration pipeline.

## Quick Start

```bash
cd /path/to/financer
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r requirements.txt
cp .env.example .env
# add GEMINI_API_KEY in .env only if you want to wire an external model later
python3 app.py
```

or:

```bash
make setup
make run
```

## Run Modes

### Terminal Agent (chat)
```bash
python3 app.py
```
This is the mode where you chat with the agent (`>` prompt).

### API Mode (deterministic endpoints)
```bash
python3 app.py --api
```
API docs: `http://127.0.0.1:8000/docs`
This mode does **not** open an interactive chat prompt. It only starts the HTTP server.

If you only see logs like:
`[launcher] starting API at http://127.0.0.1:8000`
you are in API mode, not terminal chat mode.

## V2 Architecture

- Multi-agent pipeline per turn:
  - `planner`
  - `ontology_resolver`
  - `critic_safety`
  - `executor`
- Dynamic taxonomies with semantic tags (no fixed enum lock-in for user labels).
- Fresh V2 DB at `backend/data/finance_v2.db` (no backward compatibility with V1 DB).
- Feature flag: `multi_agent.enabled` (defaults to `true`).

## Core V2 Endpoints

- `GET /api/health`
- `POST /api/setup/init`
- `GET /api/taxonomies`
- `POST /api/taxonomies`
- `POST /api/taxonomies/resolve`
- `POST /api/taxonomies/aliases`
- `GET /api/accounts`
- `POST /api/accounts`
- `GET /api/income-sources`
- `POST /api/income-sources`
- `POST /api/transactions`
- `POST /api/salary/payments`
- `GET /api/dashboard`
- `GET /api/stats`
- `GET /api/agent/runs/latest`

## Terminal Commands

- `:help`
- `:tools`
- `:capabilities`
- `:trace last`
- `:clear`
- `:settings`
- `:settings show [key]`
- `:settings set <key> <value>`
- `:settings reset <key>`
- `:settings history [limit]`
- `:settings proposals [pending|applied|denied]`
- `:confirm <proposal_id>`
- `:deny <proposal_id>`
- `:quit`

## Environment Variables

- `app.py` auto-loads `.env` / `.env.local` from root and `backend/`.
- You do **not** need to run `export GEMINI_API_KEY=...` every time if it is in `.env`.
- `GEMINI_API_KEY` is optional for the current deterministic V2 runtime.

## requirements.txt

`requirements.txt` is the top-level entry point and points to `backend/requirements.txt`.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests_v2
```

## Public source copy

Fresh history with no personal ledgers, database backups, cached code, provider tokens or machine-specific paths. Runtime databases remain local and ignored. Configure secrets through the process environment or a local ignored .env file.

Template CSV files contain synthetic demonstration accounts and transactions.
