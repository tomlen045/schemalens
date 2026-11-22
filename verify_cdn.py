#!/usr/bin/env python3
"""CDN 图床就绪验证：SHA 锚定 URL 逐张下载 md5 比对本地字节"""
import hashlib
import subprocess
import os

REPO = "tomlen045/schemalens"
SHA = "b17d47e67715b42d1d44c4762649f19464745b0b"
SHOTS = ["01_profile", "02_demo_verdicts", "03_pollution",
         "04_dogfood_v100", "05_dogfood_v101", "06_selftest_cron"]
LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")

ok = 0
for n in SHOTS:
    url = "https://gcore.jsdelivr.net/gh/%s@%s/shots/%s.png" % (REPO, SHA, n)
    r = subprocess.run(["curl", "-s", "-o", "/tmp/sl_cdn_%s.png" % n,
                        "-w", "%{http_code}", "-m", "40", url],
                       capture_output=True, text=True)
    code = r.stdout.strip()
    lp = os.path.join(LOCAL, "%s.png" % n)
    lmd5 = hashlib.md5(open(lp, "rb").read()).hexdigest()
    try:
        cmd5 = hashlib.md5(open("/tmp/sl_cdn_%s.png" % n, "rb").read()).hexdigest()
    except OSError:
        cmd5 = "MISSING"
    match = "MATCH" if lmd5 == cmd5 else "MISMATCH"
    if lmd5 == cmd5 and code == "200":
        ok += 1
    print("%s: http=%s md5=%s" % (n, code, match))
print("READY %d/%d" % (ok, len(SHOTS)))
