"""Vonis pemeriksa bab (§21–§24) — MURNI.

Empat pemeriksa blueprint — fakta (§21), sitasi (§22), pedagogi (§23), dan
konsistensi (§24) — menanyakan hal yang berbeda atas bahan yang sama, tetapi
jawabannya berbentuk sama: **vonis atas bab, beserta temuan per subjek**. Bentuk
jawaban itulah yang tinggal di sini, sekali. Kalau tidak, keempatnya akan
menumbuhkan empat kosakata yang mirip tetapi tidak sama, dan laporan akhirnya
tidak dapat dibaca sebagai satu hal.

Dua lapis, dan pemisahannya sama persis dengan pasangan
:class:`~domain.chapter.ReviewVerdict` / :class:`~domain.chapter.ReviewResult`:

* :class:`CheckVerdict` dan :class:`CheckJudgement` — **apa yang dikatakan
  model**. ``strict_schema`` menandai semua properti sebagai wajib, jadi apa pun
  yang ada di sini akan diminta dari model. Karena itu tidak ada ``source``
  maupun ``page`` di dalamnya: model tidak pernah melihat halaman yang tepat,
  dan memaksanya mengisi nomor halaman sama dengan membiarkannya mengarang nomor
  yang tidak akan pernah ditemukan pembaca.
* :class:`GateReport` dan :class:`CheckFinding` — **apa yang dicatat sistem**.
  Sumber dan halaman diisi program, dari bukti yang benar-benar dimilikinya.

Vonisnya diterjemahkan menjadi :class:`~domain.chapter.ReviewResult` oleh
:func:`~domain.rules.decide_review` yang sudah ada. Lulus atau tidaknya sebuah
bab tetap diputuskan di satu tempat saja; berkas ini menambah **alasan**, bukan
jalan kedua.
"""

from __future__ import annotations

from typing import Sequence

from pydantic import Field

from domain.base import FrozenModel
from domain.chapter import Evidence, ReviewVerdict


class CheckJudgement(FrozenModel):
    """Penilaian model atas **satu subjek** — klaim, kunci sitasi, atau istilah.

    ``subject`` adalah pengait antara penilaian ini dan hal yang dinilai, dan
    karena itu ia ditulis apa adanya dari draf. Model yang memparafrase subjeknya
    memutus kait itu, dan temuannya berhenti dapat ditindaklanjuti penulis:
    "klaim tentang kompleksitas" tidak menunjuk kalimat mana pun untuk diperbaiki.
    """

    subject: str = Field(
        min_length=1,
        description="Klaim, kunci sitasi, atau istilah yang dinilai — apa adanya dari draf.",
    )
    ok: bool = Field(description="True hanya bila subjek ini memenuhi syarat pemeriksaan.")
    detail: str = Field(
        default="",
        description="Satu kalimat: apa yang didukung, atau apa yang tidak dan mengapa.",
    )


class CheckVerdict(FrozenModel):
    """Vonis sebuah pemeriksa **sebagaimana dikatakan model** (§21–§24).

    Tanpa satu pun field milik kita — tanpa nama gate, tanpa bendera
    ``skipped``, tanpa sumber dan halaman. Yang tidak diketahui model tidak
    boleh ditanyakan kepadanya.
    """

    approved: bool
    score: int = Field(default=0, ge=0, le=10)
    feedback: tuple[str, ...] = ()
    findings: tuple[CheckJudgement, ...] = ()


class CheckFinding(FrozenModel):
    """Satu temuan pemeriksa, lengkap dengan sumber yang diisi program (§21).

    Bidangnya sengaja sama dengan yang diminta §21 untuk pemeriksaan fakta —
    ``{claim, supported, source, page}`` — karena bentuk itu memang yang
    dibutuhkan pembaca laporan: bukan "ada yang salah", melainkan "klaim ini, di
    halaman ini, tidak didukung".
    """

    subject: str = Field(min_length=1)
    ok: bool
    detail: str = ""
    source: str = Field(default="", description="Sumber temuan — diisi program, bukan model.")
    page: int | None = Field(
        default=None, description="Halaman sumber — diisi program, bukan model."
    )

    @classmethod
    def of(
        cls,
        judgement: CheckJudgement,
        *,
        evidence: Sequence[Evidence] = (),
    ) -> "CheckFinding":
        """Lengkapi penilaian model dengan sumber dan halaman (MURNI).

        Pencocokannya **persis**, mengikuti :func:`~domain.rules.citations_allowed_by`:
        subjek yang tidak menunjuk satu pun bukti tetap dicatat, tanpa sumber.
        Menebak sumber terdekat akan menghasilkan temuan yang menunjuk halaman
        yang salah — persis jenis kesalahan yang tidak dapat ditemukan manusia.
        """
        match = evidence_for(judgement.subject, evidence)
        return cls(
            subject=judgement.subject,
            ok=judgement.ok,
            detail=judgement.detail,
            source=match.source if match is not None else "",
            page=match.page if match is not None else None,
        )

    def describe(self) -> str:
        """Satu baris untuk ``feedback``: apa yang salah, dan di mana (MURNI).

        Ditulis untuk temuan yang **gagal**; temuan yang lolos tidak perlu
        diceritakan lagi kepada penulis, dan menceritakannya justru mengubur
        yang harus dikerjakan di antara yang sudah beres.
        """
        where = ""
        if self.source:
            where = f" [{self.source}" + (f" hlm. {self.page}]" if self.page is not None else "]")
        reason = self.detail.strip() or "tidak memenuhi syarat"
        return f"{self.subject}{where}: {reason}"


class GateReport(FrozenModel):
    """Laporan sebuah pemeriksa, sebelum menjadi :class:`~domain.chapter.ReviewResult`.

    Terpisah dari :class:`CheckVerdict` karena satu alasan yang sama dengan
    pemisahan ``ReviewResult`` dari ``ReviewVerdict``: apa yang dikatakan model
    dan apa yang dicatat sistem memang dua hal. Yang membedakannya di sini
    adalah ``findings`` — daftar temuan per subjek, yang hanya dapat disusun
    lengkap setelah program mengisi sumber dan halamannya.
    """

    approved: bool
    score: int = Field(default=0, ge=0, le=10)
    feedback: tuple[str, ...] = ()
    findings: tuple[CheckFinding, ...] = ()

    def failed(self) -> tuple[CheckFinding, ...]:
        """Temuan yang tidak memenuhi syarat, urut kemunculan (MURNI)."""
        return tuple(finding for finding in self.findings if not finding.ok)

    def enforce_findings(self) -> "GateReport":
        """Turunkan vonis yang bertentangan dengan temuannya sendiri (MURNI).

        Pemeriksa yang menandai sebuah rujukan tidak didukung, lalu tetap
        menyatakan babnya lulus, telah membatalkan pekerjaannya sendiri — dan
        tanpa aturan ini, bab itu akan lolos tanpa satu pun tanda. Ini aturan
        yang sama dengan ambang skor di :func:`~domain.rules.decide_review`,
        diterapkan pada temuan alih-alih pada angka: vonis ditegakkan sistem,
        bukan diserahkan kepada pemeriksa.

        Ketidaksepakatan itu **dilaporkan**, bukan disembunyikan — sama seperti
        di ``decide_review``: menelannya akan membuat laporan tampak seperti
        penolakan biasa, dan tidak ada yang tahu pemeriksa sebenarnya menyetujui.
        """
        failing = self.failed()
        if not self.approved or not failing:
            return self
        return self.model_copy(
            update={
                "approved": False,
                "feedback": (
                    *self.feedback,
                    f"{len(failing)} temuan tidak memenuhi syarat; persetujuan "
                    "pemeriksa diabaikan oleh sistem.",
                ),
            }
        )

    def verdict(self) -> ReviewVerdict:
        """Vonis dalam bentuk yang dimengerti :func:`~domain.rules.decide_review` (MURNI)."""
        return ReviewVerdict(
            approved=self.approved,
            score=self.score,
            feedback=self.feedback,
        )


# ---------------------------------------------------------------------------
# Pencocokan temuan dengan bukti
# ---------------------------------------------------------------------------
def evidence_for(subject: str, evidence: Sequence[Evidence]) -> Evidence | None:
    """Bukti pertama yang sumbernya sama persis dengan ``subject`` (MURNI).

    :returns: bukti yang cocok, atau ``None`` bila tidak ada — dan ``None``
        memang jawaban yang benar: sebuah subjek boleh saja tidak menunjuk bukti
        mana pun, dan mengarang kaitannya lebih buruk daripada mengakuinya.
    """
    wanted = subject.strip()
    if not wanted:
        return None
    for item in evidence:
        if item.source.strip() == wanted:
            return item
    return None


def unexamined(subjects: Sequence[str], findings: Sequence[CheckFinding]) -> tuple[str, ...]:
    """Subjek yang tidak muncul di temuan mana pun, urut kemunculan (MURNI).

    Pemeriksa yang menyatakan "semuanya baik" tanpa menyebut satu pun subjek
    belum memeriksa apa pun; ia hanya tidak menemukan apa yang hendak dikatakan.
    Bedanya penting bagi orang yang membaca laporan, dan karena itu dilaporkan —
    bukan dianggap lulus. Ia sengaja **tidak** menurunkan vonis: yang dilaporkan
    di sini adalah kekurangan laporan, bukan kekurangan bab.
    """
    seen: dict[str, None] = {finding.subject.strip(): None for finding in findings}
    missing: dict[str, None] = {}
    for subject in subjects:
        normalized = subject.strip()
        if normalized and normalized not in seen:
            missing.setdefault(normalized, None)
    return tuple(missing)


__all__ = [
    "CheckFinding",
    "CheckJudgement",
    "CheckVerdict",
    "GateReport",
    "evidence_for",
    "unexamined",
]
