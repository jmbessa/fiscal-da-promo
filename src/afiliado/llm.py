import json
import shutil
import subprocess
from dataclasses import dataclass

_JSON_DECODER = json.JSONDecoder()


@dataclass
class LlmStats:
    """Contador de módulo (fase 5A, C4c): quantas chamadas o run fez e
    quantas voltaram None (fallback de ranking/copy). O pipeline zera no
    início e, se houve falha, avisa o ops — antes, LLM fora significava 100
    posts com a MESMA headline e um resumo idêntico ao de um run saudável."""
    chamadas: int = 0
    falhas: int = 0

    def reset(self) -> None:
        self.chamadas = 0
        self.falhas = 0


stats = LlmStats()


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


def _ask(prompt: str, model: str, timeout: int):
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


def ask_json(prompt: str, model: str = "haiku", timeout: int = 120):
    """JSON da resposta do `claude -p`, ou None em qualquer falha (CLI
    ausente, timeout, exit != 0, saída sem JSON). Cada chamada conta em
    `stats`; cada None conta como falha."""
    stats.chamadas += 1
    result = _ask(prompt, model, timeout)
    if result is None:
        stats.falhas += 1
    return result
