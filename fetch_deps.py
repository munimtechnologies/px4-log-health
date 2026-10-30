"""Fetch gps_lag.py (0.1.0), innov.py (0.2.0), fleet.py (0.1.1) and
test_gps_lag.py from px4-gps-lag at a pinned commit. checkup.py uses
gps_lag.py and innov.py; fleet_health.py and smoke.py use fleet.py; the tests
use all of them. Writes fetched.json with their hashes.

    python fetch_deps.py [commit]

By MV2 of Munim, Inc.; written with AI agents. Apache-2.0."""

import hashlib
import json
import sys
import urllib.request

PIN = "967b5ad92d04ca6ac7e29684b411fbf5df243e05"
FILES = (("gps_lag.py", "0.1.0"), ("innov.py", "0.2.0"), ("fleet.py", "0.1.1"), ("test_gps_lag.py", None))


def get(url):
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def main(ref=PIN):
    fetched = {"px4-gps-lag": ref}
    for name, version in FILES:
        data = get("https://raw.githubusercontent.com/munimtechnologies/px4-gps-lag/%s/%s" % (ref, name))
        if version and ('__version__ = "%s"' % version).encode() not in data:
            sys.exit("px4-gps-lag's %s at %s is not %s" % (name, ref, version))
        with open(name, "wb") as f:
            f.write(data)
        fetched[name] = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
    with open("fetched.json", "w") as f:
        json.dump(fetched, f, indent=1)
    print(json.dumps(fetched), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:2])
