"""Tes Chapter Writer — dua jalur, satu tangga perbaikan.

Bagian terpenting di berkas ini adalah **penyaringan sitasi**. Rujukan yang
tidak ada tampak persis seperti rujukan yang ada, jadi ia tidak dapat ditangkap
dengan membaca; ia hanya dapat dicegah secara mekanis. Karena itu tesnya bukan
"prompt memuat larangan" (itu sudah diuji di ``test_prompting.py``), melainkan
"model yang melanggar larangan itu tidak dapat mencemari draf".
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.writer import ChapterWriter
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, Evidence, ResearchPackage
from domain.errors import AgentOutputError
from domain.rules import citations_allowed_by, enforce_draft_contract
from domain.state import BookState
from tests.fakes.chat_models import SchemaEchoChatModel, ScriptedChatModel

OBJECTIVE_1 = "Mahasiswa mampu menghitung kompleksitas waktu algoritma sederhana"
OBJECTIVE_2 = "Mahasiswa mampu membandingkan dua algoritma berdasarkan notasi Big-O"

CORMEN = "Cormen, Introduction to Algorithms, 4th ed."

SPEC = ChapterSpec(
    number=2,
    title="Analisis Kompleksitas",
    objectives=(OBJECTIVE_1, OBJECTIVE_2),
    sections=("Pengantar", "Notasi Big-O"),
    required_examples=3,
    required_exercises=5,
)


def draft_json(**overrides: Any) -> str:
    """Keluaran writer yang sah, dengan kemungkinan penimpaan per field."""
    payload: dict[str, Any] = {
        "title": "Analisis Kompleksitas",
        "learning_objectives": [OBJECTIVE_1, OBJECTIVE_2],
        "sections": [{"heading": "Pengantar", "body": "Algoritma adalah urutan langkah."}],
        "examples": ["Pencarian linear pada larik."],
        "exercises": ["Hitung kompleksitas pencarian biner."],
        "citations": [],
        "unresolved_claims": ["Big-O diperkenalkan pada 1894."],
        "summary": "Bab ini membahas cara mengukur laju pertumbuhan algoritma.",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


@pytest.fixture
def book() -> BookState:
    return BookState(
        request=BookRequest(title="Algoritma dan Struktur Data", target_chapters=2),
        spec=BookSpec(title="Algoritma dan Struktur Data", chapters=(SPEC,)),
        summaries={1: "Bab 1 membahas pengertian algoritma."},
        terminology={"kompleksitas waktu": "laju pertumbuhan waktu eksekusi"},
    )


@pytest.fixture
def degraded_research() -> ResearchPackage:
    """Kondisi seluruh MVP ini: tidak ada retrieval."""
    return ResearchPackage.empty()


@pytest.fixture
def supported_research() -> ResearchPackage:
    """Kondisi setelah RAG tersedia: satu bukti dengan sumber yang jelas."""
    return ResearchPackage(
        concepts=("kompleksitas waktu",),
        evidence=(Evidence(source=CORMEN, page=45, text="Notasi asimtotik.", score=0.91),),
        sources=(CORMEN,),
        degraded=False,
    )


def _writer(model: Any, prompts: FilePromptLibrary, **kwargs: Any) -> ChapterWriter:
    return ChapterWriter(model=model, prompts=prompts, **kwargs)


# ---------------------------------------------------------------------------
# 1. Jalur tulis — bahagia
# ---------------------------------------------------------------------------
def test_write_calls_the_model_once_and_returns_a_draft(
    prompt_library: FilePromptLibrary, book: BookState, degraded_research: ResearchPackage
) -> None:
    model = ScriptedChatModel([draft_json()])

    draft, notes = _writer(model, prompt_library).write(SPEC, book=book, research=degraded_research)

    assert model.call_count == 1
    assert notes == ()
    assert isinstance(draft, ChapterDraft)
    assert draft.sections[0].heading == "Pengantar"
    assert draft.summary.startswith("Bab ini membahas")


def test_write_sends_the_schema_on_every_call(
    prompt_library: FilePromptLibrary, book: BookState, degraded_research: ResearchPackage
) -> None:
    model = ScriptedChatModel([draft_json()])

    _writer(model, prompt_library).write(SPEC, book=book, research=degraded_research)

    schema = model.schemas()[0]
    assert schema is not None
    assert "unresolved_claims" in schema["properties"]


def test_write_prompt_carries_the_plan_and_the_target_length(
    prompt_library: FilePromptLibrary, book: BookState, degraded_research: ResearchPackage
) -> None:
    model = ScriptedChatModel([draft_json()])

    _writer(model, prompt_library).write(
        SPEC, book=book, research=degraded_research, min_words=1500
    )

    sent = model.requests[0].user
    assert OBJECTIVE_1 in sent
    assert "Notasi Big-O" in sent
    assert "1500" in sent
    assert "kompleksitas waktu: laju pertumbuhan waktu eksekusi" in sent


def test_degraded_research_reaches_the_prompt_as_a_warning(
    prompt_library: FilePromptLibrary, book: BookState, degraded_research: ResearchPackage
) -> None:
    """Penulis harus **tahu** bahwa ia tidak punya bahan — bukan menyimpulkannya."""
    model = ScriptedChatModel([draft_json()])

    _writer(model, prompt_library).write(SPEC, book=book, research=degraded_research)

    assert "tidak ada bahan rujukan" in model.requests[0].user


# ---------------------------------------------------------------------------
# 2. Penyaringan sitasi — permukaan halusinasi yang paling berbahaya
# ---------------------------------------------------------------------------
def test_invented_citations_are_dropped_when_research_is_degraded(
    prompt_library: FilePromptLibrary, book: BookState, degraded_research: ResearchPackage
) -> None:
    """Tanpa bahan, rujukan apa pun adalah karangan — termasuk yang bentuknya masuk akal."""
    fabricated = ["Sedgewick, Algorithms, 2011", CORMEN]
    model = ScriptedChatModel([draft_json(citations=fabricated)])

    draft, notes = _writer(model, prompt_library).write(
        SPEC, book=book, research=degraded_research
    )

    assert draft.citations == ()
    assert any("2 sitasi dibuang" in note for note in notes)


def test_citations_matching_the_evidence_are_kept(
    prompt_library: FilePromptLibrary, book: BookState, supported_research: ResearchPackage
) -> None:
    """Dengan bahan tersedia, sumber yang benar-benar ada di paket boleh dikutip."""
    model = ScriptedChatModel([draft_json(citations=[CORMEN])])

    draft, notes = _writer(model, prompt_library).write(
        SPEC, book=book, research=supported_research
    )

    assert draft.citations == (CORMEN,)
    assert notes == ()


def test_invented_citations_are_dropped_even_when_research_is_available(
    prompt_library: FilePromptLibrary, book: BookState, supported_research: ResearchPackage
) -> None:
    """Bahan yang tersedia bukan izin untuk menambah rujukan lain di sebelahnya."""
    model = ScriptedChatModel([draft_json(citations=[CORMEN, "Buku Karangan, 2019"])])

    draft, _ = _writer(model, prompt_library).write(
        SPEC, book=book, research=supported_research
    )

    assert draft.citations == (CORMEN,)


def test_duplicate_citations_are_collapsed(
    prompt_library: FilePromptLibrary, book: BookState, supported_research: ResearchPackage
) -> None:
    model = ScriptedChatModel([draft_json(citations=[CORMEN, f"  {CORMEN}  "])])

    draft, _ = _writer(model, prompt_library).write(
        SPEC, book=book, research=supported_research
    )

    assert draft.citations == (CORMEN,)


def test_rewritten_learning_objectives_are_reverted(
    prompt_library: FilePromptLibrary, book: BookState, degraded_research: ResearchPackage
) -> None:
    """Draf yang tujuannya ditulis ulang tidak lagi dapat dinilai terhadap RPS."""
    model = ScriptedChatModel([draft_json(learning_objectives=["Tujuan karangan model"])])

    draft, notes = _writer(model, prompt_library).write(
        SPEC, book=book, research=degraded_research
    )

    assert draft.learning_objectives == (OBJECTIVE_1, OBJECTIVE_2)
    assert any("tujuan pembelajaran" in note for note in notes)


# ---------------------------------------------------------------------------
# 3. Jalur revisi
# ---------------------------------------------------------------------------
def _rejecting_draft() -> ChapterDraft:
    return ChapterDraft(
        title="Analisis Kompleksitas",
        learning_objectives=(OBJECTIVE_1, OBJECTIVE_2),
        citations=(CORMEN,),
    )


def test_revise_uses_the_revision_prompt_contract(
    prompt_library: FilePromptLibrary, book: BookState, supported_research: ResearchPackage
) -> None:
    """Jalur revisi memakai berkas prompt yang berbeda — bukan prompt tulis."""
    model = ScriptedChatModel([draft_json(citations=[CORMEN])])

    _writer(model, prompt_library).revise(
        _rejecting_draft(),
        spec=SPEC,
        book=book,
        feedback=("Sub-bab 2.1 belum menjelaskan notasi Big-O.",),
        revision=1,
        research=supported_research,
    )

    assert "merevisi" in model.requests[0].system
    assert "catatan peninjau" in model.requests[0].user.lower()


def test_revise_prompt_carries_every_note_and_the_current_draft(
    prompt_library: FilePromptLibrary, book: BookState, supported_research: ResearchPackage
) -> None:
    """Catatan yang dipangkas menghasilkan revisi yang ditolak dengan catatan yang sama."""
    notes = ("Sub-bab 2.1 belum menjelaskan notasi Big-O.", "Contoh ke-3 tidak dapat dijalankan.")
    model = ScriptedChatModel([draft_json(citations=[CORMEN])])

    _writer(model, prompt_library).revise(
        _rejecting_draft(),
        spec=SPEC,
        book=book,
        feedback=notes,
        revision=2,
        research=supported_research,
    )

    sent = model.requests[0].user
    assert all(note in sent for note in notes)
    assert "revisi ke-2" in sent
    assert "Analisis Kompleksitas" in sent, "draf saat ini harus ikut dikirim"


def test_revise_may_keep_inherited_citations_but_not_add_new_ones(
    prompt_library: FilePromptLibrary, book: BookState, degraded_research: ResearchPackage
) -> None:
    """Penulis tidak dihukum karena mempertahankan sitasi lama, tetapi tidak boleh menambah.

    Kondisi ini nyata: bab yang ditulis saat RAG tersedia lalu direvisi setelah
    profil berpindah ke paket terdegradasi. Menghapus sitasinya akan merusak bab
    yang sudah benar; membiarkan yang baru masuk akan membuka pintu halusinasi.
    """
    model = ScriptedChatModel([draft_json(citations=[CORMEN, "Buku Baru Karangan, 2020"])])

    draft, _ = _writer(model, prompt_library).revise(
        _rejecting_draft(),
        spec=SPEC,
        book=book,
        feedback=("Perbaiki.",),
        revision=1,
        research=degraded_research,
    )

    assert draft.citations == (CORMEN,)


def test_revise_does_not_mutate_the_incoming_draft(
    prompt_library: FilePromptLibrary, book: BookState, supported_research: ResearchPackage
) -> None:
    """Draf lama tetap utuh — pemanggilnya masih memegangnya untuk dibandingkan."""
    previous = _rejecting_draft()
    snapshot = previous.model_dump()
    model = ScriptedChatModel([draft_json(citations=[CORMEN])])

    _writer(model, prompt_library).revise(
        previous,
        spec=SPEC,
        book=book,
        feedback=("Perbaiki.",),
        revision=1,
        research=supported_research,
    )

    assert previous.model_dump() == snapshot


# ---------------------------------------------------------------------------
# 4. Tangga perbaikan — satu untuk kedua jalur
# ---------------------------------------------------------------------------
def test_broken_output_is_repaired_with_zero_temperature(
    prompt_library: FilePromptLibrary, book: BookState, degraded_research: ResearchPackage
) -> None:
    model = ScriptedChatModel(["bukan json", draft_json()])

    draft, _ = _writer(model, prompt_library).write(
        SPEC, book=book, research=degraded_research
    )

    assert model.call_count == 2
    assert model.temperatures() == (None, 0.0)
    assert draft.title == "Analisis Kompleksitas"


def test_persistent_failure_raises_with_the_raw_text(
    prompt_library: FilePromptLibrary, book: BookState, degraded_research: ResearchPackage
) -> None:
    """Percobaan habis: ``AgentOutputError`` membawa teks terakhir, bukan menelannya."""
    model = ScriptedChatModel(["rusak pertama", "rusak kedua", "rusak ketiga"])
    writer = _writer(model, prompt_library, max_repair_attempts=2)

    with pytest.raises(AgentOutputError) as excinfo:
        writer.write(SPEC, book=book, research=degraded_research)

    assert model.call_count == 3
    assert excinfo.value.attempts == 3
    assert excinfo.value.raw == "rusak ketiga"


def test_missing_required_field_triggers_repair_not_a_partial_draft(
    prompt_library: FilePromptLibrary, book: BookState, degraded_research: ResearchPackage
) -> None:
    """Draf tanpa ``title`` tidak dapat dipakai; ia diperbaiki, bukan diterima separuh."""
    incomplete = json.dumps({"sections": [{"heading": "A"}]})
    model = ScriptedChatModel([incomplete, draft_json()])

    draft, _ = _writer(model, prompt_library).write(
        SPEC, book=book, research=degraded_research
    )

    assert model.call_count == 2
    assert draft.title == "Analisis Kompleksitas"


def test_schema_echo_model_satisfies_both_writer_contracts(
    prompt_library: FilePromptLibrary, book: BookState, supported_research: ResearchPackage
) -> None:
    """Kedua kontrak prompt menunjuk model keluaran yang sama; keduanya harus dapat dijalankan."""
    model = SchemaEchoChatModel()
    writer = _writer(model, prompt_library)

    written, _ = writer.write(SPEC, book=book, research=supported_research)
    revised, _ = writer.revise(
        written,
        spec=SPEC,
        book=book,
        feedback=("Perbaiki.",),
        revision=1,
        research=supported_research,
    )

    assert isinstance(written, ChapterDraft)
    assert isinstance(revised, ChapterDraft)
    assert model.call_count == 2


# ---------------------------------------------------------------------------
# citations_allowed_by & enforce_draft_contract (murni)
# ---------------------------------------------------------------------------
def test_degraded_research_allows_nothing_on_its_own(
    degraded_research: ResearchPackage,
) -> None:
    assert citations_allowed_by(degraded_research) == frozenset()


def test_degraded_research_still_allows_what_was_already_there(
    degraded_research: ResearchPackage,
) -> None:
    allowed = citations_allowed_by(degraded_research, inherited=(CORMEN,))

    assert allowed == frozenset({CORMEN})


def test_available_research_allows_its_own_sources(
    supported_research: ResearchPackage,
) -> None:
    assert citations_allowed_by(supported_research) == frozenset({CORMEN})


def test_a_source_listed_only_in_evidence_is_still_allowed() -> None:
    """``sources`` yang kosong tidak boleh membatalkan sumber yang tampak di ``evidence``."""
    package = ResearchPackage(
        evidence=(Evidence(source=CORMEN, text="..."),), sources=(), degraded=False
    )

    assert citations_allowed_by(package) == frozenset({CORMEN})


def test_blank_evidence_sources_are_not_treated_as_citable() -> None:
    """Sumber kosong bukan sumber — tanpa penyaringan ini, sitasi kosong akan lolos."""
    package = ResearchPackage(evidence=(Evidence(source="   "),), degraded=False)

    assert citations_allowed_by(package) == frozenset()


def test_enforce_draft_contract_is_pure() -> None:
    draft = ChapterDraft(title="X", sections=(), citations=("Karangan",))
    snapshot = draft.model_dump()

    enforce_draft_contract(draft, spec=SPEC, allowed_citations=frozenset())

    assert draft.model_dump() == snapshot


def test_enforce_restores_a_blank_draft_title() -> None:
    draft = ChapterDraft(title="   ", citations=())

    fixed, notes = enforce_draft_contract(draft, spec=SPEC, allowed_citations=frozenset())

    assert fixed.title == SPEC.title
    assert any("judul draf kosong" in note for note in notes)


def test_a_clean_draft_produces_no_notes() -> None:
    """Catatan hanya muncul saat ada yang dipulihkan."""
    draft = ChapterDraft(
        title="Analisis Kompleksitas",
        learning_objectives=(OBJECTIVE_1, OBJECTIVE_2),
        citations=(CORMEN,),
    )

    _, notes = enforce_draft_contract(
        draft, spec=SPEC, allowed_citations=frozenset({CORMEN})
    )

    assert notes == ()
