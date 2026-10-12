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

## Typical run

```bash
python3 ingest.py self-test
python3 ingest.py urns /tmp/hc/172553_p1.txt            # URN batches for get_people(location)
python3 ingest.py post --market RO --search-urn urn:harmonic:saved_search:172553 \
  --webhook "$RO_WEBHOOK" --locations /tmp/hc/loc_1.txt --report /tmp/hc/report_172553.json \
  /tmp/hc/172553_p1.txt /tmp/hc/172553_p2.txt
python3 verify_clay.py posted --via api --reports /tmp/hc/report_*.json --write-missing /tmp/hc/missing.txt
python3 ingest.py post ... --only-linkedin /tmp/hc/missing.txt   # re-post only what Clay is missing
```

Exit codes: `ingest.py` 0 ok / 3 refused, nothing posted / 4 some posts failed;
`verify_clay.py` prints `status`: ok | pending | eval_errors | needs_repost | broken.
