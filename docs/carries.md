# Carry ledger

This fork's `main` is upstream's `main` plus the ordered stack of feature
commits below, one per carry, each with a `carry/NN-name` branch at its tip.
Everything the fork adds is in one of them. Only the top of the stack is built
and tested: earlier branches mark where a feature ends and may not build alone,
because later carries lean on earlier ones and the fork's migrations are one chain.

## The stack

Base: `upstream/main` at `ecee8242`. Previous history: tag `pre-ledger-2026-11-02`.

| # | Branch | What it carries | Retire when |
|---|--------|-----------------|-------------|
| 1 | `carry/01-migrations` | Fork migrations `f001`+, the one-line links to upstream's chain | Never whole: applied migrations stay. A retired feature adds a migration that drops its tables |
| 2 | `carry/02-security` | Safe uploads, CSP, regex timeout, re-auth under 15 minutes, encrypted TOTP | Upstream ships each fix |
| 3 | `carry/03-money` | Account-currency amounts, unstamped rows, opening rows | Upstream fixes foreign-currency balances the same way |
| 4 | `carry/04-backup` | Server-side database backups with retention | Upstream ships scheduled backups |
| 5 | `carry/05-akahu` | Akahu provider, pending rows, bank edits and removals | Upstream ships an Akahu provider |
| 6 | `carry/06-sync` | `PROVIDER_REFRESH_MINUTES`, rows the provider categorises or names later | Upstream makes the refresh interval configurable |
| 7 | `carry/07-rules-nz` | New Zealand rule pack, Insurance and Loan repayments defaults | Upstream takes the pack |
| 8 | `carry/08-payees` | Merchant logos and icons on payees | Upstream ships payee logos |
| 9 | `carry/09-loans` | Akahu `loan_details`, loan card, revolving limits, equity chart and switch | Upstream models weekly repayments, revolving loans and connected loan details |
| 10 | `carry/10-transfers` | From-to pill with thumbnails, one row per pair | Upstream draws transfers as one row with both accounts |
| 11 | `carry/11-accounts-order` | Drag accounts and wallets into your own order | Upstream ships ordering |
| 12 | `carry/12-goals` | Periodic targets, contribution history, ongoing goals | Upstream ships periodic goals |
| 13 | `carry/13-reports` | 5Y and 10Y ranges, money map timeline, per-tab defaults, investment and loan exclusions | Upstream covers each |
| 14 | `carry/14-recurring` | Recurring transfers, payees, varying bills linked on payee | Upstream ships recurring transfers |
| 15 | `carry/15-budgets` | Budget end months, budget groups and caps | Upstream ships capped groups |
| 16 | `carry/16-forecast` | Forecast page, scenarios, loan projection | Upstream's budget forecast grows to cover cash, loans and scenarios |
| 17 | `carry/17-insights` | Monthly insights briefing | Upstream ships an equivalent |
| 18 | `carry/18-ui` | Chart legend toggles, navigation, small interface changes | Upstream covers each |
| 19 | `carry/19-locales` | Fork strings in every locale. Czech and Hungarian hold English text | With each feature's retirement |
| 20 | `carry/20-contract` | API contract baseline for fork routes | Regenerated on each sync |

The owner approves every retirement, with any functional loss spelled out first.

## Syncing with upstream

1. `git fetch upstream` and read `git log main..upstream/main`. Look for anything that overlaps the table above and put each retirement candidate to the owner.
2. Rebase the stack: `git switch -c sync/<date> main && git rebase --update-refs upstream/main`. `--update-refs` moves every `carry/*` branch with it. Turn on `rerere` so repeat conflicts resolve themselves.
3. Check whether upstream added migrations. The chain must stay one line (`backend/scripts/check_migration_chain.py`): upstream's first new migration moves onto the last fork migration applied, and later fork migrations go after upstream's newest.
4. In the contract baseline, take upstream's and regenerate: `UPDATE_API_CONTRACT=1 uv run pytest tests/api_contract -n 0`.
5. Test the top of the stack, including PostgreSQL. Rehearse any data migration on a copy of the database first.
6. Back up the database, force-push `main` and every `carry/*` branch with `--force-with-lease`, then deploy.

## Retiring a carry

Drop or shrink its commit during the rebase. Add a fork migration for any tables or columns it owned. Then remove its row here and add it below.

## Retired

| When | Carry | Replaced by | What was lost |
|------|-------|-------------|---------------|
| 2026-11 | Recorded loan payments, recurring loan payment schedules | Upstream's mortgage payment split | Recording a payment as transfer plus interest on a manual loan; schedules with a fixed interest share |
| 2026-11 | Loan type, secured asset link, dashboard loan row | Upstream's loan accounts | A loan can only be secured on a property or vehicle; "line of credit" and "vehicle" subtypes became "other" and "auto" |
| 2026-11 | Negative stored balance on connected loans | Upstream's positive amount owed | Nothing shown changes |
| 2026-11 | Recurring exact-amount fix | Upstream #1213 | Nothing |
| 2026-11 | `SYNC_STALE_MINUTES` | Upstream's `BANK_SYNC_MIN_INTERVAL_HOURS` | Nothing |
| 2026-11 | Legend toggle on the dashboard's comparison month | Upstream's month comparison | That one legend entry no longer hides its line |
| 2026-11 | Default account on a new recurring item | Upstream requires a choice | One more click |
