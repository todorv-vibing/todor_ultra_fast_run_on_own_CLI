#!/usr/bin/env python3
"""Harmonic net-new -> Clay webhook ingest for LAUNCHub deal flow.

This is the ONE script the "Daily Harmonic net-new -> Clay ingest" routine and the local
Clay check use to post leads. Do not replace it with an ad-hoc script: the Clay tables read
specific payload keys (see CLAY_CONTRACT) and silently leave columns blank for anything else.
That is exactly what happened between 2026-09-14 and 2026-10-12.

Subcommands (stdlib only, Python 3.8+):
  self-test                       parser + payload-contract checks against bundled fixtures
  urns FILE...                    print person URNs from net-new output (for get_people batches of 50)
  build --market M FILE...        parse + validate + print the records that would be posted
  post  --market M --webhook URL FILE...
                                  parse + validate + POST one record per request, write a report

Inputs (FILE) are Harmonic `get_saved_search_net_new` outputs saved verbatim (the TOON-style
text the MCP tool returns), or JSON (a list of people, or {"results"|"items"|"data": [...]}).
`--locations` takes `get_people(field_groups=["location"])` outputs saved verbatim, or JSON
{"urn:harmonic:person:1": "Country", ...}.

Exit codes: 0 ok | 2 usage/config | 3 validation refused (nothing posted) | 4 some posts failed
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

INGEST_VERSION = "2.0.0"

# Payload keys the Clay tables read. Full Name <- full_name, LinkedIn Profile <- linkedin_url,
# Country <- country (First/Last Name are derived from Full Name inside Clay).
CLAY_CONTRACT = ("full_name", "linkedin_url", "country")
REQUIRED = ("full_name", "linkedin_url", "harmonic_urn")

URN_RE = re.compile(r"^urn:harmonic:person:\d+$")
LINKEDIN_RE = re.compile(r"^https://linkedin\.com/in/[^/?#\s]+$")
TOON_HEADER_RE = re.compile(r"^\s*items\[(\d+)\](?:\{([^}]*)\})?:(.*)$")

HERE = os.path.dirname(os.path.abspath(__file__))


class ParseError(Exception):
    pass


# ---------------------------------------------------------------- parsing: net-new results

def _split_toon_row(line):
    """Split one TOON row on commas, honouring "..." quoting and backslash escapes."""
    fields, buf, quoted, in_quotes, i = [], [], False, False, 0
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
    while i < len(line):
        ch = line[i]
        if in_quotes:
            if ch == "\\" and i + 1 < len(line):
                buf.append(escapes.get(line[i + 1], line[i + 1]))
                i += 2
                continue
            if ch == '"':
                in_quotes = False
            else:
                buf.append(ch)
        elif ch == '"' and not "".join(buf).strip():
            in_quotes, quoted, buf = True, True, []
        elif ch == ",":
            fields.append((("".join(buf)).strip() if not quoted else "".join(buf), quoted))
            buf, quoted = [], False
        else:
            buf.append(ch)
        i += 1
    if in_quotes:
        raise ParseError("unterminated quote in row: %r" % line[:120])
    fields.append((("".join(buf)).strip() if not quoted else "".join(buf), quoted))
    return [None if (not q and v == "null") else v for v, q in fields]


def parse_toon(text, source="<text>"):
    lines = text.splitlines()
    people, found_header = [], False
    i = 0
    while i < len(lines):
        m = TOON_HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        found_header = True
        expected = int(m.group(1))
        cols = [c.strip() for c in (m.group(2) or "").split(",") if c.strip()]
        i += 1
        if expected == 0:
            continue
        if m.group(3).strip():
            raise ParseError("%s: unexpected text after items[%d] header" % (source, expected))
        rows = []
        while i < len(lines) and lines[i].startswith((" ", "\t")) and lines[i].strip():
            rows.append(lines[i].strip())
            i += 1
        if len(rows) != expected:
            raise ParseError("%s: header says items[%d] but found %d rows (file not saved verbatim?)"
                             % (source, expected, len(rows)))
        for r in rows:
            vals = _split_toon_row(r)
            if len(vals) != len(cols):
                raise ParseError("%s: row has %d fields, header has %d: %r"
                                 % (source, len(vals), len(cols), r[:160]))
            people.append(dict(zip(cols, vals)))
    if not found_header:
        raise ParseError("%s: no 'items[N]{...}:' header found" % source)
    return people


def parse_people_file(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    stripped = text.lstrip()
    if stripped.startswith(("[", "{")):
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("results", "items", "data", "people"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                raise ParseError("%s: JSON object has no results/items/data/people list" % path)
        if not isinstance(data, list):
            raise ParseError("%s: JSON must be a list of people" % path)
        return data
    return parse_toon(text, source=path)


# ---------------------------------------------------------------- parsing: locations

def parse_locations_text(text, source="<text>"):
    """Parse get_people(field_groups=["location"]) output -> {urn: {"country":..,"city":..}}."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        data = json.loads(text)
        return {k: ({"country": v} if isinstance(v, str) or v is None else v) for k, v in data.items()}
    out, block = {}, []
    blocks = []
    for line in text.splitlines():
        if re.match(r"^\s*-\s+personFound:", line):
            if block:
                blocks.append(block)
            block = [line]
        elif block:
            block.append(line)
    if block:
        blocks.append(block)
    for b in blocks:
        joined = "\n".join(b)
        m = re.search(r'entityUrn:\s*"?(urn:harmonic:person:\d+)"?', joined) or \
            re.search(r'urn:\s*"?(urn:harmonic:person:\d+)"?', joined)
        if not m:
            continue
        loc = {"country": None, "city": None}
        in_loc, loc_indent = False, None
        for line in b:
            lm = re.match(r"^(\s*)location:\s*(.*)$", line)
            if lm:
                in_loc = lm.group(2).strip() in ("", "|")
                loc_indent = len(lm.group(1))
                continue
            if in_loc:
                indent = len(line) - len(line.lstrip())
                if line.strip() and indent <= loc_indent:
                    in_loc = False
                    continue
                km = re.match(r"^\s*(country|city):\s*(.*?)\s*$", line)
                if km:
                    val = km.group(2).strip().strip('"')
                    loc[km.group(1)] = None if val in ("", "null") else val
        out[m.group(1)] = loc
    return out


def load_locations(paths):
    merged = {}
    for p in paths or []:
        with open(p, encoding="utf-8") as fh:
            merged.update(parse_locations_text(fh.read(), source=p))
    return merged


# ---------------------------------------------------------------- records

def normalize_linkedin(url):
    if not url:
        return None
    u = url.strip()
    m = re.match(r"^(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/?#\s]+)/?(?:[?#].*)?$", u, re.I)
    return "https://linkedin.com/in/%s" % m.group(1) if m else u


def build_records(people, market, search_urn, locations, now=None):
    now = now or _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    records, invalid, seen, dupes = [], [], set(), 0
    for idx, p in enumerate(people):
        name = p.get("full_name") or p.get("name")
        name = re.sub(r"\s+", " ", name).strip() if isinstance(name, str) else None
        urn = p.get("harmonic_urn") or p.get("urn") or p.get("entityUrn") or p.get("entity_urn")
        li = normalize_linkedin(p.get("linkedin_url") or p.get("linkedin"))
        loc = locations.get(urn) or {}
        parts = name.split(" ", 1) if name else ["", ""]
        rec = {
            "full_name": name,
            "first_name": parts[0],
            "last_name": parts[1] if len(parts) > 1 else "",
            "linkedin_url": li,
            "country": loc.get("country") or p.get("country") or None,
            "city": loc.get("city") or None,
            "headline": p.get("headline"),
            "harmonic_urn": urn,
            "harmonic_console_url": p.get("console_url")
            or ("https://console.harmonic.ai/dashboard/person/%s" % urn if urn else None),
            "saved_search_urn": search_urn,
            "market": market,
            "ingested_at": now,
            "ingest_source": "harmonic-net-new",
            "ingest_version": INGEST_VERSION,
        }
        problems = []
        if (p.get("entity_type") or "PERSON") != "PERSON":
            problems.append("entity_type is %r, expected PERSON" % p.get("entity_type"))
        if not name or name.lower() == "null" or len(name) < 2:
            problems.append("missing full_name")
        if not li or not LINKEDIN_RE.match(li):
            problems.append("bad linkedin_url %r" % li)
        if not urn or not URN_RE.match(str(urn)):
            problems.append("bad harmonic_urn %r" % urn)
        if problems:
            invalid.append({"index": idx, "problems": problems, "name": name, "urn": urn})
            continue
        if li in seen:
            dupes += 1
            continue
        seen.add(li)
        records.append(rec)
    return records, invalid, dupes


def check_contract(records):
    """Every record must carry the keys Clay reads, with the two identity keys non-empty."""
    for r in records:
        for k in CLAY_CONTRACT:
            if k not in r:
                raise AssertionError("record missing Clay key %r" % k)
        for k in REQUIRED:
            if not r.get(k):
                raise AssertionError("record has empty %r" % k)


# ---------------------------------------------------------------- posting

def post_records(records, webhook, delay=0.25, timeout=30, retries=3):
    results = []
    for r in records:
        body = json.dumps(r, ensure_ascii=False).encode("utf-8")
        status, err = None, None
        for attempt in range(retries + 1):
            req = urllib.request.Request(webhook, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    status, err = resp.status, None
                break
            except urllib.error.HTTPError as e:
                status, err = e.code, "HTTP %s" % e.code
                if e.code not in (408, 429) and e.code < 500:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                status, err = None, str(e)
            time.sleep(2 ** attempt)
        ok = status is not None and 200 <= status < 300
        results.append({"linkedin_url": r["linkedin_url"], "full_name": r["full_name"],
                        "harmonic_urn": r["harmonic_urn"], "ok": ok, "http_status": status, "error": err})
        time.sleep(delay)
    return results


# ---------------------------------------------------------------- CLI

def load_markets():
    with open(os.path.join(HERE, "markets.json"), encoding="utf-8") as fh:
        return json.load(fh)["markets"]


def gather(args):
    people = []
    for path in args.files:
        people.extend(parse_people_file(path))
    locations = load_locations(args.locations)
    return people, locations


def validate_or_refuse(people, records, invalid, max_invalid_ratio):
    if not people:
        return None
    if not records:
        return "0 valid records out of %d parsed" % len(people)
    ratio = len(invalid) / float(len(people))
    if ratio > max_invalid_ratio:
        return "%d of %d records invalid (%.0f%% > %.0f%% allowed) - likely a parsing/transcription problem" % (
            len(invalid), len(people), ratio * 100, max_invalid_ratio * 100)
    return None


def cmd_urns(args):
    urns = []
    for path in args.files:
        for p in parse_people_file(path):
            u = p.get("harmonic_urn") or p.get("urn") or p.get("entityUrn")
            if u and u not in urns:
                urns.append(u)
    for i in range(0, len(urns), 50):
        print(json.dumps(urns[i:i + 50]))
    print("# %d urns in %d batch(es) of <=50" % (len(urns), (len(urns) + 49) // 50), file=sys.stderr)
    return 0


def cmd_build_or_post(args, post):
    markets = load_markets()
    if args.market not in markets:
        print("unknown market %r (known: %s)" % (args.market, ", ".join(sorted(markets))), file=sys.stderr)
        return 2
    try:
        people, locations = gather(args)
    except (ParseError, ValueError) as e:
        print("PARSE ERROR: %s" % e, file=sys.stderr)
        return 3
    records, invalid, dupes = build_records(people, args.market, args.search_urn, locations)
    if args.only_linkedin:
        with open(args.only_linkedin, encoding="utf-8") as fh:
            keep = {normalize_linkedin(l) for l in fh.read().split() if l.strip()}
        records = [r for r in records if r["linkedin_url"] in keep]
        invalid = []  # re-post of a verified subset: validation already passed on the full batch
    check_contract(records)
    refusal = validate_or_refuse(people, records, invalid, args.max_invalid_ratio)
    report = {
        "ingest_version": INGEST_VERSION, "market": args.market,
        "table_id": markets[args.market]["table_id"], "table_name": markets[args.market]["table_name"],
        "search_urn": args.search_urn, "files": args.files, "location_files": args.locations or [],
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "parsed": len(people), "valid": len(records), "invalid": invalid, "duplicates_in_batch": dupes,
        "missing_country": sum(1 for r in records if not r["country"]),
        "refused": refusal, "dry_run": not post, "posted": [],
    }
    if refusal:
        print("REFUSED (nothing posted): %s" % refusal, file=sys.stderr)
    elif post:
        report["posted"] = post_records(records, args.webhook, delay=args.delay)
    else:
        print(json.dumps(records, ensure_ascii=False, indent=1))
    report["finished_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    failed = [r for r in report["posted"] if not r["ok"]]
    summary = {"market": args.market, "parsed": len(people), "valid": len(records), "invalid": len(invalid),
               "dupes": dupes, "missing_country": report["missing_country"],
               "posted_ok": sum(1 for r in report["posted"] if r["ok"]), "post_failed": len(failed),
               "refused": refusal}
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), file=sys.stderr)
    if refusal:
        return 3
    return 4 if failed else 0


# ---------------------------------------------------------------- self-test

FIXTURE_NETNEW = '''items[4]{urn,entity_type,name,console_url,linkedin_url,headline,profile_picture_url}:
  "urn:harmonic:person:57212681",PERSON,"Meltem Günyüzlü Ateş, Cfcim, Caip","https://console.harmonic.ai/dashboard/person/urn:harmonic:person:57212681","https://linkedin.com/in/meltemgunyuzlu",Responsible AI for Marketing | Startup Mentor,"https://assets.harmonic.ai/p.png"
  "urn:harmonic:person:206473930",PERSON,Egor Iachimov,"https://console.harmonic.ai/dashboard/person/urn:harmonic:person:206473930","https://www.linkedin.com/in/egoriachimov/",null,null
  "urn:harmonic:person:155374731",PERSON,Klauss  A,"https://console.harmonic.ai/dashboard/person/urn:harmonic:person:155374731","https://linkedin.com/in/klaussa","He said \\"ship it\\", then shipped",null
  "urn:harmonic:person:7671295",PERSON,Lavinia Felberbaum,"https://console.harmonic.ai/dashboard/person/urn:harmonic:person:7671295","https://linkedin.com/in/laviniafelberbaum",Co-Founder at KleverDeck,null
total_count: null
next_cursor: eyJtb2RlbCI6IlBFUlNPTiJ9
new_results_since: "2026-09-13T00:00:00Z"
'''

FIXTURE_LOCATIONS = '''results[3]:
  - personFound: true
    person:
      id: 7671295
      entityUrn: "urn:harmonic:person:7671295"
      location:
        city: Miami
        state: Florida
        country: United States
    input:
      urn: "urn:harmonic:person:7671295"
  - personFound: true
    person:
      id: 206473930
      entityUrn: "urn:harmonic:person:206473930"
      location: null
    input:
      urn: "urn:harmonic:person:206473930"
  - personFound: true
    person:
      id: 57212681
      entityUrn: "urn:harmonic:person:57212681"
      location:
        city: Bucharest
        country: Romania
    input:
      urn: "urn:harmonic:person:57212681"
total: 3
found: 3
'''


def cmd_self_test(_args):
    fails = []

    def expect(cond, msg):
        if not cond:
            fails.append(msg)

    people = parse_toon(FIXTURE_NETNEW, "fixture")
    expect(len(people) == 4, "expected 4 people, got %d" % len(people))
    locs = parse_locations_text(FIXTURE_LOCATIONS, "fixture")
    expect(locs.get("urn:harmonic:person:7671295", {}).get("country") == "United States", "country parse (US)")
    expect(locs.get("urn:harmonic:person:206473930", {}).get("country") is None, "null location -> no country")
    expect(locs.get("urn:harmonic:person:57212681", {}).get("country") == "Romania", "country parse (RO)")
    recs, invalid, dupes = build_records(people, "RO", "urn:harmonic:saved_search:172553", locs, now="2026-01-01T00:00:00Z")
    expect(not invalid and dupes == 0 and len(recs) == 4, "all 4 fixture records valid (invalid=%s)" % invalid)
    by_li = {r["linkedin_url"]: r for r in recs}
    r0 = by_li.get("https://linkedin.com/in/meltemgunyuzlu", {})
    expect(r0.get("full_name") == "Meltem Günyüzlü Ateş, Cfcim, Caip", "quoted name with commas: %r" % r0.get("full_name"))
    expect(r0.get("country") == "Romania", "country merged by urn")
    expect("https://linkedin.com/in/egoriachimov" in by_li, "www./trailing slash LinkedIn normalised")
    expect(by_li.get("https://linkedin.com/in/egoriachimov", {}).get("headline") is None, "null headline -> None")
    expect(by_li.get("https://linkedin.com/in/klaussa", {}).get("full_name") == "Klauss A", "whitespace collapsed in name")
    expect(by_li.get("https://linkedin.com/in/klaussa", {}).get("headline") == 'He said "ship it", then shipped', "escaped quote in headline")
    try:
        check_contract(recs)
    except AssertionError as e:
        fails.append("contract: %s" % e)
    for k in ("full_name", "linkedin_url", "country"):
        expect(all(k in r for r in recs), "payload carries Clay key %r" % k)
    # A truncated/re-typed file must be refused, not half-posted.
    truncated = "\n".join(l for l in FIXTURE_NETNEW.split("\n") if "Lavinia" not in l)
    try:
        parse_toon(truncated, "truncated")
        fails.append("row-count mismatch was not detected")
    except ParseError:
        pass
    bad, inv, _ = build_records([{"urn": "urn:harmonic:person:1", "name": None, "linkedin_url": "x"}], "RO", None, {})
    expect(not bad and len(inv) == 1, "record without name/linkedin rejected")
    expect(validate_or_refuse([{}] * 10, [{}] * 5, [{}] * 5, 0.1) is not None, "high invalid ratio refused")
    try:
        load_markets()
    except Exception as e:  # noqa: BLE001
        fails.append("markets.json unreadable: %s" % e)
    if fails:
        print("SELF-TEST FAILED (%d):" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("SELF-TEST OK (ingest %s): parser, locations, normalisation, validation, Clay contract %s"
          % (INGEST_VERSION, ", ".join(CLAY_CONTRACT)))
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("self-test")
    pu = sub.add_parser("urns")
    pu.add_argument("files", nargs="+")
    for name in ("build", "post"):
        p = sub.add_parser(name)
        p.add_argument("--market", required=True)
        p.add_argument("--search-urn", required=True)
        p.add_argument("--locations", action="append", default=[],
                       help="get_people location output file; repeat the flag for several files")
        p.add_argument("--report")
        p.add_argument("--max-invalid-ratio", type=float, default=0.1)
        p.add_argument("--only-linkedin", help="file of LinkedIn URLs (one per line) to restrict to, "
                                               "e.g. verify_clay.py --write-missing output")
        p.add_argument("--delay", type=float, default=0.25)
        if name == "post":
            p.add_argument("--webhook", required=True)
        p.add_argument("files", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "self-test":
        return cmd_self_test(args)
    if args.cmd == "urns":
        return cmd_urns(args)
    return cmd_build_or_post(args, post=(args.cmd == "post"))


if __name__ == "__main__":
    sys.exit(main())
