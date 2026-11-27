from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.models.common import PRICE_UNITS
from app.models.request import CLOSE_REASONS, REQUEST_STATUSES, TIMINGS

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Jinja2Templates enables autoescaping for .html files. Never mark user content with |safe.
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Banner codes after a request redirect (`?done=`).
REQUEST_DONE_CODES = ("created", "saved", "photos", "closed", "cancelled", "reopened")

# Translation keys picked by value (status, unit, ...), spelled out so the key-usage test can see
# them; templates read them as `request_keys.status[x]`.
templates.env.globals["request_keys"] = {
    "status": {s: f"requests.status.{s}" for s in REQUEST_STATUSES},
    "close_reason": {r: f"requests.close_reason.{r}" for r in CLOSE_REASONS},
    "unit": {u: f"requests.unit.{u}" for u in PRICE_UNITS},
    "timing": {k: f"requests.timing.{k}" for k in TIMINGS},
    "done": {d: f"requests.done.{d}" for d in REQUEST_DONE_CODES},
}
