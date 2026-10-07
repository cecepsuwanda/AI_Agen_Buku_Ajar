"""Permintaan & spesifikasi buku (§15) — MURNI.

Perhatikan: path di sini bertipe ``str``, **bukan** ``pathlib.Path``. Aturan
kemurnian ``domain/`` melarang import ``pathlib``, dan konversi ke ``Path``
adalah pekerjaan adapter. Ini koreksi kecil atas rencana awal, yang sempat
memakai ``Path`` di tipe domain dan akan langsung melanggar gerbang AST.
"""

from __future__ import annotations

from pydantic import Field

from domain.base import FrozenModel


class StyleGuide(FrozenModel):
    """Panduan gaya penulisan buku (§18 — dikirim ke Writer)."""

    language: str = "id"
    tone: str = "akademik formal"
    notes: str = ""


class BookRequest(FrozenModel):
    """Masukan tingkat buku (§15).

    ``rps_text`` sudah berisi **isi** berkas RPS yang dibaca adapter. Pada MVP,
    RPS diperlakukan sebagai teks biasa — parser terstruktur CPMK/Sub-CPMK
    menyusul di Tahap 4 (§12) tanpa mengubah tipe ini.
    """

    title: str = Field(min_length=1)
    audience: str = "mahasiswa S1"
    language: str = "id"
    target_chapters: int = Field(default=8, ge=1, le=60)
    rps_path: str | None = None
    references_dir: str | None = None
    latex_template_dir: str | None = None
    rps_text: str = ""
    topics: tuple[str, ...] = ()
    style: StyleGuide = Field(default_factory=StyleGuide)


class ChapterSpec(FrozenModel):
    """Spesifikasi satu bab (§16) — keluaran Chapter Planner."""

    number: int = Field(ge=1)
    title: str = Field(min_length=1)
    objectives: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    required_examples: int = Field(default=3, ge=0, le=20)
    required_exercises: int = Field(default=5, ge=0, le=50)
    references: tuple[str, ...] = ()
    source_weeks: tuple[str, ...] = ()


class BookSpec(FrozenModel):
    """Spesifikasi buku (§15) — keluaran Book Planner.

    Planner **hanya** menghasilkan spesifikasi; ia tidak menulis isi buku.
    """

    title: str = Field(min_length=1)
    language: str = "id"
    style_guide: str = ""
    chapters: tuple[ChapterSpec, ...] = ()

    def chapter_numbers(self) -> tuple[int, ...]:
        """Nomor bab yang tersedia, terurut."""
        return tuple(sorted(c.number for c in self.chapters))
