"""Perapian teks hasil ekstraksi (§9, §10) — MURNI.

Setiap sumber datang dengan kerapiannya sendiri, dan semuanya kotor dengan cara
yang berbeda. Yang disamakan di sini hanya hal-hal yang **tidak pernah bermakna
sebagai isi**:

* akhir baris ``\\r\\n`` yang tidak terlihat tetapi ikut terkirim ke model,
* spasi di ujung baris,
* deretan baris kosong — tiga baris kosong adalah jarak yang lebih lebar, bukan
  makna yang lebih banyak, dan tiap baris kosong berlebih ikut membayar token,
* spasi berlebih **di dalam baris** pada PDF, yang lahir dari tata letak dua kolom
  dan bukan dari kalimatnya.

Yang **tidak** dilakukan sama sekali: menyambung kembali kata yang terpotong tanda
hubung, menerjemahkan perintah LaTeX, atau menebak judul. Setiap tebakan semacam
itu menghasilkan kalimat yang tidak ditulis siapa pun, dan kalimat itu kelak
dikutip sebagai bahan buku ajar yang bersumber dari halaman tertentu — padahal
isinya berbeda dari halaman itu. Kutipan yang tidak dapat dicocokkan dengan
sumbernya tidak dapat diperiksa manusia, dan itulah satu-satunya yang membuat
seluruh jalur sitasi ini berguna.

Perapian spasi di dalam baris dilakukan **hanya untuk PDF** dan hasil OCR. Di
Markdown dan LaTeX, lekukan adalah isi: ia yang membedakan blok kode dari prosa
dan daftar bersarang dari daftar biasa. Di PDF, lekukan seperti itu tidak ada —
yang ada hanyalah sisa tata letak.
"""

from __future__ import annotations

from typing import Iterable

from domain.document import Document, SourceType

#: Jenis sumber yang spasinya boleh dirapikan di dalam baris.
#:
#: PDF dan hasil OCR tidak punya lekukan yang bermakna; Markdown dan LaTeX punya.
SPACE_COLLAPSING_SOURCES: frozenset[SourceType] = frozenset(
    {SourceType.PDF, SourceType.PDF_OCR}
)

#: Jumlah maksimum baris kosong beruntun yang dipertahankan — satu pemisah paragraf.
MAX_BLANK_RUN = 1


def normalize_text(text: str, *, collapse_spaces: bool = False) -> str:
    """Rapikan ``text`` tanpa mengubah isinya (MURNI).

    :param collapse_spaces: rapikan juga spasi berlebih di dalam baris. Hanya
        untuk sumber yang tidak memakai lekukan sebagai isi.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        if collapse_spaces:
            line = " ".join(line.split())
        else:
            line = line.rstrip()
        if line:
            blank_run = 0
            cleaned.append(line)
            continue
        blank_run += 1
        if blank_run <= MAX_BLANK_RUN:
            cleaned.append("")

    return "\n".join(cleaned).strip()


def normalize_document(document: Document) -> Document:
    """Rapikan teks satu dokumen sesuai jenis sumbernya (MURNI).

    Jenis sumber menentukan apakah lekukan berarti: ia dipakai apa adanya, bukan
    diminta sebagai parameter, supaya pemanggil tidak dapat merusak blok kode
    LaTeX karena lupa mengirim satu argumen.
    """
    return document.model_copy(
        update={
            "text": normalize_text(
                document.text,
                collapse_spaces=document.source_type in SPACE_COLLAPSING_SOURCES,
            )
        }
    )


def normalize_documents(documents: Iterable[Document]) -> tuple[Document, ...]:
    """Rapikan sekumpulan dokumen, urutannya dipertahankan (MURNI)."""
    return tuple(normalize_document(document) for document in documents)


__all__ = [
    "MAX_BLANK_RUN",
    "SPACE_COLLAPSING_SOURCES",
    "normalize_document",
    "normalize_documents",
    "normalize_text",
]
