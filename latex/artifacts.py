"""Penulisan sumber LaTeX ke ``output/latex/`` (§25, §39).

Layout yang dijaga berkas ini::

    output/
    └── latex/
        ├── main.tex              ← skeleton buku, dipakai `export --latex` (§42)
        ├── references.bib        ← daftar pustaka, dari BookState.citations
        └── chapters/
            ├── chapter01.tex
            └── chapter02.tex

**``output/`` di-track git** (§39), sehingga setiap berkas yang ditulis di sini
muncul di diff dan formatnya harus stabil. Karena itu penulisannya memakai
``newline="\\n"`` dan UTF-8 tanpa escape, sama seperti Markdown dan state: CRLF
akan mengubah setiap baris Bahasa Indonesia menjadi diff raksasa pada perubahan
berikutnya.

**Mengapa penulis atomiknya ditulis ulang di sini, bukan dipinjam dari
:func:`memory.checkpoints.atomic_write_text`.** Bukan karena lupa, dan bukan
karena berbeda: perilakunya sengaja identik (``.tmp`` di direktori yang sama,
``os.replace`` dengan percobaan ulang untuk handle Windows yang tertahan).
Alasannya arsitektur — ``memory/`` termasuk lapisan luar, dan sebuah adapter
tidak boleh mengenalnya (``test_adapter_layers_do_not_import_outer_layers``).
Yang justru akan salah adalah sebaliknya: mengizinkan ``latex/`` mengimpor
``memory/`` berarti pemindahan direktori state menyentuh paket LaTeX, dan
menguji paket LaTeX berarti menyeret penyimpan JSON.

Template ada di :func:`templates_dir`. Isinya **data**, bukan kode: berkas
``.tex`` yang di-``\\include`` ``main.tex``, dan yang boleh ditimpa pengguna
lewat ``input/source_latex/`` (§42). Fungsi itu ada di sini, bukan di
``domain/``, karena menemukan direktori paket menuntut ``pathlib`` dan
``domain/`` terlarang menyentuhnya.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from domain.errors import ArtifactWriteError
from domain.latex import chapter_latex_filename

#: Subdirektori potongan bab di dalam ``output/latex/``.
CHAPTERS_DIRNAME = "chapters"

#: Nama berkas daftar pustaka.
#:
#: Nama ini **terikat** pada ``latex/templates/preamble.tex``, yang memuat
#: ``\\bibliography{references}`` — BibTeX mencari ``references.bib`` dari nama
#: itu. Menggantinya berarti menyunting dua tempat, dan karena itu ada tes yang
#: mengunci keduanya agar tidak dapat menyimpang tanpa suara.
BIBLIOGRAPHY_FILENAME = "references.bib"

#: Subdirektori template bawaan di dalam paket ini.
_TEMPLATES_DIRNAME = "templates"

#: Berapa kali ``os.replace`` diulang sebelum menyerah. Sama dengan state, dan
#: dengan alasan yang sama: antivirus dan indexer di Windows dapat memegang
#: handle berkas beberapa puluh milidetik setelah penulis menutupnya.
_REPLACE_RETRIES = 8
_REPLACE_BACKOFF_S = 0.025


def templates_dir() -> Path:
    """Direktori template LaTeX bawaan (``latex/templates/``)."""
    return Path(__file__).resolve().parent / _TEMPLATES_DIRNAME


def _replace_with_retry(tmp: Path, target: Path) -> None:
    """Pindahkan ``tmp`` ke ``target``, tahan terhadap handle Windows yang tertahan.

    :raises ArtifactWriteError: bila seluruh percobaan gagal.
    """
    last: PermissionError | None = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, target)
            return
        except PermissionError as exc:  # pragma: no cover - bergantung timing OS
            last = exc
            time.sleep(_REPLACE_BACKOFF_S * (attempt + 1))

    raise ArtifactWriteError(target, "berkas terkunci proses lain") from last


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Tulis ``text`` ke ``path`` secara atomik.

    :raises ArtifactWriteError: bila penulisan gagal.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
        _replace_with_retry(tmp, path)
    except OSError as exc:
        raise ArtifactWriteError(path, str(exc)) from exc


class FileLatexArtifacts:
    """Implementasi :class:`domain.ports.LatexArtifacts` di atas filesystem.

    Menerima **direktori LaTeX**, bukan dua jalur terpisah, supaya layout §25
    hanya didefinisikan di satu tempat. Bila ``output/latex/`` suatu saat
    berpindah, yang berubah adalah konstanta di modul ini, bukan setiap
    pemanggil.

    Tidak ada method yang membaca: sumber LaTeX adalah **turunan** dari
    ``ChapterRecord`` persis seperti Markdown, dan bila berkasnya hilang ia
    dirender ulang dengan murah karena :func:`domain.latex.render_chapter_latex`
    murni. Satu arah saja, sama seperti :class:`~memory.artifacts.MarkdownArtifacts`.
    """

    def __init__(self, latex_dir: Path) -> None:
        self._latex_dir = latex_dir

    @property
    def latex_dir(self) -> Path:
        """Direktori keluaran LaTeX (``output/latex``)."""
        return self._latex_dir

    @property
    def chapters_dir(self) -> Path:
        """Direktori ``output/latex/chapters/``."""
        return self._latex_dir / CHAPTERS_DIRNAME

    def chapter_path(self, number: int) -> Path:
        """Jalur potongan LaTeX bab ``number``.

        Nama berkasnya diambil dari :func:`domain.latex.chapter_latex_filename`
        supaya tidak ada dua definisi penamaan berkas bab di seluruh aplikasi —
        ``\\include`` di ``main.tex`` harus menunjuk nama yang sama persis.
        """
        return self.chapters_dir / chapter_latex_filename(number)

    def bibliography_path(self) -> Path:
        """Jalur ``output/latex/references.bib``."""
        return self._latex_dir / BIBLIOGRAPHY_FILENAME

    def save_chapter(self, number: int, text: str) -> str:
        """Tulis ``output/latex/chapters/chapterNN.tex``, kembalikan jalurnya."""
        path = self.chapter_path(number)
        atomic_write_text(path, text)
        return str(path)

    def save_bibliography(self, text: str) -> str:
        """Tulis ``output/latex/references.bib``, kembalikan jalurnya.

        Ditulis ulang **seluruhnya** dari ``BookState.citations`` pada setiap
        bab, bukan ditambahi. Entri yang tertinggal dari sumber yang sudah
        dihapus akan tetap dikompilasi ke daftar pustaka buku, dan tidak ada
        satu pun cara menemukannya setelah PDF-nya jadi.
        """
        path = self.bibliography_path()
        atomic_write_text(path, text)
        return str(path)


__all__ = [
    "BIBLIOGRAPHY_FILENAME",
    "CHAPTERS_DIRNAME",
    "FileLatexArtifacts",
    "atomic_write_text",
    "templates_dir",
]
