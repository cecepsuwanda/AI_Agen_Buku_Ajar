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
  yang ada di sini akan diminta dari model. Karena itu tidak ada ``page`` di
  dalamnya: model tidak pernah melihat halaman yang tepat, dan memaksanya
  mengisi nomor halaman sama dengan membiarkannya mengarang nomor yang tidak
  akan pernah ditemukan pembaca. Yang **ada** adalah ``source`` — nama sumber
  yang disalin model apa adanya dari daftar bahan yang memang dibacanya. Itu
  bukan hal yang sama: menyalin satu nama pendek dari daftar berbeda dari
  mengarang nomor halaman, dan tanpa salinan itu pemeriksa fakta tidak punya
  jalan apa pun untuk menunjuk bahan yang dibandingkannya.
* :class:`GateReport` dan :class:`CheckFinding` — **apa yang dicatat sistem**.
  Sumber dan halaman diisi program, dari bukti yang benar-benar dimilikinya.

Vonisnya diterjemahkan menjadi :class:`~domain.chapter.ReviewResult` oleh
:func:`~domain.rules.decide_review` yang sudah ada. Lulus atau tidaknya sebuah
bab tetap diputuskan di satu tempat saja; berkas ini menambah **alasan**, bukan
jalan kedua.

Satu keputusan di sini menyimpang dari bacaan harfiah §21, dan disengaja.
§21 berbunyi "jika ``supported = false`` → chapter kembali ke Writer untuk
revisi". Diterapkan apa adanya, aturan itu tidak pernah berhenti: penulis dan
pemeriksa membaca bahan yang **sama**, dan penulis memang **diwajibkan**
mencatat setiap klaim yang tidak dapat didukungnya di ``unresolved_claims``
(§34). Setiap revisi akan menghasilkan penandaan yang sama, dan bab itu berputar
selamanya di antara dua gate. Karena itu yang ditolak gate adalah klaim yang
tidak didukung **dan** tidak dinyatakan babnya sendiri — sebuah perbedaan yang
hanya dapat dibuat program, karena ia membandingkan draf dengan dirinya sendiri,
bukan dengan bahan. Bab yang jujur tetap melihat temuannya di laporan
(``describe``), tetapi tidak dihukum karenanya. Lihat
:meth:`GateReport.silent_failures`.

Satu pemeriksa — konsistensi §24 — meminta satu hal lagi kepada model, dan
karena itu punya vonisnya sendiri: :class:`ConsistencyVerdict` menambahkan
``glossary`` pada bentuk yang sama. Ia tetap tinggal di berkas ini supaya kosakata
keempat pemeriksa tetap satu, bukan supaya bentuknya dipaksa seragam.
"""

from __future__ import annotations

from typing import Sequence

from pydantic import Field

from domain.base import FrozenModel
from domain.chapter import Evidence, ReviewVerdict
from domain.graph import TerminologyEntry


class CheckJudgement(FrozenModel):
    """Penilaian model atas **satu subjek** — klaim, kunci sitasi, atau istilah.

    ``subject`` adalah pengait antara penilaian ini dan hal yang dinilai, dan
    karena itu ia ditulis apa adanya dari draf. Model yang memparafrase subjeknya
    memutus kait itu, dan temuannya berhenti dapat ditindaklanjuti penulis:
    "klaim tentang kompleksitas" tidak menunjuk kalimat mana pun untuk diperbaiki.

    ``source`` adalah pengait yang **kedua**, dan hanya berarti bagi pemeriksa
    yang subjeknya bukan nama sumber: pemeriksa fakta menilai *klaim*, sehingga
    tanpa field ini tidak ada satu pun cara mengetahui bahan mana yang
    dibandingkan — dan temuan "klaim ini tidak didukung" berhenti dapat
    diperiksa pembacanya. Ia sengaja dibiarkan kosong-boleh-isi, berbeda dari
    ``subject`` yang wajib: pemeriksa sitasi sudah menaruh nama sumber di
    ``subject``, dan memaksanya menuliskannya dua kali hanya menambah satu
    tempat untuk tidak konsisten.
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
    source: str = Field(
        default="",
        description=(
            "Sumber yang dibandingkan dengan subjek ini, disalin apa adanya dari "
            "daftar bahan yang diberikan. Kosongkan bila subjeknya sendiri sudah "
            "berupa sumber, atau bila tidak ada bahan yang dapat ditunjuk. "
            "Halamannya diisi program, bukan oleh Anda."
        ),
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


class ConsistencyVerdict(CheckVerdict):
    """Vonis pemeriksa konsistensi (§24), beserta glosarium bab yang diperiksanya.

    Satu-satunya pemeriksa yang keluarannya lebih dari sekadar temuan, dan
    alasannya bukan kenyamanan: §24 membandingkan bab baru dengan **seluruh**
    state buku, sehingga satu-satunya tempat istilah buku dapat bertambah adalah
    pemeriksaan ini. Tanpa ``glossary``, ``BookState.terminology`` akan tetap
    kosong selamanya — dan contoh §24 sendiri (*finite automaton* di bab 2,
    *finite-state machine* di bab 7) tidak akan pernah dapat ditemukan, sebab bab
    ketujuh tidak punya apa pun untuk dibandingkan.

    Yang menuliskannya ke memori bersama tetap program, bukan model: glosarium ini
    hanya masuk ke ``BookState`` bila gate-nya **lulus** — lihat
    :func:`~domain.rules.approved_terminology`.
    """

    glossary: tuple[TerminologyEntry, ...] = ()


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
    declared: bool = Field(
        default=False,
        description=(
            "Bab ini sendiri sudah menyatakan subjek ini belum berbukti "
            "(``ChapterDraft.unresolved_claims``) — diisi program, bukan model."
        ),
    )

    @classmethod
    def of(
        cls,
        judgement: CheckJudgement,
        *,
        evidence: Sequence[Evidence] = (),
        declared: frozenset[str] = frozenset(),
    ) -> "CheckFinding":
        """Lengkapi penilaian model dengan sumber dan halaman (MURNI).

        Yang dijadikan kunci pencarian adalah ``judgement.source`` bila model
        mengisinya, dan ``judgement.subject`` bila tidak. Dua-duanya masuk akal
        untuk pemeriksa yang berbeda: pemeriksa sitasi menjadikan kunci rujukan
        sebagai subjeknya, sedangkan pemeriksa fakta menjadikan klaim sebagai
        subjek dan menyebut bahannya di ``source``. Tanpa cabang ini, salah satu
        dari keduanya selalu kehilangan halamannya.

        Pencocokannya **persis**, mengikuti :func:`~domain.rules.citations_allowed_by`:
        subjek yang tidak menunjuk satu pun bukti tetap dicatat, tanpa sumber.
        Menebak sumber terdekat akan menghasilkan temuan yang menunjuk halaman
        yang salah — persis jenis kesalahan yang tidak dapat ditemukan manusia.

        ``declared`` adalah himpunan subjek yang **sudah dinyatakan** bab ini
        sendiri belum berbukti. Model tidak menentukannya dan tidak dapat
        menentukannya: ia hanya melihat draf, sedangkan yang dibandingkan di sini
        adalah draf dengan dirinya sendiri. Ia sengaja dicocokkan dengan
        ``subject`` — yang dinyatakan penulis adalah klaimnya, bukan sumbernya.
        """
        match = evidence_for(judgement.source.strip() or judgement.subject, evidence)
        return cls(
            subject=judgement.subject,
            ok=judgement.ok,
            detail=judgement.detail,
            source=match.source if match is not None else "",
            page=match.page if match is not None else None,
            declared=judgement.subject.strip() in declared,
        )

    def describe(self) -> str:
        """Satu baris untuk ``feedback``: apa yang salah, dan di mana (MURNI).

        Ditulis untuk temuan yang **gagal**; temuan yang lolos tidak perlu
        diceritakan lagi kepada penulis, dan menceritakannya justru mengubur
        yang harus dikerjakan di antara yang sudah beres.

        Temuan yang sudah dinyatakan bab ini sendiri tetap disebutkan, dengan
        tanda bahwa babnya memang sudah jujur. Pembaca laporan berhak melihatnya;
        yang tidak boleh adalah memperlakukannya sama dengan klaim yang
        disembunyikan.
        """
        where = ""
        if self.source:
            where = f" [{self.source}" + (f" hlm. {self.page}]" if self.page is not None else "]")
        reason = self.detail.strip() or "tidak memenuhi syarat"
        if self.declared:
            return f"{self.subject}{where}: {reason} (sudah dinyatakan bab ini)"
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

    def silent_failures(self) -> tuple[CheckFinding, ...]:
        """Temuan gagal yang **tidak** dinyatakan bab ini sendiri (MURNI).

        Perbedaan antara "bab yang salah" dan "bab yang jujur tentang apa yang
        tidak dapat didukungnya" hanya dapat dibuat sistem, bukan pemeriksa:
        ``unresolved_claims`` adalah permukaan jujur §34, dan menghukumnya akan
        membuat penulis menyembunyikan klaim alih-alih menandainya — persis
        kebalikan dari yang diminta ``writer.revise.md`` sendiri.

        Karena itu bab yang **sudah** menandai klaimnya tetap melaporkan temuan
        itu (lihat :meth:`CheckFinding.describe`), tetapi tidak ditolak karena
        temuan itu. Yang ditolak adalah klaim yang tidak didukung **dan** tidak
        dinyatakan — itulah §21 yang sungguh dapat ditindaklanjuti.

        Untuk pemeriksa selain pemeriksa fakta — §22–§24 — ``declared`` selalu
        bernilai ``False``, sehingga fungsi ini identik dengan
        :meth:`failed`. Perilakunya sengaja tidak berubah di sana.
        """
        return tuple(
            finding for finding in self.findings if not finding.ok and not finding.declared
        )

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

        Yang dihitung adalah :meth:`silent_failures`, bukan seluruh
        :meth:`failed`: temuan yang sudah dinyatakan babnya sendiri tetap
        dilaporkan, tetapi tidak membatalkan persetujuan — sebab bila
        membatalkannya, tidak ada draf yang dapat lolos selama materinya memang
        belum menopang, dan revisi berikutnya hanya akan menghasilkan kalimat
        yang sama dengan tanda yang sama.
        """
        failing = self.silent_failures()
        if not self.approved or not failing:
            return self
        return self.model_copy(
            update={
                "approved": False,
                "feedback": (
                    *self.feedback,
                    f"{len(failing)} temuan tidak memenuhi syarat dan tidak dinyatakan "
                    "bab ini; persetujuan pemeriksa diabaikan oleh sistem.",
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
    "ConsistencyVerdict",
    "GateReport",
    "evidence_for",
    "unexamined",
]
