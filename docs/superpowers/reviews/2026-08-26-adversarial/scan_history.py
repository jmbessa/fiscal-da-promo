"""Varre o historico do git (todas as refs) atras de valores de segredo no formato VAR=valor."""
import re
import subprocess

REPO = r"G:\Biblioteca\Documentos\Projetos\Afiliado\.claude\worktrees\fase3b-meli-hibrido"
PAT = re.compile(
    r"^\+.*\b(SHOPEE_APP_(?:ID|SECRET)|TELEGRAM_BOT_TOKEN|MELI_CLIENT_(?:ID|SECRET)|"
    r"IG_ACCESS_TOKEN|CLAUDE_CODE_OAUTH_TOKEN|MELI_REFRESH_TOKEN|TELEGRAM_OPS_CHAT_ID|"
    r"TELEGRAM_CHANNEL_ID|IG_USER_ID)\s*[=:]\s*[\"']?([A-Za-z0-9_:.\-]{8,})", re.M)
out = subprocess.run(["git", "-C", REPO, "log", "--all", "-p", "--format=@@COMMIT %h %s"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
commit = ""
hits = 0
for line in out.splitlines():
    if line.startswith("@@COMMIT "):
        commit = line[9:]
        continue
    m = PAT.search(line)
    if m and not re.search(r"\$\{\{|secrets\.|<|placeholder|exemplo|example|xxx", line, re.I):
        hits += 1
        print(f"{commit} :: {line[:140]}")
print(f"linhas suspeitas: {hits} (varridos {len(out.splitlines())} linhas de diff)")
