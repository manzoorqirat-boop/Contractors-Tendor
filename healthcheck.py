"""Docker HEALTHCHECK: asks the running app's own health route. Exit 0 = healthy."""
import os
import sys
import urllib.request

try:
    with urllib.request.urlopen(f"http://127.0.0.1:{os.environ.get('PORT', '8000')}/api/health", timeout=4) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:  # noqa: BLE001 - any failure means unhealthy
    sys.exit(1)
