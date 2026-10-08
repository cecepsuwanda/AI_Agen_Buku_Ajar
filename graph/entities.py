"""Konsep yang dapat dipastikan dari sumbernya (§11, §14).

**Mengapa berkas ini membaca LaTeX sendiri, padahal ``ingestion/latex_loader.py``
sudah melakukannya.** Bukan kelalaian, dan bukan duplikasi: keduanya membaca teks
yang sama untuk **dua pertanyaan yang berbeda**. Pemuat di ``ingestion/`` mengubah
berkas menjadi potongan untuk pencarian semantik, dan karena itu ia meratakan
strukturnya menjadi teks. Yang dibutuhkan graf justru sebaliknya — batas antar
bagian, judulnya, dan label yang dirujuk — dan meratakannya lebih dulu berarti
membangunnya kembali dari teks yang sudah kehilangan batas itu. Aturan lapisan
menegakkan pemisahan ini: sebuah adapter tidak boleh memanggil adapter lain
(``test_adapter_layers_do_not_import_each_other``).

**Yang diambil, dan yang sengaja tidak.** Hanya bagian dan definisi. Relasi
``uses``/``produces``/``consumes`` seperti contoh §14 (*Lexer memakai Regular
Expression*) **tidak** diambil di sini: kalimat itu tidak menyatakan dirinya
sebagai relasi dengan cara yang dapat dibaca mesin, dan menebaknya dari kedekatan
kata akan mengisi graf dengan hubungan yang tidak pernah ditulis siapa pun.
:data:`~domain.graph.RELATIONS` sudah menyediakan kosakatanya supaya pengekstrak
yang sesungguhnya — kelak, lewat model — punya sasaran yang jelas.

Isi ``input/source_latex/`` pada proyek ini untuk sementara **kosong**, sehingga
graf yang dibangun darinya juga kosong. Itu keadaan yang jujur, bukan kegagalan:
§14 sendiri menyebut knowledge graph sebagai bagian yang opsional pada MVP.
"""

from __future__ import annotations

import re

from domain.graph import ConceptNode

#: Judul bagian LaTeX, termasuk ``\subsection`` dan bentuk berbintangnya.
SECTION_PATTERN = re.compile(r"\\(?:sub)*section\*?\{([^}]*)\}")

#: Blok definisi LaTeX (§11). Isinya diambil sebagai definisi konsep bagian itu.
DEFINITION_PATTERN = re.compile(r"\\begin\{definition\}(.*?)\\end\{definition\}", re.DOTALL)

#: Perintah LaTeX yang dibuang sebelum teks disimpan sebagai definisi.
_LABEL_PATTERN = re.compile(r"\\label\{[^}]*\}")

#: Perintah penekanan yang **penandanya** dibuang, isinya dipertahankan.
_EMPHASIS_PATTERN = re.compile(r"\\(?:emph|textbf|textit|texttt|mathrm)\{([^}]*)\}")


def sections_of(text: str) -> tuple[tuple[str, str], ...]:
    """Pasangan ``(judul, isi)`` untuk setiap bagian LaTeX, urut kemunculan (MURNI).

    Judul yang sama yang muncul dua kali **tetap dua bagian**: yang kedua bukan
    kekeliruan yang boleh dirapikan di sini, dan menghapusnya berarti menyembunyikan
    bab yang mengulang dirinya sendiri dari satu-satunya pembaca yang dapat
    melihatnya.

    Teks sebelum bagian pertama — prakata, ``\\chapter``, dan sebagainya — tidak
    menjadi bagian. Ia tidak punya judul, dan mengarang satu untuknya berarti
    memasukkan kalimat pembuka ke dalam graf sebagai konsep.
    """
    found: list[tuple[str, str]] = []
    matches = list(SECTION_PATTERN.finditer(text))
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        if not title:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        found.append((title, text[match.end() : end]))
    return tuple(found)


def plain_definition(text: str) -> str:
    """Teks definisi yang dapat dibaca, tanpa penanda LaTeX yang mengganggu (MURNI).

    Yang dibuang hanyalah yang **tidak menyatakan apa pun**: label dan perintah
    penekanan. Isi perintah penekanan justru dipertahankan — ``\\emph{DFA}``
    menyatakan hal yang sama dengan ``DFA``, dan membuang seluruh perintahnya
    bersama isinya akan menghapus istilah yang justru sedang dicari.
    """
    without_labels = _LABEL_PATTERN.sub(" ", text)
    without_commands = _EMPHASIS_PATTERN.sub(r"\1", without_labels)
    return " ".join(without_commands.split())


def concepts_from_latex(text: str, *, chapter: int | None = None) -> tuple[ConceptNode, ...]:
    """Konsep yang dapat dipastikan dari satu berkas LaTeX (§11, §14), MURNI.

    Nama konsepnya adalah **judul bagiannya**, dan definisinya adalah blok
    ``definition`` pertama di dalam bagian itu. Bagian tanpa definisi tetap
    menjadi konsep dengan definisi kosong: ia tetap diajarkan, dan
    :func:`~domain.graph.find_duplicate_explanations` justru perlu dapat melihat
    bahwa ia belum dijelaskan.
    """
    concepts: list[ConceptNode] = []
    for title, body in sections_of(text):
        match = DEFINITION_PATTERN.search(body)
        definition = plain_definition(match.group(1)) if match is not None else ""
        concepts.append(ConceptNode(name=title, definition=definition, chapter=chapter))
    return tuple(concepts)


__all__ = [
    "DEFINITION_PATTERN",
    "SECTION_PATTERN",
    "concepts_from_latex",
    "plain_definition",
    "sections_of",
]
