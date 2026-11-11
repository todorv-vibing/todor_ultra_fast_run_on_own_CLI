# Harmonic → Clay ingest

Scripts used by the "Daily Harmonic net-new -> Clay ingest" routine (cloud) and the local Clay check.
Stdlib-only Python 3.

| File | Purpose |
|---|---|
| `ingest.py` | Parses Harmonic `get_saved_search_net_new` + `get_people(location)` output, validates every lead, posts one JSON record per lead to a market's Clay webhook, writes a report. `self-test` must pass before any posting. |
| `verify_clay.py` | Confirms in Clay that posted leads landed (name + LinkedIn present) and were evaluated (Founder Status / Founder AI). `--via api` (Clay Public API, `CLAY_PUBLIC_API_KEY`) or `--via cli` (Clay CLI from the Clay plugin). |
| `markets.json` | Market code → Clay table. Webhook URLs are passed with `--webhook` and are not stored in this public repo. |

## Payload contract

Clay's tables read `full_name` (→ Full Name, from which First/Last Name are derived), `linkedin_url`
(→ LinkedIn Profile, also the dedupe key) and `country` (→ Country). Any other shape leaves those
columns blank and the row is never evaluated. That happened 2026-09-14 → 2026-10-12 when the routine
lost this script and improvised its own. **Never post with an ad-hoc script.**

## Which searches run when

`rotation.json` is the single source of truth for the daily routine: each saved search, the
market it feeds, and the weekdays it runs. `python3 ingest.py today` prints today's list
(markets without a Clay table in `markets.json` come back under `skipped`). `self-test` fails
if a declared search is on no weekday. Add a new search here, not in the routine prompt.
Searches deliberately left out are listed under `not_rotated` with the reason.

## Guards against a silently skipped market

A market whose searches never run, or that has no webhook, fails loudly instead of quietly
getting nothing (this cost Slovenia and Bosnia a month of leads in 2026):

- `self-test` fails if any market in `markets.json` has no scheduled search, or a search is on no weekday.
- `preflight --webhooks FILE` (the routine writes FILE in /tmp from its prompt) fails if any market
  has no webhook, two markets share one, or the rotation is broken. It writes a stamp for today.
- `post` refuses without today's stamp (so an outdated routine prompt is blocked, not silent) and
  refuses a webhook that is not the one registered for that market. Manual runs: `--no-preflight`.
- `coverage` compares Harmonic's saved searches (`list_saved_searches`, saved verbatim) with
  `rotation.json`: any search that is neither rotated nor listed under `not_rotated` is reported.

## Typical run

```bash
python3 ingest.py self-test
python3 ingest.py preflight --webhooks /tmp/hc/webhooks.json  # every market has searches + a webhook
python3 ingest.py urns /tmp/hc/172553_p1.txt            # URN batches for get_people(location)
python3 ingest.py post --market RO --search-urn urn:harmonic:saved_search:172553 \
  --webhook "$RO_WEBHOOK" --locations /tmp/hc/loc_1.txt --report /tmp/hc/report_172553.json \
  /tmp/hc/172553_p1.txt /tmp/hc/172553_p2.txt
python3 verify_clay.py posted --via api --reports /tmp/hc/report_*.json --write-missing /tmp/hc/missing.txt
python3 ingest.py post ... --only-linkedin /tmp/hc/missing.txt   # re-post only what Clay is missing
```

Exit codes: `ingest.py` 0 ok / 3 refused, nothing posted / 4 some posts failed;
`verify_clay.py` prints `status`: ok | pending | eval_errors | needs_repost | broken.
