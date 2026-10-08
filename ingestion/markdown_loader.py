"""Pemuat Markdown dan teks biasa (§9) — MURNI.

Berkas ini **tidak** membuka berkas. Ia menerima teks dan mengembalikan potongan;
yang membacanya adalah :mod:`ingestion.sources`, dan pemisahan itu yang membuat
seluruh perilakunya dapat diuji dengan teks buatan.

Satu keputusan yang menentukan gunanya: **potongan dipisah menurut judul, bukan
menjadi satu dokumen raksasa.** Judul bagian adalah metadata ``section`` yang
diminta §9, dan ia satu-satunya hal yang memberi tahu pembaca kutipan kelak bahwa
sebuah paragraf berasal dari bagian "Analisis Leksikal" dan bukan dari kata
pengantar. Berkas tanpa satu pun judul — ``.txt``, misalnya — tetap menjadi satu
potongan dengan ``section`` kosong: lebih baik tidak ada keterangan bagian
daripada keterangan bagian yang dikarang.

Judulnya **ikut menjadi bagian dari teks**, tanpa tanda ``#``-nya. Menaruhnya
hanya di metadata akan membuat kata-kata judul tidak dapat ditemukan oleh
retrieval — padahal pertanyaan penulis bab justru sering berbunyi seperti judul.
"""

from __future__ import annotations

import re

from domain.document import Document, SourceType

#: Judul Markdown: satu sampai enam ``#`` di awal baris.
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _documents_from_sections(
    sections: tuple[tuple[str, str], ...],
    *,
    filename: str,
    source_type: SourceType = SourceType.MARKDOWN,
) -> tuple[Document, ...]:
    """Ubah ``(judul, isi)`` menjadi dokumen, membuang yang kosong.

    Potongan tanpa isi tidak dihasilkan: judul yang menggantung tidak dapat
    dikutip (tidak ada yang dikutip), dan menanamnya ke indeks hanya akan
    menghasilkan hasil pencarian yang menunjuk ke halaman kosong.
    """
    return tuple(
        Document(
            document_id=filename,
            filename=filename,
            source_type=source_type,
            text=body,
            section=heading,
        )
        for heading, body in sections
        if body
    )


def parse_markdown(
    text: str,
    *,
    filename: str,
    source_type: SourceType = SourceType.MARKDOWN,
) -> tuple[Document, ...]:
    """Pecah Markdown menjadi satu :class:`Document` per judul (MURNI).

    Baris-baris di bawah sebuah judul disatukan apa adanya, **termasuk baris
    kosongnya** — di situlah pemisah paragraf berada, dan pemisah itu yang
    dipakai :mod:`rag.chunker` untuk memotong. Merapikannya di sini berarti
    memindahkan keputusan pemotongan ke tempat yang tidak membacanya.
    """
    sections: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        # Hanya baris **kosong** di tepi yang dibuang, bukan seluruh spasi:
        # ``strip()`` akan ikut menghapus lekukan baris pertama isi, dan pada
        # blok kode lekukan itu adalah isinya — baris pertama ``x = 1`` di bawah
        # judul "Contoh" harus tetap menjorok.
        body = "\n".join(buffer).strip("\n")
        if body.strip():
            sections.append((heading, f"{heading}\n\n{body}" if heading else body))

    for line in text.splitlines():
        match = _HEADING.match(line)
        if match is None:
            buffer.append(line)
            continue
        flush()
        heading = match.group(2).strip()
        buffer = []

    flush()
    return _documents_from_sections(tuple(sections), filename=filename, source_type=source_type)


def parse_plain_text(text: str, *, filename: str) -> tuple[Document, ...]:
    """Perlakukan teks tanpa struktur sebagai satu dokumen utuh (MURNI).

    Berkas ``.txt`` tidak punya judul, dan menganggap baris pertamanya judul akan
    mengubah kalimat pertama setiap berkas menjadi nama bagian.
    """
    body = text.strip()
    if not body:
        return ()
    return _documents_from_sections((("", body),), filename=filename, source_type=SourceType.TEXT)


__all__ = ["parse_markdown", "parse_plain_text"]
