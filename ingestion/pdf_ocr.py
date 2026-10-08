"""Jalur kedua PDF: halaman → gambar → model ``vision`` → teks (§10).

**Satu-satunya berkas yang boleh mengimpor ``pymupdf``.** Jalur ini dipakai hanya
ketika jalur pertama hampir tidak menghasilkan teks — PDF hasil scan, yang
halamannya adalah gambar dan bukan huruf. Memanggilnya untuk setiap PDF akan
membayar satu panggilan model per halaman untuk teks yang sudah bisa dibaca
tanpa model sama sekali.

Dua hal yang menjaga jalur ini tetap jujur:

**Jenis sumbernya ``PDF_OCR``, bukan ``PDF``.** Teks yang lahir dari pembacaan
gambar mengandung salah baca huruf dan angka — itu sifat jalurnya, bukan cacat
yang dapat dihilangkan. Menyamakannya dengan teks yang diekstrak langsung akan
menghilangkan satu-satunya petunjuk bahwa kutipan ini perlu diperiksa lebih
teliti, dan petunjuk itu dipakai sampai ke pemeriksaan fakta (§21).

**Balasan model yang tidak ter-parse tidak dibuang.** Bila model menjawab dengan
teks biasa alih-alih JSON, teks itu **tetap dipakai** sebagai isi halaman: ia
adalah pembacaan gambar yang kita punya, dan membuangnya berarti kehilangan
halaman itu sepenuhnya. Ini satu-satunya tempat di seluruh repositori di mana
keluaran yang melanggar kontrak diterima — dan alasannya karena keluaran ini
adalah **bahan mentah**, bukan keputusan: ia tidak pernah menjadi vonis, skor,
atau status, hanya teks yang kelak diperiksa manusia dan pemeriksa fakta.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from domain.document import Document, OcrPage, SourceType
from domain.errors import InputError
from domain.ports import ChatModel, ChatRequest, PromptLibrary
from domain.structured import parse_output, strict_schema

#: Resolusi render halaman. 150 dpi adalah titik di mana huruf 10 pt masih
#: terbaca jelas oleh model vision tanpa membuat berkas gambar membengkak —
#: pada 300 dpi, satu halaman kuarto menghasilkan gambar beberapa megabita, dan
#: itu dikirim lewat HTTP untuk setiap halaman.
DEFAULT_OCR_DPI = 150

#: Nama prompt vision (§10).
OCR_PROMPT_NAME = "ocr.page"


def render_pages(path: str | Path, *, dpi: int = DEFAULT_OCR_DPI) -> tuple[bytes, ...]:
    """Render setiap halaman PDF menjadi gambar PNG (pintu ``pymupdf``).

    :raises InputError: bila berkasnya tidak ada atau pustakanya menolaknya.
    """
    import pymupdf  # impor lokal — lihat catatan di ``pdf_loader``

    target = Path(path)
    if not target.is_file():
        raise InputError("PDF hasil scan", target, "berkas tidak ditemukan")

    try:
        # ``pymupdf`` mengirim anotasi yang tidak lengkap: konstruktor dokumennya
        # tidak bertipe. Ignore-nya diletakkan pada barisnya sendiri, bukan pada
        # seluruh modul, supaya bagian lain tetap diperiksa ketat.
        with pymupdf.open(str(target)) as document:  # type: ignore[no-untyped-call]
            return tuple(
                page.get_pixmap(dpi=dpi).tobytes("png") for page in document
            )
    except InputError:
        raise
    except Exception as exc:  # noqa: BLE001 - pustaka pihak ketiga punya taksonomi sendiri
        raise InputError("PDF hasil scan", target, f"gagal dirender ({exc})") from exc


def read_ocr_text(raw: str) -> str:
    """Teks halaman dari balasan model, dengan JSON diurai lebih dulu (MURNI).

    Diurai lewat :func:`~domain.structured.parse_output` — fungsi yang sama yang
    dipakai seluruh agent — sehingga balasan yang terbungkus pagar Markdown atau
    kalimat pembuka tetap terselamatkan. Bila tetap tidak berbentuk JSON, balasan
    mentahnya dipakai apa adanya; lihat catatan modul soal mengapa itu disengaja.
    """
    try:
        return parse_output(raw, OcrPage).text.strip()
    except (ValidationError, ValueError):
        return raw.strip()


def ocr_page_documents(
    images: tuple[bytes, ...],
    *,
    model: ChatModel,
    prompts: PromptLibrary,
    filename: str,
    first_page: int = 1,
) -> tuple[Document, ...]:
    """Baca gambar-gambar halaman dan kembalikan dokumennya, urut halaman.

    ``first_page`` ada karena jalur ini juga dipakai untuk membaca **halaman
    tertentu** dari sebuah PDF — dan nomor halaman yang salah akan menghasilkan
    kutipan yang menunjuk ke halaman yang tidak memuatnya, yaitu kesalahan yang
    paling sulit ditemukan manusia.
    """
    schema = strict_schema(OcrPage)
    documents: list[Document] = []

    for offset, image in enumerate(images):
        number = first_page + offset
        rendered = prompts.render(OCR_PROMPT_NAME, {"page": number})
        result = model.complete(
            ChatRequest(
                model="",  # dibiarkan kosong: adapter memakai model peran ``vision``
                system=rendered.system,
                user=rendered.user,
                images=(image,),
                format_schema=schema,
                role="vision",
                prompt_version=f"{rendered.version}+{rendered.hash}",
            )
        )
        text = read_ocr_text(result.text)
        if not text:
            continue
        documents.append(
            Document(
                document_id=filename,
                filename=filename,
                source_type=SourceType.PDF_OCR,
                text=text,
                page=number,
            )
        )

    return tuple(documents)


def ocr_documents(
    path: str | Path,
    *,
    model: ChatModel,
    prompts: PromptLibrary,
    dpi: int = DEFAULT_OCR_DPI,
) -> tuple[Document, ...]:
    """Render seluruh halaman PDF lalu bacanya dengan model ``vision``.

    Nama berkas diambil dari jalurnya, seperti pada jalur pertama: yang dikutip
    kelak adalah nama berkas, bukan jalur lengkapnya, dan jalur absolut di dalam
    kutipan akan membuat buku menyebut direktori mesin penulisnya.
    """
    target = Path(path)
    return ocr_page_documents(
        render_pages(target, dpi=dpi),
        model=model,
        prompts=prompts,
        filename=target.name,
    )


__all__ = [
    "DEFAULT_OCR_DPI",
    "OCR_PROMPT_NAME",
    "ocr_documents",
    "ocr_page_documents",
    "read_ocr_text",
    "render_pages",
]
