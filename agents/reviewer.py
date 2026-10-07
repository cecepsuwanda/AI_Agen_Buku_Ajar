"""Reviewer (§21–§24) dan gate-nya (§27).

Dua kelas, dan pembagiannya bukan formalitas:

* :class:`ChapterReviewerAgent` — memanggil LLM dan menghasilkan
  :class:`~domain.chapter.ReviewVerdict`, yaitu **apa yang dikatakan peninjau**.
* :class:`ChapterReviewer` — gate yang memiliki identitasnya (``name``), tahu
  status yang dicapainya (``produces``), dan menegakkan ambang skor. Ia
  menghasilkan :class:`~domain.chapter.ReviewResult`, yaitu **apa yang
  diputuskan sistem**.

Pemisahan ini ada karena satu alasan teknis yang tegas: ``strict_schema``
menandai **semua** properti sebagai wajib, jadi apa pun yang ada di model
keluaran akan diminta dari model. Bila ``ReviewResult`` dipakai langsung
sebagai skema, peninjau akan dipaksa mengarang ``gate`` (nama gate-nya sendiri,
yang tidak ia ketahui) dan ``skipped`` (yang justru kita yang menentukan).
Model yang dipaksa mengisi field yang tidak ia ketahui akan mengisinya dengan
apa saja — dan itu lebih buruk daripada tidak menanyakannya.

Peninjau sengaja dipetakan ke keluarga model yang **berbeda** dari penulis dan
perencana (lihat ``config.yaml``). Meninjau pekerjaan sendiri, atau pekerjaan
model sekeluarga, menghasilkan korelasi: kesalahan yang sama tidak akan
tertangkap, karena keduanya menganggapnya benar.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import (
    ChapterDraft,
    ChapterRecord,
    ResearchPackage,
    ReviewResult,
    ReviewVerdict,
)
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.ports import ChatModel, PromptLibrary
from domain.rules import decide_review
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title
from agents.gates import GateContext, register_gate
from agents.writer import DEFAULT_MIN_WORDS

#: Ambang skor kelulusan. Selaras dengan yang dinyatakan prompt peninjau
#: ("tetapkan ``approved`` benar hanya bila skor >= 7") dan dengan
#: ``book.review_threshold`` di ``config.yaml``.
DEFAULT_REVIEW_THRESHOLD = 7


class ChapterReviewerAgent(StructuredAgent[ReviewVerdict]):
    """Menilai satu bab dan mengembalikan vonis **sebagaimana dikatakan model**."""

    role = "reviewer"
    prompt_name = "reviewer.chapter"

    def judge(
        self,
        draft: ChapterDraft,
        *,
        spec: ChapterSpec,
        book: BookState,
        research: ResearchPackage,
        style_guide: str = "",
        min_words: int = DEFAULT_MIN_WORDS,
    ) -> ReviewVerdict:
        """Nilai ``draft`` terhadap tujuan, gaya, dan bahan yang tersedia (§21–§24).

        Draf dikirim sebagai **JSON**, bukan sebagai Markdown hasil render.
        Alasannya bukan kerapian: ``ChapterDraft`` adalah bentuk yang benar-benar
        dinilai — termasuk ``unresolved_claims``, yang tidak memiliki padanan
        apa pun di dalam Markdown dan justru merupakan permukaan kejujuran §34.
        Peninjau yang hanya melihat Markdown tidak akan pernah dapat menilainya.
        """
        return self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "min_words": min_words,
                "objectives": spec.objectives,
                "style_guide": style_guide or "(tidak ada panduan gaya khusus)",
                "draft_json": draft.model_dump_json(indent=2),
                "research": research,
            }
        )


class ChapterReviewer:
    """Gate review (§27): adaptor dari :class:`ChapterReviewerAgent` ke port gate.

    Gate ini **tidak** memutuskan sendiri kapan sebuah bab layak. Ia memakai
    vonis peninjau, lalu menegakkan ambang skor dan memberi vonis itu identitas
    yang benar. Yang ia tambahkan hanyalah hal-hal yang memang tidak dapat
    diketahui model: nama gate-nya, dan apakah tahap ini benar-benar dijalankan.
    """

    #: Nama gate di ``config.yaml`` — ``pipeline.gates: [reviewer]``.
    name = "reviewer"

    #: Status yang dicapai bila gate ini lulus. ``REVIEWED``, bukan ``APPROVED``:
    #: persetujuan akhir adalah peristiwa tersendiri di rantai §27, dan
    #: menyamakannya dengan "lulus review" akan menghapus perbedaan antara
    #: "babnya baik" dan "babnya selesai".
    produces = ChapterStatus.REVIEWED

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        threshold: int = DEFAULT_REVIEW_THRESHOLD,
        min_words: int = DEFAULT_MIN_WORDS,
        style_guide: str = "",
        repair_attempts: int = 2,
    ) -> None:
        self._agent = ChapterReviewerAgent(
            model=model,
            prompts=prompts,
            max_repair_attempts=repair_attempts,
        )
        self._threshold = threshold
        self._min_words = min_words
        self._style_guide = style_guide

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Periksa bab pada ``record`` dan kembalikan vonis sistem (§27).

        :raises GatePreconditionError: bila record belum memuat spesifikasi atau
            draf. Gate tidak dapat menilai tanpa acuan — menilai bab kosong akan
            menghasilkan skor yang tidak bermakna, dan meloloskannya lebih buruk
            lagi.
        :raises AgentOutputError: bila peninjau gagal menghasilkan vonis yang
            dapat di-parse. ``BookDirector`` yang memutuskan apa artinya itu
            bagi bab ini; gate tidak menelannya.
        """
        spec = record.spec
        if spec is None:
            raise GatePreconditionError(self.name, record.number, "spesifikasi bab")
        draft = record.draft
        if draft is None:
            raise GatePreconditionError(self.name, record.number, "draf")

        verdict = self._agent.judge(
            draft,
            spec=spec,
            book=book,
            research=record.research or ResearchPackage.empty(),
            style_guide=self._style_guide,
            min_words=self._min_words,
        )

        return decide_review(verdict, gate=self.name, threshold=self._threshold)


@register_gate(ChapterReviewer.name)
def _build_reviewer(context: GateContext) -> ChapterReviewer:
    """Bangun gate review dari konteksnya (§27).

    Inilah satu-satunya tempat ``router.chat("reviewer")`` disebut untuk gate
    ini. Agent-nya sendiri tidak pernah tahu model mana yang menjalankannya.
    """
    return ChapterReviewer(
        model=context.router.chat("reviewer"),
        prompts=context.prompts,
        threshold=context.review_threshold,
        min_words=context.min_words,
        repair_attempts=context.repair_attempts,
    )


__all__ = ["DEFAULT_REVIEW_THRESHOLD", "ChapterReviewer", "ChapterReviewerAgent"]
