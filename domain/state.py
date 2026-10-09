"""State buku & laporan proses (§29, §35) — MURNI."""

from __future__ import annotations

from typing import Mapping

from pydantic import Field

from domain.base import FrozenModel
from domain.book import BookRequest, BookSpec
from domain.chapter import ChapterRecord


class BookState(FrozenModel):
    """Shared memory antar-agent (§29).

    Ini **model Pydantic, bukan kelas biasa seperti sketsa §29**. Alasannya
    praktis: state harus round-trip ke ``state/book.json`` dan memeriksa
    ``schema_version`` saat dibaca. (Deviasi sadar dari §29.)

    Blueprint §29 menegaskan jangan memakai satu string konteks raksasa —
    struktur inilah penggantinya, dan tiap agent hanya menerima irisan yang
    relevan untuknya.
    """

    schema_version: int = 1
    request: BookRequest
    spec: BookSpec | None = None
    #: Disediakan untuk §29, **belum diisi siapa pun**. Record bab tinggal di
    #: ``state/chapterNN.json`` (satu berkas per bab, §28), sehingga state yang
    #: dibaca ``run`` berasal dari sana — bukan dari sini. Field ini ikut
    #: diserialisasi dan selalu ``()``.
    #:
    #: Karena itu **tidak ada** accessor di kelas ini: ``record_for``/``with_record``
    #: pernah ada di sini dan dihapus, sebab keduanya menjawab atas field yang tidak
    #: pernah terisi — ``record_for(3)`` akan mengembalikan ``None`` untuk bab yang
    #: nyatanya ada di disk. Pertanyaan "di mana record bab N" dijawab
    #: :func:`~domain.rules.find_chapter` (spesifikasi) dan ``StateStore`` (record).
    chapters: tuple[ChapterRecord, ...] = ()
    terminology: Mapping[str, str] = Field(default_factory=dict)
    summaries: Mapping[int, str] = Field(default_factory=dict)
    citations: Mapping[str, str] = Field(default_factory=dict)
    updated_at: str = ""


class RunReport(FrozenModel):
    """Ringkasan satu kali eksekusi (§35).

    Dipakai CLI untuk mencetak "6 dari 8 bab disetujui; 2 gagal" beserta
    alasannya, dan untuk menentukan exit code.

    ``pending`` adalah bab yang berhenti karena **menunggu keputusan manusia**
    (§44), dan ia berdiri sendiri alih-alih dilebur ke ``failed`` karena dua hal
    yang menuntut tindakan berbeda. Bab yang gagal meminta penyebabnya diperbaiki
    dan ``run`` dijalankan lagi; bab yang menunggu meminta seseorang membaca
    hasilnya dan menjalankan ``approve``. Menggabungkannya juga akan membuat
    ``exit_code`` bernilai 1 untuk buku yang sesungguhnya sudah selesai
    dikerjakan — dan itu membuat skrip apa pun yang memeriksa kode keluar
    melaporkan kegagalan yang tidak ada.

    Keempat angka itu tetap menjumlah tepat ke ``total``:

    * ``approved`` — record ada dan disetujui.
    * ``pending`` — record ada dan sedang menunggu keputusan manusia.
    * ``failed`` — record ada, tidak disetujui, dan tidak menunggu apa pun.
    * ``skipped`` — belum pernah disentuh.
    """

    total: int = 0
    approved: int = 0
    failed: int = 0
    skipped: int = 0
    pending: int = 0
    records: tuple[ChapterRecord, ...] = ()
    aborted: bool = False
    abort_reason: str | None = None

    def exit_code(self) -> int:
        """0 bila bersih, 1 bila ada bab gagal atau proses dibatalkan.

        Bab yang menunggu persetujuan **bukan** kegagalan: ia adalah pekerjaan
        yang berhenti tepat di tempat yang seharusnya, menunggu orang yang
        memang harus memutuskan.
        """
        return 1 if (self.failed or self.aborted) else 0
