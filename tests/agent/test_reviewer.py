"""Tes Reviewer — memisahkan *apa yang dikatakan peninjau* dari *apa yang diputuskan*.

Dua hal yang paling mudah salah di sini, dan keduanya diuji di bawah:

1. **Ambang skor.** Prompt menyatakan "``approved`` benar hanya bila skor >= 7",
   tetapi perintah di prompt adalah permintaan. Model yang menulis "skor 6,
   sebenarnya sudah cukup baik" lalu menyetujui akan meloloskan bab di bawah
   ambang tanpa satu pun tanda.
2. **Identitas gate.** ``ReviewResult.gate`` harus berasal dari kita. Model
   tidak pernah ditanya nama gate-nya sendiri — dan tidak boleh, karena
   ``strict_schema`` akan memaksanya mengarang jawaban.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.gates import GateContext
from agents.reviewer import ChapterReviewer
from agents.writer import ChapterWriter
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import (
    ChapterDraft,
    ChapterRecord,
    Evidence,
    ResearchPackage,
    ReviewResult,
    ReviewVerdict,
)
from domain.enums import ChapterEvent, ChapterStatus
from domain.errors import AgentOutputError, GatePreconditionError
from domain.rules import decide_review, with_draft, with_gate_result
from domain.state import BookState
from domain.transitions import transition
from tests.fakes.chat_models import SchemaEchoChatModel, ScriptedChatModel

OBJECTIVE_1 = "Mahasiswa mampu menghitung kompleksitas waktu algoritma sederhana"
CORMEN = "Cormen, Introduction to Algorithms, 4th ed."

SPEC = ChapterSpec(
    number=2,
    title="Analisis Kompleksitas",
    objectives=(OBJECTIVE_1,),
    sections=("Pengantar", "Notasi Big-O"),
)


def verdict_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "approved": True,
        "score": 9,
        "feedback": ["Bab ini sudah baik."],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def draft_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "title": "Analisis Kompleksitas",
        "learning_objectives": [OBJECTIVE_1],
        "sections": [{"heading": "Pengantar", "body": "Algoritma adalah urutan langkah."}],
        "examples": ["Pencarian linear."],
        "exercises": ["Hitung kompleksitas pencarian biner."],
        "citations": [],
        "unresolved_claims": ["Big-O diperkenalkan pada 1894."],
        "summary": "Bab ini membahas laju pertumbuhan algoritma.",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class StubProvider:
    """``ModelProvider`` palsu yang mencatat peran yang diminta.

    ``embedder`` sengaja **melempar** alih-alih mengembalikan sesuatu: gate review
    tidak punya urusan dengan embedding (ISP), dan tes ini membuktikannya dengan
    gagal keras bila suatu saat ia memintanya.
    """

    def __init__(self, model: Any) -> None:
        self._model = model
        self.requested: list[str] = []

    def chat(self, role: str) -> Any:
        self.requested.append(role)
        return self._model

    def embedder(self, role: str = "embedding") -> Any:
        raise AssertionError("gate review tidak boleh meminta model embedding")


@pytest.fixture
def book() -> BookState:
    return BookState(
        request=BookRequest(title="Algoritma", target_chapters=1),
        spec=BookSpec(title="Algoritma", chapters=(SPEC,)),
    )


@pytest.fixture
def record() -> ChapterRecord:
    return ChapterRecord(
        number=2,
        status=ChapterStatus.DRAFTED,
        spec=SPEC,
        draft=ChapterDraft(
            title="Analisis Kompleksitas",
            learning_objectives=(OBJECTIVE_1,),
            sections=(),
        ),
        research=ResearchPackage.empty(),
    )


@pytest.fixture
def reviewer(prompt_library: FilePromptLibrary) -> ChapterReviewer:
    return ChapterReviewer(model=ScriptedChatModel([verdict_json()]), prompts=prompt_library)


# ---------------------------------------------------------------------------
# 1. Jalur bahagia
# ---------------------------------------------------------------------------
def test_an_approving_verdict_produces_an_approving_result(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel([verdict_json(score=9)])
    gate = ChapterReviewer(model=model, prompts=prompt_library)

    result = gate.evaluate(record, book)

    assert model.call_count == 1
    assert result.approved is True
    assert result.score == 9
    assert result.skipped is False
    assert result.gate == "reviewer"


def test_the_gate_identity_never_comes_from_the_model(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Model tidak diminta mengarang ``gate`` maupun ``skipped``.

    ``strict_schema`` menandai semua properti wajib, jadi apa pun yang ada di
    model keluaran akan diminta dari model. Skema yang dikirim karena itu hanya
    boleh memuat tiga field milik peninjau.
    """
    model = ScriptedChatModel([verdict_json()])
    ChapterReviewer(model=model, prompts=prompt_library).evaluate(record, book)

    schema = model.schemas()[0]
    assert set(schema["properties"]) == {"approved", "score", "feedback"}


def test_every_rejection_is_recorded_with_its_feedback(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    notes = ("Sub-bab 2.1 menyebut Big-O tanpa menjelaskannya.",)
    model = ScriptedChatModel([verdict_json(approved=False, score=4, feedback=list(notes))])
    gate = ChapterReviewer(model=model, prompts=prompt_library)

    result = gate.evaluate(record, book)

    assert result.approved is False
    assert result.score == 4
    assert result.feedback == notes


# ---------------------------------------------------------------------------
# 2. Ambang skor — ditegakkan sistem, bukan diharapkan dari model
# ---------------------------------------------------------------------------
def test_a_below_threshold_approval_is_overridden(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """"Skor 6, sebenarnya sudah cukup baik" tetap berarti tidak lulus."""
    model = ScriptedChatModel([verdict_json(approved=True, score=6)])
    gate = ChapterReviewer(model=model, prompts=prompt_library, threshold=7)

    result = gate.evaluate(record, book)

    assert result.approved is False
    assert result.score == 6


def test_the_overridden_approval_is_reported_not_swallowed(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Tanpa catatan ini, laporan akhir tampak seperti penolakan biasa.

    Padahal peninjau sebenarnya menyetujui — dan itu informasi yang harus
    terlihat oleh siapa pun yang membaca mengapa babnya dikembalikan.
    """
    model = ScriptedChatModel([verdict_json(approved=True, score=5, feedback=["Hampir."])])
    gate = ChapterReviewer(model=model, prompts=prompt_library, threshold=7)

    result = gate.evaluate(record, book)

    assert "Hampir." in result.feedback
    assert any("ambang 7" in note for note in result.feedback)


def test_a_score_exactly_at_the_threshold_passes(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Ambangnya inklusif — ">= 7" tertulis di prompt dan di kode."""
    model = ScriptedChatModel([verdict_json(approved=True, score=7)])
    gate = ChapterReviewer(model=model, prompts=prompt_library, threshold=7)

    assert gate.evaluate(record, book).approved is True


def test_a_strict_reviewer_cannot_be_overruled_by_a_high_score(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Penolakan tetap penolakan — ambang adalah lantai, bukan pengganti penilaian."""
    model = ScriptedChatModel([verdict_json(approved=False, score=10)])
    gate = ChapterReviewer(model=model, prompts=prompt_library, threshold=7)

    assert gate.evaluate(record, book).approved is False


# ---------------------------------------------------------------------------
# 3. Konteks yang diterima peninjau
# ---------------------------------------------------------------------------
def test_the_reviewer_sees_the_draft_as_json_including_unresolved_claims(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Markdown tidak memuat ``unresolved_claims`` — dan itu justru yang dinilai.

    Peninjau yang hanya melihat Markdown tidak akan pernah dapat memeriksa
    permukaan kejujuran §34.
    """
    model = ScriptedChatModel([verdict_json()])
    ChapterReviewer(model=model, prompts=prompt_library).evaluate(record, book)

    sent = model.requests[0].user
    assert OBJECTIVE_1 in sent
    assert "Analisis Kompleksitas" in sent
    assert "2" in sent


def test_the_reviewer_is_told_that_empty_citations_are_correct_when_degraded(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Tanpa bahan, peninjau yang menolak karena ``citations`` kosong salah.

    Prompt-nya menyatakan itu terang-terangan, dan tes ini memastikan kalimat
    tersebut benar-benar sampai — bukan sekadar ada di berkas.
    """
    model = ScriptedChatModel([verdict_json()])
    ChapterReviewer(model=model, prompts=prompt_library).evaluate(record, book)

    assert "kosong adalah benar" in model.requests[0].user


def test_the_reviewer_is_shown_the_evidence_when_research_is_available(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Dengan bahan, peninjau dapat memeriksa apakah sitasi draf benar-benar ada."""
    researched = ChapterRecord(
        number=2,
        status=ChapterStatus.DRAFTED,
        spec=SPEC,
        draft=ChapterDraft(title="Analisis Kompleksitas", learning_objectives=(OBJECTIVE_1,)),
        research=ResearchPackage(
            evidence=(Evidence(source=CORMEN, page=45, text="Notasi asimtotik."),),
            sources=(CORMEN,),
            degraded=False,
        ),
    )
    model = ScriptedChatModel([verdict_json()])
    ChapterReviewer(model=model, prompts=prompt_library).evaluate(researched, book)

    assert CORMEN in model.requests[0].user
    assert "karangan" in model.requests[0].user


# ---------------------------------------------------------------------------
# 4. Prasyarat
# ---------------------------------------------------------------------------
def test_a_record_without_a_draft_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Menilai bab kosong akan menghasilkan skor yang tidak bermakna."""
    bare = ChapterRecord(number=2, status=ChapterStatus.PLANNED, spec=SPEC)
    model = ScriptedChatModel([verdict_json()])

    with pytest.raises(GatePreconditionError) as excinfo:
        ChapterReviewer(model=model, prompts=prompt_library).evaluate(bare, book)

    assert excinfo.value.missing == "draf"
    assert model.call_count == 0, "tidak boleh ada token yang dibakar"


def test_a_record_without_a_spec_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Tanpa tujuan yang harus dicapai, tidak ada acuan untuk menilai."""
    bare = ChapterRecord(number=2, status=ChapterStatus.DRAFTED, draft=ChapterDraft(title="X"))

    with pytest.raises(GatePreconditionError) as excinfo:
        ChapterReviewer(model=ScriptedChatModel([verdict_json()]), prompts=prompt_library).evaluate(
            bare, book
        )

    assert excinfo.value.missing == "spesifikasi bab"


# ---------------------------------------------------------------------------
# 5. Tangga perbaikan
# ---------------------------------------------------------------------------
def test_broken_verdict_is_repaired_with_zero_temperature(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel(["bukan json", verdict_json()])
    gate = ChapterReviewer(model=model, prompts=prompt_library)

    result = gate.evaluate(record, book)

    assert model.call_count == 2
    assert model.temperatures() == (None, 0.0)
    assert result.approved is True


def test_persistent_failure_propagates_rather_than_rejecting_silently(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Peninjau yang tidak dapat di-parse bukan "bab ditolak" — itu kegagalan lain.

    Menelannya menjadi ``approved=False`` akan mengirim bab ke revisi tanpa satu
    pun catatan untuk dikerjakan penulis, dan revisinya pasti gagal lagi.
    """
    model = ScriptedChatModel(["rusak", "rusak", "rusak"])
    gate = ChapterReviewer(model=model, prompts=prompt_library, repair_attempts=2)

    with pytest.raises(AgentOutputError):
        gate.evaluate(record, book)

    assert model.call_count == 3


def test_schema_echo_reviewer_produces_a_verdict_that_passes(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """``SchemaEchoChatModel`` memakai ``approved=True, score=9`` sebagai bawaan."""
    model = SchemaEchoChatModel()
    gate = ChapterReviewer(model=model, prompts=prompt_library, threshold=7)

    result = gate.evaluate(record, book)

    assert result.approved is True


# ---------------------------------------------------------------------------
# 6. Perakitan dari registri
# ---------------------------------------------------------------------------
def test_the_gate_takes_its_model_from_the_router_by_role(
    prompt_library: FilePromptLibrary,
) -> None:
    """Satu-satunya tempat ``router.chat("reviewer")`` dipanggil untuk gate ini."""
    from agents.gates import build_gates  # lokal: menghindari impor melingkar di tingkat modul

    provider = StubProvider(ScriptedChatModel([verdict_json()]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
        review_threshold=7,
    )

    gates = build_gates(("reviewer",), context)

    assert provider.requested == ["reviewer"]
    assert gates[0].name == "reviewer"
    assert gates[0].produces is ChapterStatus.REVIEWED


# ---------------------------------------------------------------------------
# 7. Lingkaran penolakan §31: REVIEW_FAIL → REVISION → draf baru
# ---------------------------------------------------------------------------
def test_a_rejected_chapter_returns_to_revision_and_can_be_rewritten(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Lingkaran penuh dengan gate dan penulis sungguhan, hanya LLM yang palsu.

    Inilah yang membuat §31 dapat dipercaya: penolakan bukan jalan buntu. Ia
    menaikkan penghitung revisi, memindahkan bab ke ``REVISION``, dan membuka
    jalan bagi draf berikutnya — semuanya lewat fungsi murni atas record, bukan
    lewat state yang termutasi di suatu tempat.
    """
    reviewer_model = ScriptedChatModel(
        [verdict_json(approved=False, score=3, feedback=["Sub-bab 2.1 belum menjelaskan."]),]
    )
    gate = ChapterReviewer(model=reviewer_model, prompts=prompt_library, threshold=7)

    record = ChapterRecord(
        number=2,
        status=ChapterStatus.DRAFTED,
        spec=SPEC,
        draft=ChapterDraft(title="Analisis Kompleksitas", learning_objectives=(OBJECTIVE_1,)),
        research=ResearchPackage.empty(),
    )

    # 1. Gate menolak.
    verdict = gate.evaluate(record, book)
    rejected = with_gate_result(record, verdict, produces=gate.produces)

    assert verdict.approved is False
    assert rejected.status is ChapterStatus.FAILED_REVIEW
    assert rejected.revision == 1
    assert rejected.last_review() is not None

    # 2. Bab masuk kembali ke antrean revisi.
    queued = rejected.model_copy(
        update={"status": transition(rejected.status, ChapterEvent.REVISE)}
    )
    assert queued.status is ChapterStatus.REVISION

    # 3. Penulis menghasilkan draf baru, yang kembali menjadi DRAFTED.
    writer = ChapterWriter(
        model=ScriptedChatModel([draft_json()]), prompts=prompt_library
    )
    produced, _ = writer.revise(
        queued.draft or ChapterDraft(title="X"),
        spec=SPEC,
        book=book,
        feedback=verdict.feedback,
        revision=queued.revision,
        research=ResearchPackage.empty(),
    )
    redrafted = with_draft(queued, produced)

    assert redrafted.status is ChapterStatus.DRAFTED
    assert redrafted.revision == 1, "penghitung revisi bertahan melewati penulisan ulang"


# ---------------------------------------------------------------------------
# decide_review (murni)
# ---------------------------------------------------------------------------
def test_decide_review_is_deterministic_and_side_effect_free() -> None:
    verdict = ReviewVerdict(approved=True, score=4, feedback=("Catatan.",))

    first = decide_review(verdict, gate="g", threshold=7)
    second = decide_review(verdict, gate="g", threshold=7)

    assert first == second
    assert verdict.score == 4, "vonis masukan tidak boleh diubah"


def test_decide_review_carries_the_gate_name_it_was_given() -> None:
    """Nama gate datang dari argumen — bukan dari model, dan bukan dari dirinya sendiri."""
    result = decide_review(ReviewVerdict(approved=True, score=8), gate="pedagogy", threshold=7)

    assert result.gate == "pedagogy"
    assert result.skipped is False


def test_decide_review_never_marks_a_real_verdict_as_skipped() -> None:
    """``skipped`` milik pass-through gate; peninjau sungguhan tidak pernah melewati apa pun."""
    result = decide_review(ReviewVerdict(approved=False, score=0), gate="reviewer", threshold=7)

    assert isinstance(result, ReviewResult)
    assert result.skipped is False
