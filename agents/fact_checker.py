"""Fact Checker (§21) dan gate-nya (§27).

§21 menulis alurnya sebagai satu baris:

    Claim → Retrieved Evidence → Supported?

dan menutupnya dengan "jika ``supported = false`` → chapter kembali ke Writer
untuk revisi". Gate ini mewujudkan keduanya, dengan satu pengecualian yang
disengaja dan dijelaskan di bawah.

**Yang diperiksa.** Setiap klaim faktual yang spesifik — angka, tahun, nama
orang, nama standar, hasil penelitian. Daftar itu bukan karangan gate ini: ia
persis daftar yang ``writer.chapter.md`` wajibkan untuk dicatat di
``unresolved_claims``. Karena itu gate ini memakai ``unresolved_claims`` sebagai
**masukan**, bukan sebagai hiasan: klaim yang penulis sendiri tandai belum
berbukti diperiksa lebih dulu, dengan ``subject`` disalin apa adanya dari daftar
itu. Penyalinan itu yang membuat sistem dapat mengenali klaim yang **sudah
dinyatakan** — dan pengenalan itu adalah inti pengecualian di bawah.

**Pengecualian yang disengaja.** Bacaan harfiah §21 — setiap ``supported=false``
mengembalikan bab ke penulis — tidak dapat berhenti: penulis dan pemeriksa
membaca bahan yang sama, dan penulis **diwajibkan** mencatat klaim yang tidak
dapat didukungnya. Setiap revisi akan menghasilkan penandaan yang sama, dan bab
itu berputar selamanya di antara dua gate. Karena itu yang **menolak** adalah
klaim yang tidak didukung **dan** tidak dinyatakan babnya sendiri; klaim yang
sudah dinyatakan tetap dilaporkan, tetapi tidak dihukum. Perbedaan itu hanya
dapat dibuat program — ia membandingkan draf dengan dirinya sendiri, bukan
dengan bahan — dan ia sejalan dengan §34 maupun dengan aturan 5
``writer.revise.md``: "Jangan menurunkan kejujuran demi kelengkapan."

**Mengapa gate ini berhenti tanpa memanggil model bila tidak ada bukti.** Tanpa
satu pun potongan bahan, satu-satunya cara model menjawab adalah dari ingatannya
sendiri — dan klaim yang "sepertinya benar" adalah klaim yang tidak pernah
diperiksa siapa pun. Gate yang tidak dapat memeriksa harus mengatakan bahwa ia
tidak memeriksa (``skipped=True``), bukan melaporkan bab sebagai sudah
diperiksa fakta.
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
from domain.rules import decide_review
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title
from agents.gates import GateContext, register_gate
from agents.reviewer import DEFAULT_REVIEW_THRESHOLD


class FactCheckerAgent(StructuredAgent[CheckVerdict]):
    """Memeriksa klaim faktual bab terhadap bukti yang benar-benar ditemukan (§21)."""

    role = "fact_checker"
    prompt_name = "factcheck.chapter"

    def check(
        self,
        draft: ChapterDraft,
        *,
        spec: ChapterSpec,
        book: BookState,
        research: ResearchPackage,
        evidence: Sequence[Evidence],
    ) -> CheckVerdict:
        """Nilai setiap klaim dalam bab terhadap ``evidence`` (§21).

        ``claims`` — yaitu ``unresolved_claims`` — dikirim terpisah dari
        ``draft_json`` sekalipun ia sudah termuat di dalamnya. Prompt perlu dapat
        menyuruh model menyalin setiap butirnya **apa adanya**, dan daftar yang
        terpisah adalah satu-satunya bentuk yang membuat perintah itu masuk akal
        dibaca: daftar di tengah JSON adalah daftar yang terlupakan.
        """
        return self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "claims": draft.unresolved_claims,
                "evidence": tuple(evidence),
                "research": research,
                "draft_json": draft.model_dump_json(indent=2),
            }
        )


class FactChecker:
    """Gate §21: klaim → bukti → didukung?

    Ia **menolak** bab, bukan memperkayanya. Satu-satunya hal yang dapat
    dilakukan penulis terhadap klaim yang tidak didukung bahan adalah menandai
    atau membuangnya, dan keduanya dikerjakan pada putaran revisi berikutnya —
    jalur ``_enter_revision`` yang sudah ada.
    """

    #: Nama gate di ``config.yaml`` — ``pipeline.gates``.
    name = "fact_checker"

    #: Status yang dicapai bila gate ini lulus (§27).
    produces = ChapterStatus.FACT_CHECKED

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        threshold: int = DEFAULT_REVIEW_THRESHOLD,
        repair_attempts: int = 2,
    ) -> None:
        self._agent = FactCheckerAgent(
            model=model, prompts=prompts, max_repair_attempts=repair_attempts
        )
        self._threshold = threshold

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Periksa klaim bab pada ``record`` dan kembalikan vonis sistem (§21, §27).

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
        evidence = tuple(research.evidence)
        if not evidence:
            return self._nothing_to_verify(research)

        verdict = self._agent.check(
            draft, spec=spec, book=book, research=research, evidence=evidence
        )
        report = self._report(verdict, draft=draft, evidence=evidence)

        return decide_review(report.verdict(), gate=self.name, threshold=self._threshold)

    def _nothing_to_verify(self, research: ResearchPackage) -> ReviewResult:
        """Tanpa satu pun bukti: lulus tanpa memanggil model, dan mengatakannya.

        Ini bukan kelonggaran, melainkan satu-satunya jawaban yang jujur. Model
        yang diminta menilai klaim tanpa bahan akan menilainya dari ingatannya
        sendiri, dan vonis yang keluar dari ingatan adalah vonis yang tidak
        dapat dibedakan dari karangan — persis yang dilarang §34.

        ``skipped`` bernilai benar, sama seperti gate tahap yang belum
        berpenghuni dan seperti pemeriksa sitasi yang tidak punya pertanyaan
        untuk ditanyakan: bab yang lolos tanpa pemeriksaan harus terlihat
        sebagai bab yang lolos tanpa pemeriksaan.
        """
        sebab = (
            "Pencarian tidak dijalankan pada iterasi ini"
            if research.degraded
            else "Pencarian tidak menemukan satu pun potongan bahan"
        )
        return ReviewResult(
            gate=self.name,
            approved=True,
            score=10,
            feedback=(
                f"{sebab}, sehingga tidak ada bahan untuk memeriksa klaim bab ini.",
                "Tidak ada klaim yang diverifikasi; jalankan ulang setelah indeks "
                "pengetahuan terisi agar pemeriksaan fakta benar-benar terjadi.",
            ),
            skipped=True,
        )

    def _report(
        self,
        verdict: CheckVerdict,
        *,
        draft: ChapterDraft,
        evidence: Sequence[Evidence],
    ) -> GateReport:
        """Susun laporan gate dari vonis model, temuan, dan kaitannya ke bukti (MURNI).

        Tiga hal terjadi di sini, dan ketiganya pekerjaan program:

        * sumber dan halaman diisi dari bukti — model tidak pernah melihatnya,
        * setiap temuan yang gagal ditandai ``declared`` bila bab ini sendiri
          sudah mencatat klaimnya di ``unresolved_claims``,
        * klaim yang penulis tandai tetapi tidak disebut model sama sekali
          dilaporkan sebagai belum diperiksa.
        """
        declared = frozenset(claim.strip() for claim in draft.unresolved_claims if claim.strip())
        findings = tuple(
            CheckFinding.of(item, evidence=evidence, declared=declared)
            for item in verdict.findings
        )

        feedback = [*verdict.feedback]
        feedback.extend(finding.describe() for finding in findings if not finding.ok)

        unchecked = unexamined(draft.unresolved_claims, findings)
        if unchecked:
            listed = "; ".join(repr(claim) for claim in unchecked)
            feedback.append(f"Klaim yang tidak diperiksa model: {listed}.")

        return GateReport(
            approved=verdict.approved,
            score=verdict.score,
            feedback=tuple(feedback),
            findings=findings,
        ).enforce_findings()


@register_gate(FactChecker.name)
def _build_fact_checker(context: GateContext) -> FactChecker:
    """Bangun gate §21 dari konteksnya.

    Satu-satunya tempat ``router.chat("fact_checker")`` disebut untuk gate ini.
    Peran ini berdiri sendiri di §6, terpisah dari ``reviewer``: menilai apakah
    sebuah klaim **benar** menurut bahan adalah pertanyaan yang berbeda dari
    menilai apakah babnya **baik**, dan keduanya boleh saja dijawab model yang
    berbeda.
    """
    return FactChecker(
        model=context.router.chat("fact_checker"),
        prompts=context.prompts,
        threshold=context.review_threshold,
        repair_attempts=context.repair_attempts,
    )


__all__ = ["FactChecker", "FactCheckerAgent"]
