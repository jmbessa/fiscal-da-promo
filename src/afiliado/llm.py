import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

_JSON_DECODER = json.JSONDecoder()

# Fase 5A (A2): `claude -p` é um agente com ferramentas, não uma função de
# texto — sem estas flags ele lia o `.env` do CWD sob demanda e executava
# hooks de `.claude/settings.json` do repositório em modo headless.
#   --tools ""               desliga todas as ferramentas embutidas
#   --setting-sources ""     não carrega settings (nem hooks) de user/project/local
#   --strict-mcp-config      ignora servidores MCP configurados fora da linha de comando
#   --no-session-persistence não grava a sessão em disco
# `--bare` NÃO entra: desliga a autenticação OAuth (verificado ao vivo:
# "Not logged in"), e a cota vem da assinatura Max via CLAUDE_CODE_OAUTH_TOKEN.
CLI_FLAGS = ("--tools", "", "--setting-sources", "", "--strict-mcp-config",
             "--no-session-persistence")

# Lista branca do ambiente do subprocesso: nada de TELEGRAM_*, SHOPEE_*,
# MELI_*, IG_*. CLAUDE_CONFIG_DIR só entra se existir.
ENV_WHITELIST = ("PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT",
                 "TEMP", "TMP", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR")


def _env_minimo() -> dict[str, str]:
    return {k: v for k in ENV_WHITELIST if (v := os.environ.get(k)) is not None}


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
        # cwd = diretório temporário VAZIO, nunca o repo: sem .env, sem
        # .claude/settings.json, sem CLAUDE.md ao alcance.
        with tempfile.TemporaryDirectory(prefix="afiliado-llm-",
                                         ignore_cleanup_errors=True) as cwd:
            proc = subprocess.run(
                [exe, "-p", prompt, "--model", model, "--output-format", "text", *CLI_FLAGS],
                capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                cwd=cwd, env=_env_minimo(),
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
