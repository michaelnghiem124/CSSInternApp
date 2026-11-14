"""Refresh the Opportunities job feeds and classify new postings (weekly).

    .venv/bin/python -m scripts.refresh_opportunities

Schedule it with cron, e.g. Mondays at 6 AM:
    0 6 * * 1  cd /path/to/project && .venv/bin/python -m scripts.refresh_opportunities
"""

import json

from backend import db, opportunities

if __name__ == "__main__":
    db.init()
    result = opportunities.refresh(sync=True)
    print(json.dumps({k: result.get(k) for k in ("stage", "finished", "classifier", "error")}, indent=1))
    for s in opportunities.sources_status():
        print(f"{s['org']:36} {s['platform']:10} {s['message']}")
