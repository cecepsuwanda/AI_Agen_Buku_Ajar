"""Pintu masuk ingestion: berkas bahan menjadi dokumen bermetadata (§§9–§11).

Berkas ini adalah **satu-satunya tempat yang tahu akhiran berkas berarti apa**.
Pengetahuan itu sengaja tidak disebar ke ``app/commands.py``: perintah ``ingest``
mengurus laporan, kode keluar, dan batas kesalahan — ia tidak perlu tahu bahwa
``.tex`` punya parser sendiri, dan menaruh peta akhiran di sana akan membuat
setiap format baru menyentuh lapisan CLI.

Jalur PDF adalah satu-satunya yang bercabang, dan percabangannya persis yang
diminta §10: coba ekstraksi teks lebih dulu, dan **hanya** bila hasilnya nyaris
kosong barulah halaman dirender dan dibaca model ``vision``. Memanggil jalur OCR
untuk setiap PDF akan membayar satu panggilan model per halaman untuk teks yang
sudah dapat dibaca tanpa model sama sekali.

:class:`OcrRoute` membawa model dan pustaka prompt sekaligus, bukan dua parameter
terpisah. Keduanya selalu dipakai bersama dan keduanya tidak bermakna sendiri —
dan ``None`` di sini punya arti yang jelas dan penting: **jalur vision tidak
tersedia**. Dalam keadaan itu, PDF hasil scan menggagalkan ingest dengan pesan
yang menyebut berkasnya, bukan menghasilkan nol dokumen tanpa penjelasan. PDF
hasil scan yang diam-diam tidak menghasilkan apa pun adalah kegagalan yang paling
mahal di sini: seluruh bab sesudahnya akan menulis dari korpus yang tampak
lengkap padahal halaman-halamannya hilang.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from domain.document import Document
from domain.errors import InputError
from domain.ports import ChatModel, PromptLibrary

from ingestion.latex_loader import parse_latex
from ingestion.markdown_loader import parse_markdown, parse_plain_text
from ingestion.pdf_loader import (
    DEFAULT_OCR_MIN_CHARS,
    documents_from_pages,
    needs_ocr,
    read_page_texts,
)
from ingestion.pdf_ocr import DEFAULT_OCR_DPI, ocr_documents
from ingestion.sources import iter_reference_files, read_text


@dataclass(frozen=True, slots=True)
class OcrRoute:
    """Model ``vision`` dan prompt yang dipakainya (§10).

    ``dpi`` ikut di sini karena ia bukan pilihan per berkas: seluruh korpus
    dibaca pada resolusi yang sama, dan resolusi yang berbeda menghasilkan mutu
    OCR yang berbeda untuk bahan yang seharusnya setara.
    """

    model: ChatModel
    prompts: PromptLibrary
    dpi: int = DEFAULT_OCR_DPI


@dataclass(frozen=True, slots=True)
class IngestedFile:
    """Hasil membaca satu berkas bahan.

    ``used_ocr`` ada supaya laporan dapat menyebut berkas mana yang dibaca model
    ``vision``. Bukan hiasan: teks hasil OCR lebih lemah daripada teks yang
    diekstrak langsung, dan orang yang menelusuri kutipan aneh perlu dapat
    menemukan berkas mana yang perlu dibaca ulang dengan mata.
    """

    path: Path
    documents: tuple[Document, ...]
    used_ocr: bool = False

    @property
    def filename(self) -> str:
        """Nama berkas seperti yang akan muncul di kutipan."""
        return self.path.name


def load_documents(
    path: str | Path,
    *,
    ocr: OcrRoute | None = None,
    ocr_min_chars: int = DEFAULT_OCR_MIN_CHARS,
) -> IngestedFile:
    """Baca satu berkas bahan menjadi dokumen, apa pun formatnya.

    :raises InputError: bila berkasnya tidak terbaca, atau bila PDF hasil scan
        ditemukan sementara jalur ``vision`` tidak tersedia.
    """
    target = Path(path)
    suffix = target.suffix.lower()

    if suffix == ".pdf":
        return _pdf_documents(target, ocr=ocr, ocr_min_chars=ocr_min_chars)

    text = read_text(target)
    if suffix == ".tex":
        return IngestedFile(path=target, documents=parse_latex(text, filename=target.name))
    if suffix == ".md":
        return IngestedFile(path=target, documents=parse_markdown(text, filename=target.name))
    return IngestedFile(path=target, documents=parse_plain_text(text, filename=target.name))


def load_reference_files(
    directory: str | Path,
    *,
    ocr: OcrRoute | None = None,
    ocr_min_chars: int = DEFAULT_OCR_MIN_CHARS,
) -> tuple[IngestedFile, ...]:
    """Baca seluruh bahan rujukan di ``directory``, terurut menurut jalurnya.

    Direktori yang tidak ada mengembalikan himpunan kosong, bukan galat — sama
    seperti :func:`~ingestion.sources.iter_reference_files`. Yang memutuskan
    apakah "tidak ada bahan" itu wajar atau kegagalan adalah pemanggilnya, dan
    ``ingest`` memutuskan bahwa itu kegagalan yang harus dilaporkan.
    """
    return tuple(
        load_documents(path, ocr=ocr, ocr_min_chars=ocr_min_chars)
        for path in iter_reference_files(directory)
    )


def documents_of(files: Iterable[IngestedFile]) -> tuple[Document, ...]:
    """Gabungkan dokumen seluruh berkas, urutannya dipertahankan (MURNI)."""
    return tuple(document for item in files for document in item.documents)


def _pdf_documents(
    target: Path,
    *,
    ocr: OcrRoute | None,
    ocr_min_chars: int,
) -> IngestedFile:
    """Jalur PDF: ekstraksi teks, atau vision bila halamannya hampir kosong (§10)."""
    page_texts = read_page_texts(target)

    if not needs_ocr(page_texts, min_chars=ocr_min_chars):
        return IngestedFile(
            path=target,
            documents=documents_from_pages(page_texts, filename=target.name),
        )

    if ocr is None:
        raise InputError(
            "PDF hasil scan",
            target,
            "teksnya nyaris kosong dan jalur OCR tidak tersedia — "
            "periksa peran 'vision' di profil model yang dipakai",
        )

    return IngestedFile(
        path=target,
        documents=ocr_documents(target, model=ocr.model, prompts=ocr.prompts, dpi=ocr.dpi),
        used_ocr=True,
    )


__all__ = [
    "IngestedFile",
    "OcrRoute",
    "documents_of",
    "load_documents",
    "load_reference_files",
]
