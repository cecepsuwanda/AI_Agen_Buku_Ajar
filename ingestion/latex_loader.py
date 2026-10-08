"""Pemuat sumber LaTeX (§11) — MURNI.

§11 menolak satu pendekatan dengan tegas: *"LaTeX source jangan diperlakukan
hanya sebagai plain text."* Yang diminta bukan menghapus perintahnya, melainkan
**mengenali strukturnya** — dan struktur itu yang menjadi metadata ``section``
tiap potongan, persis seperti judul Markdown pada :mod:`ingestion.markdown_loader`.

Bentuk yang dipilih di sini adalah **segmentasi, bukan penerjemahan**. Parser ini
memotong sumber pada batas bab, sub-bab, dan lingkungan yang disebut §11
(``definition``, ``example``, ``theorem``, ``figure``, ``table``, ``lstlisting``),
lalu mengembalikan setiap potongan **verbatim** — perintah, komentar, dan rumusnya
utuh. Dua alasan:

* **Menyusun ulang teks berarti mengarang isi.** Setiap perintah yang
  diterjemahkan menjadi prosa adalah kalimat yang tidak ditulis siapa pun, dan
  kalimat itu kelak muncul di dalam kutipan buku ajar. Segmentasi tidak dapat
  mengarang apa pun — ia hanya memilih di mana memotong.
* **Kutipan harus sama dengan sumbernya.** Bahan ini akan dikutip dengan nomor
  halaman dan nama berkas. Kutipan yang isinya berbeda dari berkas aslinya tidak
  dapat diperiksa manusia, dan itu satu-satunya hal yang membuat pemeriksaan
  fakta (§21) bermakna.

Karena itu :func:`parse_latex` **tidak mengarang dan tidak menjatuhkan baris**:
setiap baris sumber yang berisi teks muncul verbatim di salah satu potongan,
sekali, dan dalam urutan yang sama. Yang hilang hanyalah baris kosong di tepi
antar-potongan — pemisah yang tidak memuat apa pun — sehingga menggabungkan
seluruh potongan dengan satu baris baru menghasilkan sumber semula persis untuk
berkas yang potongannya dipisahkan satu baris baru. Sifat itu diuji, dan ia yang
membuat parser ini dapat dipercaya untuk bahan yang bentuknya belum pernah
dilihat.
"""

from __future__ import annotations

import re

from domain.document import Document, SourceType

#: Perintah penanda bagian → tingkatnya. Tingkat dipakai untuk memotong tumpukan
#: judul: ``\section`` di dalam dua bab tidak boleh muncul di bawah bab pertama.
SECTION_LEVELS: dict[str, int] = {
    "chapter": 1,
    "section": 2,
    "subsection": 3,
    "subsubsection": 4,
}

#: Lingkungan yang dianggap batas potongan (§11).
#:
#: Daftarnya sengaja tertutup. ``itemize`` atau ``equation`` yang juga dijadikan
#: batas akan memecah penjelasan menjadi serpihan yang tidak dapat dibaca,
#: sedangkan keenam di bawah ini memang satuan yang berdiri sendiri — definisi,
#: contoh, teorema, gambar, tabel, dan kode.
STRUCTURAL_ENVIRONMENTS: frozenset[str] = frozenset(
    {"definition", "example", "theorem", "figure", "table", "lstlisting"}
)

#: Perintah bagian hanya dikenali di **awal baris**. Tanpa syarat itu, setiap
#: penyebutan ``\ref{sec:...}`` di tengah kalimat akan memotong potongan.
_SECTION = re.compile(r"^\\(chapter|section|subsection|subsubsection)\*?\{([^}]*)\}")
_BEGIN = re.compile(r"^\\begin\{([A-Za-z*]+)\}")
_END = re.compile(r"^\\end\{([A-Za-z*]+)\}")

#: Pemisah jalur bagian pada metadata, mis. ``"Bab 2 > Token > definition"``.
SECTION_SEPARATOR = " > "


def _match(line: str) -> tuple[str, str] | None:
    """Klasifikasi satu baris: ``("section"|"begin"|"end", nama)`` atau ``None``."""
    stripped = line.strip()
    section = _SECTION.match(stripped)
    if section is not None:
        return ("section", section.group(1) + "\x00" + section.group(2).strip())
    begin = _BEGIN.match(stripped)
    if begin is not None and begin.group(1).lower() in STRUCTURAL_ENVIRONMENTS:
        return ("begin", begin.group(1).lower())
    end = _END.match(stripped)
    if end is not None and end.group(1).lower() in STRUCTURAL_ENVIRONMENTS:
        return ("end", end.group(1).lower())
    return None


def parse_latex(text: str, *, filename: str) -> tuple[Document, ...]:
    """Pecah sumber LaTeX menjadi potongan pada batas strukturnya (MURNI).

    Baris pertama sebuah blok selalu merupakan penanda yang membukanya
    (``\\chapter{...}``, ``\\begin{definition}``), sehingga perintah itu ikut
    terbaca — dan dengan begitu kata-kata judulnya dapat ditemukan oleh retrieval.
    """
    lines = text.splitlines()
    blocks: list[tuple[str, str]] = []
    sections: list[str] = []
    environments: list[str] = []
    start = 0

    def close(end: int, path: tuple[str, ...]) -> None:
        body = "\n".join(lines[start:end]).strip()
        if body:
            blocks.append((SECTION_SEPARATOR.join(path), body))

    path: tuple[str, ...] = ()

    for index, line in enumerate(lines):
        marker = _match(line)
        if marker is None:
            continue

        kind, name = marker
        if kind == "section":
            close(index, path)
            command, title = name.split("\x00", 1)
            level = SECTION_LEVELS[command]
            sections = sections[: level - 1] + [title or command]
            start = index
        elif kind == "begin":
            close(index, path)
            environments.append(name)
            start = index
        else:  # end — baris penutupnya ikut masuk ke dalam lingkungannya
            close(index + 1, path)
            if environments:
                environments.pop()
            start = index + 1

        path = tuple(sections) + tuple(environments)

    close(len(lines), path)

    return tuple(
        Document(
            document_id=filename,
            filename=filename,
            source_type=SourceType.LATEX,
            text=body,
            section=section,
        )
        for section, body in blocks
    )


__all__ = [
    "SECTION_LEVELS",
    "SECTION_SEPARATOR",
    "STRUCTURAL_ENVIRONMENTS",
    "parse_latex",
]
