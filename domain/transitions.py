"""State machine bab (§27) — MURNI.

Transisi bukan rantai ``if`` yang tersebar, melainkan **data + satu fungsi
total**. Menambah status = menambah satu baris di tabel, tanpa rantai ``if``
yang harus disunting.
"""

from __future__ import annotations

from typing import Mapping

from domain.enums import ChapterEvent, ChapterStatus
from domain.errors import IllegalTransitionError, InvalidGateError

#: Urutan bahagia §27. Sebuah gate boleh memajukan status ke posisi mana pun
#: yang **lebih jauh** di rantai ini — itulah yang memungkinkan MVP melompat
#: dari DRAFTED langsung ke REVIEWED, sementara rantai 10-tahap tetap utuh dan
#: siap diisi gate baru satu per satu.
CHAIN: tuple[ChapterStatus, ...] = (
    ChapterStatus.PLANNED,
    ChapterStatus.RESEARCHED,
    ChapterStatus.DRAFTED,
    ChapterStatus.FACT_CHECKED,
    ChapterStatus.CITATION_CHECKED,
    ChapterStatus.PEDAGOGY_REVIEWED,
    ChapterStatus.CONSISTENCY_CHECKED,
    ChapterStatus.REVIEWED,
    ChapterStatus.LATEX_GENERATED,
    ChapterStatus.LATEX_COMPILED,
    ChapterStatus.APPROVED,
)

_CHAIN_INDEX: Mapping[ChapterStatus, int] = {s: i for i, s in enumerate(CHAIN)}

#: Transisi struktural — pergerakan yang bukan hasil vonis sebuah gate.
#:
#: ``ChapterEvent.FAIL`` sengaja TIDAK ada di tabel: ia berlaku dari status mana
#: pun kecuali yang sudah terminal, dan tabel statis menyatakan "dari mana saja"
#: dengan buruk. Ditangani eksplisit di :func:`transition` (lihat komentarnya).
_TRANSITIONS: Mapping[tuple[ChapterStatus, ChapterEvent], ChapterStatus] = {
    # Alur maju
    (ChapterStatus.PLANNED, ChapterEvent.RESEARCH): ChapterStatus.RESEARCHED,
    (ChapterStatus.RESEARCHED, ChapterEvent.DRAFT): ChapterStatus.DRAFTED,
    # Draf ulang setelah revisi (§27 cabang kegagalan)
    (ChapterStatus.REVISION, ChapterEvent.DRAFT): ChapterStatus.DRAFTED,
    # Vonis gate: gagal
    (ChapterStatus.DRAFTED, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.FACT_CHECKED, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.CITATION_CHECKED, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.PEDAGOGY_REVIEWED, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    (ChapterStatus.CONSISTENCY_CHECKED, ChapterEvent.REVIEW_FAIL): ChapterStatus.FAILED_REVIEW,
    # Masuk kembali ke antrean revisi
    (ChapterStatus.FAILED_REVIEW, ChapterEvent.REVISE): ChapterStatus.REVISION,
    # Persetujuan akhir
    (ChapterStatus.REVIEWED, ChapterEvent.APPROVE): ChapterStatus.APPROVED,
    (ChapterStatus.LATEX_COMPILED, ChapterEvent.APPROVE): ChapterStatus.APPROVED,
}


def transition(status: ChapterStatus, event: ChapterEvent) -> ChapterStatus:
    """Status berikutnya untuk ``(status, event)``. **Fungsi total.**

    :raises IllegalTransitionError: bila pasangan tersebut tidak sah.
    """
    if event is ChapterEvent.FAIL:
        # Kegagalan keras (parse gagal, output terpotong, dll) dapat terjadi dari
        # status mana pun yang belum final — tetapi tidak dari bab yang sudah
        # disetujui, karena menandai bab yang telah disetujui sebagai gagal akan
        # menghapus pekerjaan yang sudah diterima.
        if status is ChapterStatus.APPROVED:
            raise IllegalTransitionError(status, event)
        return ChapterStatus.FAILED

    try:
        return _TRANSITIONS[(status, event)]
    except KeyError:
        raise IllegalTransitionError(status, event) from None


def can_advance(source: ChapterStatus, target: ChapterStatus) -> bool:
    """True bila sebuah gate boleh memajukan ``source`` → ``target`` (MURNI).

    Hanya pergerakan **maju** di sepanjang :data:`CHAIN` yang sah. Ini yang
    memvalidasi ``produces`` milik setiap gate saat aplikasi start, sehingga
    konfigurasi yang salah gagal di baris pertama — bukan diam-diam merusak
    state di tengah proses.
    """
    if source not in _CHAIN_INDEX or target not in _CHAIN_INDEX:
        return False
    return _CHAIN_INDEX[target] > _CHAIN_INDEX[source]


def status_after_gate(
    source: ChapterStatus, produces: ChapterStatus, *, gate: str = "?"
) -> ChapterStatus:
    """Status setelah sebuah gate **lulus** (MURNI).

    :raises InvalidGateError: bila gate melompat mundur atau ke status di luar rantai.
    """
    if not can_advance(source, produces):
        raise InvalidGateError(gate, source, produces)
    return produces
