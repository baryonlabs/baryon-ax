"""Test environment defaults.

Set before any test module imports `baryon.main`, so process-wide stores
(gaps, ontology packs) write into the test temp tree instead of /tmp.
"""

import os
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="baryon-ax-tests-"))

os.environ.setdefault("GAP_STORE", str(ROOT / "gaps" / "gaps.jsonl"))
os.environ.setdefault("ONTOLOGY_STORE", str(ROOT / "ontology"))
os.environ.setdefault("SYSTEM_METRICS_ENABLED", "false")
