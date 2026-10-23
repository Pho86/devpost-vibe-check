"""Extract the inline JS from index.html and run it through Node to catch syntax errors."""

import re
import subprocess
from pathlib import Path

html = Path("output/smoke/review/index.html").read_text(encoding="utf-8")
# grab everything between the last <script> and </script>
scripts = re.findall(r"<script(?![^>]*id=)[^>]*>(.*?)</script>", html, re.DOTALL)
# last script block is the app JS
js = scripts[-1] if scripts else ""
Path("_tmp_check.js").write_text(js, encoding="utf-8")
result = subprocess.run(["node", "--check", "_tmp_check.js"], capture_output=True, text=True)
print("stdout:", result.stdout)
print("stderr:", result.stderr)
print("returncode:", result.returncode)
Path("_tmp_check.js").unlink(missing_ok=True)
