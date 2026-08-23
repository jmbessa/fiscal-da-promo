from dataclasses import dataclass
from typing import Protocol

from afiliado.models import Post


@dataclass
class PublishResult:
    ok: bool
    message_id: str = ""
    error: str = ""


class Channel(Protocol):
    name: str

    def publish(self, post: Post) -> PublishResult: ...
