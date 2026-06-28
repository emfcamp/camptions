"""Export the FastAPI OpenAPI schema to docs/openapi.json."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from camptions.main import app  # noqa: E402

out = Path(__file__).parent.parent / "docs" / "openapi.json"
out.parent.mkdir(exist_ok=True)
with open(out, "w") as f:
    json.dump(app.openapi(), f, indent=2)
print(f"Wrote {out}")
