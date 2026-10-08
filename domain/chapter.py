"""Tipe tingkat bab (§§16–§21, §36) — MURNI."""

from __future__ import annotations

from typing import Mapping

from pydantic import Field

from domain.base import FrozenModel
from domain.book import ChapterSpec
from domain.enums import ChapterStatus


class Evidence(FrozenModel):
    """Satu potongan bukti dari knowledge base (§13).

    Bentuknya sengaja sudah memuat ``source``/``page``/``section``/``score``
    sejak sekarang, meskipun RAG baru ada di Tahap 3. Blueprint §13 tegas:
    memberikan hanya ``text`` kepada writer akan menghilangkan informasi sumber,
    dan citation checker tidak akan bisa bekerja.
    """

    source: str
    page: int | None = None
    section: str = ""
    text: str = ""
    score: float | None = None


class ResearchFindings(FrozenModel):
    """Penyaringan bukti menjadi bahan yang siap dipakai penulis (§17, §36).

    Ini **keluaran model** riset, dan ia sengaja tidak memuat ``evidence`` maupun
    ``sources``. Keduanya sudah dimiliki program sebelum model dipanggil — model
    hanya dipanggil untuk hal yang memang butuh penilaian: dari potongan bukti
    yang ditemukan retriever, mana yang merupakan konsep, mana yang definisi, dan
    mana yang contoh. Meminta model mengembalikan ulang ``source``/``page`` akan
    membuatnya dapat **mengarang** halaman, dan kutipan yang menunjuk halaman
    yang salah persis jenis kesalahan yang tidak dapat ditemukan manusia.

    Bentuknya sengaja tiga daftar pendek, bukan prosa: penulis memakai bahan ini
    sebagai rujukan, dan rujukan berbentuk prosa akan disalin apa adanya ke dalam
    bab.
    """

    concepts: tuple[str, ...] = ()
    definitions: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()


class ResearchPackage(FrozenModel):
    """Keluaran Research Agent (§17).

    ``degraded=True`` berarti paket ini dihasilkan tanpa retrieval — keadaan
    jujur ketika RAG dimatikan atau indeksnya masih kosong. Bendera itu disimpan
    ke checkpoint, sehingga menjalankan ulang bab yang sama setelah indeks
    terbangun akan memperbaikinya, bukan mengulang dari nol.
    """

    concepts: tuple[str, ...] = ()
    definitions: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    sources: tuple[str, ...] = ()
    degraded: bool = True

    @classmethod
    def empty(cls) -> "ResearchPackage":
        """Paket riset kosong — dipakai :class:`agents.researcher.NullResearcher`."""
        return cls()


class Section(FrozenModel):
    """Satu sub-bab di dalam draf."""

    heading: str = Field(min_length=1)
    body: str = ""
    level: int = Field(default=2, ge=1, le=6)


class ChapterDraft(FrozenModel):
    """Draf bab — keluaran Chapter Writer (§18), dan kontrak OUTPUT §36.

    ``unresolved_claims`` adalah permukaan jujur untuk §34: klaim yang ditulis
    tanpa bukti pendukung **ditandai**, bukan disembunyikan. Pada MVP (tanpa RAG)
    daftar ini akan terisi, dan itu memang benar — lebih baik terlihat daripada
    menyamar sebagai fakta yang terdokumentasi.
    """

    title: str = Field(
        min_length=1,
        description=(
            "Judul bab saja, TANPA awalan 'Bab N:' — penomoran ditambahkan "
            "perender, sehingga awalan di sini akan tercetak dua kali."
        ),
    )
    learning_objectives: tuple[str, ...] = ()
    sections: tuple[Section, ...] = ()
    examples: tuple[str, ...] = ()
    exercises: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    unresolved_claims: tuple[str, ...] = ()
    summary: str = ""


class ReviewResult(FrozenModel):
    """Vonis sebuah gate.

    Ini **nilai balik biasa**, bukan exception: review yang tidak lolos adalah
    kondisi bisnis yang diharapkan (§35), bukan kesalahan. Gate yang dilewati
    (belum diimplementasikan) ditandai ``skipped=True`` — sehingga rantai §27
    tetap terekam utuh meski gate-nya belum ada.

    ``enriched_draft`` membuat gate dapat **memperkaya** draf, bukan sekadar
    menilainya. Blueprint menaruh Example Agent dan Exercise Agent *sesudah*
    Chapter Writer (§19, §20), dan §31 bahkan menulis
    ``draft = writer.revise(draft, fact_result.feedback)`` — jadi ini memang
    bentuk yang dimaksud blueprint, bukan kelonggaran demi kenyamanan.

    Field ini punya bawaan ``None`` dengan sengaja: gate yang hanya menilai
    tidak perlu menyentuhnya, dan **skema state tetap versi 1** — berkas
    ``state/chapterNN.json`` yang sudah ada tetap sah tanpa migrasi. Saat
    checkpoint ditulis, field ini dibuang (lihat ``memory.chapter_state``):
    ``record.draft`` sudah menyimpan draf itu, dan menyimpannya lagi di dalam
    setiap review akan menggandakan ukuran berkas state hanya untuk
    menduplikasi isi yang sama.

    ``terminology`` adalah jalur kedua yang bentuknya sama, dan ia ada karena
    alasan yang sama pula: gate §24 menilai bab **terhadap seluruh buku**, jadi
    ia satu-satunya yang tahu istilah apa saja yang bab ini pakai. Tanpa jalur
    ini, ``BookState.terminology`` tidak akan pernah terisi, dan bab berikutnya
    tidak punya pembanding apa pun. Yang memindahkannya ke state buku adalah
    :func:`~domain.rules.approved_terminology`, dan hanya dari review yang
    **lulus** — glosarium bab yang ditolak belum tentu mewakili babnya yang
    sesungguhnya.
    """

    gate: str
    approved: bool
    score: int = Field(default=0, ge=0, le=10)
    feedback: tuple[str, ...] = ()
    skipped: bool = False
    enriched_draft: "ChapterDraft | None" = None
    terminology: Mapping[str, str] = Field(default_factory=dict)


class ReviewVerdict(FrozenModel):
    """Vonis **sebagaimana dikembalikan LLM** — tanpa field milik kita.

    Dipisah dari :class:`ReviewResult` karena satu alasan teknis yang tegas:
    ``strict_schema`` menandai **semua** properti sebagai wajib, jadi apa pun
    yang ada di sini akan diminta dari model. Bila ``ReviewResult`` dipakai
    langsung sebagai skema keluaran, reviewer akan dipaksa **mengarang**
    ``gate`` (nama gate-nya sendiri, yang ia tidak tahu) dan ``skipped``
    (yang justru kita yang menentukan). Gate kemudian menggabungkan vonis ini
    dengan identitasnya untuk membentuk :class:`ReviewResult`.
    """

    approved: bool
    score: int = Field(default=0, ge=0, le=10)
    feedback: tuple[str, ...] = ()


class ChapterRecord(FrozenModel):
    """State satu bab di checkpoint (§28).

    ``state/chapterNN.json`` adalah **otoritatif**; Markdown di ``output/``
    hanyalah turunannya dan dapat dirender ulang kapan saja.
    """

    schema_version: int = 1
    number: int = Field(ge=1)
    status: ChapterStatus = ChapterStatus.PLANNED
    revision: int = Field(default=0, ge=0)
    spec: ChapterSpec | None = None
    research: ResearchPackage | None = None
    draft: ChapterDraft | None = None
    reviews: tuple[ReviewResult, ...] = ()
    markdown_path: str | None = None
    updated_at: str = ""
    error: str | None = None

    def last_review(self) -> ReviewResult | None:
        """Vonis gate terakhir, bila ada."""
        return self.reviews[-1] if self.reviews else None
