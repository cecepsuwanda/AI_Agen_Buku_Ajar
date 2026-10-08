"""Penerjemahan record ↔ dict, dan pemeriksaan ``schema_version`` — MURNI.

Berkas ini **tidak menyentuh filesystem**. Ia menerima data yang sudah diurai
dan mengembalikan model, atau menjelaskan dengan tepat mengapa ia tidak bisa.
Pemisahan itu disengaja: penguraian JSON adalah urusan
:mod:`memory.checkpoints`, sedangkan *arti* dari apa yang diurai adalah urusan
berkas ini — dan yang kedua itulah yang layak diuji tanpa ``tmp_path``.

``schema_version`` diperiksa sebelum validasi, bukan sesudah. Bedanya penting:
berkas dari versi yang lebih baru akan gagal divalidasi dengan pesan field yang
membingungkan ("field 'x' tidak dikenal"), padahal masalah sebenarnya adalah
versi. Memeriksa versinya lebih dulu membuat pesannya dapat ditindaklanjuti.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from domain.chapter import ChapterRecord
from domain.enums import is_approved as is_approved_status
from domain.errors import StateCorruptError
from domain.state import BookState

#: Versi skema yang dipahami build ini.
#:
#: Naikkan bila ada perubahan yang **tidak** kompatibel. Menambah field dengan
#: nilai bawaan tidak mengharuskan kenaikan versi — berkas lama tetap terbaca.
SUPPORTED_SCHEMA_VERSION = 1


def _check_version(payload: Mapping[str, Any], *, source: str) -> None:
    """Pastikan ``schema_version`` dapat dipahami build ini.

    :raises StateCorruptError: bila versinya berbeda atau bukan bilangan.
    """
    version = payload.get("schema_version", SUPPORTED_SCHEMA_VERSION)
    if version != SUPPORTED_SCHEMA_VERSION:
        raise StateCorruptError(
            source,
            f"schema_version {version!r} tidak dikenal "
            f"(build ini memahami {SUPPORTED_SCHEMA_VERSION})",
        )


def decode_chapter(payload: Any, *, source: str) -> ChapterRecord:
    """Ubah data yang sudah diurai menjadi :class:`~domain.chapter.ChapterRecord`.

    :param source: label untuk pesan kesalahan — biasanya jalur berkasnya.
        Sengaja ``str``, bukan ``Path``: berkas ini tidak boleh tahu bahwa
        keadaan itu disimpan di berkas.

    :raises StateCorruptError: bila isinya bukan objek, versinya tidak dikenal,
        atau field-nya tidak sah.
    """
    if not isinstance(payload, dict):
        raise StateCorruptError(source, f"isi bukan objek JSON melainkan {type(payload).__name__}")

    _check_version(payload, source=source)

    try:
        return ChapterRecord.model_validate(payload)
    except ValidationError as exc:
        raise StateCorruptError(source, str(exc)) from exc


def decode_book(payload: Any, *, source: str) -> BookState:
    """Ubah data yang sudah diurai menjadi :class:`~domain.state.BookState`.

    :raises StateCorruptError: bila isinya tidak sah.
    """
    if not isinstance(payload, dict):
        raise StateCorruptError(source, f"isi bukan objek JSON melainkan {type(payload).__name__}")

    _check_version(payload, source=source)

    try:
        return BookState.model_validate(payload)
    except ValidationError as exc:
        raise StateCorruptError(source, str(exc)) from exc


def encode_chapter(record: ChapterRecord) -> dict[str, Any]:
    """Ubah record menjadi dict siap disimpan (MURNI).

    ``mode="json"`` dipakai supaya ``StrEnum`` dan tipe lain yang bukan JSON
    asli ditulis sebagai nilainya, bukan sebagai repr Python — berkas state
    harus dapat dibaca alat lain, bukan hanya oleh Pydantic.

    Draf pada ``ReviewResult.enriched_draft`` **dibuang** sebelum penulisan.
    ``record.draft`` sudah menyimpan draf yang sama — gate penulisan
    menyerahkannya tepat supaya ia menjadi draf berjalan — jadi menyimpannya
    lagi di dalam tiap review akan menyalin seluruh isi bab sekali per gate
    yang memperkayanya. Berkas state yang menggelembung dua kali lipat bukan
    masalah ruang, melainkan masalah **keterbacaan**: setiap perubahan satu
    paragraf akan menghasilkan diff sebesar dua bab pada dua tempat berbeda.

    Yang tersimpan di dalam review adalah **vonisnya**, bukan isi drafnya.
    Draf yang diperkaya tetap ada di ``record.draft``, sehingga resume
    memulihkannya utuh tanpa perlu membaca ulang review mana pun.
    """
    payload = record.model_dump(mode="json")
    for review in payload.get("reviews", ()):
        review.pop("enriched_draft", None)
    return payload


def encode_book(state: BookState) -> dict[str, Any]:
    """Ubah state buku menjadi dict siap disimpan (MURNI)."""
    return state.model_dump(mode="json")


def is_approved(record: ChapterRecord) -> bool:
    """True bila bab ini sudah selesai dan tidak akan dikerjakan ulang (§28) (MURNI).

    Hanya ``APPROVED`` yang dihitung. ``FAILED`` juga status terminal, tetapi ia
    berarti "berhenti karena rusak" — dan bab yang berhenti karena rusak justru
    yang paling perlu dikerjakan ulang, bukan yang paling perlu dilewati.

    Definisinya sendiri tinggal di :func:`domain.enums.is_approved` supaya
    ``domain/`` dan ``memory/`` tidak dapat berbeda pendapat soal apa artinya
    "selesai" — kalau keduanya punya salinannya sendiri, cepat atau lambat
    resume akan memakai definisi yang lain daripada yang dipakai ``--force``.
    """
    return is_approved_status(record.status)


__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "decode_book",
    "decode_chapter",
    "encode_book",
    "encode_chapter",
    "is_approved",
]
