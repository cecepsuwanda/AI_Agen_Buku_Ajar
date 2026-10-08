"""Pemeriksa rujukan silang tingkat buku (§26).

Log kompilasi sudah melaporkan ``\\ref`` dan ``\\cite`` yang menggantung. Berkas
ini ada karena pemeriksaan itu **juga harus bekerja di mesin tanpa LaTeX** — dan
justru di situlah isyaratnya paling dibutuhkan. Kalau ``latexmk`` tidak terpasang
atau gagal sebelum sempat menulis log, satu-satunya cara mengetahui bahwa bab 5
mengutip kunci yang tidak pernah masuk ``references.bib`` adalah memeriksanya
sendiri.

Perbedaannya dengan gate §26 perlu dinyatakan terang, karena keduanya membaca hal
yang sama: gate §26 mengompilasi **satu bab** dan karena itu tidak dapat
mengetahui apa pun tentang bab lain — ``\\ref`` ke bab berikutnya selalu tampak
menggantung di sana. Di berkas ini seluruh bab sudah dirakit, sehingga "menggantung"
berarti menggantung sungguhan.

Aturannya sendiri murni dan tinggal di :func:`~domain.latex.crossref_findings`;
yang ada di sini hanyalah pembacaan berkas, karena ``domain/`` tidak boleh
menyentuh ``pathlib``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from domain.latex import chapter_latex_filename, crossref_findings
from latex.artifacts import BIBLIOGRAPHY_FILENAME, CHAPTERS_DIRNAME

#: Kunci setiap entri BibTeX (``@misc{kunci,``).
#:
#: Bukan parser BibTeX: yang dibutuhkan hanya nama kunci, dan kunci BibTeX tidak
#: boleh memuat koma. Parser yang mengurai seluruh sintaksis BibTeX — termasuk
#: kurung bersarang di dalam nilai — akan menjadi seratus baris kode untuk
#: mengambil satu kata yang sudah dibatasi oleh tanda baca.
_ENTRY_RE = re.compile(r"@\w+\{([^,]+),")


def bibliography_keys(text: str) -> tuple[str, ...]:
    """Kunci entri di dalam berkas ``.bib``, urut kemunculan (MURNI)."""
    return tuple(key.strip() for key in _ENTRY_RE.findall(text) if key.strip())


def check_crossrefs(
    latex_dir: Path,
    *,
    chapter_numbers: Sequence[int],
) -> tuple[str, ...]:
    """Temuan rujukan silang yang menggantung di seluruh buku (§26).

    Bab yang berkasnya belum ada dilewati tanpa suara. Bukan karena kelalaian:
    berkas yang hilang sudah dilaporkan di tempat lain — ia muncul di log
    kompilasi sebagai ``File not found`` — dan mengulanginya di sini hanya akan
    menambah satu baris yang menunjuk masalah yang sama.
    """
    sources: list[str] = []
    for number in chapter_numbers:
        path = latex_dir / CHAPTERS_DIRNAME / chapter_latex_filename(number)
        if path.is_file():
            sources.append(path.read_text(encoding="utf-8"))

    bib = latex_dir / BIBLIOGRAPHY_FILENAME
    keys = bibliography_keys(bib.read_text(encoding="utf-8")) if bib.is_file() else ()
    if not sources:
        return ()
    return crossref_findings("\n\n".join(sources), bibliography_keys=keys)


__all__ = ["bibliography_keys", "check_crossrefs"]
