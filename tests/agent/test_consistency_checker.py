"""Tes Consistency Checker dan gate-nya (§24).

Gate ini berbeda dari pemeriksa lain dalam satu hal yang menentukan seluruh
bentuk tesnya: **tidak ada lapis deterministik yang menolak.** §22 dan §23 dapat
memastikan kegagalan sebelum model dipanggil (kunci sitasi asing, anak tangga
yang absen); di sini kesembilan hal yang §24 minta diperiksa — terminologi,
notasi, akronim, definisi, variabel, rujukan bab, dan ketiga penomoran —
seluruhnya penilaian.

Yang dikerjakan program karena itu bukan menolak, melainkan **membuat
pertanyaannya terlihat**, dan yang perlu dibuktikan ada dua:

1. **Dugaan penyimpangan istilah benar-benar sampai ke model.** Tanpa itu,
   contoh §24 sendiri (*finite automaton* di bab 2, *finite-state machine* di
   bab 7) hanya ditemukan bila model kebetulan memutuskan membandingkannya.
2. **Pertanyaan yang tidak dijawab dilaporkan.** Dua kewajiban prompt —
   kesembilan hal dijawab, setiap dugaan dijawab — diperiksa program, bukan oleh
   model yang sama yang diminta menepatinya.

Satu hal lagi yang hanya dimiliki gate ini: **glosariumnya masuk ke memori
bersama**, dan hanya bila sistem menyetujui babnya. Itu satu-satunya jalan
``BookState.terminology`` terisi.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.consistency_checker import (
    ConsistencyCheckerAgent,
    ConsistencyGate,
    candidate_terms,
    unanswered_drift,
)
from agents.gates import GateContext, build_gates
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, Section
from domain.checking import CheckFinding
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.graph import CHECKS, TerminologyDrift, detect_terminology_drift
from domain.state import BookState
from domain.transitions import can_advance
from tests.fakes.chat_models import ScriptedChatModel

SPEC = ChapterSpec(
    number=7,
    title="Mesin Keadaan Berhingga",
    objectives=("Mahasiswa mampu menjelaskan automaton berhingga.",),
    sections=("finite-state machine",),
    required_examples=2,
    required_exercises=4,
)

#: Draf yang judul sub-babnya memakai istilah yang **berbeda secara mekanis**
#: dari yang sudah dipakai buku — inilah yang memicu pemeriksaan awal.
DRIFTING = ChapterDraft(
    title="Mesin Keadaan Berhingga",
    learning_objectives=("Mahasiswa mampu menjelaskan automaton berhingga.",),
    sections=(Section(heading="finite state machine", body="Automaton berhingga."),),
    summary="Bab ini membahas automaton berhingga.",
)

#: Istilah yang sudah dipakai bab-bab sebelumnya. Bentuk bertanda hubung dipilih
#: dengan sadar: perbedaan seperti inilah yang **dapat** dipastikan program,
#: sedangkan contoh §24 sendiri (*finite automaton* / *finite-state machine*)
#: adalah sinonim sungguhan yang hanya dapat diputuskan model — lihat
#: ``tests/unit/test_graph.py``.
KNOWN_TERMINOLOGY = {"finite-state machine": "Mesin keadaan berhingga."}

DRIFT = TerminologyDrift(
    term="finite state machine",
    known="finite-state machine",
    reason="hanya berbeda tanda hubung atau spasi",
)


def finding(subject: str, *, ok: bool = True, detail: str = "terpenuhi") -> dict[str, Any]:
    """Satu temuan model atas sebuah subjek."""
    return {"subject": subject, "ok": ok, "detail": detail, "source": ""}


def every_check_ok(*, glossary: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Kesembilan hal §24, semuanya terpenuhi."""
    return [finding(name) for name in CHECKS]


def verdict_json(**overrides: Any) -> str:
    """Balasan ``ConsistencyVerdict`` yang sah: kesembilan hal dijawab."""
    payload: dict[str, Any] = {
        "approved": True,
        "score": 9,
        "feedback": [],
        "findings": every_check_ok(),
        "glossary": [],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


GLOSSARY = [{"term": "finite-state machine", "definition": "Mesin keadaan berhingga."}]


class StubProvider:
    """``ModelProvider`` palsu yang mencatat peran yang diminta.

    ``embedder`` sengaja melempar: pemeriksa konsistensi tidak punya urusan
    dengan embedding, dan tes ini membuktikannya dengan gagal keras bila suatu
    saat ia memintanya.
    """

    def __init__(self, model: Any) -> None:
        self._model = model
        self.requested: list[str] = []

    def chat(self, role: str) -> Any:
        self.requested.append(role)
        return self._model

    def embedder(self, role: str = "embedding") -> Any:
        raise AssertionError("pemeriksa konsistensi tidak boleh meminta model embedding")


@pytest.fixture
def book() -> BookState:
    """Buku yang sudah punya dua bab: satu istilah tercatat, satu ringkasan."""
    return BookState(
        request=BookRequest(title="Algoritma", target_chapters=8),
        spec=BookSpec(title="Algoritma", chapters=(SPEC,)),
        terminology=KNOWN_TERMINOLOGY,
        summaries={2: "Bab 2 membahas finite automaton."},
    )


@pytest.fixture
def empty_book() -> BookState:
    """Buku yang belum punya satu pun istilah — bab pertama."""
    return BookState(request=BookRequest(title="Algoritma", target_chapters=8))


@pytest.fixture
def record() -> ChapterRecord:
    """Bab yang datang dari pemeriksaan pedagogi."""
    return ChapterRecord(
        number=7,
        status=ChapterStatus.PEDAGOGY_REVIEWED,
        spec=SPEC,
        draft=DRIFTING,
    )


def make_gate(
    model: ScriptedChatModel, prompts: FilePromptLibrary, **kwargs: Any
) -> ConsistencyGate:
    return ConsistencyGate(model=model, prompts=prompts, **kwargs)


# ---------------------------------------------------------------------------
# 1. Fungsi murni — apa yang diketahui program tentang bab ini
# ---------------------------------------------------------------------------
def test_the_candidate_terms_are_the_section_headings() -> None:
    """Prosa tidak dapat dipotong menjadi istilah tanpa penilaian — dan itu tugas model."""
    draft = DRIFTING.model_copy(
        update={
            "sections": (
                Section(heading="  finite state machine  ", body="A."),
                Section(heading="   ", body="B."),
                Section(heading="Notasi", body="C."),
            )
        }
    )

    assert candidate_terms(draft) == ("finite state machine", "Notasi")


def test_a_draft_without_sections_has_no_candidate_terms() -> None:
    assert candidate_terms(DRIFTING.model_copy(update={"sections": ()})) == ()


def test_the_fixture_suspicion_is_the_one_the_domain_function_finds() -> None:
    """Pemeriksa sendiri atas premis seluruh berkas ini.

    Sisa berkas ini mengandaikan ``DRIFTING`` benar-benar memicu dugaan terhadap
    ``KNOWN_TERMINOLOGY``. Andaikannya salah, setiap tes di bawah akan tetap
    **hijau** — karena gate yang tidak menemukan dugaan tidak melaporkan apa pun —
    sementara seluruh berkas ini berhenti menguji apa yang dikatakannya menguji.
    Karena itu premisnya diperiksa di sini, sekali, terhadap fungsi yang
    sesungguhnya dipakai gate.
    """
    found = detect_terminology_drift(candidate_terms(DRIFTING), tuple(KNOWN_TERMINOLOGY))

    assert found == (DRIFT,)


def test_a_suspicion_the_model_never_mentions_is_reported_as_unanswered() -> None:
    """Kewajiban prompt yang diperiksa program, bukan oleh model yang sama."""
    findings = (CheckFinding(subject="Terminology", ok=True, detail="tidak ada penyimpangan"),)

    assert unanswered_drift((DRIFT,), findings) == (DRIFT,)


def test_a_suspicion_answered_inside_the_terminology_finding_is_not_reported() -> None:
    """Jawabannya tidak perlu berupa temuan tersendiri; detailnya sudah cukup."""
    findings = (
        CheckFinding(
            subject="Terminology",
            ok=True,
            detail="finite state machine sama dengan finite automaton; dipakai sebagai sinonim.",
        ),
    )

    assert unanswered_drift((DRIFT,), findings) == ()


def test_the_matching_ignores_case_because_the_model_may_rephrase() -> None:
    findings = (
        CheckFinding(subject="terminology", ok=True, detail="FINITE STATE MACHINE sinonim"),
    )

    assert unanswered_drift((DRIFT,), findings) == ()


def test_an_empty_drift_has_nothing_to_answer() -> None:
    assert unanswered_drift((), ()) == ()


# ---------------------------------------------------------------------------
# 2. Prasyarat
# ---------------------------------------------------------------------------
def test_a_record_without_a_spec_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Bab tak bernomor tidak punya pembanding terhadap bab lain."""
    bare = ChapterRecord(number=7, status=ChapterStatus.PEDAGOGY_REVIEWED, draft=DRIFTING)

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(ScriptedChatModel([]), prompt_library).evaluate(bare, book)

    assert "spesifikasi bab" in str(excinfo.value)


def test_a_record_without_a_draft_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Membandingkan buku dengan bab yang belum ditulis berarti membandingkan dengan ketiadaan."""
    bare = ChapterRecord(number=7, status=ChapterStatus.PEDAGOGY_REVIEWED, spec=SPEC)

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(ScriptedChatModel([]), prompt_library).evaluate(bare, book)

    assert "draf" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 3. Dugaan penyimpangan istilah sampai ke model
# ---------------------------------------------------------------------------
def test_a_suspicion_reaches_the_prompt_as_a_question(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Tanpa ini, contoh §24 hanya ditemukan bila model kebetulan membandingkannya.

    Yang dikirim bukan sekadar "ada yang mencurigakan", melainkan **apa**
    perbedaannya: model yang menerima "keduanya mirip" tidak punya apa pun untuk
    diputuskan, sedangkan "hanya berbeda tanda hubung atau spasi" adalah
    pertanyaan yang jelas.
    """
    model = ScriptedChatModel([verdict_json()])

    make_gate(model, prompt_library).evaluate(record, book)

    sent = model.requests[0].user
    assert DRIFT.term in sent
    assert DRIFT.known in sent
    assert DRIFT.reason in sent


def test_the_prompt_carries_the_nine_checks_as_a_list(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Daftar periksa yang tersembunyi di dalam prosa instruksi adalah daftar yang dilewati."""
    model = ScriptedChatModel([verdict_json()])

    make_gate(model, prompt_library).evaluate(record, book)

    sent = model.requests[0].user
    for check in CHECKS:
        assert check in sent, f"hal §24 {check!r} tidak ikut terkirim"


def test_the_prompt_carries_the_rest_of_the_book(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Yang dibandingkan adalah bab dengan **seluruh** buku, jadi bukunya harus ikut."""
    model = ScriptedChatModel([verdict_json()])

    make_gate(model, prompt_library).evaluate(record, book)

    sent = model.requests[0].user
    for term, definition in KNOWN_TERMINOLOGY.items():
        assert term in sent, "istilah buku tidak ikut terkirim"
        assert definition in sent, "definisi istilah buku tidak ikut terkirim"
    assert "Bab 2 membahas" in sent, "ringkasan bab sebelumnya tidak ikut terkirim"
    assert "finite state machine" in sent, "drafnya tidak ikut terkirim"


def test_a_book_without_terms_produces_no_suspicion(
    prompt_library: FilePromptLibrary, empty_book: BookState, record: ChapterRecord
) -> None:
    """Bab pertama tidak punya apa pun untuk dibandingkan — dan itu bukan kegagalan."""
    model = ScriptedChatModel([verdict_json()])

    result = make_gate(model, prompt_library).evaluate(record, empty_book)

    assert result.approved is True
    assert not any("tidak dijawab" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 4. Laporan: hal yang tidak dikerjakan model tetap terlihat
# ---------------------------------------------------------------------------
def test_a_check_the_model_never_mentions_is_reported_as_unexamined(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Model yang menyebut delapan dari sembilan belum memeriksa yang kesembilan.

    Dilaporkan tetapi **tidak** menurunkan vonis: yang kurang di sini laporan,
    bukan babnya.
    """
    spoken = [finding(name) for name in CHECKS if name != "Table numbering"]
    model = ScriptedChatModel([verdict_json(findings=spoken)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is True
    assert any(
        "tidak diperiksa model" in note and "Table numbering" in note for note in result.feedback
    )


def test_a_suspicion_the_model_ignores_is_reported_as_unanswered(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Model menjawab kesembilan hal, tetapi tidak menjawab pertanyaan yang diajukan padanya."""
    findings = [finding(name, detail="tidak ada penyimpangan") for name in CHECKS]
    model = ScriptedChatModel([verdict_json(findings=findings)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is True, "yang kurang di sini laporan, bukan babnya"
    assert any("tidak dijawab model" in note for note in result.feedback)
    assert any(DRIFT.term in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 5. Vonis
# ---------------------------------------------------------------------------
def test_a_consistent_chapter_is_accepted(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Jalur bahagia: kesembilan hal terpenuhi, penyimpangannya dijawab."""
    findings = [finding(name) for name in CHECKS]
    findings[0] = finding("Terminology", detail="finite state machine = finite automaton, sinonim.")
    model = ScriptedChatModel([verdict_json(findings=findings)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 1
    assert result.approved is True
    assert result.skipped is False, "ini pemeriksaan, bukan pelewatan"
    assert result.score == 9
    assert result.gate == "consistency_checker"


def test_an_inconsistent_chapter_is_rejected_and_its_feedback_is_actionable(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """§24: peninjau menyebut istilah mana yang harus distandardisasi."""
    findings = every_check_ok()
    findings[0] = finding(
        "Terminology",
        ok=False,
        detail="finite state machine dan finite automaton dipakai bergantian untuk hal yang sama.",
    )
    model = ScriptedChatModel([verdict_json(approved=False, score=4, findings=findings)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert result.score == 4
    assert any("Terminology" in note and "bergantian" in note for note in result.feedback)


def test_the_verdict_is_enforced_when_the_model_contradicts_its_own_findings(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Model yang menyetujui bab sambil menandai penyimpangan membatalkan pekerjaannya sendiri."""
    findings = every_check_ok()
    findings[0] = finding("Terminology", ok=False, detail="dua istilah dipakai bergantian.")
    findings[0]["detail"] = "finite state machine vs finite automaton."
    model = ScriptedChatModel([verdict_json(approved=True, score=9, findings=findings)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert any("diabaikan oleh sistem" in note for note in result.feedback)


def test_a_score_under_the_threshold_is_lowered_by_the_shared_rule(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Ambang ditegakkan sistem, bukan diserahkan kepada pemeriksa."""
    model = ScriptedChatModel([verdict_json(approved=True, score=6)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert any("di bawah ambang 7" in note for note in result.feedback)


def test_the_threshold_comes_from_the_caller(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Ambang adalah konfigurasi, bukan konstanta yang tertanam di gate."""
    model = ScriptedChatModel([verdict_json(approved=True, score=6)])

    assert make_gate(model, prompt_library, threshold=5).evaluate(record, book).approved is True


# ---------------------------------------------------------------------------
# 6. Glosarium — satu-satunya jalan BookState.terminology terisi
# ---------------------------------------------------------------------------
def test_an_approved_chapter_carries_its_glossary_to_shared_memory(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Hanya pemeriksaan inilah yang membaca bab terhadap seluruh buku.

    Karena itu hanya ia yang tahu istilah apa saja yang bab ini pakai — dan
    tanpa itu, bab ketujuh tidak punya apa pun untuk dibandingkan dengan bab
    kedua.
    """
    model = ScriptedChatModel([verdict_json(glossary=GLOSSARY)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.terminology == {"finite-state machine": "Mesin keadaan berhingga."}


def test_a_rejected_chapter_contributes_no_glossary(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Glosarium bab yang ditolak adalah catatan tentang bab yang menuju revisi."""
    model = ScriptedChatModel([verdict_json(approved=False, score=3, glossary=GLOSSARY)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.terminology == {}


def test_a_chapter_the_system_rejects_contributes_no_glossary_either(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Bukan vonis **model** yang menentukan, melainkan vonis **sistem**.

    ``decide_review`` menurunkan persetujuan karena skornya di bawah ambang, dan
    glosarium bab yang ditolak sistem belum tentu mewakili bab yang akan dibaca
    mahasiswa.
    """
    model = ScriptedChatModel([verdict_json(approved=True, score=4, glossary=GLOSSARY)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert result.terminology == {}


def test_an_empty_glossary_is_not_a_failure(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Bab yang tidak memperkenalkan satu istilah pun adalah bab yang sah."""
    model = ScriptedChatModel([verdict_json(glossary=[])])

    assert make_gate(model, prompt_library).evaluate(record, book).terminology == {}


# ---------------------------------------------------------------------------
# 7. Agent dan rantai §27
# ---------------------------------------------------------------------------
def test_the_agent_asks_for_the_schema_it_declares(prompt_library: FilePromptLibrary) -> None:
    """``format=`` tidak pernah dicabut, dan glosariumnya termasuk di dalamnya."""
    model = ScriptedChatModel([verdict_json()])
    agent = ConsistencyCheckerAgent(model=model, prompts=prompt_library)

    agent.review(DRIFTING, spec=SPEC, book=BookState(request=BookRequest(title="B")))

    schema = model.schemas()[0]
    assert schema is not None
    assert set(schema["properties"]) == {"approved", "score", "feedback", "findings", "glossary"}


def test_the_agent_uses_the_reviewer_role_and_its_own_prompt() -> None:
    """Peran ``reviewer``: memutuskan sesuatu tentang pekerjaan yang sudah ada."""
    assert ConsistencyCheckerAgent.role == "reviewer"
    assert ConsistencyCheckerAgent.prompt_name == "consistency.chapter"


def test_the_gate_produces_the_status_the_chain_expects() -> None:
    """``produces`` harus memajukan rantai dari status yang mendahuluinya."""
    assert ConsistencyGate.name == "consistency_checker"
    assert ConsistencyGate.produces is ChapterStatus.CONSISTENCY_CHECKED
    assert can_advance(ChapterStatus.PEDAGOGY_REVIEWED, ConsistencyGate.produces)
    assert can_advance(ConsistencyGate.produces, ChapterStatus.REVIEWED)


def test_the_gate_takes_its_model_from_the_router_by_role(
    prompt_library: FilePromptLibrary,
) -> None:
    """Peran → model ditentukan ``config.yaml``, bukan oleh gate."""
    provider = StubProvider(ScriptedChatModel([verdict_json()]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
    )

    gates = build_gates(("consistency_checker",), context)

    assert provider.requested == ["reviewer"]
    assert gates[0].name == "consistency_checker"
    assert gates[0].produces is ChapterStatus.CONSISTENCY_CHECKED
