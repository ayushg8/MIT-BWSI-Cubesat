"""Print the quality-gate verdict for every capture in Images/ at flight resolution."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

from config import BLUR_THRESHOLD  # noqa: E402

rows = common.score_all(common.flight_jpegs(tempfile.mkdtemp(prefix="gate-")))

print(f"{'file':<18}{'blur var':>9}  verdict")
for name, r in rows.items():
    verdict = "ACCEPT" if r["status"] == "passed" else "REJECT " + r["status"].removeprefix("rejected_")
    print(f"{name:<18}{r['variance']:>9.2f}  {verdict}")
accepted = sum(r["status"] == "passed" for r in rows.values())
print(f"{accepted} of {len(rows)} accepted at 640 x 480, threshold {BLUR_THRESHOLD}")
