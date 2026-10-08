"""``ChatModel`` palsu untuk pengujian offline.

Tiga model, tiga peran yang berbeda — dan yang ketiga adalah alasan seluruh
suite ini mungkin:

* :class:`ScriptedChatModel` — urutan balasan yang ditentukan. Untuk menguji
  jalur bahagia **dan jalur gagal** dengan presisi.
* :class:`SchemaEchoChatModel` — **mensintesis instance valid dari
  ``request.format_schema``**. Konsekuensinya besar: pipeline delapan bab penuh
  dapat dijalankan ujung-ke-ujung tanpa JSON tulisan tangan dan tanpa jaringan.
  Lebih penting lagi, setiap kali model domain mendapat field baru, model ini
  mengikutinya **otomatis** — sehingga tes integrasinya tidak membusuk. Tes
  integrasi yang membusuk adalah tes yang dihapus orang.
* :class:`ExplodingChatModel` — selalu gagal. Untuk memastikan kesalahan
  infrastruktur benar-benar naik ke atas, bukan ditelan diam-diam.

Ketiganya mematuhi port ``ChatModel`` secara struktural; tidak ada satu pun
yang mewarisi apa pun. Itulah gunanya ``typing.Protocol``.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Iterable, Mapping

from domain.ports import ChatRequest, ChatResult
from domain.structured import example_instance

#: Nilai yang dipakai untuk field dengan nama tertentu, apa pun tipenya.
#:
#: Berguna untuk mengarahkan skenario: reviewer yang menyetujui bab memerlukan
#: ``approved=True``, dan itu tidak dapat ditebak dari skema. Hal yang sama
#: berlaku untuk ``difficulty`` — skema hanya menyatakan ``string``, sedangkan
#: nilai yang sah bagi gate §20 adalah salah satu dari ``DIFFICULTY_LEVELS``.
DEFAULT_OVERRIDES: Mapping[str, Any] = {
    "approved": True,
    "score": 9,
    "skipped": False,
    "error": None,
    "difficulty": "mudah",
}


class RecordingChatModel:
    """Merekam setiap permintaan, dan membalas lewat strategi yang diberikan.

    Kelas dasar untuk model palsu lain. Perekamannya yang membuat tes dapat
    menegaskan **apa yang sebenarnya dikirim** — misalnya bahwa percobaan
    perbaikan benar-benar memakai ``temperature=0.0``.
    """

    def __init__(self, responder: Any) -> None:
        self.requests: list[ChatRequest] = []
        self._responder = responder

    def complete(self, request: ChatRequest) -> ChatResult:
        """Catat permintaan, lalu minta balasan dari strategi."""
        self.requests.append(request)
        return self._responder(request, len(self.requests))

    # -- Pemeriksaan untuk tes ---------------------------------------------
    @property
    def call_count(self) -> int:
        """Berapa kali model ini dipanggil."""
        return len(self.requests)

    def temperatures(self) -> tuple[float | None, ...]:
        """Temperature setiap panggilan, berurutan — untuk memeriksa tangga perbaikan."""
        return tuple(request.temperature for request in self.requests)

    def schemas(self) -> tuple[Mapping[str, Any] | None, ...]:
        """Skema ``format`` setiap panggilan — untuk memastikan ia tidak pernah dicabut."""
        return tuple(request.format_schema for request in self.requests)


class ScriptedChatModel(RecordingChatModel):
    """Membalas dengan urutan teks yang sudah ditentukan, lalu gagal keras.

    **Ketat secara sengaja**: memanggilnya lebih banyak daripada jumlah balasan
    yang disiapkan adalah kesalahan tes, bukan kesempatan mengembalikan nilai
    bawaan. Balasan bawaan yang longgar akan menyembunyikan percobaan perbaikan
    yang tidak seharusnya terjadi.
    """

    def __init__(self, responses: Iterable[str]) -> None:
        self._queue: deque[str] = deque(responses)
        super().__init__(self._next_response)

    def _next_response(self, request: ChatRequest, call_number: int) -> ChatResult:
        if not self._queue:
            raise AssertionError(
                f"ScriptedChatModel kehabisan balasan pada panggilan ke-{call_number}. "
                f"Tes menyiapkan terlalu sedikit respons — atau kode memanggil model "
                f"lebih sering daripada yang seharusnya."
            )
        return _result(self._queue.popleft(), request)


class ExplodingChatModel(RecordingChatModel):
    """Selalu melempar ``error``.

    Dipakai untuk memastikan kesalahan infrastruktur benar-benar naik ke
    permukaan — dan sejak :class:`~agents.book_director.BookDirector` ada, untuk
    membedakan kegagalan **sistemik** dari kegagalan **per-bab**. Bedanya
    ditentukan sepenuhnya oleh jenis exception-nya (lihat ``SYSTEMIC_ERRORS``),
    jadi model ini harus dapat melempar exception mana pun — bukan hanya
    ``RuntimeError``.
    """

    def __init__(self, error: BaseException | None = None) -> None:
        self.error: BaseException = error or RuntimeError("model meledak (sengaja)")
        super().__init__(self._explode)

    def _explode(self, request: ChatRequest, call_number: int) -> ChatResult:
        raise self.error


class SchemaEchoChatModel(RecordingChatModel):
    """Mensintesis keluaran valid **dari skema yang diminta**.

    Inilah yang membuat tes integrasi tidak membusuk: ia tidak menyimpan contoh
    JSON tulisan tangan yang harus diperbarui setiap kali model domain berubah.
    Ia membaca ``request.format_schema`` dan membangkitkan instance minimal yang
    memenuhinya.

    ``overrides`` menimpa nilai berdasarkan **nama properti**, di kedalaman mana
    pun. Itulah cara mengarahkan skenario (mis. reviewer yang menolak) tanpa
    menulis JSON.
    """

    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        self._overrides: dict[str, Any] = {**DEFAULT_OVERRIDES, **(overrides or {})}
        super().__init__(self._echo)

    def _echo(self, request: ChatRequest, call_number: int) -> ChatResult:
        schema = request.format_schema
        if not schema:
            raise AssertionError(
                "SchemaEchoChatModel hanya berguna bila permintaan menyertakan "
                "format_schema. Permintaan tanpa skema berarti ada jalur kode yang "
                "memanggil model tanpa kontrak keluaran."
            )
        payload = example_instance(schema, self._overrides)
        return _result(json.dumps(payload, ensure_ascii=False), request)


def _result(text: str, request: ChatRequest) -> ChatResult:
    """Bungkus teks menjadi ``ChatResult`` dengan metadata yang masuk akal."""
    return ChatResult(
        text=text,
        model=request.model or "palsu:test",
        done_reason="stop",
        prompt_tokens=10,
        output_tokens=20,
        latency_ms=1,
    )


def json_draft(**fields: Any) -> str:
    """Bantu menulis balasan skrip: ``json_draft(title="Bab 1")``."""
    return json.dumps(fields, ensure_ascii=False)


__all__ = [
    "DEFAULT_OVERRIDES",
    "ExplodingChatModel",
    "RecordingChatModel",
    "SchemaEchoChatModel",
    "ScriptedChatModel",
    "json_draft",
]
