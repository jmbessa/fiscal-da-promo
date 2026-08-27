from dataclasses import dataclass
from typing import Protocol

from afiliado.models import Post


@dataclass
class PublishResult:
    ok: bool
    message_id: str = ""
    error: str = ""
    # Fase 5F (C2): o post FOI ao ar apesar de `ok=False`.
    #
    # Um story publicado sem a figurinha de link é falha — não converte — mas
    # ele está na conta e o público vê. Enquanto só `ok` contava, esse story
    # não consumia `max_per_run` nem `max_per_day` e não entrava no dedupe: o
    # canal quebrado publicava dois por run, para sempre, invisível a todo
    # teto. Quem marca isto é o canal; o pipeline grava o post (teto + dedupe)
    # e mesmo assim descarta a oferta — porque sucesso não foi.
    #
    # `message_id` carrega o id do post nesse caso (vazio quando o canal
    # publicou e não soube dizer qual foi).
    publicado: bool = False


class Channel(Protocol):
    name: str

    def publish(self, post: Post) -> PublishResult: ...
