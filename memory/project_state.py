"""Penyimpanan state berbasis berkas JSON (§28).

Layout yang dijaga berkas ini::

    state/
    ├── book.json                    ← BookState (spesifikasi + ringkasan + istilah)
    ├── chapter01.json               ← ChapterRecord — OTORITATIF
    ├── chapter02.json
    └── parse_fail/
        └── chapter02.attempt2.txt   ← teks mentah LLM yang gagal di-parse

**``chapterNN.json`` otoritatif; Markdown di ``output/`` turunan.** Itu bukan
preferensi gaya, melainkan syarat agar resume bekerja: ``ChapterRecord`` memuat
status, riwayat vonis, dan penghitung revisi; Markdown hanya memuat prosa.
Markdown yang hilang dapat dirender ulang dari record dengan murah — karena
``domain.rendering`` murni — sedangkan status yang hilang tidak dapat
direkonstruksi dari prosa siapa pun.

**Tidak ada yang pernah dihapus otomatis.** State yang rusak adalah bukti, bukan
sampah. Satu-satunya cara menghapusnya adalah perintah eksplisit dari pengguna.
"""

from __future__ import annotations

from pathlib import Path

from domain.chapter import ChapterRecord
from domain.ports import Clock
from domain.state import BookState
from memory.chapter_state import (
    decode_book,
    decode_chapter,
    encode_book,
    encode_chapter,
    is_approved,
)
from memory.checkpoints import atomic_write_json, atomic_write_text, file_exists, read_json

#: Nama berkas state tingkat buku.
BOOK_FILENAME = "book.json"


class JsonStateStore:
    """Implementasi :class:`domain.ports.StateStore` di atas berkas JSON.

    Satu-satunya kelas di seluruh aplikasi yang menulis ke ``state/``. Itulah
    yang membuat §28 dapat dipercaya: tidak ada agent yang "sekadar menyimpan
    sesuatu" di tempatnya sendiri, sehingga tidak ada state kedua yang harus
    dijaga sinkron.

    ``clock`` di-inject, bukan dibaca langsung dari ``datetime.now()``, supaya
    berkas hasil tes dapat dibandingkan persis. Adapter menstempel waktu saat
    **menulis** — nilai itu keterangan tentang berkasnya, bukan bagian dari
    keputusan bisnis mana pun, jadi pemanggil tidak perlu memikirkannya.
    """

    def __init__(
        self,
        state_dir: Path,
        *,
        clock: Clock,
        parse_fail_dir: Path | None = None,
    ) -> None:
        self._state_dir = state_dir
        self._clock = clock
        self._parse_fail_dir = parse_fail_dir or (state_dir / "parse_fail")

    # -- jalur -------------------------------------------------------------
    @property
    def state_dir(self) -> Path:
        """Direktori state (dibuat bila belum ada saat pertama menulis)."""
        return self._state_dir

    @property
    def parse_fail_dir(self) -> Path:
        """Direktori teks mentah yang gagal di-parse (§37)."""
        return self._parse_fail_dir

    def book_path(self) -> Path:
        """Jalur ``state/book.json``."""
        return self._state_dir / BOOK_FILENAME

    def chapter_path(self, number: int) -> Path:
        """Jalur ``state/chapterNN.json`` untuk bab ``number``.

        Dua digit, dengan nol di depan, supaya ``ls`` dan diff git menampilkan
        bab dalam urutan yang benar. ``target_chapters`` dibatasi 60 oleh
        :class:`~domain.book.BookRequest`, jadi dua digit selalu cukup.
        """
        return self._state_dir / f"chapter{number:02d}.json"

    # -- tingkat buku ------------------------------------------------------
    def load_book(self) -> BookState | None:
        """Baca ``state/book.json``, atau ``None`` bila belum pernah direncanakan."""
        path = self.book_path()
        if not file_exists(path):
            return None
        return decode_book(read_json(path), source=str(path))

    def save_book(self, state: BookState) -> None:
        """Tulis ``state/book.json`` secara atomik."""
        stamped = state.model_copy(update={"updated_at": self._clock.now_iso()})
        atomic_write_json(self.book_path(), encode_book(stamped))

    # -- tingkat bab -------------------------------------------------------
    def load_chapter(self, number: int) -> ChapterRecord | None:
        """Baca ``state/chapterNN.json``, atau ``None`` bila bab belum dikerjakan."""
        path = self.chapter_path(number)
        if not file_exists(path):
            return None
        return decode_chapter(read_json(path), source=str(path))

    def save_chapter(self, record: ChapterRecord) -> None:
        """Tulis ``state/chapterNN.json`` secara atomik."""
        stamped = record.model_copy(update={"updated_at": self._clock.now_iso()})
        atomic_write_json(self.chapter_path(record.number), encode_chapter(stamped))

    def is_completed(self, number: int) -> bool:
        """True bila bab ``number`` sudah ``APPROVED`` (§28).

        Bab yang gagal **tidak** dianggap selesai — justru ia yang paling perlu
        dikerjakan ulang saat resume.
        """
        record = self.load_chapter(number)
        return record is not None and is_approved(record)

    # -- bukti kegagalan ---------------------------------------------------
    def save_raw_failure(self, number: int, attempt: int, raw: str) -> str:
        """Simpan teks mentah LLM yang gagal di-parse, kembalikan jalurnya (§37).

        Ini satu-satunya bukti untuk memperbaiki prompt: keluaran yang tidak
        dapat di-parse tidak muncul di log mana pun, karena ia tidak pernah
        menjadi objek apa pun. Menelannya berarti kehilangan bukti tersebut.

        Disimpan sebagai teks mentah, **bukan** JSON: isinya justru sering bukan
        JSON, dan membungkusnya dalam struktur hanya akan menambah lapisan yang
        harus dilewati saat membacanya.
        """
        path = self._parse_fail_dir / f"chapter{number:02d}.attempt{attempt}.txt"
        header = (
            f"# Bab {number}, percobaan {attempt} - keluaran mentah yang gagal di-parse\n"
            f"# Disimpan {self._clock.now_iso()}\n"
            f"# Berkas ini bukti, bukan sampah. Jangan dihapus sebelum promptnya diperbaiki.\n\n"
        )
        atomic_write_text(path, header + raw)
        return str(path)

    # -- kemudahan ---------------------------------------------------------
    def load_chapters(self, numbers: tuple[int, ...]) -> tuple[ChapterRecord, ...]:
        """Baca beberapa bab sekaligus, melewati yang belum ada.

        Bukan bagian dari port: ini hanya penghematan bagi pemanggil yang memang
        membutuhkan seluruh bab yang sudah dikerjakan.
        """
        found = (self.load_chapter(number) for number in numbers)
        return tuple(record for record in found if record is not None)

    def completed_numbers(self, numbers: tuple[int, ...]) -> frozenset[int]:
        """Nomor bab yang sudah ``APPROVED`` di antara ``numbers`` (§28).

        Inilah himpunan yang membuat resume bekerja: ``run`` melewatinya secara
        bawaan, dan ``--force`` mengabaikannya.

        :raises StateCorruptError: bila salah satu berkasnya rusak. Sengaja tidak
            ditelan: resume yang diam-diam melewati bab karena berkasnya rusak
            adalah resume yang kehilangan pekerjaan.
        """
        return frozenset(number for number in numbers if self.is_completed(number))


__all__ = ["BOOK_FILENAME", "JsonStateStore"]
