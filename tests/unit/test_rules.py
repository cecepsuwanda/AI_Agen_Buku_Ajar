"""Aturan resume dan pemulihan — §28, §35.

Berkas ini ada karena satu sebab yang konkret: tiga fungsi di sini
(:func:`~domain.rules.pending_numbers`, :func:`~domain.rules.reset_for_rerun`,
:func:`~domain.rules.reopen_failed`) sebelumnya **tidak punya tes sama sekali**,
dan justru di situlah dua bagian kode saling bertentangan tanpa ada yang tahu.

``pending_numbers`` menyatakan dengan tegas bahwa bab berstatus ``FAILED`` harus
dikerjakan ulang; tabel §27 tidak punya baris yang keluar dari ``FAILED``. Kedua
pernyataan itu benar masing-masing, dan bersamanya membuat ``run`` yang kedua
mati dengan ``IllegalTransitionError`` pada setiap buku yang pernah gagal
(terjadi sungguhan, 2026-10-07). Tes di sini menguji **keduanya bersama-sama** —
sebab itulah satu-satunya cara cacat seperti itu terlihat.

Fungsi ``rules`` yang lain (``reconcile_*``, ``enforce_draft_contract``,
``citations_allowed_by``, ``decide_review``) diuji lewat agent yang memakainya,
di ``tests/agent/``, karena di sana perilakunya punya konteks. Dua pengecualian
adalah :func:`~domain.rules.foreign_citations` dan
:func:`~domain.rules.remembered_citations` — keduanya diletakkan di sini karena
yang diuji bukan perilaku sebuah agent, melainkan **predikat** yang harus berlaku
sama di mana pun ia dipakai.
"""

from __future__ import annotations

import pytest

from domain.book import BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ResearchPackage, ReviewResult
from domain.enums import ChapterEvent, ChapterStatus
from domain.errors import ChapterNotPlannedError, InvalidGateError
from domain.rules import (
    find_chapter,
    foreign_citations,
    pending_numbers,
    progress_status,
    remembered_citations,
    reopen_failed,
    reset_for_rerun,
    validate_gates,
    with_gate_result,
)
from domain.state import BookState
from domain.transitions import can_advance, transition

SPEC = BookSpec(
    title="Buku Uji",
    chapters=tuple(
        ChapterSpec(number=n, title=f"Bab {n}", sections=("Pengantar",)) for n in range(1, 4)
    ),
)

RESEARCH = ResearchPackage.empty()
DRAFT = ChapterDraft(title="Draf Bab")


def make_record(number: int, status: ChapterStatus, **overrides: object) -> ChapterRecord:
    """Record dengan data yang dapat diatur per tes."""
    base: dict[str, object] = {"number": number, "status": status}
    base.update(overrides)
    return ChapterRecord.model_validate(base)


# ---------------------------------------------------------------------------
# Pencarian
# ---------------------------------------------------------------------------
def test_finding_a_chapter_that_was_never_planned_says_what_exists() -> None:
    """Bab yang tidak ada adalah kesalahan pemakaian, dan pesannya harus menolong.

    ``run --chapter 99`` adalah salah ketik yang lazim, dan satu-satunya hal yang
    membuatnya dapat diperbaiki sendiri adalah daftar bab yang benar-benar ada.
    Karena itu yang diperiksa di sini adalah **field**-nya, bukan kalimatnya: teks
    pesan boleh berubah, janji bahwa nomor dan daftar tersedia tidak.
    """
    with pytest.raises(ChapterNotPlannedError) as excinfo:
        find_chapter(SPEC, 99)

    assert excinfo.value.number == 99
    assert excinfo.value.available == (1, 2, 3)


# ---------------------------------------------------------------------------
# pending_numbers — apa yang masih perlu dikerjakan
# ---------------------------------------------------------------------------
def test_approved_chapters_are_skipped() -> None:
    """Inti resume §28: yang selesai dilewati, sisanya dikerjakan."""
    records = (
        make_record(1, ChapterStatus.APPROVED),
        make_record(2, ChapterStatus.PLANNED),
        make_record(3, ChapterStatus.APPROVED),
    )

    assert pending_numbers(SPEC, records) == (2,)


def test_failed_chapters_are_included_not_skipped() -> None:
    """"Berhenti karena rusak" adalah keadaan yang **paling** perlu dikerjakan ulang.

    Menyamakan ``FAILED`` dengan "selesai" akan membuat ``run`` yang dijalankan
    dua kali melewati tepat bab-bab yang gagal — diam-diam, dan tanpa satu pun
    tanda bahwa bukunya tidak lengkap.
    """
    records = (
        make_record(1, ChapterStatus.FAILED),
        make_record(2, ChapterStatus.APPROVED),
        make_record(3, ChapterStatus.APPROVED),
    )

    assert pending_numbers(SPEC, records) == (1,)


def test_chapters_without_a_record_are_pending() -> None:
    """Buku yang baru direncanakan belum punya record bab sama sekali."""
    assert pending_numbers(SPEC, ()) == (1, 2, 3)


def test_force_puts_everything_back_on_the_list() -> None:
    """``--force`` mengabaikan status sepenuhnya."""
    records = tuple(make_record(n, ChapterStatus.APPROVED) for n in (1, 2, 3))

    assert pending_numbers(SPEC, records, force=True) == (1, 2, 3)


def test_the_result_is_ordered_by_chapter_number() -> None:
    """Urutan berkas yang diberikan tidak menentukan urutan pengerjaan."""
    records = (make_record(3, ChapterStatus.APPROVED), make_record(1, ChapterStatus.APPROVED))

    assert pending_numbers(SPEC, records) == (2,)


# ---------------------------------------------------------------------------
# progress_status — status yang konsisten dengan data
# ---------------------------------------------------------------------------
def test_progress_status_follows_the_evidence_in_the_record() -> None:
    """Yang ditanyakan adalah "sejauh mana datanya", bukan "sejauh mana seharusnya"."""
    assert progress_status(make_record(1, ChapterStatus.FAILED)) is ChapterStatus.PLANNED
    assert (
        progress_status(make_record(1, ChapterStatus.FAILED, research=RESEARCH))
        is ChapterStatus.RESEARCHED
    )
    assert (
        progress_status(make_record(1, ChapterStatus.FAILED, research=RESEARCH, draft=DRAFT))
        is ChapterStatus.DRAFTED
    )


def test_a_draft_outranks_earlier_progress_even_without_research() -> None:
    """Draf adalah bukti paling kuat yang dimiliki record."""
    assert (
        progress_status(make_record(1, ChapterStatus.FAILED, draft=DRAFT))
        is ChapterStatus.DRAFTED
    )


# ---------------------------------------------------------------------------
# reopen_failed — membuka kembali tanpa membuang pekerjaan
# ---------------------------------------------------------------------------
def test_reopening_keeps_the_research_the_plan_and_the_draft() -> None:
    """Berbeda dari ``--force``: tidak ada pekerjaan yang dibuang."""
    spec = ChapterSpec(number=1, title="Bab 1")
    failed = make_record(
        1, ChapterStatus.FAILED, spec=spec, research=RESEARCH, draft=DRAFT, revision=2
    )

    reopened = reopen_failed(failed)

    assert reopened.research == RESEARCH
    assert reopened.spec == spec
    assert reopened.draft == DRAFT
    assert reopened.revision == 2, "penghitung revisi adalah riwayat, bukan pekerjaan"


def test_reopening_clears_the_previous_attempts_error() -> None:
    """``error`` yang tertinggal akan terbaca sebagai kegagalan percobaan yang baru."""
    reopened = reopen_failed(make_record(1, ChapterStatus.FAILED, error="jejak lama"))

    assert reopened.error is None


def test_reopening_a_chapter_that_did_not_fail_changes_nothing() -> None:
    """Fungsi ini idempoten terhadap status: ia tidak menyentuh bab yang sehat.

    Penting karena pemanggilnya tidak perlu memeriksa status lebih dulu — dan
    pemanggil yang harus memeriksa lebih dulu adalah tempat kesalahan berikutnya
    bersembunyi.
    """
    healthy = make_record(1, ChapterStatus.REVIEWED, research=RESEARCH, draft=DRAFT, error="x")

    assert reopen_failed(healthy) is healthy


def test_reset_for_rerun_discards_what_reopening_preserves() -> None:
    """Kontras yang menjadi alasan kedua fungsi ini ada.

    ``--force`` adalah pilihan sadar untuk membayar ulang seluruh pekerjaan bab;
    mengerjakan ulang bab yang gagal bukan.
    """
    spec = ChapterSpec(number=1, title="Bab 1")
    failed = make_record(
        1,
        ChapterStatus.FAILED,
        spec=spec,
        research=RESEARCH,
        draft=DRAFT,
        revision=2,
        markdown_path="output/chapters/chapter01.md",
        error="gagal",
    )

    reset = reset_for_rerun(failed)

    assert reset.status is ChapterStatus.PLANNED
    assert reset.research is None
    assert reset.draft is None
    assert reset.reviews == ()
    assert reset.revision == 0
    assert reset.markdown_path is None
    assert reset.error is None
    assert reset.spec == spec, "spesifikasi milik BookSpec, bukan hasil kerja bab ini"


def test_reopening_does_not_silently_invent_gate_progress() -> None:
    """Vonis gate tidak dipulihkan, dan itu pertukaran yang dipilih secara sadar.

    Sebuah bab yang gagal **setelah** peninjau meloloskannya akan dibuka kembali
    sebagai ``DRAFTED``, sehingga peninjauannya berjalan lagi. Yang tidak dapat
    dipulihkan adalah **nomor** tahap yang sudah dilewati — ``ReviewResult`` tidak
    membawa status yang dihasilkannya, jadi tidak ada tempat untuk menyimpannya.

    Mengulang penilaian jauh lebih murah daripada mengulang penulisan, dan inilah
    harga yang dibayar agar bab yang gagal tidak berubah menjadi buntu.
    """
    approved_review = ReviewResult(gate="reviewer", approved=True, score=9)
    failed = make_record(
        1,
        ChapterStatus.FAILED,
        research=RESEARCH,
        draft=DRAFT,
        reviews=(approved_review,),
    )

    reopened = reopen_failed(failed)

    assert reopened.status is ChapterStatus.DRAFTED, "bukan REVIEWED — lihat docstring"
    assert reopened.reviews == (approved_review,), "vonis lama tidak dihapus"


# ---------------------------------------------------------------------------
# Invarian penghubung — inilah yang menangkap kontradiksinya
# ---------------------------------------------------------------------------
#
# Yang menentukan bukan statusnya sendiri, melainkan apakah **langkah
# berikutnya** sah dari status itu. Untuk bab yang gagal, langkah berikutnya
# ditentukan oleh data yang tersisa, dan itulah tiga bentuk yang mungkin —
# persis urutan ``run_chapter``: ``_ensure_research`` lalu ``_ensure_draft``.
#
# Bentuk keempat — draf tanpa riset — sengaja tidak diikutsertakan, karena
# pipeline tidak dapat menghasilkannya: ``with_draft`` hanya sah dari
# ``RESEARCHED`` dan ``REVISION``, dan keduanya selalu dilewati lewat
# ``with_research``. Satu-satunya cara bentuk itu muncul adalah menyunting
# ``state/chapterNN.json`` dengan tangan; bila itu terjadi, ``run``
# menandainya gagal, dan itu memang hasil yang benar untuk state yang tidak
# konsisten.
REACHABLE_FAILED_SHAPES = [
    (None, None),
    (RESEARCH, None),
    (RESEARCH, DRAFT),
]


@pytest.mark.parametrize(("research", "draft"), REACHABLE_FAILED_SHAPES)
def test_a_reopened_chapter_survives_the_next_steps_the_director_takes(
    research: ResearchPackage | None,
    draft: ChapterDraft | None,
) -> None:
    """Janji ``pending_numbers`` harus dapat ditepati oleh pipeline.

    Bab yang masuk daftar kerja harus benar-benar dapat **dilanjutkan**. Versi
    sebelumnya lolos seluruh suite sambil melanggar tepat janji ini: statusnya
    tetap ``FAILED``, dan ``run`` kedua mati pada transisi pertama.

    Yang dijalankan di sini adalah dua langkah pertama :meth:`BookDirector.
    run_chapter` — riset lalu draf — bukan daftar transisi yang dikarang tes.
    Bila urutan pipeline berubah, tes ini ikut berubah; itulah gunanya.
    """
    failed = make_record(1, ChapterStatus.FAILED, research=research, draft=draft)
    status = reopen_failed(failed).status

    # Langkah pertama — pakai :meth:`BookDirector._ensure_research`.
    if research is None:
        status = transition(status, ChapterEvent.RESEARCH)

    # Langkah kedua — pakai :meth:`BookDirector._produce_draft`, yang dipanggil
    # hanya bila drafnya belum ada; kalau sudah ada, penulis tidak dipanggil lagi.
    if draft is None:
        status = transition(status, ChapterEvent.DRAFT)
    else:
        assert status is ChapterStatus.DRAFTED, (
            "draf yang masih baik tidak boleh memaksa penulisan ulang"
        )

    # Dan dari sana gate harus dapat memajukannya — bukan buntu.
    assert can_advance(status, ChapterStatus.REVIEWED)


# ---------------------------------------------------------------------------
# Penjagaan gerbang — gagal sebelum satu token pun dibakar
# ---------------------------------------------------------------------------
class _Gate:
    """Gate minimal untuk menguji penjagaan, bukan penilaian.

    ``evaluate`` sengaja melempar: kedua penjagaan di bawah harus menolak
    **sebelum** gate mana pun dinilai. Bila salah satunya keliru memanggil
    ``evaluate``, tesnya gagal dengan pesan yang jelas alih-alih diam-diam lulus.
    """

    def __init__(self, name: str, produces: ChapterStatus) -> None:
        self.name = name
        self.produces = produces

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        raise AssertionError("penjagaan seharusnya menolak sebelum evaluate dipanggil")


def test_a_gate_that_marches_backward_is_refused_at_startup() -> None:
    """Konfigurasi gate yang salah harus gagal sebelum buku pertama ditulis.

    ``validate_gates`` dipanggil composition root saat start. Gate yang
    ``produces``-nya berada **di belakang** ``DRAFTED`` akan memundurkan status
    di tengah proses — dan itu jauh lebih mahal ditemukan saat delapan bab sudah
    setengah jalan daripada sebelum satu token pun dibakar.
    """
    with pytest.raises(InvalidGateError) as excinfo:
        validate_gates((_Gate("mundur", ChapterStatus.PLANNED),))

    assert excinfo.value.gate == "mundur"
    assert excinfo.value.target is ChapterStatus.PLANNED


def test_a_gate_that_claims_approval_without_advancing_is_refused() -> None:
    """Vonis "lulus" tidak boleh memindahkan bab ke tempat yang tidak sah.

    Inilah bedanya lulus dari *sah*. Sebuah gate boleh mengembalikan
    ``approved=True``; yang menentukan status adalah ``produces``, dan bila
    ``produces`` itu bukan langkah maju dari status sekarang, keadaan state
    berikutnya tidak terdefinisi. Lebih baik melempar di sini — di tengah
    proses, dengan nomor bab yang jelas — daripada menulis status yang salah ke
    ``chapterNN.json``.
    """
    record = make_record(1, ChapterStatus.DRAFTED, research=RESEARCH, draft=DRAFT)
    optimistic = ReviewResult(gate="terlalu-optimis", approved=True, score=10)

    with pytest.raises(InvalidGateError) as excinfo:
        with_gate_result(record, optimistic, produces=ChapterStatus.PLANNED)

    assert excinfo.value.gate == "terlalu-optimis"
    assert excinfo.value.source is ChapterStatus.DRAFTED
    assert excinfo.value.target is ChapterStatus.PLANNED


def test_a_refused_gate_is_not_a_guard_against_the_honest_path() -> None:
    """Penjagaan di atas tidak boleh menghalangi kemajuan yang sah.

    Tanpa tes ini, "selalu melempar" akan lolos keduanya.
    """
    record = make_record(1, ChapterStatus.DRAFTED, research=RESEARCH, draft=DRAFT)
    verdict = ReviewResult(gate="reviewer", approved=True, score=9)

    advanced = with_gate_result(record, verdict, produces=ChapterStatus.REVIEWED)

    assert advanced.status is ChapterStatus.REVIEWED
    assert validate_gates((_Gate("reviewer", ChapterStatus.REVIEWED),)) is None


# ---------------------------------------------------------------------------
# enriched_draft — gate boleh memperkaya draf, bukan hanya menilainya (§19, §20)
# ---------------------------------------------------------------------------
def test_an_approved_gate_replaces_the_draft_it_enriched() -> None:
    """Inilah cara gate penulisan menyerahkan hasil kerjanya.

    ``BookDirector`` tidak tahu apa pun tentang Example atau Exercise Agent:
    baginya ini hanyalah "gate yang lulus", dan drafnya sudah berubah.
    """
    enriched = DRAFT.model_copy(update={"examples": ("Contoh dari gate.",)})
    record = make_record(1, ChapterStatus.DRAFTED, research=RESEARCH, draft=DRAFT)
    verdict = ReviewResult(
        gate="example_writer", approved=True, score=10, enriched_draft=enriched
    )

    advanced = with_gate_result(record, verdict, produces=ChapterStatus.EXAMPLES_WRITTEN)

    assert advanced.status is ChapterStatus.EXAMPLES_WRITTEN
    assert advanced.draft == enriched


def test_a_gate_that_only_judges_leaves_the_draft_untouched() -> None:
    """Peninjau tidak mengubah draf; field dengan bawaan ``None`` menjaganya begitu."""
    record = make_record(1, ChapterStatus.DRAFTED, research=RESEARCH, draft=DRAFT)
    verdict = ReviewResult(gate="reviewer", approved=True, score=9)

    advanced = with_gate_result(record, verdict, produces=ChapterStatus.REVIEWED)

    assert advanced.draft == DRAFT


def test_a_rejecting_gate_cannot_install_its_own_draft() -> None:
    """Penolakan berarti "kerjakan ulang" — memasang draf versi gate menutupi itu.

    Bab itu sedang menuju revisi. Draf hasil kerja gate yang ditolak akan menjadi
    "draf terakhir" yang justru tidak pernah disetujui siapa pun.
    """
    enriched = DRAFT.model_copy(update={"examples": ("Contoh dari gate.",)})
    record = make_record(1, ChapterStatus.DRAFTED, research=RESEARCH, draft=DRAFT)
    verdict = ReviewResult(
        gate="pedagogy", approved=False, score=3, enriched_draft=enriched
    )

    rejected = with_gate_result(record, verdict, produces=ChapterStatus.PEDAGOGY_REVIEWED)

    assert rejected.status is ChapterStatus.FAILED_REVIEW
    assert rejected.draft == DRAFT
    assert rejected.revision == 1


# ---------------------------------------------------------------------------
# foreign_citations — pemeriksa menolak di tempat penulis membersihkan (§22)
# ---------------------------------------------------------------------------
def cited(*keys: str) -> ChapterDraft:
    """Draf yang mengutip ``keys``, urut sebagaimana ditulis."""
    return ChapterDraft(title="Draf Bab", citations=keys)


def test_citations_outside_the_allowed_set_are_reported_in_order() -> None:
    draft = cited("Cormen", "Knuth", "Aho", "Knuth")

    assert foreign_citations(draft, allowed=frozenset({"Cormen", "Aho"})) == ("Knuth",)


def test_a_key_cited_three_times_is_still_one_thing_to_fix() -> None:
    """Duplikat dibuang: penulis memperbaiki rujukan, bukan kemunculannya."""
    draft = cited("Knuth", "Knuth", "Knuth")

    assert foreign_citations(draft, allowed=frozenset()) == ("Knuth",)


def test_the_order_of_appearance_is_preserved() -> None:
    """Urutannya mengikuti draf, supaya laporannya dapat dibaca berdampingan dengan babnya."""
    draft = cited("Knuth", "Cormen", "Rusell")

    assert foreign_citations(draft, allowed=frozenset({"Cormen"})) == ("Knuth", "Rusell")


def test_allowed_citations_are_never_reported() -> None:
    """Bila tidak ada yang asing, jawabannya kosong — dan itu yang membuat gate diam."""
    draft = cited("Cormen", "Aho")

    assert foreign_citations(draft, allowed=frozenset({"Cormen", "Aho"})) == ()


def test_an_empty_allowed_set_makes_every_citation_foreign() -> None:
    """Penulis yang bekerja tanpa knowledge base tidak boleh mengutip apa pun (§17)."""
    assert foreign_citations(cited("Cormen"), allowed=frozenset()) == ("Cormen",)


def test_blank_citation_entries_are_not_foreign_keys() -> None:
    """Entri kosong adalah kotoran, bukan rujukan asing — dan tidak dilaporkan sebagai rujukan."""
    draft = cited("", "   ")

    assert foreign_citations(draft, allowed=frozenset()) == ()


def test_whitespace_around_a_key_does_not_make_it_foreign() -> None:
    """Sama seperti pencocokan bukti di ``domain.checking``: spasi di ujung bukan perbedaan."""
    draft = cited("  Cormen  ")

    assert foreign_citations(draft, allowed=frozenset({"Cormen"})) == ()


def test_a_draft_without_citations_has_nothing_foreign() -> None:
    assert foreign_citations(DRAFT, allowed=frozenset()) == ()


# ---------------------------------------------------------------------------
# remembered_citations — yang membuat §22 dapat ditegakkan lintas bab
# ---------------------------------------------------------------------------
def test_the_sources_of_the_research_are_remembered() -> None:
    research = ResearchPackage(evidence=(), sources=("Cormen",), degraded=False)

    assert remembered_citations({}, research) == {"Cormen": "Cormen"}


def test_the_order_of_appearance_is_preserved_with_new_sources_last() -> None:
    """Buku yang sudah tercatat tidak berpindah tempat karena bab baru selesai."""
    existing = {"Aho": "Aho"}
    research = ResearchPackage(evidence=(), sources=("Cormen", "Knuth"), degraded=False)

    assert tuple(remembered_citations(existing, research)) == ("Aho", "Cormen", "Knuth")


def test_a_source_already_remembered_is_not_duplicated() -> None:
    existing = {"Cormen": "Cormen"}
    research = ResearchPackage(evidence=(), sources=("Cormen",), degraded=False)

    remembered = remembered_citations(existing, research)

    assert tuple(remembered) == ("Cormen",)


def test_a_degraded_package_adds_nothing_and_returns_the_same_object() -> None:
    """Paket terdegradasi memang tidak menemukan apa pun; menambah entri berarti mengarang.

    Objek yang sama dikembalikan — bukan sekadar salinan yang kebetulan kosong —
    supaya pemanggilnya dapat melewati penulisan ``state/book.json`` sepenuhnya.
    """
    existing = {"Aho": "Aho"}

    assert remembered_citations(existing, ResearchPackage.empty()) is existing


def test_blank_source_names_are_not_remembered() -> None:
    """Nama kosong akan menjadi kunci kosong di ``book.citations`` — dan mengizinkan apa saja."""
    research = ResearchPackage(evidence=(), sources=("", "   "), degraded=False)

    assert remembered_citations({}, research) == {}


def test_whitespace_around_a_source_name_is_trimmed_before_it_is_stored() -> None:
    """Kunci dan nilainya sama-sama dirapikan, agar cocok dengan yang ditulis penulis."""
    research = ResearchPackage(evidence=(), sources=("  Cormen  ",), degraded=False)

    assert remembered_citations({}, research) == {"Cormen": "Cormen"}


def test_existing_entries_survive_a_later_chapter() -> None:
    """Bab 5 tidak boleh menghapus sumber yang ditemukan bab 1 — itu inti §22."""
    existing = {"Cormen": "Cormen", "Aho": "Aho"}
    research = ResearchPackage(evidence=(), sources=("Knuth",), degraded=False)

    remembered = remembered_citations(existing, research)

    assert remembered["Cormen"] == "Cormen"
    assert remembered["Aho"] == "Aho"
    assert remembered["Knuth"] == "Knuth"
