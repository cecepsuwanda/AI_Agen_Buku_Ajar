"""Router model (§7).

Tiga bagian, dengan tanggung jawab yang tajam:

* :class:`~models.model_registry.ModelRegistry` — **data**: peran → model. Semua
  kebijakan resolusi ada di sana.
* :class:`~models.ollama_client.ChatModelFactory` — **konstruksi**: spec → adapter,
  beserta memo dan kebijakan retry.
* :class:`ModelRouter` — **lookup**: dua method, tanpa percabangan.

Bila ``ModelRouter`` suatu saat menerima argumen ``retry`` atau ``temperature``,
desainnya sudah regresi: keduanya milik factory dan ``ModelSpec``.
"""

from __future__ import annotations

from typing import Protocol

from domain.errors import UnknownRoleError
from domain.ports import ChatModel, EmbeddingModel
from models.model_registry import ModelRegistry


class ChatModelSource(Protocol):
    """Port minimal yang dibutuhkan router: pembuat ``ChatModel`` per peran.

    Protocol struktural, bukan kelas dasar: ``ChatModelFactory`` di
    :mod:`models.ollama_client` **tidak mewarisi apa pun** dari sini, dan tetap
    memenuhi kontraknya. Itulah bedanya dengan ``raise NotImplementedError`` —
    yang terakhir hanya menunda kegagalan ke saat pemanggilan, sedangkan Protocol
    membuat ``mypy`` menolaknya saat perakitan.
    """

    def chat_for(self, role: str) -> ChatModel: ...

    def embedding_for(self, role: str) -> EmbeddingModel: ...


class ModelRouter:
    """Titik tunggal tempat agent meminta model (§6, §7).

    Agent **tidak pernah** menuliskan nama model — selalu
    ``router.chat("writer")``. Mengganti model = mengedit ``config.yaml`` saja.
    """

    def __init__(self, registry: ModelRegistry, source: ChatModelSource) -> None:
        self._registry = registry
        self._source = source

    @property
    def registry(self) -> ModelRegistry:
        """Registry yang mendasari router ini (dipakai ``doctor``/``status``)."""
        return self._registry

    def has_role(self, role: str) -> bool:
        """True bila ``role`` terdaftar di profil aktif."""
        return role in self._registry.roles

    def chat(self, role: str) -> ChatModel:
        """Model chat untuk ``role``.

        :raises UnknownRoleError: bila peran tidak dikenal.
        """
        if not self.has_role(role):
            raise UnknownRoleError(role, tuple(sorted(self._registry.roles)))
        return self._source.chat_for(role)

    def embedder(self, role: str = "embedding") -> EmbeddingModel:
        """Model embedding. Terpisah dari :meth:`chat` (ISP)."""
        if not self.has_role(role):
            raise UnknownRoleError(role, tuple(sorted(self._registry.roles)))
        return self._source.embedding_for(role)
