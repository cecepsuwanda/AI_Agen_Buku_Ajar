"""Chapter Planner (§16) — satu bab dari BookSpec menjadi rencana penulisan.

Agent ini menghasilkan ``ChapterSpec`` yang **lebih rinci**, bukan ``ChapterDraft``.
Ia tidak menulis satu paragraf materi pun; batas itu ditegakkan oleh tipe keluaran,
bukan oleh disiplin penulis prompt. Rencana yang menumpahkan isi bab akan ditulis
ulang seluruhnya oleh penulis, dan usaha itu terbuang.

Peran modelnya juga berbeda dari Book Planner: ini panggilan **per bab** dengan
konteks terbatas (satu bab + ringkasan bab sebelumnya), sehingga ia dipetakan ke
model yang lebih kecil. Model berkonteks 1M disimpan untuk panggilan tingkat buku
yang benar-benar membutuhkannya.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.rules import reconcile_chapter_spec
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title, summaries_before, terminology_lines


class ChapterPlannerAgent(StructuredAgent[ChapterSpec]):
    """Merinci satu :class:`~domain.book.ChapterSpec` menjadi rencana penulisan."""

    role = "chapter_planner"
    prompt_name = "planner.chapter"

    def detail(
        self,
        base: ChapterSpec,
        *,
        book: BookState,
        style_guide: str = "",
    ) -> tuple[ChapterSpec, tuple[str, ...]]:
        """Rincikan ``base`` memakai konteks buku (§16).

        :param base: spesifikasi bab dari Book Planner. Ia adalah **batas**
            pekerjaan agent ini: nomor, tujuan, dan rujukannya dikunci.
        :param book: state buku, sumber ringkasan dan istilah sebelumnya.
        :param style_guide: panduan gaya dari ``config.yaml``.

        :returns: ``(rencana, catatan)``. Catatan memuat setiap hal yang terpaksa
            dikembalikan ke versi Book Planner — sehingga penyimpangan model
            terlihat, bukan tertelan diam-diam.
        """
        produced = self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": base.number,
                "chapter_title": base.title,
                "objectives": base.objectives,
                "planned_sections": base.sections,
                "style_guide": style_guide or "(tidak ada panduan gaya khusus)",
                "previous_summaries": summaries_before(book, base.number),
                "terminology": terminology_lines(book),
            }
        )

        return reconcile_chapter_spec(produced, base=base)


__all__ = ["ChapterPlannerAgent"]
