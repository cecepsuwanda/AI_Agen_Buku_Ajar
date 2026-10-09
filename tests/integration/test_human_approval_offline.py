"""Persetujuan manusia di dalam pipeline (§44) — direktur sungguhan, tanpa jaringan.

Berkas ini menguji satu perilaku yang tidak dapat dibuktikan unit tes mana pun,
karena ia baru muncul saat gate dirangkai dengan direkturnya: **bab yang menunggu
keputusan manusia berhenti, dan berhentinya itu bukan kegagalan.**

Tiga akibatnya diperiksa bersama-sama di sini, karena ketiganya mudah hilang
sendiri-sendiri: anggaran revisi tidak boleh terpakai (menunggu bukan kesalahan
penulis), Markdown belum boleh ditulis (babnya belum disetujui siapa pun), dan
kode keluarnya harus nol (skrip yang memeriksa exit code tidak boleh melihat
kegagalan pada buku yang sesungguhnya sudah selesai dikerjakan).

Perlengkapannya dipinjam dari ``test_orchestrator_offline`` — direktur asli,
state asli, dan model palsu yang sama — supaya yang diuji di sini benar-benar
**rangkaiannya**, bukan tiruannya.
"""

from __future__ import annotations

import pytest

from agents.human_approval import HUMAN_APPROVAL_GATE
from app.prompting import FilePromptLibrary
from domain.chapter import ChapterDraft, ChapterRecord, ResearchPackage
from domain.enums import ChapterStatus
from domain.errors import ApprovalNotPossibleError, ChapterNotPlannedError
from domain.rules import awaiting_approval
from memory.artifacts import MarkdownArtifacts
from memory.project_state import JsonStateStore
from tests.fakes.reporting import RecordingReporter
from tests.integration.test_orchestrator_offline import build_director, seed_book


@pytest.fixture
def waiting_director(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> object:
    """Direktur dengan rantai **hanya** berisi gate §44.

    Rantai satu gate dipilih dengan sengaja: yang sedang diperiksa adalah
    perilaku "menunggu" itu sendiri, bukan penilaian pemeriksa fakta atau
    sitasi. Gate lain akan menambah panggilan model tanpa menambah satu pun
    fakta tentang apa yang diuji di sini.
    """
    seed_book(state, chapters=1)
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        gates=(HUMAN_APPROVAL_GATE,),
    )
    return director


# ---------------------------------------------------------------------------
# Berhenti menunggu
# ---------------------------------------------------------------------------
def test_a_waiting_gate_stops_the_chapter_without_spending_a_revision(
    waiting_director: object,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
) -> None:
    """Bab yang menunggu bukan bab yang gagal, dan bukan bab yang revisi."""
    report = waiting_director.run()  # type: ignore[attr-defined]

    assert report.pending == 1
    assert report.approved == 0
    assert report.failed == 0
    assert report.exit_code() == 0
    assert report.total == report.approved + report.pending + report.failed + report.skipped

    record = state.load_chapter(1)
    assert record is not None
    assert record.revision == 0, "menunggu bukan kesalahan penulis"
    assert awaiting_approval(record) is True
    assert record.markdown_path is None
    assert not artifacts.exists(1), "bab yang belum disetujui belum punya deliverable"


def test_a_waiting_chapter_is_reported_as_waiting_not_as_rejected(
    waiting_director: object,
    state: JsonStateStore,
    reporter: RecordingReporter,
) -> None:
    """Yang dibaca dosen di terminal harus berbunyi "menunggu", bukan "ditolak"."""
    waiting_director.run()  # type: ignore[attr-defined]

    assert reporter.said("menunggu persetujuan manusia")


def test_checking_again_while_waiting_costs_nothing(
    waiting_director: object,
    state: JsonStateStore,
) -> None:
    """``run`` yang dijalankan berkali-kali selama PDF-nya diperiksa tidak menumpuk.

    Dua hal sekaligus: vonis menunggu yang berulang **diganti** alih-alih
    ditambahkan, dan tidak ada panggilan model baru — drafnya sudah ada, dan gate
    §44 tidak memanggil model sama sekali.
    """
    waiting_director.run()  # type: ignore[attr-defined]
    first = state.load_chapter(1)
    assert first is not None

    waiting_director.run()  # type: ignore[attr-defined]

    second = state.load_chapter(1)
    assert second is not None
    assert sum(1 for review in second.reviews if review.blocked) == 1
    assert len(second.reviews) == len(first.reviews)


# ---------------------------------------------------------------------------
# Menyetujui
# ---------------------------------------------------------------------------
def test_approving_a_waiting_chapter_writes_its_deliverable(
    waiting_director: object,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
) -> None:
    """``approve`` adalah kelanjutan rantai, bukan jalan pintas di luarnya.

    Markdown-nya baru ditulis setelah babnya benar-benar ``APPROVED``: berkas di
    ``output/`` yang di-track git tidak boleh ada untuk bab yang belum disetujui
    (§28, §39).
    """
    waiting_director.run()  # type: ignore[attr-defined]

    approved = waiting_director.approve(1)  # type: ignore[attr-defined]

    assert approved.status is ChapterStatus.APPROVED
    assert approved.markdown_path == str(artifacts.chapter_path(1))
    assert artifacts.exists(1)
    assert approved.last_review() is not None
    assert approved.last_review().gate == HUMAN_APPROVAL_GATE
    assert approved.last_review().approved is True
    assert approved.last_review().blocked is False

    record = state.load_chapter(1)
    assert record is not None
    assert awaiting_approval(record) is False


def test_the_chapter_is_not_worked_on_again_after_it_is_approved(
    waiting_director: object,
) -> None:
    """Sesudah disetujui, ia berhenti menjadi pekerjaan yang menunggu."""
    waiting_director.run()  # type: ignore[attr-defined]
    waiting_director.approve(1)  # type: ignore[attr-defined]

    again = waiting_director.run()  # type: ignore[attr-defined]

    assert again.approved == 1
    assert again.pending == 0


def test_approving_twice_is_not_an_error(
    waiting_director: object,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Perintah yang dijalankan dua kali karena ragu bukan kesalahan pemakaian."""
    waiting_director.run()  # type: ignore[attr-defined]
    waiting_director.approve(1)  # type: ignore[attr-defined]

    twice = waiting_director.approve(1)  # type: ignore[attr-defined]

    assert twice.status is ChapterStatus.APPROVED
    assert artifacts.exists(1)
    assert reporter.said("sudah disetujui sebelumnya")


# ---------------------------------------------------------------------------
# Menolak
# ---------------------------------------------------------------------------
def test_rejecting_a_waiting_chapter_hands_it_back_to_the_writer(
    waiting_director: object,
    reporter: RecordingReporter,
) -> None:
    """Penolakan manusia mengembalikan babnya tanpa membuang pekerjaan sebelumnya.

    Yang mahal adalah riset dan drafnya, dan keduanya tetap ada. Alasan yang
    ditulis dosen menjadi umpan balik yang dibaca penulis pada ``run`` berikutnya.
    """
    waiting_director.run()  # type: ignore[attr-defined]

    rejected = waiting_director.reject(  # type: ignore[attr-defined]
        1, reason="Contoh pada bagian 2 tidak relevan."
    )

    assert rejected.status is ChapterStatus.REVISION
    assert rejected.revision == 1
    assert rejected.research is not None
    assert rejected.draft is not None
    assert rejected.last_review() is not None
    assert rejected.last_review().feedback == ("Contoh pada bagian 2 tidak relevan.",)
    assert reporter.said("dikembalikan ke penulis")


def test_a_rejection_without_a_reason_still_says_something(
    waiting_director: object,
) -> None:
    """Vonis tanpa catatan akan terbaca sebagai penolakan yang tidak dapat ditindaklanjuti."""
    waiting_director.run()  # type: ignore[attr-defined]

    rejected = waiting_director.reject(1, reason="   ")  # type: ignore[attr-defined]

    assert rejected.last_review() is not None
    assert rejected.last_review().feedback == ("Ditolak tanpa alasan tertulis.",)


def test_rejecting_a_chapter_that_already_counted_as_approved(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Jalur yang paling berguna justru saat gate §44 **mati**.

    Tanpa gate §44, babnya selesai sebagai ``APPROVED`` dan tidak lagi dilewati
    gate mana pun — jadi tidak ada jalur otomatis yang dapat mengembalikannya ke
    penulis. Inilah satu-satunya.
    """
    seed_book(state, chapters=1)
    director, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )
    finished = director.run()
    assert finished.approved == 1

    rejected = director.reject(1, reason="Babnya terlalu ringkas.")

    assert rejected.status is ChapterStatus.REVISION
    assert rejected.markdown_path is None

    # Dan jalan keluarnya tetap ada: bab yang dikembalikan dapat dikerjakan lagi.
    assert director.run().approved == 1


# ---------------------------------------------------------------------------
# Yang belum boleh diputuskan
# ---------------------------------------------------------------------------
def _seed_stuck_chapter(state: JsonStateStore) -> None:
    """Bab yang terputus di tengah rantai gate — bukan menunggu siapa pun."""
    spec = seed_book(state, chapters=1)
    state.save_chapter(
        ChapterRecord(
            number=1,
            status=ChapterStatus.DRAFTED,
            spec=spec.chapters[0],
            draft=ChapterDraft(title="Bab 1"),
            research=ResearchPackage.empty(),
        )
    )


def test_a_chapter_that_is_not_waiting_cannot_be_approved(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Bab yang terputus di tengah rantai tidak boleh disahkan begitu saja.

    §44 ada justru untuk mencegah penerbitan yang belum diperiksa; menyetujui bab
    yang berhenti di tengah akan melewati pemeriksa fakta, sitasi, pedagogi, dan
    konsistensi sekaligus.
    """
    _seed_stuck_chapter(state)
    director, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )

    with pytest.raises(ApprovalNotPossibleError):
        director.approve(1)
    with pytest.raises(ApprovalNotPossibleError):
        director.reject(1, reason="apa pun")


def test_a_chapter_that_was_never_written_cannot_be_approved(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Bab yang belum dikerjakan sama sekali tidak punya apa pun untuk disahkan."""
    seed_book(state, chapters=1)
    director, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )

    with pytest.raises(ApprovalNotPossibleError):
        director.approve(1)


def test_approving_a_chapter_the_plan_does_not_have_says_which_ones_exist(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Nomor yang salah adalah kesalahan pemakaian, dan pesannya menyebut yang benar."""
    seed_book(state, chapters=2)
    director, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )

    with pytest.raises(ChapterNotPlannedError) as excinfo:
        director.approve(9)

    assert excinfo.value.available == (1, 2)
