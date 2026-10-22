# QuickBooks Online to Excel cash flow automation

This system fills an accounting firm's existing Excel cash flow template from each client's QuickBooks Online books, and it leaves the template's formulas, chart and notes exactly as the firm's staff built them. For every client and closed month it pulls the P&L, balance sheet and cash flow report by month, along with the chart of accounts. It places every balance sheet account on a line a person has approved and writes only the template's input cells. It then recalculates the workbook's own formulas and checks the result against QuickBooks before anyone sends it. A workbook that fails a check is held, with a plain-language reason. New accounts get a suggested line (from rules, or from an OpenAI model) that a reviewer approves in a queue, and a Streamlit dashboard shows the close as the reviewer sees it.

This is a reference implementation of the architecture behind a system I built for a mid-sized outsourced accounting and fractional CFO firm. The production build is under NDA, so a few integrations are abstracted behind adapters: the QuickBooks Online connection, delivery of finished workbooks to client folders, and staff sign-on. An HTTP adapter for Intuit's API is included and tested against a mock transport, but the stack runs on a synthetic QuickBooks. It runs on synthetic data, and every company, place, person, phone number and figure in it is made up.

[![ci](https://github.com/AlexKurinets/quickbooks-excel-cash-flow-automation/actions/workflows/ci.yml/badge.svg)](https://github.com/AlexKurinets/quickbooks-excel-cash-flow-automation/actions/workflows/ci.yml)

## Architecture

```mermaid
flowchart LR
  subgraph QBO["QuickBooks Online (adapter)"]
    OAUTH["OAuth 2.0, rotating refresh tokens"]
    REP["Reports API by month: P&L, Balance Sheet, Cash Flow"]
    ACC["Account query"]
  end
  subgraph RUN["cashflow run: one client, one closed month"]
    CONN["Connection check"]
    MAP["Approved mappings only"]
    ST["Indirect-method lines"]
    W["Writer: input cells only"]
    RC["Recalculate the template's formulas"]
    CK["Tie-out checks"]
  end
  TPL[("templates/ monthly-cash-flow.xlsx + map.yml")]
  SUG["Suggester: rules or OpenAI"]
  PG[("Postgres: runs, checks, workbooks, mappings, suggestions, audit")]
  API["FastAPI"]
  DASH["Streamlit dashboard"]
  OAUTH --> CONN
  REP --> ST
  ACC --> MAP
  CONN --> MAP --> ST --> W --> RC --> CK --> PG
  TPL --> W
  MAP -. "new account" .-> SUG -. "suggestion, held run" .-> PG
  PG --> API --> DASH
  DASH -. "approve, re-run, mark sent" .-> API
```

The synthetic firm has eight client companies (a landscaper, a dental practice, a brewery, a property manager, an HVAC contractor, a design agency, a veterinary clinic and a bakery). Each has its own chart of accounts and 21 months of double-entry books, January 2025 to September 2026, with posting timestamps. The seed runs the August close against the books as they stood on 8 September and marks every workbook as sent. It then runs the September close against the books as of 8 October, which ends with 5 clients ready and 3 held, each held for a different reason.

## Design decisions

**The workbook is never re-saved by a spreadsheet library.** openpyxl and similar libraries load a workbook into their own model and write a new file from it, and they drop what the model doesn't cover. A plain openpyxl load-and-save of this template loses the disclaimer text box and rebuilds the chart; [tests/test_workbook.py](tests/test_workbook.py) keeps a test that shows it. [src/cashflow/workbook.py](src/cashflow/workbook.py) edits the worksheet XML inside the .xlsx package instead. It changes only the target cells and copies every other part byte for byte. After each fill it proves three things: the formula inventory (the exact XML of all 172 `<f>` elements) is unchanged, every part other than the two worksheets matches the template, and every cell other than the inputs matches.

**Cells are found by label, never by address.** [templates/monthly-cash-flow.map.yml](templates/monthly-cash-flow.map.yml) names each input row by the text of its label in column A, and each month by its header. If someone inserts a row, the numbers move with it; `test_an_inserted_row_moves_the_numbers_with_it` builds such a template and checks that the results are identical. A label that is missing or appears twice stops the run before anything is written. So does an input cell that holds a formula, or a formula row that someone typed a number over. The writer also refuses outright to write into a formula cell.

**The template's own formulas are recalculated, and the results are stored in the file.** A file whose formula cells carry stale cached values shows zeros in any viewer that doesn't recalculate, such as a phone's mail preview. After writing the inputs, the writer evaluates the workbook with the [`formulas`](https://github.com/vinci1it2000/formulas) library and stores each result as that formula cell's cached value. It also keeps the workbook's flag that tells Excel to recalculate everything on open. The checks read the recalculated figures, so they test the template's arithmetic as delivered rather than a separate copy of it.

**Two independent QuickBooks figures, plus the template's own check.** A run is `ready` only when recalculated ending cash equals the bank accounts on the balance sheet for every month, and net change and ending cash equal QuickBooks' own cash flow report. The template's Difference row must also recalculate to zero. Before any of that, P&L net income has to equal the change in retained earnings plus the balance sheet's current-year Net Income row. A journal entry posted straight to retained earnings breaks that check, and it would also put the cash flow out by the same amount; the Saltbrook client shows it at $2,350.00. The checks are listed in [src/cashflow/runner.py](src/cashflow/runner.py).

**Mappings are keyed on QuickBooks account IDs, and a person approves every one.** Report rows carry the account's ID, so renaming an account never moves it to another line. A balance sheet account with a balance and no approved mapping holds the run and files a suggestion. [src/cashflow/mapping.py](src/cashflow/mapping.py) has two suggesters behind one interface. The rules work offline from QuickBooks' account type, detail type and a few name keywords. The OpenAI suggester uses the Responses API with a strict JSON schema whose `line_key` enum is the list of template lines. A reply that fails validation, or a failed call, falls back to the rules, and the reason the reviewer sees says so. A suggestion is never a mapping; [src/cashflow/runner.py](src/cashflow/runner.py) `approve_and_rerun` writes the mapping, audits it and re-runs the held month.

**The indirect method comes from balance sheet movements.** [src/cashflow/statement.py](src/cashflow/statement.py) requests the balance sheet from one month before the fiscal year, so January has an opening balance. An asset that grows used cash and a liability that grows supplied cash, so the same sign rule turns accumulated depreciation's growth into the add-back. Retained earnings and the computed Net Income row stay out of the lines, because net income already covers them.

**Refresh tokens rotate, so refreshes are serialized.** Intuit issues a new refresh token on every refresh. [src/cashflow/qbo/intuit.py](src/cashflow/qbo/intuit.py) refreshes under `select ... for update` on the client's token row and writes the new pair in the same short transaction on its own connection. Two overlapping runs therefore can't leave a stale token behind. A 401 forces one refresh and a retry, 429 and 5xx back off, and `invalid_grant` marks the client disconnected so the dashboard can say who needs to reconnect it. Requests send `minorversion=75` and `summarize_column_by=Month`.

**A month that changed after it was sent is a warning, not a hold.** Each run compares the months of the last workbook sent to the client with what QuickBooks says now. Northvane's August debit card purchase, posted on 24 September, shows up in the September run as "ending cash is now $1,240.00 lower". The September workbook is still correct, but the client may need a corrected August.

## Running it

```bash
cp .env.example .env              # optional: change host ports or turn on OpenAI suggestions
docker compose up --build -d --wait
```

The `pipeline` service migrates and, if the database is empty, loads the synthetic firm and runs both closes; it takes about 15 seconds. Then:

- Dashboard: http://localhost:8501. Close status, run detail with the checks, tie-out and a cached-value preview of the workbook, the mapping queue and the template page. The sidebar switches the month and the staff member actions are recorded under.
- API docs: http://localhost:8000/docs

### Work through the September close from the command line

```bash
docker compose run --rm pipeline cashflow close --period 2026-09            # 5 ready, 3 held
docker compose run --rm pipeline cashflow suggestions                       # the new loan account, suggested: Loans
docker compose run --rm pipeline cashflow approve --client birchline-grounds --by mara.quist@example.com   # maps it, re-runs: ready
docker compose run --rm pipeline cashflow simulate reconnect --client rye-and-ember-bakery
docker compose run --rm pipeline cashflow run --client rye-and-ember-bakery --period 2026-09              # ready
docker compose run --rm pipeline cashflow deliver --client wrenfield-dental --period 2026-09 --by mara.quist@example.com
curl -fsS -o wrenfield-2026-09.xlsx "http://localhost:8000/clients/wrenfield-dental/workbook?period=2026-09"
docker compose run --rm pipeline cashflow inventory                         # 172 formulas and the template's fingerprint
docker compose run --rm pipeline cashflow close --period 2026-09            # 7 ready, 1 held
```

Saltbrook stays held because the fix belongs in QuickBooks: the bookkeeper reclassifies the retained earnings entry, and the next run picks it up. The same actions are buttons on the dashboard. To start over, run `docker compose down -v` and bring the stack up again.

### OpenAI suggestions

Set `CASHFLOW_SUGGESTER=openai` and `OPENAI_API_KEY` in `.env` and restart the stack. The model defaults to `gpt-5-mini` (`CASHFLOW_OPENAI_MODEL`). Suggestions are filed when a run meets an unmapped account, so to see one, start from an empty database.

### Your own template

Point `CASHFLOW_TEMPLATE` and `CASHFLOW_TEMPLATE_MAP` at a workbook and a map that names its labels. `cashflow inventory path/to/template.xlsx` prints its formula count and fingerprint, and the dashboard's Template page says whether the map still fits. The synthetic template is built by [scripts/build_template.py](scripts/build_template.py) with XlsxWriter. The automation never generates the template; it only fills it.

### Without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export CASHFLOW_DSN=postgresql://cashflow:cashflow@localhost:5432/cashflow
cashflow migrate && cashflow seed
uvicorn cashflow.api:app --port 8000 &
CASHFLOW_API_URL=http://localhost:8000 streamlit run dashboard/app.py
```

## Tests

```bash
pytest          # needs Postgres at CASHFLOW_DSN; fixtures rebuild the schema
ruff check .
```

The suite has 65 tests and runs against a real Postgres. It covers the report parser and the synthetic books: every client's balance sheet balances, the cash flow report reconciles, and the as-of view hides later postings. The indirect-method identity is checked for clean books, and the retained earnings break by its exact amount. On the writer side it checks inputs only, 172 formulas and the chart, text box, styles and workbook part byte-identical, cached values as a non-recalculating viewer reads them, refusal to write into a formula, duplicated and typed-over labels, an inserted row, and the recalculate-on-open flag. The rest covers the rules and OpenAI suggesters (against a fake client, including the fallback), approval and its audit, the Intuit adapter against a mock transport (rotation, reuse, 401 retry, backoff, `invalid_grant`, paging), and the seeded closes end to end, including approve-and-rerun and reconnect. It finishes with the API and every dashboard page rendered against a live API process. CI runs the suite, then brings the Compose stack up and runs [scripts/smoke.sh](scripts/smoke.sh), which executes every command block in this README.

## Layout

```
templates/          monthly-cash-flow.xlsx (the synthetic house template), monthly-cash-flow.map.yml
sql/                001_schema.sql
src/cashflow/       workbook (writer, recalculation, proof), statement, runner (checks), mapping, lines, api, cli, seed
src/cashflow/qbo/   reports (parser), intuit (HTTP adapter), synthetic_source (QuickBooks-shaped reports), source
src/cashflow/synthetic.py   the made-up clients and their books
dashboard/          Streamlit app, one file per page under views/
scripts/            build_template.py, smoke.sh
tests/              pytest against Postgres, plus Streamlit AppTest pages against a live API
```

## What is deliberately left out

- **The OAuth consent flow.** The adapter refreshes and stores tokens. The first authorization, Intuit's redirect and code exchange, is a few lines of web glue and needs a registered Intuit app, so `simulate reconnect` stands in for it here. Tokens sit in a plain table; production kept them encrypted.
- **Delivery to client folders.** Finished workbooks are stored in Postgres and downloaded from the API. Production wrote them to each client's document folder; that adapter is out.
- **Staff sign-on.** The dashboard's "Signed in as" picker records who approved or sent what. Production sat behind the firm's single sign-on.
- **Non-calendar fiscal years and multi-currency.** Every synthetic client runs a calendar year in USD.
- **Scheduling.** `cashflow run --all` is a command; production ran it the morning after each client's books were marked closed.
- **Live model calls in CI.** The OpenAI suggester is tested against a fake client. Running it for real needs an API key and costs money.

## License

MIT, Alex Kurinets.
