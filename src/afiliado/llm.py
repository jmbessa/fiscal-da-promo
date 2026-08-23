import json
import shutil
import subprocess

_JSON_DECODER = json.JSONDecoder()


def parse_json_block(text: str):
    """Extract the first JSON block from text, tolerating surrounding prose and markdown fences."""
    for i, char in enumerate(text):
        if char in ('{', '['):
            try:
                obj, _ = _JSON_DECODER.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
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
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return parse_json_block(proc.stdout or "")
