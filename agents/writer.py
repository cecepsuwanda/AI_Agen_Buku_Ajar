"""Chapter Writer (§18) — rencana menjadi draf bab yang utuh.

Agent ini adalah satu-satunya yang menghasilkan **prosa**, dan karena itu ia
membawa beban terberat di seluruh pipeline. Dua hal membuatnya tetap dapat
dipercaya:

**Ia tidak tahu model mana yang menjalankannya.** Ia menerima ``ChatModel``,
bukan router. Karena itu mustahil baginya untuk "sekadar mencoba model lain"
saat keluarannya buruk — keputusan itu milik konfigurasi.

**Ia tidak menulis berkas.** Markdown adalah turunan dari ``ChapterDraft``
(§28), dan merender serta menyimpannya adalah pekerjaan lapisan lain. Writer
yang menulis berkas sendiri akan membuat resume mustahil: tidak ada satu pun
tempat yang tahu state bab yang sebenarnya.

Dua jalur di berkas ini — :meth:`write` dan :meth:`revise` — **berbagi satu
tangga perbaikan** dari :class:`~agents.base.StructuredAgent`. Yang berbeda
hanya kontrak prompt dan cara menyusun konteksnya, bukan cara menangani
keluaran yang rusak. Menyalin loop perbaikan ke jalur kedua berarti dua tempat
yang harus dijaga sinkron, dan cepat atau lambat keduanya berbeda.
"""

from __future__ import annotations

from typing import Sequence

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft, ResearchPackage
from domain.rules import citations_allowed_by, enforce_draft_contract
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title, summaries_before, terminology_lines

#: Panjang minimum draf, dalam kata. Dikirim ke prompt sebagai target, bukan
#: ditegakkan sebagai syarat — memotong bab karena kurang 50 kata akan membuang
#: bab yang sebenarnya baik.
DEFAULT_MIN_WORDS = 1200


class ChapterWriter(StructuredAgent[ChapterDraft]):
    """Menulis dan merevisi satu bab (§18)."""

    role = "writer"
    prompt_name = "writer.chapter"

    #: Kontrak prompt untuk jalur revisi. Berkas terpisah, model keluaran sama.
    revise_prompt_name = "writer.revise"

    def write(
        self,
        spec: ChapterSpec,
        *,
        book: BookState,
        research: ResearchPackage,
        style_guide: str = "",
        min_words: int = DEFAULT_MIN_WORDS,
    ) -> tuple[ChapterDraft, tuple[str, ...]]:
        """Tulis draf pertama untuk ``spec`` (§18).

        :param research: paket riset dari :class:`~domain.ports.Researcher`.
            Pada MVP ini selalu terdegradasi, dan prompt penulis memang punya
            cabang untuk kondisi itu: tanpa bahan, ia dilarang mengutip dan
            wajib menandai setiap klaim faktual di ``unresolved_claims``.

        :returns: ``(draf, catatan)``.
        """
        produced = self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "min_words": min_words,
                "objectives": spec.objectives,
                "section_plan": spec.sections,
                "required_examples": spec.required_examples,
                "required_exercises": spec.required_exercises,
                "style_guide": style_guide or "(tidak ada panduan gaya khusus)",
                "previous_summaries": summaries_before(book, spec.number),
                "terminology": terminology_lines(book),
                "research": research,
            }
        )

        return enforce_draft_contract(
            produced,
            spec=spec,
            allowed_citations=citations_allowed_by(research),
        )

    def revise(
        self,
        draft: ChapterDraft,
        *,
        spec: ChapterSpec,
        book: BookState,
        feedback: Sequence[str],
        revision: int,
        research: ResearchPackage,
        style_guide: str = "",
    ) -> tuple[ChapterDraft, tuple[str, ...]]:
        """Revisi draf berdasarkan catatan peninjau (§31).

        :param feedback: catatan gate yang menolak draf. Dikirim **apa adanya**
            dan seluruhnya: catatan yang dipangkas akan menghasilkan revisi yang
            mengabaikan sebagian kekurangan, lalu ditolak lagi dengan catatan
            yang sama.
        :param revision: nomor revisi, dipakai prompt untuk menyebut dirinya.
            Berasal dari record, bukan dari penghitung lokal — supaya resume
            melanjutkan penomoran, bukan mengulanginya.

        ``inherited`` pada :func:`~domain.rules.citations_allowed_by` berisi
        sitasi draf saat ini: penulis tidak boleh **menambah** rujukan saat
        merevisi, tetapi tidak dihukum karena mempertahankan yang sudah ada.
        """
        produced = self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "revision": revision,
                "feedback": tuple(feedback),
                "current_draft_json": draft.model_dump_json(indent=2),
                "objectives": spec.objectives,
                "style_guide": style_guide or "(tidak ada panduan gaya khusus)",
            },
            prompt_name=self.revise_prompt_name,
        )

        return enforce_draft_contract(
            produced,
            spec=spec,
            allowed_citations=citations_allowed_by(research, inherited=draft.citations),
        )


__all__ = ["DEFAULT_MIN_WORDS", "ChapterWriter"]
