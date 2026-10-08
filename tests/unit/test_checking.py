"""Vonis pemeriksa — bentuknya, dan siapa yang mengisi apa (§21–§24).

Dua hal yang dijaga berkas ini, dan keduanya adalah keputusan yang tidak terlihat
dari tipe saja:

* **Model tidak pernah ditanya hal yang tidak diketahuinya.** ``source`` dan
  ``page`` tidak ada di model keluaran, karena ``strict_schema`` menandai semua
  properti wajib — memintanya sama dengan meminta model mengarang halaman.
  Yang mengisinya adalah program, dari bukti yang benar-benar dimilikinya.
* **Vonis yang bertentangan dengan temuannya sendiri diturunkan sistem.**
  Pemeriksa yang menandai sebuah rujukan tidak didukung lalu tetap menyetujui
  babnya telah membatalkan pekerjaannya sendiri; tanpa aturan itu, bab tersebut
  lolos tanpa satu pun tanda.

Vonis akhirnya tetap keluar lewat :func:`~domain.rules.decide_review` yang sudah
ada — berkas ini membuktikan bahwa jalurnya memang satu, bukan dua.
"""

from __future__ import annotations

from domain.chapter import Evidence
from domain.checking import (
    CheckFinding,
    CheckJudgement,
    CheckVerdict,
    GateReport,
    evidence_for,
    unexamined,
)
from domain.rules import decide_review
from domain.structured import strict_schema

CORMEN = "Cormen, Introduction to Algorithms, 4th ed."
AHO = "Aho, Compilers, 2nd ed."

#: Dua potongan bukti dari **satu** sumber, dengan halaman berbeda. Bentuk ini
#: yang membuat tes halaman bermakna: yang diuji bukan sekadar "ada halaman",
#: melainkan halaman mana yang dipilih.
EVIDENCE = (
    Evidence(
        source=CORMEN,
        page=45,
        section="3.1",
        text="Notasi asimtotik menggambarkan laju pertumbuhan.",
    ),
    Evidence(
        source=CORMEN,
        page=46,
        section="3.2",
        text="O(n) berarti waktu eksekusi tumbuh linear terhadap masukan.",
    ),
    Evidence(source=AHO, page=12, text="Analisis leksikal memecah masukan."),
)


def finding(subject: str, ok: bool, detail: str = "") -> CheckJudgement:
    """Penilaian model atas satu subjek."""
    return CheckJudgement(subject=subject, ok=ok, detail=detail)


# ---------------------------------------------------------------------------
# Yang diminta dari model — dan yang sengaja tidak
# ---------------------------------------------------------------------------
def test_the_model_is_never_asked_for_a_source_or_a_page() -> None:
    """``strict_schema`` mewajibkan semua properti; yang tidak ditanyakan tidak dikarang.

    Model tidak pernah melihat halaman yang tepat. Memaksa field itu ada di skema
    berarti mengundang nomor halaman karangan — dan halaman yang salah adalah
    kesalahan yang tidak akan pernah ditemukan pembaca.
    """
    assert set(strict_schema(CheckVerdict)["properties"]) == {
        "approved",
        "score",
        "feedback",
        "findings",
    }
    assert set(strict_schema(CheckJudgement)["properties"]) == {"subject", "ok", "detail"}


def test_the_model_is_not_asked_for_the_gate_name_or_the_skipped_flag() -> None:
    """Keduanya milik sistem: satu diketahui gate-nya, satu ditentukan gate-nya."""
    properties = set(strict_schema(GateReport)["properties"])

    assert "gate" not in properties
    assert "skipped" not in properties


# ---------------------------------------------------------------------------
# evidence_for — pencocokan persis, bukan yang terdekat
# ---------------------------------------------------------------------------
def test_the_first_evidence_of_a_source_is_the_one_that_is_used() -> None:
    """Dua potongan dari satu sumber: yang pertama itu bagian awalnya."""
    match = evidence_for(CORMEN, EVIDENCE)

    assert match is not None
    assert match.page == 45


def test_surrounding_whitespace_does_not_break_the_match() -> None:
    """Spasi di ujung bukan perbedaan sumber; menolaknya akan menghukum hal yang benar."""
    match = evidence_for(f"  {AHO}  ", EVIDENCE)

    assert match is not None
    assert match.page == 12


def test_a_subject_that_matches_nothing_returns_none() -> None:
    """``None`` memang jawaban yang benar: mengarang kaitannya lebih buruk."""
    assert evidence_for("Knuth, The Art of Computer Programming", EVIDENCE) is None


def test_an_empty_subject_matches_nothing() -> None:
    """Subjek kosong bukan sumber; ia hanya kesalahan yang tidak boleh cocok dengan apa pun."""
    assert evidence_for("", EVIDENCE) is None


def test_a_partial_source_name_does_not_match() -> None:
    """Pencocokannya persis: "Cormen" bukan "Cormen, Introduction to Algorithms"."""
    assert evidence_for("Cormen", EVIDENCE) is None


# ---------------------------------------------------------------------------
# CheckFinding.of — program melengkapi apa yang tidak diketahui model
# ---------------------------------------------------------------------------
def test_a_finding_about_a_source_is_completed_with_its_page() -> None:
    completed = CheckFinding.of(finding(CORMEN, ok=False, detail="Beda topik."), evidence=EVIDENCE)

    assert completed.ok is False
    assert completed.source == CORMEN
    assert completed.page == 45
    assert completed.detail == "Beda topik."


def test_a_finding_without_matching_evidence_keeps_the_subject_but_no_source() -> None:
    """Temuan yang tidak menunjuk bukti tetap dicatat — tanpa sumber yang dikarang."""
    completed = CheckFinding.of(
        finding("Klaim tentang kompleksitas ruang", ok=False), evidence=EVIDENCE
    )

    assert completed.subject == "Klaim tentang kompleksitas ruang"
    assert completed.source == ""
    assert completed.page is None


# ---------------------------------------------------------------------------
# describe — satu baris yang dapat dikerjakan penulis
# ---------------------------------------------------------------------------
def test_describe_names_the_source_and_the_page() -> None:
    """Penulis harus tahu harus membuka apa — bukan hanya bahwa ada yang salah."""
    completed = CheckFinding(
        subject=CORMEN,
        ok=False,
        detail="Bahan hanya membahas notasi, bukan kompleksitas ruang.",
        source=CORMEN,
        page=45,
    )

    line = completed.describe()

    assert line.startswith(f"{CORMEN} [Cormen")
    assert "hlm. 45]" in line
    assert "kompleksitas ruang" in line


def test_describe_without_a_page_does_not_invent_one() -> None:
    completed = CheckFinding(subject="Aho", ok=False, detail="Tidak dipakai.", source="Aho")

    assert completed.describe() == "Aho [Aho]: Tidak dipakai."


def test_describe_without_a_source_is_just_the_subject_and_the_reason() -> None:
    completed = CheckFinding(subject="Klaim tanpa bukti", ok=False)

    assert completed.describe() == "Klaim tanpa bukti: tidak memenuhi syarat"


# ---------------------------------------------------------------------------
# GateReport — vonis ditegakkan terhadap temuannya sendiri
# ---------------------------------------------------------------------------
def test_failed_returns_only_the_findings_that_are_not_ok() -> None:
    report = GateReport(
        approved=False,
        findings=(
            CheckFinding(subject="a", ok=True),
            CheckFinding(subject="b", ok=False),
            CheckFinding(subject="c", ok=False),
        ),
    )

    assert tuple(item.subject for item in report.failed()) == ("b", "c")


def test_a_report_that_approves_its_own_failing_finding_is_turned_down() -> None:
    """Persetujuan model yang bertentangan dengan temuannya sendiri tidak menolong bab ini."""
    report = GateReport(
        approved=True,
        score=9,
        findings=(CheckFinding(subject="Cormen", ok=False, detail="Beda topik."),),
    )

    enforced = report.enforce_findings()

    assert enforced.approved is False
    assert enforced.score == 9, "angkanya tidak diubah; yang diturunkan adalah vonisnya"
    assert any("diabaikan oleh sistem" in note for note in enforced.feedback)


def test_a_report_with_no_failing_finding_is_left_alone() -> None:
    report = GateReport(
        approved=True,
        score=9,
        feedback=("Semua rujukan ditopang bahannya.",),
        findings=(CheckFinding(subject="Cormen", ok=True),),
    )

    assert report.enforce_findings() == report


def test_a_report_that_already_rejects_is_not_annotated_twice() -> None:
    """Catatan "diabaikan oleh sistem" hanya benar bila sistem memang mengabaikan sesuatu."""
    report = GateReport(approved=False, findings=(CheckFinding(subject="X", ok=False),))

    assert report.enforce_findings().feedback == ()


# ---------------------------------------------------------------------------
# Jalur vonis — satu, bukan dua
# ---------------------------------------------------------------------------
def test_the_report_becomes_a_review_verdict_the_existing_rule_understands() -> None:
    """``decide_review`` tetap satu-satunya tempat lulus/tidaknya sebuah bab diputuskan."""
    report = GateReport(approved=True, score=6, feedback=("Bahan hanya menopang sebagian.",))

    result = decide_review(report.verdict(), gate="citation_checker", threshold=7)

    assert result.gate == "citation_checker"
    assert result.approved is False
    assert result.score == 6
    assert result.skipped is False
    assert "Bahan hanya menopang sebagian." in result.feedback


def test_a_rejected_report_stays_rejected_through_the_rule() -> None:
    report = GateReport(approved=False, score=0, feedback=("Rujukan asing.",))

    result = decide_review(report.verdict(), gate="citation_checker", threshold=7)

    assert result.approved is False
    assert result.feedback == ("Rujukan asing.",)


# ---------------------------------------------------------------------------
# unexamined — "tidak ada temuan" bukan "sudah diperiksa"
# ---------------------------------------------------------------------------
def test_subjects_that_no_finding_mentions_are_reported() -> None:
    findings = (CheckFinding(subject=CORMEN, ok=True),)

    assert unexamined([CORMEN, AHO], findings) == (AHO,)


def test_a_finding_that_mentions_a_different_subject_does_not_count_as_coverage() -> None:
    findings = (CheckFinding(subject="sesuatu yang lain", ok=True),)

    assert unexamined([AHO], findings) == (AHO,)


def test_no_findings_at_all_means_nothing_was_examined() -> None:
    """Model yang menyatakan "semuanya baik" tanpa menyebut satu subjek belum memeriksa apa pun."""
    assert unexamined([AHO, CORMEN], ()) == (AHO, CORMEN)


def test_repeated_subjects_are_reported_once_each() -> None:
    assert unexamined([AHO, AHO, AHO], ()) == (AHO,)


def test_blank_subjects_are_not_reported() -> None:
    assert unexamined(["", "   "], ()) == ()


def test_whitespace_around_a_subject_does_not_make_it_unexamined() -> None:
    findings = (CheckFinding(subject=" Aho ", ok=True),)

    assert unexamined(["Aho"], findings) == ()


# ---------------------------------------------------------------------------
# Kemurnian
# ---------------------------------------------------------------------------
def test_none_of_this_mutates_its_inputs() -> None:
    judgement = finding(AHO, ok=False, detail="Tidak dipakai.")
    report = GateReport(
        approved=True,
        score=9,
        findings=(CheckFinding.of(judgement, evidence=EVIDENCE),),
    )
    judgement_snapshot = judgement.model_dump()
    report_snapshot = report.model_dump()

    CheckFinding.of(judgement, evidence=EVIDENCE).describe()
    report.failed()
    report.enforce_findings()
    report.verdict()
    unexamined([AHO], report.findings)

    assert judgement.model_dump() == judgement_snapshot
    assert report.model_dump() == report_snapshot
