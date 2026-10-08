"""Pipeline penuh tanpa jaringan — inilah pembayaran DIP (§31, §35, §37).

Tidak ada satu pun panggilan jaringan di berkas ini. Yang di-*fake* hanya
``ChatModel`` dan ``Reporter``; sisanya sungguhan — prompt asli dari ``prompts/``,
state asli ke ``tmp_path``, Markdown asli ke ``output/``, dan
:class:`~agents.book_director.BookDirector` asli. Karena itu tes di sini
membuktikan lebih dari "fungsinya jalan": ia membuktikan bahwa **batas
ketergantungannya memang di tempat yang diklaim**.

Yang dijaga berkas ini adalah perilaku yang tidak terlihat dari satu unit tes
mana pun, karena hanya muncul saat tahap-tahapnya dirangkai:

* urutan §31 benar-benar dijalankan, dan Markdown-nya mendarat di disk;
* resume melewati bab yang sudah selesai **tanpa satu pun panggilan model**;
* satu bab gagal tidak membunuh sisanya (§35);
* tetapi kegagalan sistemik membatalkan seluruhnya, sebelum membakar anggaran;
* Markdown bab yang sudah disetujui dapat dirender ulang dari state (§28) —
  yaitu bahwa deliverable benar-benar turunan, bukan sumber kebenaran.

Model ``SchemaEchoChatModel`` yang dipakai di sebagian besar tes **mensintesis
keluaran dari skema yang diminta**, bukan dari JSON tulisan tangan. Itulah yang
membuat tes ini tidak membusuk: menambah field pada ``ChapterDraft`` tidak
memerlukan satu pun suntingan di sini.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from agents.book_director import BookDirector, DirectorSettings
from agents.chapter_planner import ChapterPlannerAgent
from agents.gates import GateContext, build_gates
from agents.planner import BookPlanner
from agents.researcher import NullResearcher
from agents.writer import ChapterWriter
from app.container import FixedClock
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ResearchPackage
from domain.enums import ChapterStatus
from domain.errors import (
    AgentOutputError,
    BookNotPlannedError,
    ChapterNotPlannedError,
    ModelUnavailableError,
    StateWriteError,
)
from domain.ports import Researcher
from domain.state import BookState
from domain.structured import example_instance, strict_schema
from memory.artifacts import MarkdownArtifacts
from memory.project_state import JsonStateStore
from tests.fakes.chat_models import (
    ExplodingChatModel,
    SchemaEchoChatModel,
    ScriptedChatModel,
    json_draft,
)
from tests.fakes.providers import StaticModelProvider
from tests.fakes.reporting import RecordingReporter

#: Judul yang dipakai seluruh berkas ini.
BOOK_TITLE = "Algoritma dan Struktur Data"


def valid_json(model: type[BaseModel]) -> str:
    """Balasan valid untuk ``model``, **disintesis dari skemanya**.

    Bukan JSON tulisan tangan: yang tulisan tangan harus diperbarui setiap kali
    model domain berubah, dan justru itu yang membuat tes integrasi membusuk lalu
    dihapus orang.
    """
    return json.dumps(example_instance(strict_schema(model)), ensure_ascii=False)


def draft_json(summary: str) -> str:
    """Balasan penulis yang valid, dengan ringkasan yang **dapat dibedakan**.

    Ringkasan berbeda per bab diperlukan untuk membuktikan memori bersama §29
    benar-benar bekerja. Memakai ringkasan bawaan yang sama untuk semua bab akan
    membuat pemeriksaan apa pun tentangnya lolos begitu saja — termasuk di
    prompt yang tidak pernah menerimanya.
    """
    payload: dict[str, Any] = example_instance(strict_schema(ChapterDraft))
    payload["summary"] = summary
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Perlengkapan
# ---------------------------------------------------------------------------
@pytest.fixture
def state(tmp_path: Path) -> JsonStateStore:
    """Penyimpanan state asli di ``tmp_path`` — bukan palsu.

    Sengaja: atomik, ``schema_version``, dan penomoran berkas ikut diuji di sini.
    Penyimpanan palsu akan membuat seluruh berkas ini tetap hijau meskipun
    ``memory/`` rusak — dan itu justru satu-satunya bagian yang paling mahal
    bila salah.
    """
    return JsonStateStore(tmp_path / "state", clock=FixedClock("2026-10-07T12:00:00+00:00"))


@pytest.fixture
def artifacts(tmp_path: Path) -> MarkdownArtifacts:
    """Penulis Markdown asli di ``output/`` milik tes."""
    return MarkdownArtifacts(tmp_path / "output")


@pytest.fixture
def reporter() -> RecordingReporter:
    return RecordingReporter()


def seed_book(state: JsonStateStore, chapters: int = 3) -> BookSpec:
    """Tulis ``book.json`` langsung, tanpa memanggil Book Planner.

    Merencanakan lewat LLM hanya menghasilkan **satu** bab pada model echo —
    skema JSON tidak dapat menyatakan "hasilkan tiga elemen". Untuk tes yang
    memerlukan beberapa bab, menulis spesifikasinya langsung justru lebih jujur:
    jumlah babnya eksplisit, bukan akibat sampingan dari perilaku model palsu.
    """
    request = BookRequest(title=BOOK_TITLE, target_chapters=chapters)
    spec = BookSpec(
        title=BOOK_TITLE,
        chapters=tuple(
            ChapterSpec(
                number=number,
                title=f"Bab {number}",
                objectives=(f"Mahasiswa mampu menjelaskan materi bab {number}",),
                sections=("Pengantar", "Pembahasan"),
            )
            for number in range(1, chapters + 1)
        ),
    )
    state.save_book(BookState(request=request, spec=spec))
    return spec


def build_director(
    *,
    prompts: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
    writer_model: object | None = None,
    reviewer_model: object | None = None,
    chapter_planner_model: object | None = None,
    writer: ChapterWriter | None = None,
    researcher: Researcher | None = None,
    settings: DirectorSettings | None = None,
) -> tuple[BookDirector, StaticModelProvider]:
    """Rakit direktur sungguhan dengan model palsu.

    Mengembalikan providernya juga, supaya tes dapat menghitung panggilan model.
    Tanpa itu, satu-satunya cara membuktikan resume tidak memanggil model adalah
    mengukur waktu — dan itu tes yang gagal di mesin yang lambat.
    """
    provider = StaticModelProvider(
        default=SchemaEchoChatModel(),  # type: ignore[arg-type]
        writer=writer_model,  # type: ignore[arg-type]
        reviewer=reviewer_model,  # type: ignore[arg-type]
        chapter_planner=chapter_planner_model,  # type: ignore[arg-type]
    )

    gates = build_gates(
        ("reviewer",),
        GateContext(router=provider, prompts=prompts, reporter=reporter),  # type: ignore[arg-type]
    )

    director = BookDirector(
        planner=BookPlanner(model=provider.chat("planner"), prompts=prompts),
        chapter_planner=ChapterPlannerAgent(
            model=provider.chat("chapter_planner"), prompts=prompts
        ),
        writer=writer or ChapterWriter(model=provider.chat("writer"), prompts=prompts),
        researcher=researcher or NullResearcher(),
        gates=gates,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        settings=settings,
    )
    return director, provider


class _FixedResearcher:
    """``Researcher`` yang selalu mengembalikan paket yang sama.

    Dipakai untuk menguji apa yang **dilakukan direktur** dengan paket riset,
    bukan risetnya sendiri. Paket yang sungguhan hanya dapat dihasilkan oleh
    retriever, dan memaksakan satu di sini justru akan menyembunyikan pembagian
    tugas yang sedang diperiksa.
    """

    def __init__(self, package: ResearchPackage) -> None:
        self._package = package

    def collect(self, spec: ChapterSpec, book: BookState) -> ResearchPackage:
        del spec, book
        return self._package


def chapter_markdown(artifacts: MarkdownArtifacts, number: int) -> str:
    """Isi Markdown bab ``number``, gagal jelas bila berkasnya tidak ada."""
    path = artifacts.chapter_path(number)
    assert path.is_file(), f"Markdown bab {number} tidak ada di {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Jalan bahagia, ujung ke ujung
# ---------------------------------------------------------------------------
def test_a_full_run_writes_the_chapter_file_and_leaves_it_approved(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Verifikasi yang diminta rencana: ``chapter01.md`` + ``chapter01.json`` + APPROVED."""
    director, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )
    director.plan(BookRequest(title=BOOK_TITLE, target_chapters=1))

    report = director.run()

    assert report.approved == 1
    assert report.exit_code() == 0

    record = state.load_chapter(1)
    assert record is not None
    assert record.status is ChapterStatus.APPROVED
    assert record.draft is not None
    assert record.markdown_path == str(artifacts.chapter_path(1))

    # Isinya diperiksa, bukan sekadar keberadaan berkasnya: berkas kosong juga
    # "ada", dan pipeline yang menghasilkan berkas kosong akan lolos dari tes
    # yang hanya memeriksa `exists()`. Judulnya diambil dari draf, bukan dari
    # BookRequest — itu memang alur §31: Book Planner menyusun kerangka, penulis
    # yang menamai babnya.
    assert record.draft is not None
    markdown = chapter_markdown(artifacts, 1)
    assert markdown.startswith("# Bab 1.")
    assert record.draft.title in markdown
    assert "Tujuan Pembelajaran" in markdown


def test_planning_writes_the_book_state_and_reports_a_chapter_count_mismatch(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Perencana yang menghasilkan jumlah bab berbeda harus **mengatakannya**.

    Model echo selalu menghasilkan satu bab; meminta tiga adalah cara termurah
    memicu ketidaksesuaian itu tanpa menulis JSON palsu.
    """
    director, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )

    spec, notes = director.plan(BookRequest(title=BOOK_TITLE, target_chapters=3))

    assert state.load_book() is not None
    assert state.load_book().spec is not None  # type: ignore[union-attr]
    assert spec.chapter_numbers() == tuple(range(1, len(spec.chapters) + 1))
    assert any("diminta" in note for note in notes)


# ---------------------------------------------------------------------------
# 2. Penolakan yang menunjuk perintah berikutnya
# ---------------------------------------------------------------------------
def test_running_before_planning_is_refused_with_a_usable_message(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """``run`` tanpa ``plan`` adalah kesalahan urutan perintah, bukan kerusakan.

    Kegagalan seperti ini adalah pengalaman pertama setiap pengguna baru, jadi
    pesannya harus menyebut perintah yang harus dijalankan — bukan sekadar
    menyatakan berkasnya tidak ditemukan.
    """
    director, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )

    with pytest.raises(BookNotPlannedError) as excinfo:
        director.run()

    assert "plan" in str(excinfo.value)


def test_a_chapter_outside_the_spec_is_refused_before_any_work_happens(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """``--from 1 --to 99`` tidak boleh membayar delapan bab dulu, baru menolak.

    Bab yang tidak ada di ``BookSpec`` adalah kesalahan pemakaian, dan biayanya
    sudah jelas sebelum satu token pun dibakar.
    """
    seed_book(state, chapters=3)
    director, provider = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )

    with pytest.raises(ChapterNotPlannedError) as excinfo:
        director.run(numbers=(2, 99))

    assert "99" in str(excinfo.value)
    assert provider.total_calls() == 0
    assert state.load_chapter(2) is None


# ---------------------------------------------------------------------------
# 3. Resume (§28)
# ---------------------------------------------------------------------------
def test_every_chapter_of_a_multi_chapter_book_is_worked_and_written(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    seed_book(state, chapters=3)
    director, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )

    report = director.run()

    assert (report.total, report.approved, report.failed, report.skipped) == (3, 3, 0, 0)
    for number in (1, 2, 3):
        record = state.load_chapter(number)
        assert record is not None
        assert record.status is ChapterStatus.APPROVED
        assert artifacts.chapter_path(number).is_file()


def test_a_second_run_of_a_finished_book_calls_no_model_at_all(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Resume yang benar bukan "cepat" — ia **nol** panggilan model.

    Inilah satu-satunya bukti yang tidak dapat dipalsukan oleh mesin yang cepat.
    """
    seed_book(state, chapters=3)
    director, provider = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )
    director.run()
    calls_after_first_run = provider.total_calls()
    assert calls_after_first_run > 0

    reporter.clear()
    report = director.run()

    assert provider.total_calls() == calls_after_first_run
    assert report.approved == 3
    assert reporter.said("Tidak ada bab yang perlu dikerjakan")


@pytest.mark.parametrize(
    "status", [ChapterStatus.EXAMPLES_WRITTEN, ChapterStatus.EXERCISES_WRITTEN]
)
def test_resuming_after_a_writing_stage_never_rewrites_the_draft(
    status: ChapterStatus,
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """``DRAFT_READY_STATUSES`` adalah satu-satunya himpunan yang menyebut status per tahap.

    Gate contoh dan latihan **mengganti** draf (§19, §20), jadi bab yang terputus
    tepat setelah salah satunya sudah memegang draf terbaik yang dimilikinya.
    Bila statusnya tidak ada di himpunan itu, resume akan menulis ulang bab dari
    nol: draf yang sudah diperkaya dibuang, dan satu panggilan writer penuh
    dibayar untuk menghasilkan draf yang berbeda.

    Penulis di sini sengaja diberi skrip kosong — setiap panggilan akan
    menggagalkan tes dengan pesan yang jelas, bukan diam-diam menulis ulang.
    """
    spec = seed_book(state, chapters=1)
    state.save_chapter(
        ChapterRecord(
            number=1,
            status=status,
            spec=spec.chapters[0],
            draft=ChapterDraft(title="Bab 1", summary="draf yang sudah diperkaya"),
            research=ResearchPackage.empty(),
        )
    )
    writer = ScriptedChatModel([])
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer_model=writer,
    )

    report = director.run()

    assert writer.call_count == 0, "draf yang sudah siap tidak ditulis ulang"
    assert report.approved == 1
    resumed = state.load_chapter(1)
    assert resumed is not None
    assert resumed.status is ChapterStatus.APPROVED
    assert resumed.draft is not None
    assert resumed.draft.summary == "draf yang sudah diperkaya"


def test_a_missing_markdown_file_is_rerendered_from_the_record(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Deliverable adalah turunan: menghapus ``output/`` tidak menghilangkan pekerjaan (§28)."""
    seed_book(state, chapters=1)
    director, provider = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )
    director.run()
    calls_after_first_run = provider.total_calls()
    original = chapter_markdown(artifacts, 1)

    artifacts.chapter_path(1).unlink()
    reporter.clear()
    director.run()

    assert chapter_markdown(artifacts, 1) == original
    # Dirender ulang, bukan ditulis ulang: bab yang sudah disetujui tidak boleh
    # berubah isinya hanya karena berkasnya pernah terhapus.
    assert provider.total_calls() == calls_after_first_run
    assert reporter.said("dirender ulang")


def test_force_reruns_a_chapter_that_was_already_approved(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """``--force`` mengulang bab yang diminta dan tidak menyentuh sisanya."""
    seed_book(state, chapters=3)
    writer = SchemaEchoChatModel()
    director, provider = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer_model=writer,
    )
    director.run()
    before = writer.call_count
    assert state.load_chapter(2).revision == 0  # type: ignore[union-attr]

    director.run(numbers=(2,), force=True)

    assert writer.call_count == before + 1
    assert state.load_chapter(2).status is ChapterStatus.APPROVED  # type: ignore[union-attr]


def test_a_chapter_that_failed_review_is_picked_up_again_on_the_next_run(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """``FAILED_REVIEW`` bukan "selesai" — justru bab itu yang paling perlu diulang.

    Bedanya dengan ``is_terminal`` inilah yang diuji: resume yang menyamakan
    keduanya akan melewati tepat bab-bab yang gagal, dan melaporkan buku yang
    "selesai" padahal babnya ditolak.
    """
    seed_book(state, chapters=2)
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        # Penolakan yang konstan dinyatakan sebagai override skema, bukan sebagai
        # antrean balasan: antrean harus diisi ulang untuk setiap bab, dan tes
        # yang menyiapkan balasan per bab menguji panjang antreannya, bukan
        # perilaku resume.
        reviewer_model=SchemaEchoChatModel({"approved": False, "score": 2}),
        settings=DirectorSettings(max_revisions=0),
    )

    first = director.run()

    assert first.approved == 0
    assert first.failed == 2
    assert first.exit_code() == 1
    assert state.load_chapter(1).status is ChapterStatus.FAILED_REVIEW  # type: ignore[union-attr]

    # Peninjau diganti dengan yang menyetujui — situasi nyata setelah prompt
    # peninjau diperbaiki — lalu `run` dijalankan lagi tanpa `--force`.
    healed, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )
    second = healed.run()

    assert second.approved == 2
    assert second.skipped == 0
    assert state.load_chapter(2).status is ChapterStatus.APPROVED  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 3. Loop revisi (§31)
# ---------------------------------------------------------------------------
def test_a_rejected_draft_is_revised_with_the_reviewers_own_feedback(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Catatan peninjau dikirim **apa adanya**; catatan yang dipangkas = putaran terbuang."""
    seed_book(state, chapters=1)
    feedback = "Sub-bab 2.1 belum menjelaskan notasi Big-O."
    reviewer = ScriptedChatModel(
        [
            json_draft(approved=False, score=3, feedback=[feedback]),
            json_draft(approved=True, score=9, feedback=[]),
        ]
    )
    writer = SchemaEchoChatModel()
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer_model=writer,
        reviewer_model=reviewer,
    )

    report = director.run()

    assert report.approved == 1
    record = state.load_chapter(1)
    assert record is not None
    assert record.status is ChapterStatus.APPROVED
    assert record.revision == 1
    assert [review.approved for review in record.reviews] == [False, True]

    # Penulis dipanggil dua kali: sekali menulis, sekali merevisi.
    assert writer.call_count == 2
    # Dan panggilan kedua benar-benar membawa catatan peninjau itu.
    assert feedback in writer.requests[1].user


def test_a_chapter_that_never_satisfies_the_reviewer_stops_at_the_revision_budget(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Anggaran revisi dihormati, dan babnya **berhenti** — bukan berputar selamanya."""
    seed_book(state, chapters=1)
    writer = SchemaEchoChatModel()
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer_model=writer,
        reviewer_model=SchemaEchoChatModel({"approved": False, "score": 1}),
        settings=DirectorSettings(max_revisions=1),
    )

    report = director.run()

    record = state.load_chapter(1)
    assert record is not None
    assert record.status is ChapterStatus.FAILED_REVIEW
    # ``revision`` menghitung berapa kali bab **dikembalikan** peninjau, bukan
    # berapa kali ditulis ulang. Dengan anggaran 1 revisi: satu tulis + satu
    # revisi = dua penolakan. Angka itu pula yang dikirim ke prompt revisi,
    # sehingga penulis selalu tahu ia sedang merevisi yang ke berapa.
    assert record.revision == 2
    assert writer.call_count == 2
    assert report.failed == 1
    assert not artifacts.chapter_path(1).exists()
    assert reporter.said("menyerah")


# ---------------------------------------------------------------------------
# 4. Isolasi kegagalan (§35)
# ---------------------------------------------------------------------------
def test_a_failed_chapter_does_not_stop_the_ones_after_it(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Satu bab yang keluarannya tidak dapat di-parse tidak boleh membatalkan sisanya.

    Penulis dipaksa gagal **hanya pada bab pertama**: balasan pertama bukan JSON
    sama sekali, dan ``max_repair_attempts=0`` membuatnya langsung menyerah
    (dengan perbaikan aktif ia akan meminta balasan kedua, dan bab 2 akan
    mendapat balasan yang salah). Balasan bab 2 dan 3 disintesis dari skema,
    jadi keduanya tetap sah.
    """
    seed_book(state, chapters=3)
    broken = ScriptedChatModel(
        ["ini bukan JSON sama sekali", valid_json(ChapterDraft), valid_json(ChapterDraft)]
    )
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer=ChapterWriter(model=broken, prompts=prompt_library, max_repair_attempts=0),
    )

    report = director.run()

    assert report.total == 3
    assert report.approved == 2
    assert report.failed == 1
    assert report.skipped == 0
    assert report.aborted is False
    assert report.exit_code() == 1
    assert state.load_chapter(1).status is ChapterStatus.FAILED  # type: ignore[union-attr]
    assert state.load_chapter(1).error is not None  # type: ignore[union-attr]
    # Bab yang gagal tetap menyimpan seberapa jauh ia sampai — kegagalan tidak
    # menghapus pekerjaan yang sudah ada.
    assert state.load_chapter(1).research is not None  # type: ignore[union-attr]
    assert artifacts.chapter_path(2).is_file()
    assert artifacts.chapter_path(3).is_file()
    assert not artifacts.chapter_path(1).exists()
    assert reporter.said("gagal")


def test_a_failed_chapter_leaves_the_raw_model_output_on_disk(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Keluaran mentah yang gagal di-parse ditinggalkan di ``state/parse_fail/`` (§37).

    Ini satu-satunya bukti yang tersisa setelah perbaikan menyerah. Teks yang tidak
    dapat di-parse tidak pernah menjadi objek apa pun, jadi ia tidak ada di
    ``chapterNN.json`` maupun di log: menelannya berarti kehilangan satu-satunya
    petunjuk tentang apa yang sebenarnya dikembalikan model, dan memperbaiki
    promptnya berubah menjadi menebak.

    Bukti itu ditulis oleh :class:`~agents.book_director.BookDirector`, bukan oleh
    agent penulisnya: agent tidak tahu bab mana yang dikerjakannya, dan memang
    tidak boleh tahu. Karena itu yang diuji di sini adalah berkasnya benar-benar
    ada — bukan bahwa ada sebuah callback yang dipanggil.
    """
    seed_book(state, chapters=1)
    garbage = "ini bukan JSON sama sekali"
    broken = ScriptedChatModel([garbage])
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer=ChapterWriter(model=broken, prompts=prompt_library, max_repair_attempts=0),
    )

    director.run()

    evidence = state.parse_fail_dir / "chapter01.attempt1.txt"
    assert evidence.is_file(), "keluaran mentah yang gagal seharusnya disimpan"
    assert garbage in evidence.read_text(encoding="utf-8")
    assert reporter.said(str(evidence)), "jalur buktinya seharusnya disebut agar dapat dibuka"


def test_a_failed_chapter_is_worked_again_on_the_next_run(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Bab yang gagal dapat diselesaikan oleh ``run`` **berikutnya** (§28, §35).

    Inilah janji yang dipegang :func:`~domain.rules.pending_numbers`: bab berstatus
    ``FAILED`` tetap masuk daftar kerja, karena "berhenti karena rusak" justru
    keadaan yang paling perlu dikerjakan ulang. Janji itu tidak ada gunanya bila
    mengerjakannya kembali lalu mati di tengah jalan.

    Terjadi sungguhan (2026-10-07), dan hanya terlihat pada ``run`` yang **kedua**:
    tabel §27 tidak punya baris yang keluar dari ``FAILED``, sehingga
    ``with_draft`` menolak ``(FAILED, DRAFT)``. Jalannya yang pertama menandai
    babnya gagal dengan benar; setiap jalannya sesudah itu mati dengan
    ``IllegalTransitionError``, dan satu-satunya cara menyelesaikan buku adalah
    ``--force``, yang membuang riset dan draf yang masih baik.

    Tes ini karena itu menjalankan direktur **dua kali**: sekali dengan penulis
    yang rusak, sekali lagi dengan penulis yang sehat. Satu jalannya saja tidak
    akan pernah menemukan cacat ini — dan itulah sebabnya seluruh suite offline
    hijau sementara aplikasinya tidak dapat dipakai dua kali.
    """
    seed_book(state, chapters=1)
    broken = ScriptedChatModel(["ini bukan JSON sama sekali"])
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer=ChapterWriter(model=broken, prompts=prompt_library, max_repair_attempts=0),
    )

    first = director.run()

    assert first.failed == 1
    failed = state.load_chapter(1)
    assert failed is not None and failed.status is ChapterStatus.FAILED

    # Percobaan kedua, dengan penulis yang sehat — dan tanpa --force.
    healed, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )
    second = healed.run()

    assert second.approved == 1, "bab yang gagal seharusnya dikerjakan ulang, bukan dilewati"
    assert second.exit_code() == 0
    record = state.load_chapter(1)
    assert record is not None and record.status is ChapterStatus.APPROVED
    assert record.error is None, "jejak kegagalan percobaan lama tidak boleh tertinggal"
    assert artifacts.chapter_path(1).is_file()
    assert reporter.said("dikerjakan ulang")


def test_reworking_a_failed_chapter_keeps_the_research_and_the_plan(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Mengerjakan ulang **tidak** berarti mengulang dari nol.

    Riset dan spesifikasi bab tidak pernah menjadi tidak berlaku hanya karena
    langkah sesudahnya gagal. Membuangnya akan membayar dua panggilan model untuk
    menggantinya dengan hasil yang berbeda — dan itu tepatnya yang dilakukan
    ``--force``, yang karena itu **bukan** jalur bawaan.
    """
    seed_book(state, chapters=1)
    broken = ScriptedChatModel(["bukan JSON"])
    failing, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer=ChapterWriter(model=broken, prompts=prompt_library, max_repair_attempts=0),
    )
    failing.run()

    planned = state.load_chapter(1)
    assert planned is not None and planned.research is not None and planned.spec is not None
    research_before, spec_before = planned.research, planned.spec

    # Model peran yang **eksplisit** untuk ``chapter_planner``: tanpa itu ia jatuh
    # ke model bawaan, yang dipakai bersama peran lain, dan ``calls_for`` akan
    # melaporkan jumlah panggilan peran lain sebagai miliknya.
    healed, provider = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer_model=SchemaEchoChatModel(),
        chapter_planner_model=SchemaEchoChatModel(),
    )
    healed.run()

    after = state.load_chapter(1)
    assert after is not None
    assert after.research == research_before, "paket riset ditulis ulang tanpa alasan"
    assert after.spec == spec_before, "spesifikasi bab ditulis ulang tanpa alasan"
    assert provider.calls_for("chapter_planner") == 0, "perencana bab dipanggil ulang"


def test_a_failed_evidence_write_does_not_mask_the_chapters_own_failure(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Disk yang gagal menulis bukti tidak boleh mengganti pesan kegagalan bab.

    Babnya sudah ditandai ``FAILED`` dan tersimpan sebelum bukti mentah ditulis.
    Bila kegagalan menulis itu naik ke atas, yang tercatat di laporan berubah
    menjadi cerita tentang disk — dan alasan sebenarnya bab itu gagal hilang,
    justru pada saat ia paling dibutuhkan.

    Penulisan atomik menerjemahkan galat OS menjadi ``StateWriteError`` di
    batasnya (``memory/checkpoints.py``), jadi inilah tipe yang harus ditangkap:
    menangkap ``OSError`` tidak akan menangkap apa pun, dan tes ini akan gagal
    dengan ``StateWriteError`` yang bocor.
    """

    class UnwritableEvidence(JsonStateStore):
        """State store sungguhan yang khusus gagal menyimpan bukti."""

        def save_raw_failure(self, number: int, attempt: int, raw: str) -> str:
            raise StateWriteError(self.parse_fail_dir / f"chapter{number:02d}.txt")

    seed_book(state, chapters=1)
    strict_state = UnwritableEvidence(
        state.state_dir, clock=FixedClock("2026-10-07T12:00:00+00:00")
    )
    broken = ScriptedChatModel(["ini bukan JSON sama sekali"])
    director, _ = build_director(
        prompts=prompt_library,
        state=strict_state,
        artifacts=artifacts,
        reporter=reporter,
        writer=ChapterWriter(model=broken, prompts=prompt_library, max_repair_attempts=0),
    )

    report = director.run()

    assert report.failed == 1
    failed = strict_state.load_chapter(1)
    assert failed is not None
    assert failed.status is ChapterStatus.FAILED
    # Pesan aslinya bertahan — bukan diganti oleh galat tulis.
    assert failed.error is not None
    assert "gagal menghasilkan output valid" in failed.error
    assert reporter.said("tidak dapat disimpan")


def test_a_systemic_failure_aborts_before_burning_the_remaining_chapters(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Model penulis yang tidak ada akan gagal identik untuk semua bab sisa.

    Menghentikan segera adalah benar; melanjutkan berarti membakar anggaran
    untuk bab-bab yang sudah pasti bernasib sama.
    """
    seed_book(state, chapters=3)
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer_model=ExplodingChatModel(ModelUnavailableError("model-tidak-ada")),
    )

    report = director.run()

    assert report.aborted is True
    assert report.abort_reason is not None
    assert "model-tidak-ada" in report.abort_reason
    assert report.approved == 0
    assert report.exit_code() == 1
    # Bab 2 dan 3 tidak pernah disentuh sama sekali.
    assert state.load_chapter(2) is None
    assert state.load_chapter(3) is None


def test_run_chapter_raises_instead_of_containing_its_own_failure(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """``write-chapter N`` meminta satu hasil: gagal berarti gagal, bukan exit 0.

    Inilah bedanya dengan :meth:`BookDirector.run` yang mengandung kegagalan
    per-bab. Keduanya diuji berdampingan karena perbedaannya adalah keputusan
    sadar, bukan efek samping.
    """
    seed_book(state, chapters=1)
    broken = ScriptedChatModel(["ini juga bukan JSON"])
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer=ChapterWriter(
            model=broken, prompts=prompt_library, max_repair_attempts=0
        ),
    )

    with pytest.raises(AgentOutputError):
        director.run_chapter(1)

    # Pekerjaan yang sudah dilakukan tidak dihapus oleh kegagalan itu.
    record = state.load_chapter(1)
    assert record is not None
    assert record.research is not None


# ---------------------------------------------------------------------------
# 5. Memori bersama antar-bab (§29)
# ---------------------------------------------------------------------------
def test_each_chapter_sees_the_summaries_of_the_ones_before_it(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Tanpa ini, bab ketiga ditulis tanpa tahu apa yang sudah dibahas di bab pertama.

    Ringkasan disimpan **segera** setelah satu bab selesai, bukan di akhir
    ``run`` — sebab bab berikutnya dimulai dalam proses yang sama.

    Kedua ringkasan sengaja berbeda: memakai ringkasan bawaan yang sama untuk
    semua bab akan membuat pemeriksaan ini lolos begitu saja, karena teks itu
    toh sudah ada di kontrak OUTPUT setiap prompt.
    """
    seed_book(state, chapters=2)
    first_summary = "Bab satu membahas pengertian algoritma."
    writer = ScriptedChatModel(
        [draft_json(first_summary), draft_json("Bab dua membahas kompleksitas waktu.")]
    )
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        writer_model=writer,
    )

    director.run()

    book = state.load_book()
    assert book is not None
    assert book.summaries[1] == first_summary
    assert set(book.summaries) == {1, 2}

    # Penulis dipanggil sekali per bab, berurutan: panggilan kedua adalah bab 2.
    assert len(writer.requests) == 2
    assert first_summary in writer.requests[1].user


def test_the_sources_a_chapter_was_written_from_are_remembered_for_the_next_ones(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """§22 bekerja lintas bab, dan hanya ``book.json`` yang dapat mengingatnya.

    Pemeriksa sitasi bab 5 bertanya "apakah rujukan ini berasal dari knowledge
    base?". Pertanyaan itu **tidak dapat** dijawab dari paket riset bab 5
    sendiri: sumber yang sudah ditemukan bab 1 tetap sah dikutip bab 5,
    sekalipun pencarian bab 5 kebetulan tidak memunculkannya kembali.

    Karena itu sumbernya dicatat **saat babnya disetujui** — bukan di akhir
    ``run``. Bab-bab yang ditulis dalam proses yang sama harus sudah dapat
    membacanya, dan itu justru kondisi normal.
    """
    seed_book(state, chapters=1)
    source = "Cormen, Introduction to Algorithms, 4th ed."
    research = ResearchPackage(evidence=(), sources=(source,), degraded=False)
    director, _ = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        researcher=_FixedResearcher(research),
    )

    director.run()

    book = state.load_book()
    assert book is not None
    assert book.citations == {source: source}


def test_research_stays_degraded_and_is_recorded_as_such(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """RAG belum ada; itu harus **terbaca di state**, bukan tersembunyi.

    Bendera ``degraded`` inilah yang membuat menjalankan ulang bab yang sama
    setelah RAG tersedia akan memperbaikinya — dan yang membuat siapa pun yang
    membuka ``chapter01.json`` tahu bahwa isinya ditulis tanpa bahan.
    """
    seed_book(state, chapters=1)
    director, _ = build_director(
        prompts=prompt_library, state=state, artifacts=artifacts, reporter=reporter
    )

    director.run()

    record = state.load_chapter(1)
    assert record is not None
    assert record.research is not None
    assert record.research.degraded is True
    assert record.research == ResearchPackage.empty()
    assert reporter.said("terdegradasi")


def test_each_chapter_is_detailed_by_the_chapter_planner_exactly_once(
    prompt_library: FilePromptLibrary,
    state: JsonStateStore,
    artifacts: MarkdownArtifacts,
    reporter: RecordingReporter,
) -> None:
    """Perincian per bab (§16) benar-benar diminta — sekali, lalu tidak diulang.

    Dua sisi invarian yang mudah hilang diam-diam, dan keduanya diuji di sini
    karena satu tanpanya yang lain tidak berarti:

    * **Dijalankan.** ``run_chapter`` dulu mengisi ``record.spec`` dari BookSpec
      sebelum menyerahkannya ke ``_ensure_plan``, dan penjaga "sudah ada spec?"
      karena itu selalu benar — penulis menerima rencana tingkat buku selamanya,
      dan ``ChapterPlannerAgent`` menjadi kode mati meski ia dirakit, berprompt,
      dan beruji lengkap.
    * **Tidak diulang saat resume.** Yang membedakan "belum dirincikan" dari
      "sudah" adalah record tersimpan, bukan isi spec. Bab yang sudah selesai
      tidak boleh membayar satu panggilan model untuk merincikan ulang babnya.
    """
    seed_book(state, chapters=1)
    detailer = ScriptedChatModel([valid_json(ChapterSpec)])
    director, provider = build_director(
        prompts=prompt_library,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        chapter_planner_model=detailer,
    )

    director.run()
    assert detailer.call_count == 1, "perincian per bab seharusnya diminta sekali"

    # Spesifikasi yang tersimpan adalah keluaran perincian, bukan entri BookSpec:
    # ia membawa angka contoh milik model perinci, bukan angka bawaan BookSpec.
    record = state.load_chapter(1)
    assert record is not None
    assert record.spec is not None
    detailed_examples = example_instance(strict_schema(ChapterSpec))["required_examples"]
    assert record.spec.required_examples == detailed_examples

    # Resume: babnya sudah selesai, jadi nol panggilan tambahan dari peran mana pun.
    before = provider.total_calls()
    director.run()
    assert provider.total_calls() == before
