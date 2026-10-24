from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Jinja2Templates enables autoescaping for .html files. Never mark user content with |safe.
templates = Jinja2Templates(directory=TEMPLATES_DIR)
