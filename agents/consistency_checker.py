"""Consistency Checker (§24) dan gate-nya (§27).

§24 menutup daftar periksaannya dengan satu kalimat yang menentukan bentuk gate
ini: *"Agent harus menentukan apakah ketiga istilah memang sinonim atau harus
distandardisasi."* Kalimat itu menyatakan dua hal sekaligus — bahwa keputusannya
milik model, dan bahwa yang **dapat** dipastikan program hanyalah bahan mentah
bagi keputusan itu. Gate ini dibangun tepat di atas pembagian tersebut.

**Beda dari pemeriksa lain, dan mengapa.** §22 dan §23 punya lapis deterministik
yang **menolak** sebelum model dipanggil: kunci sitasi asing (§22) dan anak
tangga yang absen (§23) tidak punya jawaban yang baik, sehingga menanyakannya
kepada model hanya menghasilkan temuan tentang pertanyaan yang tidak dapat
diajukan. Di sini tidak ada yang seperti itu. Kesembilan hal yang §24 minta
diperiksa — terminologi, notasi, akronim, definisi, variabel, rujukan bab, dan
ketiga penomoran — seluruhnya adalah penilaian atas bab terhadap buku, dan tidak
satu pun dapat diputuskan program tanpa model.

Yang dikerjakan lapis deterministik di sini karena itu bukan menolak, melainkan
**membuat pertanyaannya terlihat**:
:func:`~domain.graph.detect_terminology_drift` membandingkan istilah yang
diketahui program tentang bab ini dengan seluruh istilah yang sudah dipakai buku,
dan setiap pasangan yang berbeda hanya pada tanda hubung atau bentuk jamak
dikirim ke model sebagai **pertanyaan** — beserta alasan mekanisnya. Tanpa itu,
contoh §24 sendiri (*finite automaton* di bab 2, *finite-state machine* di bab 7)
hanya dapat ditemukan bila model kebetulan memutuskan membandingkannya sendiri.
Dan yang memastikan pertanyaan itu benar-benar dijawab bukan model yang sama:
:func:`unanswered_drift` melaporkan dugaan yang tidak disebut di temuan mana pun.

**Mengapa gate ini memperkaya, bukan hanya menilai.** ``BookState.terminology``
tidak dapat diisi dari mana pun selain dari sini. Hanya pemeriksaan inilah yang
membaca bab terhadap **seluruh** buku, jadi hanya ia yang tahu istilah apa saja
yang bab ini pakai — dan tanpa itu, bab ketujuh tidak punya apa pun untuk
dibandingkan dengan bab kedua. Karena itu
:class:`~domain.checking.ConsistencyVerdict.glossary` ada, dan karena itu isinya
dipindahkan ke memori bersama oleh :func:`~domain.rules.approved_terminology`
— dan **hanya** dari review yang lulus: glosarium bab yang ditolak belum tentu
mewakili babnya yang sesungguhnya.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ReviewResult
from domain.checking import (
    CheckFinding,
    ConsistencyVerdict,
    GateReport,
    unexamined,
)
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.graph import CHECKS, TerminologyDrift, TerminologyEntry, detect_terminology_drift
from domain.ports import ChatModel, PromptLibrary
from domain.rules import decide_review
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title, summaries_before, terminology_lines
from agents.gates import GateContext, register_gate
from agents.reviewer import DEFAULT_REVIEW_THRESHOLD


def candidate_terms(draft: ChapterDraft) -> tuple[str, ...]:
    """Istilah yang **diketahui program** tentang bab ini, urut kemunculan (MURNI).

    Judul sub-bab, bukan seluruh prosa. Prosa tidak dapat dipotong menjadi istilah
    tanpa penilaian — dan penilaian itu justru yang sedang diminta dari model.
    Menebaknya lebih dulu akan menghasilkan pertanyaan tentang istilah yang tidak
    pernah ditulis siapa pun, dan model yang menjawabnya akan menyatakan babnya
    menyimpang dari sesuatu yang tidak ada.

    Judul sub-bab adalah pernyataan bab ini sendiri tentang apa yang diajarkannya.
    Bab yang menamai sub-babnya *Finite-State Machine* padahal buku ini memakai
    *finite automaton* menyatakannya di sana, dan di situlah pertanyaannya
    berguna.
    """
    return tuple(
        section.heading.strip() for section in draft.sections if section.heading.strip()
    )


def unanswered_drift(
    drift: tuple[TerminologyDrift, ...],
    findings: tuple[CheckFinding, ...],
) -> tuple[TerminologyDrift, ...]:
    """Dugaan yang istilahnya tidak disebut di temuan mana pun (MURNI).

    Aturannya sejenis dengan :func:`~domain.checking.unexamined` dan berdiri di
    atas alasan yang sama: prompt mewajibkan setiap dugaan dijawab, dan yang
    memastikan kewajiban itu ditepati adalah program — bukan model yang sama yang
    diminta menepatinya.

    Yang dicari hanyalah istilah **bab ini**, sebab itulah yang ditanyakan.
    Istilah buku yang sudah dipakai dapat muncul di temuan lain secara kebetulan,
    dan mencocokkannya akan membuat dugaan yang belum dijawab tampak terjawab.

    :returns: dugaan urut ``drift``; kosong berarti setiap pertanyaan terjawab.
    """
    mentioned = " ".join(
        f"{finding.subject} {finding.detail}" for finding in findings
    ).casefold()
    return tuple(item for item in drift if item.term.casefold() not in mentioned)


def _glossary(entries: tuple[TerminologyEntry, ...]) -> dict[str, str]:
    """Glosarium model sebagai peta ``istilah → definisi`` (MURNI).

    Istilah yang lebih baru menang bila muncul dua kali. Bukan aturan yang berarti
    apa-apa tentang kualitas — yang penting hanyalah bahwa pilihannya
    **deterministik**: peta yang isinya bergantung pada urutan kedatangan yang
    tidak tentu adalah peta yang membuat prompt bab berikutnya berubah tanpa sebab.
    """
    merged: dict[str, str] = {}
    for entry in entries:
        term = entry.term.strip()
        if term:
            merged[term] = entry.definition.strip()
    return merged


class ConsistencyCheckerAgent(StructuredAgent[ConsistencyVerdict]):
    """Membandingkan bab dengan seluruh state buku (§24)."""

    role = "reviewer"
    prompt_name = "consistency.chapter"

    def review(
        self,
        draft: ChapterDraft,
        *,
        spec: ChapterSpec,
        book: BookState,
        drift: tuple[TerminologyDrift, ...] = (),
    ) -> ConsistencyVerdict:
        """Periksa kesembilan hal §24 pada ``draft`` terhadap ``book``.

        ``checks`` dikirim terpisah dari ``draft_json`` sekalipun tidak ada di
        dalam draf: ia adalah **daftar periksa** yang harus dijawab satu per satu,
        dan daftar periksa yang tersembunyi di dalam prosa instruksi adalah daftar
        periksa yang dilewati. Yang dikirim adalah :data:`~domain.graph.CHECKS` —
        sumber yang sama dengan yang dipakai :func:`~domain.checking.unexamined`
        untuk memeriksa kelengkapannya, sehingga keduanya tidak dapat menyimpang.

        ``drift`` juga dikirim terpisah, dan di sini ia lebih dari sekadar
        kelengkapan: ia adalah **isi pertanyaannya**, bukan konteksnya.
        """
        return self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "checks": CHECKS,
                "drift": drift,
                "terminology": terminology_lines(book),
                "previous_summaries": summaries_before(book, spec.number),
                "draft_json": draft.model_dump_json(indent=2),
            }
        )


class ConsistencyGate:
    """Gate §24: bab dibandingkan dengan seluruh buku, lalu glosariumnya dicatat."""

    #: Nama gate di ``config.yaml`` — ``pipeline.gates``.
    name = "consistency_checker"

    #: Status yang dicapai bila gate ini lulus (§27).
    produces = ChapterStatus.CONSISTENCY_CHECKED

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        threshold: int = DEFAULT_REVIEW_THRESHOLD,
        repair_attempts: int = 2,
    ) -> None:
        self._agent = ConsistencyCheckerAgent(
            model=model, prompts=prompts, max_repair_attempts=repair_attempts
        )
        self._threshold = threshold

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Periksa konsistensi bab pada ``record`` dan kembalikan vonis sistem (§24, §27).

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

        drift = detect_terminology_drift(candidate_terms(draft), tuple(book.terminology))
        verdict = self._agent.review(draft, spec=spec, book=book, drift=drift)
        report = self._report(verdict, drift=drift)

        result = decide_review(report.verdict(), gate=self.name, threshold=self._threshold)
        if not result.approved:
            return result
        # Glosarium hanya ikut bila vonis **sistem** menyetujui — bukan bila model
        # menyetujui. ``decide_review`` dapat menurunkan persetujuan karena skor
        # berada di bawah ambang, dan glosarium bab yang ditolak sistem belum tentu
        # mewakili bab yang akan dibaca mahasiswa.
        return result.model_copy(update={"terminology": _glossary(verdict.glossary)})

    # -----------------------------------------------------------------
    # Laporan
    # -----------------------------------------------------------------
    def _report(
        self,
        verdict: ConsistencyVerdict,
        *,
        drift: tuple[TerminologyDrift, ...],
    ) -> GateReport:
        """Susun laporan gate dari vonis model dan hal yang tak disebutnya (MURNI).

        Tidak ada bukti yang dicocokkan di sini — sama seperti peninjau pedagogi,
        dan alasannya lebih tegas lagi: yang dibandingkan gate ini adalah bab
        dengan **buku itu sendiri**, dan buku itu tidak pernah menjadi bahan
        rujukan mana pun. Mengisi ``source`` berarti menunjuk berkas yang tidak
        pernah dibuka.

        Dua kewajiban prompt diperiksa program di sini, dan keduanya sejenis:
        kesembilan hal §24 harus punya temuan
        (:func:`~domain.checking.unexamined`), dan setiap dugaan penyimpangan harus
        dijawab (:func:`unanswered_drift`). Keduanya dilaporkan sebagai catatan,
        bukan sebagai penurunan vonis: yang kurang adalah **laporannya**, bukan
        babnya.
        """
        findings = tuple(CheckFinding.of(item) for item in verdict.findings)

        feedback = [*verdict.feedback]
        feedback.extend(finding.describe() for finding in findings if not finding.ok)

        unchecked = unexamined(CHECKS, findings)
        if unchecked:
            listed = ", ".join(repr(check) for check in unchecked)
            feedback.append(f"Hal §24 yang tidak diperiksa model: {listed}.")

        unanswered = unanswered_drift(drift, findings)
        if unanswered:
            listed = ", ".join(f"{item.term!r} vs {item.known!r}" for item in unanswered)
            feedback.append(f"Dugaan penyimpangan istilah yang tidak dijawab model: {listed}.")

        return GateReport(
            approved=verdict.approved,
            score=verdict.score,
            feedback=tuple(feedback),
            findings=findings,
        ).enforce_findings()


@register_gate(ConsistencyGate.name)
def _build_consistency_checker(context: GateContext) -> ConsistencyGate:
    """Bangun gate §24 dari konteksnya.

    ``router.chat("reviewer")`` — peran yang sama dengan peninjau akhir dan
    pemeriksa lainnya: memutuskan sesuatu tentang pekerjaan yang sudah ada,
    bukan menghasilkan bahan baru.
    """
    return ConsistencyGate(
        model=context.router.chat("reviewer"),
        prompts=context.prompts,
        threshold=context.review_threshold,
        repair_attempts=context.repair_attempts,
    )


__all__ = [
    "ConsistencyCheckerAgent",
    "ConsistencyGate",
    "candidate_terms",
    "unanswered_drift",
]
