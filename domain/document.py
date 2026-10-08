"""Unit bahan mentah hasil ingest (§9, §10, §11) — MURNI.

Berkas ini tidak menyentuh PDF mana pun. Ia hanya menetapkan **bentuk seragam**
yang harus dimiliki setiap bahan, apa pun asalnya — PDF, LaTeX, Markdown — persis
seperti yang diminta §9 ("semua file harus diproses menjadi struktur internal
yang seragam").

Yang menjadikan tipe ini penting bukan field-nya, melainkan apa yang **tidak
boleh hilang** saat bahan dipotong menjadi chunk: nomor halaman, nama berkas,
dan bagian. §10 menutup kemungkinannya untuk dibuang dengan alasan yang tegas —
"jangan membuang nomor halaman karena citation checker memerlukannya" — dan §13
melarang menyerahkan hanya ``text`` kepada penulis. Karena itu metadata §9
**melekat pada setiap potongan**, bukan pada dokumennya saja, dan
:meth:`Document.location` ada supaya potongan itu dapat menyebut dirinya sendiri
di dalam prompt.

``source_type`` membedakan jalur ekstraksi, bukan jenis berkas: PDF hasil scan
yang dibaca model ``vision`` (``PDF_OCR``) menghasilkan teks yang jauh lebih
lemah daripada PDF berlapis teks (``PDF``), dan perbedaan itu harus dapat
dilihat oleh siapa pun yang menilai keandalan kutipan kelak.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from domain.base import FrozenModel


class SourceType(StrEnum):
    """Jalur yang menghasilkan teks sebuah :class:`Document` (§10).

    ``PDF_OCR`` sengaja bukan sekadar varian dari ``PDF``: teksnya lahir dari
    pembacaan gambar, sehingga salah baca huruf dan angka adalah hal yang wajar
    terjadi. Menyamakannya dengan teks yang diekstrak langsung akan
    menyembunyikan perbedaan keandalan yang justru perlu terlihat.
    """

    PDF = "pdf"
    PDF_OCR = "pdf_ocr"
    LATEX = "latex"
    MARKDOWN = "markdown"
    TEXT = "text"


class Document(FrozenModel):
    """Satu unit bahan beserta seluruh metadata §9-nya.

    Sebuah ``Document`` adalah **potongan**, bukan berkas: satu halaman PDF, satu
    bagian LaTeX, atau satu chunk hasil pemotongan. Itulah sebabnya ``text``
    berada di sini dan bukan di tipe berkas terpisah — yang dibaca retriever,
    ditanam sebagai vektor, dan dikutip adalah potongan ini.

    ``paragraph`` adalah indeks potongan di dalam sumbernya, dan ia yang membuat
    setiap potongan punya identitas sendiri. Tanpa itu, satu halaman PDF yang
    melahirkan lima chunk akan menghasilkan lima entri dengan metadata identik —
    dan citation checker tidak akan pernah dapat menyebut **yang mana**.
    """

    document_id: str = Field(min_length=1, description="Identitas sumber, mis. 'compiler.pdf'.")
    filename: str = Field(
        min_length=1,
        description="Nama berkas seperti ditulis di daftar pustaka.",
    )
    source_type: SourceType
    text: str = ""
    page: int | None = Field(
        default=None,
        ge=1,
        description="Nomor halaman 1-basis; None bila tak ada.",
    )
    section: str = ""
    paragraph: int | None = Field(
        default=None,
        ge=0,
        description="Indeks potongan di dalam sumbernya.",
    )

    def location(self) -> str:
        """Keterangan tempat potongan ini, untuk dicetak di prompt dan temuan.

        Bentuknya sengaja stabil dan hanya memuat yang **benar-benar diketahui**:
        halaman yang tidak ada tidak ditulis sebagai "hlm. 0", dan bagian yang
        kosong tidak meninggalkan pemisah menggantung. Temuan yang berbunyi
        "compiler.pdf — hlm. 42" dapat diperiksa manusia; "compiler.pdf —  — "
        tidak.
        """
        parts = [self.filename]
        if self.section.strip():
            parts.append(self.section.strip())
        if self.page is not None:
            parts.append(f"hlm. {self.page}")
        return " — ".join(parts)


class OcrPage(FrozenModel):
    """Teks satu halaman sebagaimana dibaca model ``vision`` (§10).

    Diberi bentuk terstruktur, bukan teks bebas, karena keluaran model yang tidak
    dibatasi skema akan datang dengan pembungkusnya sendiri — "Berikut teksnya:",
    penanda pagar Markdown, atau ringkasan pendahuluan — dan seluruh pembungkus itu
    akan masuk ke dalam bahan rujukan tanpa ada yang memeriksanya. Skema
    menyingkirkannya pada tingkat *decoding*, bukan dengan pembersihan teks
    sesudahnya yang selalu ketinggalan satu bentuk.
    """

    text: str = Field(
        default="", description="Seluruh teks yang terbaca pada gambar halaman, apa adanya."
    )


def chunk_id(document: Document) -> str:
    """Identitas tunggal satu potongan, stabil lintas jalankan (§10, §13).

    Dibutuhkan karena indeks vektor menyimpan potongan, bukan berkas: satu PDF
    dua ratus halaman menghasilkan ribuan entri, dan tanpa identitas yang
    diturunkan dari **metadata §9** — bukan dari nomor urut kemunculan — menjalankan
    ulang ``ingest`` akan menanam salinan baru di sebelah yang lama. Menurunkan
    identitas dari ``document_id``/``page``/``paragraph`` membuat penanaman ulang
    bersifat idempoten: potongan yang sama menimpa dirinya sendiri.

    Halaman yang tidak ada ditulis ``-``, bukan dibuang: ``pymupdf`` yang tidak
    dapat membaca nomor halaman dan berkas Markdown tanpa halaman harus tetap
    menghasilkan kunci yang berbeda dari potongan bernomor halaman.

    MURNI — tidak menyentuh disk, dan tidak butuh pustaka apa pun.
    """
    page = "-" if document.page is None else str(document.page)
    paragraph = 0 if document.paragraph is None else document.paragraph
    return f"{document.document_id}#p{page}#c{paragraph}"


__all__ = ["Document", "OcrPage", "SourceType", "chunk_id"]
