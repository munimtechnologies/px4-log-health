"""fixscreen-lite driver: run fixscreen 0.1.0 over the cached public PX4 logs with no network at all.

Steps (each writes its own file to <out dir>, so a failure in a later step keeps the earlier results):
  1. inventory.json  - what the dataset volume actually holds (files, bytes, ULog count per top folder).
  2. tests-ulogmini.txt - ulogmini's synthetic tests; stop if they fail.
  3. fixscreen 0.1.0 (byte-identical copy of the queued fixscreen-v1 code) with ulogmini standing in for
     pyulog, which cannot be installed without network. fixscreen runs its own tests first.
  4. reader-stats.jsonl - per file: ulogmini's corruption, truncation and short-row counts.
The pyulog-based fixscreen-v1 job is still queued; when both have run, their outputs are compared file by file.

Usage: python run_lite.py <data dir> <out dir>
Apache-2.0. Made with AI agents working in the name of MV2, Munim, Inc. (mv2@munimtech.com).
"""
import json
import os
import subprocess
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def inventory(data):
    top, total, ulogs, files = {}, 0, 0, 0
    exts = {}
    for dirpath, dirnames, filenames in os.walk(data):
        dirnames.sort()
        rel = os.path.relpath(dirpath, data)
        key = rel.split(os.sep)[0] if rel != "." else "."
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(p)
                with open(p, "rb") as f:
                    is_ulog = f.read(4) == b"ULog"
            except OSError:
                continue
            files += 1
            total += size
            ulogs += is_ulog
            ext = os.path.splitext(name)[1].lower() or "(none)"
            exts[ext] = exts.get(ext, 0) + 1
            t = top.setdefault(key, {"files": 0, "bytes": 0, "ulogs": 0})
            t["files"] += 1
            t["bytes"] += size
            t["ulogs"] += is_ulog
    return {"data_dir": data, "files": files, "bytes": total, "ulogs": ulogs, "extensions": exts,
            "top_folders": top}


def write_json(out, name, obj):
    with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1)


def main(data, out):
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    inv = inventory(data)
    inv["seconds"] = round(time.time() - t0, 1)
    write_json(out, "inventory.json", inv)
    print(json.dumps({k: inv[k] for k in ("files", "bytes", "ulogs", "extensions")}), flush=True)

    p = subprocess.run([sys.executable, "-m", "unittest", "-v", "test_ulogmini"], cwd=HERE, capture_output=True,
                       text=True)
    with open(os.path.join(out, "tests-ulogmini.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(p.stdout + p.stderr)
    print((p.stdout + p.stderr)[-1500:], flush=True)
    if p.returncode != 0:
        sys.exit(1)

    import ulogmini
    stats = []
    reader = "pyulog"
    try:
        import pyulog  # noqa: F401  (only present if the image ever ships it)
    except ImportError:
        reader = "ulogmini-" + ulogmini.__version__

        class TrackedULog(ulogmini.ULog):
            def __init__(self, path, *a, **k):
                super().__init__(path, *a, **k)
                stats.append({"path": os.path.relpath(path, data), "file_version": self.file_version,
                              "corruptions": self.corruptions, "truncated": self.truncated,
                              "short_rows": self.short_rows, "dropouts": len(self.dropouts),
                              "subscriptions": len(self.data_list)})

        shim = types.ModuleType("pyulog")
        shim.ULog = TrackedULog
        shim.__version__ = reader + " (standard-library stand-in; pyulog not installed)"
        sys.modules["pyulog"] = shim

    import fixscreen
    code = 0
    try:
        fixscreen.main(data, out)
    except SystemExit as e:
        code = e.code or 0
    with open(os.path.join(out, "reader-stats.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for s in stats:
            f.write(json.dumps(s) + "\n")
    agg = {"reader": reader, "files_read": len(stats),
           "with_corruption": sum(1 for s in stats if s["corruptions"]),
           "truncated": sum(1 for s in stats if s["truncated"]),
           "with_short_rows": sum(1 for s in stats if s["short_rows"]),
           "fixscreen_exit": code, "seconds": round(time.time() - t0, 1)}
    write_json(out, "lite-summary.json", agg)
    print(json.dumps(agg), flush=True)
    sys.exit(code)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/data", sys.argv[2] if len(sys.argv) > 2 else "/out")
