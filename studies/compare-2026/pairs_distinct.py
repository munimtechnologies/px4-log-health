"""pairs_distinct.py: px4-log-health studies/compare-2026, distinct-flight replication.

The first compare-2026 null study (pairs.py 0.1.0) counted 40 null pairs, but 22 of them were the same
ULog file uploaded twice to Flight Review (identical sha256), so nothing could fire on them. This job
repeats the study on pairs of DISTINCT flights from vehicles the first study did not reach, with the
same rules (compare.py 0.1.0), the same checks (checkup 0.3.9) and the same thresholds.

Pre-registered in studies/compare-2026/PLAN-distinct.md. Before it reads any log it
1. downloads pairs.py 0.1.0, compare.py 0.1.0 and test_compare.py from px4-log-health at the commit the
   first study ran (6ded898...) and checks their sha256 against that job's job-fetched.json;
2. runs test_compare.py and its own self-test;
3. checks that PLAN-distinct.md and this file on main are the copies it runs (waits up to 90 min);
4. downloads px4-log-health v0.3.9 and runs its fetch_deps.py (via pairs.install_checkup).
Then
5. D2: streams review.px4.io/dbinfo once and counts, over every vehicle with a first-study pair, how
   often that pair's two logs have the same integer duration (no downloads);
6. D3: for the first 20 fresh vehicles (sha256 order after 02fa996ad548) whose first-study pair has
   equal durations, downloads both logs, hashes them and deletes them;
7. null pairs: for fresh vehicles in sha256 order, the most recent adjacent same-build pair at most
   7 days apart whose durations differ; runs checkup and compare.py; a pair whose two files are
   byte-identical anyway is set aside as "duplicate". Stops at 40 distinct null pairs, 400 downloads
   or 240 minutes.
Writes rows-distinct.jsonl, validation-d3.jsonl, summary-distinct.json and job-fetched-distinct.json.
Logs are deleted after they are read; no position is read or written.

    python pairs_distinct.py /out

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request

__version__ = "0.1.0"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "munimtechnologies/px4-log-health"
PIN = "6ded89861d4d1ca6b820598a60159dc8353e4024"
RAW = "https://raw.githubusercontent.com/%s/%s/%s"
# sha256 of the files the first study ran (CRLF normalised to LF), from its job-fetched.json
PINNED = {
    "studies/compare-2026/pairs.py": ("pairs.py", "ba57da236eb8832ab3b9d9aa0086ac1be1e949995b31ab90d91dec65f2b76eba"),
    "compare.py": ("compare.py", "972f57cc3d5bd0e2896a0f8047f0977c89da7cf46d2c54bc32f80c46014c74fd"),
    "test_compare.py": ("test_compare.py", "98766d8a76af0998947bf9103239a2dae483fd435a0b84ce02057346fd6b0464"),
}
MINE = {"studies/compare-2026/PLAN-distinct.md": "PLAN-distinct.md",
        "studies/compare-2026/pairs_distinct.py": "pairs_distinct.py"}
FIRST_ROWS = "studies/compare-2026/pairs-rows.jsonl"
LAST_FIRST_STUDY_VEHICLE = "02fa996ad548"
TARGET_NULL = 40
MAX_DOWNLOADS = 400
BUDGET_MIN = 240
D3_PAIRS = 20


# ---------------------------------------------------------------- pure helpers (tested in self_test)

def norm(data):
    return data.replace(b"\r\n", b"\n")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def is_fresh(v, upgrade_vehicles):
    """A vehicle the first study did not reach: after its last vehicle in hash order, not an upgrade one."""
    return bool(v) and v > LAST_FIRST_STUDY_VEHICLE and v not in upgrade_vehicles


def pick_pair_distinct(logs, day_number, same_build, max_gap_days):
    """(before, after, skipped): the most recent two adjacent logs (upload date, then log id) with the same
    build, at most max_gap_days apart, whose integer durations differ. skipped counts the adjacent
    same-build pairs passed over because their durations were equal. (None, None, skipped) if none."""
    logs = sorted(logs, key=lambda e: (e["date"], e["log_id"]))
    skipped = 0
    for k in range(len(logs) - 1, 0, -1):
        a, b = logs[k - 1], logs[k]
        da, db = day_number(a["date"]), day_number(b["date"])
        if da is None or db is None or db - da > max_gap_days:
            continue
        if not same_build(a, b):
            continue
        if a["dur"] == b["dur"]:
            skipped += 1
            continue
        return a, b, skipped
    return None, None, skipped


def classify(row):
    """'duplicate' when both files were read and are byte-identical; otherwise the first study's groups."""
    b, a = row.get("before") or {}, row.get("after") or {}
    if b.get("sha256") and b.get("sha256") == a.get("sha256"):
        return "duplicate"
    if row.get("state") != "measured":
        return "unmeasured"
    c = row["compare"]
    if c["params"]["settings"] or c["firmware"]["changed"]:
        return "config"
    return "null"


def distinct_null(rows):
    """Null rows of the first study whose two files differ."""
    out = []
    for r in rows:
        if r.get("group") != "null":
            continue
        b, a = r.get("before") or {}, r.get("after") or {}
        if b.get("sha256") and b.get("sha256") != a.get("sha256"):
            out.append(r)
    return out


def band(share, lo, hi):
    if share is None:
        return "too few"
    return "held" if lo - 1e-12 <= share <= hi + 1e-12 else "failed"


def self_test():
    import pairs
    dn, sb = pairs.day_number, pairs.same_build
    base = {"rel": "v1.16.0 255", "hw": "PX4_FMU_V6C", "af": "4001", "ver_sw": "abc", "v": "h"}
    logs = [dict(base, log_id="a", date="2026-01-01", dur=300), dict(base, log_id="b", date="2026-01-02", dur=410),
            dict(base, log_id="c", date="2026-01-03", dur=410)]
    a, b, s = pick_pair_distinct(logs, dn, sb, 7)
    assert (a["log_id"], b["log_id"], s) == ("a", "b", 1), (a, b, s)      # b-c equal durations: skipped
    logs.append(dict(base, log_id="d", date="2026-01-04", dur=411))
    a, b, s = pick_pair_distinct(logs, dn, sb, 7)
    assert (a["log_id"], b["log_id"], s) == ("c", "d", 0)
    two = [dict(base, log_id="x", date="2026-02-01", dur=200), dict(base, log_id="y", date="2026-02-01", dur=200)]
    assert pick_pair_distinct(two, dn, sb, 7) == (None, None, 1)
    far = [dict(base, log_id="x", date="2026-02-01", dur=200), dict(base, log_id="y", date="2026-03-01", dur=201)]
    assert pick_pair_distinct(far, dn, sb, 7) == (None, None, 0)
    other = [dict(base, log_id="x", date="2026-02-01", dur=200), dict(base, log_id="y", date="2026-02-02", dur=201,
                                                                        ver_sw="zzz")]
    assert pick_pair_distinct(other, dn, sb, 7) == (None, None, 0)
    assert is_fresh("02fa996ad549", set()) and not is_fresh("02fa996ad548", set())
    assert not is_fresh("0300aaaaaaaa", {"0300aaaaaaaa"}) and not is_fresh("01ffffffffff", set()) and not is_fresh("", set())
    ok = {"settings": [], "learned": []}
    row = {"before": {"sha256": "1"}, "after": {"sha256": "1"}, "state": "measured",
           "compare": {"params": ok, "firmware": {"changed": False}}}
    assert classify(row) == "duplicate"
    assert classify(dict(row, after={"sha256": "2"})) == "null"
    assert classify(dict(row, after={"sha256": "2"}, state="not both read")) == "unmeasured"
    cfg = dict(row, after={"sha256": "2"}, compare={"params": {"settings": [1]}, "firmware": {"changed": False}})
    assert classify(cfg) == "config"
    assert classify({"before": {"sha256": "1"}, "after": None, "state": "after not read", "compare": None}) == "unmeasured"
    first = [dict(row, group="null"), dict(row, group="null", after={"sha256": "2"}), dict(row, group="config",
                                                                                            after={"sha256": "3"})]
    assert len(distinct_null(first)) == 1
    assert band(0.25, 0.25, 0.55) == "held" and band(0.56, 0.25, 0.55) == "failed" and band(None, 0, 1) == "too few"
    assert norm(b"a\r\nb") == b"a\nb"


# ---------------------------------------------------------------- network

def get(url, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "px4-log-health-compare-distinct/%s (MV2 of Munim, Inc.; "
                                                             "mv2@munimtech.com)" % __version__})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_pinned(res):
    got = {}
    for path, (local, want) in PINNED.items():
        data = norm(get(RAW % (REPO, PIN, path)))
        h = sha(data)
        got[path] = h
        assert h == want, "%s at %s has sha256 %s, expected %s" % (path, PIN[:7], h, want)
        with open(os.path.join(HERE, local), "wb") as f:
            f.write(data)
    res["pinned"] = {"commit": PIN, "sha256": got}


def fetch_hash(m, log_id, max_bytes):
    """Download one log politely (pairs.DELAY_S between downloads), return (status, sha256) and delete it."""
    import pairs
    if m.downloads:
        time.sleep(pairs.DELAY_S)
    m.downloads += 1
    path = os.path.join(m.folder, log_id + ".ulg")
    status = pairs.download(m.session, log_id, path, max_bytes)
    h = None
    try:
        if status == "ok":
            with open(path, "rb") as f:
                h = sha(f.read())
    finally:
        if os.path.exists(path):
            os.remove(path)
    return status, h


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def main(out):
    os.makedirs(out, exist_ok=True)
    res = {"pairs_distinct": __version__, "ok": False, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    rows, d3 = [], []
    d2 = {}
    first_distinct = []
    try:
        fetch_pinned(res)
        sys.path.insert(0, HERE)
        import pairs
        assert pairs.__version__ == "0.1.0", pairs.__version__
        pairs.self_test()
        self_test()
        p = subprocess.run([sys.executable, "-m", "unittest", "-v", "test_compare"], cwd=HERE,
                           capture_output=True, text=True)
        with open(os.path.join(out, "tests-distinct.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(p.stdout + p.stderr)
        res["tests_exit"] = p.returncode
        assert p.returncode == 0, "test_compare failed: %s" % (p.stdout + p.stderr)[-1500:]

        pairs.PUBLISHED = dict(MINE)          # the plan check now covers this study's own plan and script
        pairs.wait_for_plan(res)
        pairs.install_checkup(res)
        # install_checkup puts the checkup repo first on sys.path; put this folder back in front so that
        # compare.py is the pinned 0.1.0 copy even if the release tarball carries its own compare.py
        while HERE in sys.path:
            sys.path.remove(HERE)
        sys.path.insert(0, HERE)
        sys.modules.pop("compare", None)
        import compare
        import health
        import requests
        assert compare.__version__ == "0.1.0", compare.__version__
        res["compare_file"] = compare.__file__
        assert os.path.dirname(os.path.abspath(compare.__file__)) == HERE, compare.__file__

        first = [json.loads(line) for line in get(RAW % (REPO, "main", FIRST_ROWS)).decode("utf-8").splitlines()
                 if line.strip()]
        res["first_rows"] = {"n": len(first), "sha256": sha("\n".join(json.dumps(r) for r in first).encode())}
        first_distinct = distinct_null(first)

        session = requests.Session()
        session.headers["User-Agent"] = pairs.UA
        m = pairs.Measurer(session, "/tmp/logs", health.load_baseline(health.BASELINE))
        upgrade_vehicles = {u["vehicle"] for u in pairs.upgrade_pairs()}

        entries = pairs.stream_dbinfo(session, res)
        by_v = {}
        for e in entries:
            by_v.setdefault(e["v"], []).append(e)
        del entries
        gc.collect()

        # D2: every vehicle with a first-study pair, no downloads
        n_pair = n_equal = 0
        d3_cands, cands = [], []
        for v in sorted(by_v):
            p1 = pairs.pick_pair(by_v[v])
            if p1:
                n_pair += 1
                if p1[0]["dur"] == p1[1]["dur"]:
                    n_equal += 1
                    if is_fresh(v, upgrade_vehicles) and len(d3_cands) < 3 * D3_PAIRS:
                        d3_cands.append((v, p1[0], p1[1]))
            if is_fresh(v, upgrade_vehicles):
                a, b, skipped = pick_pair_distinct(by_v[v], pairs.day_number, pairs.same_build, pairs.MAX_GAP_DAYS)
                if a:
                    cands.append((v, a, b, skipped))
        by_v = None
        gc.collect()
        d2 = {"vehicles_with_pair": n_pair, "equal_duration": n_equal,
              "share": round(n_equal / n_pair, 4) if n_pair else None}
        d2["result"] = band(d2["share"], 0.25, 0.55) if n_pair >= 100 else "too few"
        res["candidate_vehicles"] = len(cands)

        # D3: are equal-duration pairs byte-identical?
        for v, a, b in d3_cands:
            if sum(1 for x in d3 if x["both_read"]) >= D3_PAIRS:
                break
            sa, ha = fetch_hash(m, a["log_id"], pairs.MAX_BYTES_NULL)
            sb_, hb = (fetch_hash(m, b["log_id"], pairs.MAX_BYTES_NULL) if ha else ("skipped", None))
            d3.append({"vehicle": v, "before": a["log_id"], "after": b["log_id"], "dur": a["dur"],
                       "status": [sa, sb_], "both_read": bool(ha and hb), "identical": bool(ha and hb and ha == hb)})
            write_jsonl(os.path.join(out, "validation-d3.jsonl"), d3)

        # null pairs on distinct flights
        t0, n_null, stop = time.time(), 0, "list exhausted"
        for v, a, b, skipped in cands:
            if n_null >= TARGET_NULL:
                stop = "target reached"
                break
            if m.downloads >= MAX_DOWNLOADS - 1:
                stop = "download cap"
                break
            if time.time() - t0 > BUDGET_MIN * 60:
                stop = "time budget"
                break
            r = m.pair(a["log_id"], b["log_id"], pairs.MAX_BYTES_NULL)
            r.update(vehicle=v, skipped_equal_duration=skipped,
                     listing={"before_date": a["date"], "after_date": b["date"], "release": a["rel"],
                              "ver_sw": a["ver_sw"][:12], "hw": a["hw"], "airframe": a["af"], "mav_type": a["mav"],
                              "dur": [a["dur"], b["dur"]]})
            r["group"] = classify(r)
            if r["group"] == "null":
                n_null += 1
            rows.append(r)
            write_jsonl(os.path.join(out, "rows-distinct.jsonl"), rows)
        res["stop"] = stop
        res["downloads"] = m.downloads
        res["ok"] = True
    except Exception:
        res["error"] = traceback.format_exc()[-3000:]
        print(res["error"], flush=True)

    write_jsonl(os.path.join(out, "rows-distinct.jsonl"), rows)
    write_jsonl(os.path.join(out, "validation-d3.jsonl"), d3)
    summary = {"pairs_distinct": __version__, "d2": d2}
    try:
        import pairs
        null = [r for r in rows if r.get("group") == "null"]
        config = [r for r in rows if r.get("group") == "config"]
        measured = [r for r in rows if r.get("group") in ("null", "config", "duplicate")]
        dup = [r for r in rows if r.get("group") == "duplicate"]
        read3 = [x for x in d3 if x["both_read"]]
        ident3 = sum(1 for x in read3 if x["identical"])
        summary.update({
            "counts": {g: sum(1 for r in rows if r.get("group") == g)
                       for g in ("null", "config", "duplicate", "unmeasured")},
            "null_items": pairs.item_stats(null), "null_spread": pairs.spread(null),
            "config_items": pairs.item_stats(config), "config_spread": pairs.spread(config),
            "null_verdicts": {},
            "d1": {"measured_pairs": len(measured), "identical": len(dup),
                   "result": ("held" if len(dup) <= 2 else "failed") if len(measured) >= 30 else "too few"},
            "d3": {"both_read": len(read3), "identical": ident3,
                   "result": ("held" if ident3 >= 18 else "failed") if len(read3) >= D3_PAIRS else "too few"},
        })
        for r in null:
            vd = r["compare"]["verdict"]
            summary["null_verdicts"][vd] = summary["null_verdicts"].get(vd, 0) + 1
        summary["grade"] = pairs.grade(null, config)
        pooled = first_distinct + null
        summary["pooled_described"] = {"first_study_distinct_null": len(first_distinct), "n_null": len(pooled),
                                       "items": pairs.item_stats(pooled), "spread": pairs.spread(pooled),
                                       "pairs_with_a_worse_item": sum(1 for r in pooled
                                                                      if (r.get("compare") or {}).get("worse"))}
    except Exception:
        summary["summary_error"] = traceback.format_exc()[-2000:]
    with open(os.path.join(out, "summary-distinct.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    with open(os.path.join(out, "job-fetched-distinct.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1)
        f.write("\n")
    print(json.dumps({"res": res, "summary": {k: summary.get(k) for k in ("counts", "d1", "d2", "d3", "grade")}},
                     indent=1)[:6000], flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/out"))
