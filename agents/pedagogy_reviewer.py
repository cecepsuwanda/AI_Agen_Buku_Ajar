"""Pedagogy Reviewer (§23) dan gate-nya (§27).

§23 menggambar tangga penyajian dan menutupnya dengan enam kelemahan yang harus
dideteksi. Gate ini mewujudkannya dalam **dua lapis**, dengan pembagian yang sama
seperti pemeriksa sitasi (§22) — dan alasannya lebih tegas di sini.

**Lapis pertama tidak memanggil model, dan itu bukan penghematan.** Empat dari
enam kelemahan §23 hanya dapat dijawab pada bab yang tangganya sudah lengkap.
"Latihan tidak sesuai tujuan pembelajaran" tidak dapat dijawab pada bab tanpa
tujuan; pertanyaan itu tidak sulit, ia tidak punya jawaban. Model yang tetap
ditanya akan menjawabnya dari sesuatu yang lain — dan jawaban itu akan masuk ke
laporan sebagai temuan tentang bab yang tidak pernah diperiksa. Karena itu
:func:`~domain.pedagogy.missing_rungs` berjalan lebih dulu, dan bila ada anak
tangga yang absen, gate menolak tanpa satu token pun.

**Lapis kedua menilai, dan ia menilai seluruhnya.** Anak tangga yang sudah ada
belum tentu baik: penjelasan yang melompat, contoh yang menyinggung materi lain,
latihan yang jauh di atas pembacanya. Itu pekerjaan model, dan gate ini sengaja
meminta temuan atas **setiap** anak tangga — bukan hanya atas yang buruk. Laporan
yang menyebut yang buruk saja tidak dapat dibedakan dari laporan yang belum
memeriksa sisanya, dan §23 adalah gate yang paling sering menolak: catatannya
adalah instruksi revisi penulis (§31), sehingga yang tidak disebut tidak akan
diperbaiki.

**Mengapa gate ini tidak memperkaya draf.** Ia tidak punya bahan baru untuk
diberikan. Contoh dan latihan sudah dihasilkan §19 dan §20; yang tersisa hanyalah
vonis, dan vonis yang salah tempat — di dalam gate — akan menghapus pekerjaan
dua agent yang sudah dibayar.

**Mengapa kejujuran tidak menyelamatkan bab di sini.** Pemeriksa fakta (§21)
tidak menghukum klaim yang penulis sendiri tandai belum berbukti, sebab menandai
adalah perbuatan benar yang diwajibkan. Di sini tidak ada padanannya:
``unresolved_claims`` menyatakan klaim yang belum berbukti, bukan anak tangga
yang belum ditulis. Bab yang menandai ketiadaan penjelasannya sendiri tetap bab
tanpa penjelasan, dan tetap harus diperbaiki sebelum diterbitkan.
"""

from __future__ import annotations

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ReviewResult
from domain.checking import CheckFinding, CheckVerdict, GateReport, unexamined
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.pedagogy import LADDER, missing_rungs
from domain.ports import ChatModel, PromptLibrary
from domain.rules import decide_review
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import book_language, book_title
from agents.gates import GateContext, register_gate
from agents.reviewer import DEFAULT_REVIEW_THRESHOLD

#: Skor yang dilaporkan ketika sebuah anak tangga §23 terbukti absen.
#:
#: Nol, bukan angka rendah — sama seperti rujukan asing di §22. Bab yang kehilangan
#: anak tangga bukan bab yang kurang baik, melainkan bab yang belum lengkap
#: sebagai bab; dan angkanya tidak dipakai untuk memutuskan apa pun di sini,
#: sebab keputusannya sudah pasti: tolak.
_GAP_SCORE = 0


class PedagogyReviewerAgent(StructuredAgent[CheckVerdict]):
    """Menilai urutan dan kelengkapan penyajian bab (§23)."""

    role = "reviewer"
    prompt_name = "pedagogy.chapter"

    def review(
        self,
        draft: ChapterDraft,
        *,
        spec: ChapterSpec,
        book: BookState,
    ) -> CheckVerdict:
        """Nilai kelima anak tangga §23 pada ``draft``.

        ``ladder`` dikirim terpisah dari ``draft_json`` sekalipun anak tangganya
        tidak ada di dalam draf: ia adalah **daftar periksa** yang harus dijawab
        satu per satu, dan daftar periksa yang tersembunyi di dalam prosa
        instruksi adalah daftar periksa yang dilewati. Yang dikirim adalah
        :data:`~domain.pedagogy.LADDER` — sumber yang sama dengan yang dipakai
        lapis deterministik, sehingga keduanya tidak dapat menyimpang.

        ``objectives`` adalah tujuan yang **direncanakan** spesifikasi, bukan
        yang tertulis di draf. Keduanya memang dapat berbeda, dan perbedaan itulah
        yang membuat "latihan tidak sesuai tujuan" dapat diperiksa: yang dinilai
        bukan apakah latihannya cocok dengan babnya sendiri — bab yang mengejar
        tujuan yang salah akan lolos — melainkan apakah keduanya cocok dengan
        tujuan yang diminta.
        """
        return self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "objectives": spec.objectives,
                "ladder": LADDER,
                "draft_json": draft.model_dump_json(indent=2),
            }
        )


class PedagogyGate:
    """Gate §23: tangga §23 utuh, lalu dinilai anak tangga demi anak tangga."""

    #: Nama gate di ``config.yaml`` — ``pipeline.gates``.
    name = "pedagogy_reviewer"

    #: Status yang dicapai bila gate ini lulus (§27).
    produces = ChapterStatus.PEDAGOGY_REVIEWED

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        threshold: int = DEFAULT_REVIEW_THRESHOLD,
        repair_attempts: int = 2,
    ) -> None:
        self._agent = PedagogyReviewerAgent(
            model=model, prompts=prompts, max_repair_attempts=repair_attempts
        )
        self._threshold = threshold

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Nilai pedagogi bab pada ``record`` dan kembalikan vonis sistem (§23, §27).

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

        gaps = missing_rungs(draft, spec=spec)
        if gaps:
            return self._reject_gaps(gaps)

        verdict = self._agent.review(draft, spec=spec, book=book)
        report = self._report(verdict, draft=draft)

        return decide_review(report.verdict(), gate=self.name, threshold=self._threshold)

    # -----------------------------------------------------------------
    # Lapis deterministik — nol token
    # -----------------------------------------------------------------
    def _reject_gaps(self, gaps: tuple[CheckFinding, ...]) -> ReviewResult:
        """Tolak bab yang tangganya belum lengkap, tanpa memanggil model (§23).

        Alasannya bukan biaya, melainkan kebenaran: keempat kelemahan yang paling
        sering diminta §23 hanya punya jawaban pada bab yang tangganya utuh.
        Model yang dipanggil tetap akan mengeluarkan vonis, dan vonis itu akan
        tercatat sebagai hasil pemeriksaan — padahal yang terjadi hanyalah
        pertanyaan yang belum dapat diajukan.

        Setiap anak tangga yang hilang disebutkan **apa adanya**, dengan
        nama tangganya, sehingga penulis tahu persis mana yang harus dilengkapi;
        daftar "ada yang kurang" saja akan membuatnya menebak.
        """
        feedback = [finding.describe() for finding in gaps]
        feedback.append(
            "Tangga §23 harus utuh sebelum penilaiannya bermakna; karena itu tidak "
            "ada penilaian model yang dijalankan atas bab ini."
        )
        return decide_review(
            GateReport(approved=False, score=_GAP_SCORE, feedback=tuple(feedback)).verdict(),
            gate=self.name,
            threshold=self._threshold,
        )

    # -----------------------------------------------------------------
    # Lapis penilaian — model hanya dipanggil bila ada yang ditanyakan
    # -----------------------------------------------------------------
    def _report(self, verdict: CheckVerdict, *, draft: ChapterDraft) -> GateReport:
        """Susun laporan gate dari vonis model dan anak tangga yang tak disebutnya (MURNI).

        Tidak ada bukti yang dicocokkan di sini — berbeda dari pemeriksa fakta dan
        pemeriksa sitasi. Temuan gate ini tentang **draf itu sendiri**, bukan
        tentang hubungannya dengan bahan luar, sehingga tidak ada ``source``
        maupun ``page`` yang dapat diisi: mengisinya berarti menunjuk sumber yang
        tidak pernah dibandingkan.

        Yang tetap dikerjakan program adalah :func:`~domain.checking.unexamined`:
        anak tangga yang tidak muncul di temuan mana pun dilaporkan sebagai belum
        diperiksa. Prompt mewajibkan kelimanya, dan yang memastikan kewajiban itu
        ditepati adalah aturan ini — bukan model yang sama.
        """
        findings = tuple(CheckFinding.of(item) for item in verdict.findings)

        feedback = [*verdict.feedback]
        feedback.extend(finding.describe() for finding in findings if not finding.ok)

        unchecked = unexamined(LADDER, findings)
        if unchecked:
            listed = ", ".join(repr(rung) for rung in unchecked)
            feedback.append(f"Anak tangga §23 yang tidak diperiksa model: {listed}.")

        return GateReport(
            approved=verdict.approved,
            score=verdict.score,
            feedback=tuple(feedback),
            findings=findings,
        ).enforce_findings()


@register_gate(PedagogyGate.name)
def _build_pedagogy_reviewer(context: GateContext) -> PedagogyGate:
    """Bangun gate §23 dari konteksnya.

    Satu-satunya tempat ``router.chat("reviewer")`` disebut untuk gate ini.
    Perannya sama dengan peninjau akhir dan kedua pemeriksa lainnya: memutuskan
    sesuatu tentang pekerjaan yang sudah ada, bukan menghasilkan bahan baru.
    """
    return PedagogyGate(
        model=context.router.chat("reviewer"),
        prompts=context.prompts,
        threshold=context.review_threshold,
        repair_attempts=context.repair_attempts,
    )


__all__ = ["PedagogyGate", "PedagogyReviewerAgent"]
