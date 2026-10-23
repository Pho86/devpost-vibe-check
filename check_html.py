import json
import re
from pathlib import Path

html = Path("output/smoke/review/index.html").read_text(encoding="utf-8")
m = re.search(r'id="payload"[^>]*>(.*?)</script>', html, re.DOTALL)
data = json.loads(m.group(1))
print("marked:", len(data.get("marked", [])))
print("likely_safe:", len(data.get("likely_safe", [])))
print("no_github:", len(data.get("no_github", [])))
print("unavailable:", len(data.get("unavailable", [])))
print("summary:", data.get("summary"))
print("first marked title:", data["marked"][0]["project_title"] if data.get("marked") else "NONE")
