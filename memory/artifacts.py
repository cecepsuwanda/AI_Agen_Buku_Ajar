"""Penulisan deliverable Markdown ke ``output/`` (§39).

Layout yang dijaga berkas ini::

    output/
    ├── book.md                  ← gabungan, hasil `export`
    └── chapters/
        ├── chapter01.md
        └── chapter02.md

**``output/`` di-track git; ``state/`` tidak.** Itu keputusan §39, dan ia punya
konsekuensi yang terasa di sini: setiap berkas yang ditulis modul ini akan
muncul di diff, sehingga formatnya harus stabil. Karena itu penulisannya
memakai :func:`~memory.checkpoints.atomic_write_text` yang sama dengan state —
``newline="\\n"`` dan UTF-8 tanpa escape. CRLF atau ``\\u00e9`` yang ter-escape
akan mengubah setiap paragraf Bahasa Indonesia menjadi diff raksasa pada
perubahan berikutnya.

Tidak ada kelas ini yang membaca. Markdown adalah **turunan**: bila ia hilang,
ia dirender ulang dari ``state/chapterNN.json`` dengan murah, karena
:func:`domain.rendering.render_chapter_markdown` murni. Karena itu tidak ada
``load`` di sini — satu arah saja.
"""

from __future__ import annotations

from pathlib import Path

from domain.rendering import chapter_filename
from memory.checkpoints import atomic_write_text

#: Nama subdirektori bab di dalam ``output/``.
CHAPTERS_DIRNAME = "chapters"

#: Nama berkas gabungan.
BOOK_FILENAME = "book.md"


class MarkdownArtifacts:
    """Implementasi :class:`domain.ports.ChapterArtifacts` di atas filesystem.

    Menerima **direktori keluaran**, bukan dua jalur terpisah, supaya layout
    §39 hanya didefinisikan di satu tempat. Kalau ``output/chapters/`` suatu saat
    berpindah, yang berubah adalah konstanta di modul ini, bukan setiap pemanggil.
    """

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    @property
    def output_dir(self) -> Path:
        """Direktori keluaran (dibuat saat pertama menulis)."""
        return self._output_dir

    @property
    def chapters_dir(self) -> Path:
        """Direktori ``output/chapters/``."""
        return self._output_dir / CHAPTERS_DIRNAME

    def chapter_path(self, number: int) -> Path:
        """Jalur Markdown bab ``number``. Nama berkasnya sengaja tidak dihitung ulang.

        Dua digit dengan nol di depan, sama seperti ``state/chapterNN.json``:
        itulah yang membuat ``ls`` dan diff git menampilkan bab dalam urutan
        yang benar. Diambil dari :func:`domain.rendering.chapter_filename` supaya
        tidak ada dua definisi penamaan berkas bab di seluruh aplikasi.
        """
        return self.chapters_dir / chapter_filename(number)

    def save_chapter(self, number: int, text: str) -> str:
        """Tulis ``output/chapters/chapterNN.md``, kembalikan jalurnya."""
        path = self.chapter_path(number)
        atomic_write_text(path, text)
        return str(path)

    def exists(self, number: int) -> bool:
        """True bila Markdown bab ``number`` sudah ada.

        Memakai ``Path.is_file``, bukan ``exists``: direktori yang kebetulan
        bernama ``chapter01.md`` akan lolos dari ``exists`` dan membuat
        pemanggilnya mengira bab itu sudah dirender.
        """
        return self.chapter_path(number).is_file()

    def save_book(self, text: str) -> str:
        """Tulis ``output/book.md``, kembalikan jalurnya."""
        path = self._output_dir / BOOK_FILENAME
        atomic_write_text(path, text)
        return str(path)


__all__ = ["BOOK_FILENAME", "CHAPTERS_DIRNAME", "MarkdownArtifacts"]
