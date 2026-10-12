#!/usr/bin/env python3
"""Check in Clay that ingested Harmonic leads landed correctly and got evaluated.

Two modes:
  posted  --reports R.json...            look up every lead an ingest.py report says was posted
  health  --since ISO [--market M ...]   audit all rows created since a time (no reports needed)

Two backends:
  --via api   Clay Public API (synced query; needs CLAY_PUBLIC_API_KEY; only tables with
              query_sync=true in markets.json - the workspace allows 5)
  --via cli   the Clay CLI from the Clay plugin (`clay tables query-live`; needs `clay login`).
              Use --clay-bin / CLAY_BIN when `clay` is not directly executable.

Lead verdicts (posted mode):
  ok | pending (Clay still evaluating) | missing (row not in Clay -> re-post) |
  broken_mapping (row exists but Full Name or LinkedIn empty -> payload contract broken: STOP) |
  eval_error (Clay's founder check or founder AI errored -> re-run that cell in the Clay app)
Overall status: ok | pending | eval_errors | needs_repost | broken (worst wins)
`--write-missing FILE` writes missing LinkedIn URLs, one per line, for `ingest.py post --only-linkedin`.

Exit codes: 0 report produced | 2 usage/config | 5 auth or network problem talking to Clay
"""
import argparse
import datetime as _dt
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API_URL = "https://api.clay.com/public/v0/tables/query"
VALUE_COLS = {"li": "LinkedIn Profile", "name": "Full Name", "country": "Country", "is_founder": "Is Founder",
              "founder_score": "Founder Score", "company": "Company Name"}
STATUS_COLS = {"founder_status": "Founder Status", "founder_ai": "Founder"}
CLAYQL_MAX = 1500  # query-live limit, measured after server-side expansion


class ClayError(Exception):
    pass


def load_markets():
    with open(os.path.join(HERE, "markets.json"), encoding="utf-8") as fh:
        return json.load(fh)["markets"]


def _col(name):
    return "{{%s}}" % name


# ---------------------------------------------------------------- CLI backend (Clay plugin)

class CliBackend:
    def __init__(self, clay_bin):
        self.cmd = shlex.split(clay_bin, posix=(os.name != "nt"))

    def ql(self, table_id, query):
        try:
            p = subprocess.run(self.cmd + ["tables", "query-live", table_id, "--query", query],
                               capture_output=True, text=True, encoding="utf-8", timeout=180)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ClayError("could not run clay CLI: %s" % e)
        if p.returncode != 0:
            raise ClayError("clay exit %s: %s" % (p.returncode, (p.stderr or p.stdout)[:400]))
        return json.loads(p.stdout).get("results", [])

    @staticmethod
    def status_case(col):
        c = _col(col)
        return ("CASE WHEN RESULTS(%s) THEN 'done' WHEN RUN_CONDITION_NOT_MET(%s) THEN 'skipped' "
                "WHEN QUEUED(%s) OR HAS_NOT_RUN(%s) THEN 'pending' ELSE 'error' END") % (c, c, c, c)

    def lookup(self, table_id, urls):
        rows = {}
        values_sel = "SELECT " + ", ".join("%s AS %s" % (_col(v), k) for k, v in VALUE_COLS.items())
        for i in range(0, len(urls), 4):  # <=5 WHERE conditions; 4 keeps expanded queries short
            batch = urls[i:i + 4]
            where = " WHERE " + " OR ".join("%s = '%s'" % (_col("LinkedIn Profile"), u.replace("'", "''"))
                                            for u in batch)
            for r in self.ql(table_id, values_sel + where):
                rows[r.get("li")] = dict(r)
            for key, col in STATUS_COLS.items():
                q = "SELECT %s AS li, %s AS st%s" % (_col("LinkedIn Profile"), self.status_case(col), where)
                for r in self.ql(table_id, q):
                    rows.setdefault(r.get("li"), {"li": r.get("li")})[key] = r.get("st")
        return rows

    def health(self, table_id, since):
        w = " WHERE %s >= '%s'" % (_col("Created At"), since)
        fs, fa = _col("Founder Status"), _col("Founder")
        a = self.ql(table_id, "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE EMPTY(%s)) AS no_name, "
                              "COUNT(*) FILTER (WHERE EMPTY(%s)) AS no_linkedin, COUNT(*) FILTER (WHERE EMPTY(%s)) AS no_country%s"
                    % (_col("Full Name"), _col("LinkedIn Profile"), _col("Country"), w))[0]
        b = self.ql(table_id, "SELECT COUNT(*) FILTER (WHERE RESULTS(%s)) AS fs_done, "
                              "COUNT(*) FILTER (WHERE QUEUED(%s) OR HAS_NOT_RUN(%s)) AS fs_pending, "
                              "COUNT(*) FILTER (WHERE RUN_CONDITION_NOT_MET(%s) OR MISSING_INPUT(%s)) AS fs_skipped%s"
                    % (fs, fs, fs, fs, fs, w))[0]
        c = self.ql(table_id, "SELECT COUNT(*) AS founders, COUNT(%s) AS scored, COUNT(*) FILTER (WHERE RESULTS(%s)) AS fa_done, "
                              "COUNT(*) FILTER (WHERE QUEUED(%s) OR HAS_NOT_RUN(%s)) AS fa_pending, "
                              "COUNT(*) FILTER (WHERE RUN_CONDITION_NOT_MET(%s)) AS fa_skipped%s AND %s = 'yes'"
                    % (_col("Founder Score"), fa, fa, fa, fa, w, _col("Is Founder")))[0]
        counts = {k: int(v or 0) for d in (a, b, c) for k, v in d.items()}
        samples = self.ql(table_id, "SELECT %s AS li, %s AS company, %s AS added%s AND EMPTY(%s) ORDER BY %s DESC LIMIT 10"
                          % (_col("LinkedIn Profile"), _col("Company Name"), _col("Created At"), w,
                             _col("Full Name"), _col("Created At")))
        return counts, samples


# ---------------------------------------------------------------- API backend (Clay Public API)

class ApiBackend:
    def __init__(self, key):
        self.key = key

    def _query(self, table_id, flt, cursor=None):
        # System fields such as "Created At" are rejected by the synced query API ("Invalid field ID").
        select = [{"field": v, "as": k} for k, v in list(VALUE_COLS.items()) + list(STATUS_COLS.items())]
        body = {"query": {"tables": [{"id": table_id}], "select": select, "filter": flt, "field_mode": "names"},
                "limit": 100}
        if cursor:
            body["cursor"] = cursor
        req = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "clay-api-key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ClayError("Clay API HTTP %s: %s" % (e.code, e.read()[:400].decode("utf-8", "replace")))
        except (urllib.error.URLError, OSError) as e:
            raise ClayError("Clay API unreachable: %s" % e)

    @staticmethod
    def _flatten(row):
        out = {}
        for k in list(VALUE_COLS) + list(STATUS_COLS):
            cell = row.get(k) or {}
            st = cell.get("status")
            if k in STATUS_COLS:
                out[k] = {"success": "done", "error": "error"}.get(st, "pending")
            else:
                out[k] = cell.get("value") if st == "success" else None
        return out

    def _all(self, table_id, flt):
        rows, cursor = [], None
        while True:
            resp = self._query(table_id, flt, cursor)
            rows += [self._flatten(r) for r in resp.get("data", [])]
            cursor = resp.get("cursor")
            if not cursor:
                return rows

    def lookup(self, table_id, urls):
        rows = {}
        for i in range(0, len(urls), 100):
            for r in self._all(table_id, {"field": "LinkedIn Profile", "op": "in", "value": urls[i:i + 100]}):
                rows[r.get("li")] = r
        return rows

    def health(self, table_id, since):
        raise ClayError("health mode is not available via the API: the synced query API cannot filter on "
                        "Created At. Use --via cli for health checks.")
        rows = []
        founders = [r for r in rows if str(r.get("is_founder") or "").lower() == "yes"]
        counts = {
            "total": len(rows), "no_name": sum(1 for r in rows if not r["name"]),
            "no_linkedin": sum(1 for r in rows if not r["li"]), "no_country": sum(1 for r in rows if not r["country"]),
            "fs_done": sum(1 for r in rows if r["founder_status"] == "done"),
            "fs_pending": sum(1 for r in rows if r["founder_status"] == "pending"), "fs_skipped": 0,
            "founders": len(founders), "scored": sum(1 for r in founders if r.get("founder_score") is not None),
            "fa_done": sum(1 for r in founders if r["founder_ai"] == "done"),
            "fa_pending": sum(1 for r in founders if r["founder_ai"] == "pending"), "fa_skipped": 0,
        }
        samples = [{"li": r["li"], "company": r["company"]} for r in rows if not r["name"]][:10]
        return counts, samples


# ---------------------------------------------------------------- verdicts

def verdict(row):
    if row is None:
        return "missing"
    if not row.get("name") or not row.get("li"):
        return "broken_mapping"
    fs = row.get("founder_status")
    if fs == "error":
        return "eval_error"
    if fs in ("pending", None):
        return "pending"
    if str(row.get("is_founder") or "").lower() == "yes":
        fa = row.get("founder_ai")
        if fa == "error":
            return "eval_error"
        if fa in ("pending", None):
            return "pending"
    return "ok"


def health_status(c):
    c["fs_error"] = max(0, c["total"] - c["fs_done"] - c["fs_pending"] - c["fs_skipped"])
    c["fa_error"] = max(0, c["founders"] - c["fa_done"] - c["fa_pending"] - c["fa_skipped"])
    if c["no_name"] or c["no_linkedin"]:
        return "broken"
    if c["fs_error"] or c["fa_error"]:
        return "eval_errors"
    if c["fs_pending"] or c["fa_pending"]:
        return "pending"
    return "ok"


RANK = ["ok", "pending", "eval_errors", "needs_repost", "broken"]


def worst(statuses):
    return max(statuses or ["ok"], key=RANK.index)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["posted", "health"])
    ap.add_argument("--via", choices=["api", "cli"], required=True)
    ap.add_argument("--reports", nargs="*", default=[])
    ap.add_argument("--since")
    ap.add_argument("--market", action="append", default=[])
    ap.add_argument("--clay-bin", default=os.environ.get("CLAY_BIN", "clay"))
    ap.add_argument("--write-missing")
    ap.add_argument("--wait-seconds", type=int, default=0,
                    help="sleep before checking, so Clay can finish enriching (keep under the shell timeout)")
    args = ap.parse_args(argv)

    if args.wait_seconds > 0:
        time.sleep(args.wait_seconds)
    markets = load_markets()
    if args.via == "api":
        key = os.environ.get("CLAY_PUBLIC_API_KEY")
        if not key:
            print(json.dumps({"status": "skipped", "reason": "CLAY_PUBLIC_API_KEY is not set in this environment"}))
            return 0
        backend = ApiBackend(key)
    else:
        backend = CliBackend(args.clay_bin)

    out, skipped, missing_urls, statuses = [], [], [], []
    try:
        if args.mode == "posted":
            if not args.reports:
                print("posted mode needs --reports", file=sys.stderr)
                return 2
            by_market = {}
            for path in args.reports:
                with open(path, encoding="utf-8") as fh:
                    rep = json.load(fh)
                by_market.setdefault(rep["market"], set()).update(
                    p["linkedin_url"] for p in rep.get("posted", []) if p.get("ok"))
            for m, urls in sorted(by_market.items()):
                cfg = markets[m]
                if args.via == "api" and not cfg.get("query_sync"):
                    skipped.append({"market": m, "reason": "table not enabled for synced query (5-table limit)",
                                    "unchecked": len(urls)})
                    continue
                rows = backend.lookup(cfg["table_id"], sorted(urls))
                items = [{"linkedin_url": u, "verdict": verdict(rows.get(u)), "row": rows.get(u)} for u in sorted(urls)]
                counts = {}
                for it in items:
                    counts[it["verdict"]] = counts.get(it["verdict"], 0) + 1
                missing_urls += [it["linkedin_url"] for it in items if it["verdict"] == "missing"]
                st = ("broken" if counts.get("broken_mapping") else "needs_repost" if counts.get("missing")
                      else "eval_errors" if counts.get("eval_error") else "pending" if counts.get("pending") else "ok")
                statuses.append(st)
                out.append({"market": m, "table": cfg["table_name"], "status": st, "checked": len(items),
                            "counts": counts,
                            "missing_country": sum(1 for it in items if it["row"] and not it["row"].get("country")),
                            "problems": [it for it in items if it["verdict"] != "ok"][:60]})
        else:
            if not args.since:
                print("health mode needs --since", file=sys.stderr)
                return 2
            for m in (args.market or sorted(markets)):
                cfg = markets[m]
                if args.via == "api" and not cfg.get("query_sync"):
                    skipped.append({"market": m, "reason": "table not enabled for synced query (5-table limit)"})
                    continue
                counts, samples = backend.health(cfg["table_id"], args.since)
                st = health_status(counts)
                statuses.append(st)
                out.append({"market": m, "table": cfg["table_name"], "status": st, "counts": counts,
                            "nameless_samples": samples})
    except ClayError as e:
        print(json.dumps({"status": "clay_unreachable", "error": str(e)}))
        return 5

    if args.write_missing:
        with open(args.write_missing, "w", encoding="utf-8") as fh:
            fh.write("".join(u + "\n" for u in missing_urls))
    print(json.dumps({"status": worst(statuses), "via": args.via, "mode": args.mode,
                      "checked_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                      "markets": out, "skipped": skipped, "missing_linkedin": missing_urls},
                     ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
