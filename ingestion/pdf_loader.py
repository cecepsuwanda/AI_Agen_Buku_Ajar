"""Jalur pertama PDF: ekstraksi teks per halaman (§10).

**Satu-satunya berkas yang boleh mengimpor ``pypdf``** — ditegakkan
``tests/unit/test_architecture.py``, dan bukan demi kerapian: pustaka yang bocor
ke dalam ``agents/`` akan membuat seluruh pipeline menyeretnya, dan pipeline yang
menyeret pustaka PDF berhenti dapat dijalankan tanpa berkas PDF.

Tiga hal yang dijaga di sini:

**Nomor halaman tidak pernah dibuang.** §10 menutup perdebatan itu dengan tegas —
*"jangan membuang nomor halaman karena citation checker memerlukannya"*. Halaman
disimpan 1-basis, sebagaimana manusia menyebutnya; konversi dari indeks 0-basis
milik ``pypdf`` terjadi **tepat di sini**, sekali, sehingga tidak ada modul lain
yang perlu tahu bahwa ada dua cara menyebut halaman yang sama.

**Halaman kosong tidak menghasilkan dokumen.** PDF hasil scan memulangkan string
kosong untuk setiap halaman; menjadikannya ``Document`` kosong berarti menanam
vektor dari ketiadaan, dan vektor semacam itu cocok dengan setiap pertanyaan.
Yang benar adalah mengenali keadaan itu sebagai "butuh jalur kedua" — dan itulah
:func:`needs_ocr`.

**Keputusan OCR adalah fungsi murni.** Ia tidak membaca berkas, tidak memanggil
model, dan karena itu dapat diuji pada setiap bentuk sebaran teks: PDF teks
biasa, PDF hasil scan, dan PDF campuran.
"""

from __future__ import annotations

from pathlib import Path

from domain.document import Document, SourceType
from domain.errors import InputError

#: Ambang bawaan "halaman ini praktis tidak berisi teks" (karakter per halaman).
#:
#: Angka yang sama ada di ``config.yaml`` sebagai ``rag.ocr_min_chars_per_page``;
#: bawaan di sini hanya dipakai oleh pemanggil yang tidak membawa konfigurasi.
DEFAULT_OCR_MIN_CHARS = 80


def read_page_texts(path: str | Path) -> tuple[str, ...]:
    """Teks setiap halaman PDF, urut halaman, 1-basis dipulihkan pemanggil.

    Pintu ``pypdf``. Setiap galat pustaka — berkas terkunci, PDF rusak, PDF
    terenkripsi — diterjemahkan menjadi :class:`~domain.errors.InputError` yang
    menyebut berkasnya. Membiarkannya sebagai traceback akan memberi tahu
    pengguna bahwa ada yang salah di suatu tempat di dalam ``pypdf``, tanpa
    memberi tahu berkas mana yang harus diperbaiki.

    Halaman yang gagal diekstrak menghasilkan ``""`` dan **tidak** menggagalkan
    seluruh berkas: satu halaman hasil scan di dalam PDF dua ratus halaman adalah
    keadaan yang wajar, dan membuang dua ratus halaman karena satu halaman adalah
    pertukaran yang jelas merugikan.
    """
    # Impor lokal, bukan di kepala modul: fungsi murni di berkas ini
    # (``needs_ocr``, ``documents_from_pages``) dipakai dan diuji tanpa PDF
    # mana pun, dan menyeret pustaka PDF ke setiap impor modul ini berarti
    # membayar biayanya pada tes yang tidak menyentuh berkas sama sekali.
    from pypdf import PdfReader

    target = Path(path)
    if not target.is_file():
        raise InputError("PDF rujukan", target, "berkas tidak ditemukan")

    try:
        reader = PdfReader(str(target))
        return tuple(_page_text(page) for page in reader.pages)
    except InputError:
        raise
    except Exception as exc:  # noqa: BLE001 - pustaka pihak ketiga punya taksonomi sendiri
        raise InputError("PDF rujukan", target, f"gagal dibaca ({exc})") from exc


def _page_text(page: object) -> str:
    """Teks satu halaman, atau ``""`` bila pustakanya menolak mengurainya."""
    extract = getattr(page, "extract_text", None)
    if extract is None:
        return ""
    try:
        return str(extract() or "")
    except Exception:  # noqa: BLE001 - satu halaman rusak bukan alasan menggagalkan berkas
        return ""


def needs_ocr(page_texts: tuple[str, ...], *, min_chars: int = DEFAULT_OCR_MIN_CHARS) -> bool:
    """True bila PDF ini tampaknya hasil scan dan harus dibaca lewat jalur vision (MURNI).

    Yang diperiksa adalah **rata-rata** karakter per halaman, bukan halaman mana
    pun secara tersendiri. Rata-rata itulah yang membedakan dua keadaan yang
    dimaksud §10: PDF berlapis teks (ratusan karakter per halaman) dan PDF hasil
    scan (mendekati nol). Memeriksa "ada satu halaman yang kosong" akan mengalihkan
    hampir setiap PDF nyata ke jalur vision — dan jalur itu memanggil model
    berkali-kali per halaman.

    Konsekuensinya harus diakui: PDF **campuran** (sebagian halaman hasil scan)
    tidak terdeteksi di sini, karena rata-ratanya tetap tinggi. Mengatasinya
    berarti keputusan per halaman, dan itu pekerjaan yang berbeda — bukan yang
    diminta ambang di ``config.yaml``.

    PDF tanpa halaman mengembalikan ``False``: tidak ada yang dapat dibaca ulang
    oleh model vision, jadi memanggilnya hanya akan membuang token.
    """
    if not page_texts:
        return False
    if min_chars <= 0:
        return False
    total = sum(len(text.strip()) for text in page_texts)
    return total / len(page_texts) < min_chars


def documents_from_pages(
    page_texts: tuple[str, ...],
    *,
    filename: str,
    source_type: SourceType = SourceType.PDF,
) -> tuple[Document, ...]:
    """Ubah teks per halaman menjadi dokumen, halaman kosong dibuang (MURNI).

    Halaman yang hanya berisi spasi dianggap kosong: ia sama tidak dapat
    dikutipnya dengan halaman yang benar-benar tidak menghasilkan teks, dan
    membedakan keduanya di sini hanya akan membuat dua jalur yang harus dijaga
    sinkron.
    """
    return tuple(
        Document(
            document_id=filename,
            filename=filename,
            source_type=source_type,
            text=text.strip(),
            page=number,
        )
        for number, text in enumerate(page_texts, start=1)
        if text.strip()
    )


def load_pdf_documents(path: str | Path) -> tuple[Document, ...]:
    """Baca PDF dan kembalikan dokumen per halaman — jalur teks saja.

    Tidak memutuskan apa pun soal OCR: keputusan itu milik pemanggil, karena
    jalur kedua membutuhkan model ``vision`` yang tidak dimiliki modul ini.
    Pemisahan itu disengaja — modul yang dapat memanggil model dari dalam
    pembacaan berkas akan membuat ``ingest`` tanpa jaringan menjadi mustahil.
    """
    target = Path(path)
    return documents_from_pages(read_page_texts(target), filename=target.name)


__all__ = [
    "DEFAULT_OCR_MIN_CHARS",
    "documents_from_pages",
    "load_pdf_documents",
    "needs_ocr",
    "read_page_texts",
]
