"""Tipe tingkat bab (§§16–§18, §21, §36) — MURNI."""

from __future__ import annotations

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


class ResearchPackage(FrozenModel):
    """Keluaran Research Agent (§17).

    **Placeholder pada MVP.** ``degraded=True`` berarti paket ini dihasilkan
    tanpa retrieval — yaitu seluruhnya pada iterasi ini, karena RAG belum ada.
    Bendera itu disimpan ke checkpoint, sehingga menjalankan ulang bab yang sama
    setelah RAG tersedia akan memperbaikinya, bukan mengulang dari nol.
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

    title: str = Field(min_length=1)
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
    """

    gate: str
    approved: bool
    score: int = Field(default=0, ge=0, le=10)
    feedback: tuple[str, ...] = ()
    skipped: bool = False


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
