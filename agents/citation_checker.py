"""Citation Checker (§22) dan gate-nya (§27).

§22 menuntut tiga hal, dan urutannya penting:

1. setiap kunci yang dikutip punya entri daftar pustaka,
2. entri itu benar-benar berasal dari **knowledge base**,
3. model tidak boleh mengarang kunci sitasi.

Dua yang pertama **dapat dipastikan tanpa model**, jadi dipastikan tanpa model.
Rujukan yang tidak ada di knowledge base adalah kegagalan pasti — tidak ada
penilaian yang perlu ditanyakan, dan menanyakannya berarti membayar model untuk
menjawab pertanyaan yang jawabannya sudah diketahui program. Itu bukan penghematan
kecil: rantai §27 berisi sepuluh gate dan setiap revisi menjalankannya ulang.

Yang benar-benar butuh penilaian tinggal satu, dan hanya itulah yang sampai ke
model: **apakah bahan yang tersedia menopang apa yang bab nyatakan**. Draf tanpa
satu pun sitasi karena itu tidak memanggil model sama sekali — ia tidak punya
pertanyaan untuk ditanyakan, dan gate-nya mengatakan demikian alih-alih
melaporkan bab "sudah diperiksa".

**Mengapa gate ini menerima sitasi dari bab-bab sebelumnya.** Ia memakai
:func:`~domain.rules.citations_allowed_by` dengan ``inherited=book.citations``,
sedangkan penulis hanya boleh mengutip apa yang **terlihat** olehnya pada bab ini.
Keduanya sengaja tidak sama, dan yang lebih longgar justru yang benar di sini:
§22 menuntut rujukan "berasal dari knowledge base", bukan "berasal dari hasil
pencarian bab ini". Sumber yang sah dikutip bab 1 tetap sah dikutip bab 5,
sekalipun pencarian bab 5 kebetulan tidak memunculkannya kembali. Penulis tetap
lebih ketat — ia tidak dapat mengutip apa yang tidak dibacanya — dan ketatnya
penulis bukan urusan pemeriksa.
"""

from __future__ import annotations

from typing import Sequence

from domain.book import ChapterSpec
from domain.chapter import (
    ChapterDraft,
    ChapterRecord,
    Evidence,
    ResearchPackage,
    ReviewResult,
)
from domain.checking import CheckFinding, CheckVerdict, GateReport, unexamined
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.ports import ChatModel, PromptLibrary
from domain.rules import (
    citations_allowed_by,
    decide_review,
    foreign_citations,
)
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title
from agents.gates import GateContext, register_gate
from agents.reviewer import DEFAULT_REVIEW_THRESHOLD

#: Skor yang dilaporkan ketika sebuah rujukan asing ditemukan. Nol, bukan angka
#: rendah: rujukan yang tidak ada di knowledge base bukan bab yang kurang baik,
#: melainkan bab yang memuat rujukan yang tidak dapat dipertanggungjawabkan.
_FOREIGN_SCORE = 0


class CitationCheckerAgent(StructuredAgent[CheckVerdict]):
    """Memeriksa kesesuaian sitasi dengan bahan yang tersedia (§22)."""

    role = "reviewer"
    prompt_name = "citation.chapter"

    def check(
        self,
        draft: ChapterDraft,
        *,
        spec: ChapterSpec,
        book: BookState,
        research: ResearchPackage,
        evidence: Sequence[Evidence],
    ) -> CheckVerdict:
        """Nilai setiap kunci di ``draft.citations`` terhadap ``evidence`` (§22).

        Draf dikirim sebagai **JSON**, bukan Markdown hasil render: yang dinilai
        adalah kaitannya dengan bahan, dan kaitan itu hidup di dalam struktur
        draf, bukan di dalam teks yang sudah diratakan.
        """
        return self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "citations": draft.citations,
                "evidence": tuple(evidence),
                "research": research,
                "draft_json": draft.model_dump_json(indent=2),
            }
        )


class CitationChecker:
    """Gate §22: dua lapis, dan lapis pertama tidak memanggil model sama sekali.

    Ia **menolak** bab, bukan memperkayanya: satu-satunya hal yang dapat
    dilakukan penulis terhadap rujukan yang tidak sah adalah menghapusnya, dan
    itu memang yang akan dilakukannya pada putaran revisi berikutnya.
    """

    #: Nama gate di ``config.yaml`` — ``pipeline.gates``.
    name = "citation_checker"

    #: Status yang dicapai bila gate ini lulus (§27).
    produces = ChapterStatus.CITATION_CHECKED

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        threshold: int = DEFAULT_REVIEW_THRESHOLD,
        repair_attempts: int = 2,
    ) -> None:
        self._agent = CitationCheckerAgent(
            model=model, prompts=prompts, max_repair_attempts=repair_attempts
        )
        self._threshold = threshold

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Periksa sitasi bab pada ``record`` dan kembalikan vonis sistem (§22, §27).

        :raises GatePreconditionError: bila spesifikasi atau draf belum ada.
        :raises AgentOutputError: bila model gagal menghasilkan vonis yang dapat
            di-parse. Ditahan per bab oleh ``BookDirector`` (§35).
        """
        spec = record.spec
        if spec is None:
            raise GatePreconditionError(self.name, record.number, "spesifikasi bab")
        draft = record.draft
        if draft is None:
            raise GatePreconditionError(self.name, record.number, "draf")

        research = record.research or ResearchPackage.empty()
        allowed = citations_allowed_by(research, inherited=tuple(book.citations))

        foreign = foreign_citations(draft, allowed=allowed)
        if foreign:
            return self._reject_foreign(foreign)
        if not draft.citations:
            return self._nothing_to_check()

        evidence = tuple(item for item in research.evidence if item.source.strip() in allowed)
        verdict = self._agent.check(
            draft, spec=spec, book=book, research=research, evidence=evidence
        )
        report = self._report(verdict, draft=draft, evidence=evidence)

        return decide_review(report.verdict(), gate=self.name, threshold=self._threshold)

    # -----------------------------------------------------------------
    # Lapis deterministik — nol token
    # -----------------------------------------------------------------
    def _reject_foreign(self, foreign: tuple[str, ...]) -> ReviewResult:
        """Tolak draf yang mengutip di luar knowledge base, tanpa memanggil model (§22).

        Kunci yang asing disebutkan **apa adanya**: penulis harus tahu persis mana
        yang harus dihapus, dan daftar "ada rujukan yang tidak sah" saja akan
        membuatnya menebak.
        """
        listed = ", ".join(repr(key) for key in foreign)
        return decide_review(
            GateReport(
                approved=False,
                score=_FOREIGN_SCORE,
                feedback=(
                    f"Sitasi berikut tidak berasal dari knowledge base: {listed}.",
                    "Rujukan yang tidak ada di knowledge base adalah kegagalan pasti "
                    "(§22), jadi tidak ada penilaian model yang dijalankan atasnya.",
                ),
            ).verdict(),
            gate=self.name,
            threshold=self._threshold,
        )

    def _nothing_to_check(self) -> ReviewResult:
        """Bab tanpa sitasi: lulus tanpa memanggil model, dan mengatakan bahwa ia tidak memeriksa.

        ``skipped`` bernilai benar dan bukan hiasan. Bab yang lolos tanpa
        pemeriksaan harus terlihat sebagai bab yang lolos tanpa pemeriksaan —
        sama seperti tahap yang dimatikan karena fiturnya tidak ada. Yang salah
        adalah melaporkannya sebagai bab yang sitasinya sudah diperiksa.
        """
        return ReviewResult(
            gate=self.name,
            approved=True,
            score=10,
            feedback=(
                "Draf tidak memuat satu pun sitasi; tidak ada yang diperiksa.",
            ),
            skipped=True,
        )

    # -----------------------------------------------------------------
    # Lapis penilaian — model hanya dipanggil bila ada yang ditanyakan
    # -----------------------------------------------------------------
    def _report(
        self,
        verdict: CheckVerdict,
        *,
        draft: ChapterDraft,
        evidence: Sequence[Evidence],
    ) -> GateReport:
        """Susun laporan gate dari vonis model, temuan, dan kaitannya ke bukti (MURNI).

        Tiga hal terjadi di sini, dan ketiganya adalah pekerjaan program:
        sumber dan halaman diisi dari bukti (model tidak pernah melihatnya),
        temuan yang gagal diterjemahkan menjadi catatan yang dapat dikerjakan
        penulis, dan rujukan yang tidak disebut model sama sekali dilaporkan
        sebagai belum diperiksa.
        """
        findings = tuple(CheckFinding.of(item, evidence=evidence) for item in verdict.findings)

        feedback = [*verdict.feedback]
        feedback.extend(finding.describe() for finding in findings if not finding.ok)

        unchecked = unexamined(draft.citations, findings)
        if unchecked:
            listed = ", ".join(repr(key) for key in unchecked)
            feedback.append(f"Rujukan yang tidak diperiksa model: {listed}.")

        return GateReport(
            approved=verdict.approved,
            score=verdict.score,
            feedback=tuple(feedback),
            findings=findings,
        ).enforce_findings()


@register_gate(CitationChecker.name)
def _build_citation_checker(context: GateContext) -> CitationChecker:
    """Bangun gate §22 dari konteksnya.

    Satu-satunya tempat ``router.chat("reviewer")`` disebut untuk gate ini.
    Perannya sama dengan peninjau (§21–§24): memutuskan sesuatu tentang pekerjaan
    yang sudah ada, bukan menghasilkan bahan baru.
    """
    return CitationChecker(
        model=context.router.chat("reviewer"),
        prompts=context.prompts,
        threshold=context.review_threshold,
        repair_attempts=context.repair_attempts,
    )


__all__ = ["CitationChecker", "CitationCheckerAgent"]
