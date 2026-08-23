import json
import re
import shutil
import subprocess

_JSON_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def parse_json_block(text: str):
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def ask_json(prompt: str, model: str = "haiku", timeout: int = 120):
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-p", prompt, "--model", model, "--output-format", "text"],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return parse_json_block(proc.stdout or "")
