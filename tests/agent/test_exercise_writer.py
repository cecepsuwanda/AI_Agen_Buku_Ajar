"""Tes Exercise Agent dan gate-nya (§20).

Dua hal yang diuji di sini adalah dua hal yang paling mudah salah pada §20:

1. **Sebaran tingkat kesulitan dihitung, bukan ditanyakan.** Model yang mengisi
   sendiri field ``difficulty_mix`` akan menuliskan *ringkasan* — dan ringkasan
   yang dikarang selalu lebih lemah daripada ringkasan yang dihitung dari butir
   yang benar-benar ada. Karena itu yang muncul di laporan adalah hasil hitungan,
   termasuk ketika sebarannya sempit: §20 meminta jangkauan, dan jangkauan yang
   tidak tercapai harus **terlihat**, bukan dirapikan.
2. **Kesesuaian latihan dengan tujuan tidak diperiksa secara mekanis.** Itu
   pertanyaan penilaian yang ditugaskan §23 kepada Pedagogy Reviewer;
   membandingkan teks ``objective`` dengan daftar tujuan akan menolak latihan
   yang baik hanya karena diparafrase. Seperti gate contoh, gate ini selalu
   meloloskan drafnya dan menyerahkan hasil kerjanya lewat ``enriched_draft``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.exercise_writer import ExerciseWriterAgent, ExerciseWriterGate
from agents.gates import GateContext
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ResearchPackage, Section
from domain.enums import ChapterStatus
from domain.errors import AgentOutputError, GatePreconditionError
from domain.state import BookState
from tests.fakes.chat_models import ScriptedChatModel

OBJECTIVE_1 = "Mahasiswa mampu menjelaskan pencarian linear"
OBJECTIVE_2 = "Mahasiswa mampu membandingkan pencarian linear dan biner"

SPEC = ChapterSpec(
    number=1,
    title="Algoritma Pencarian",
    objectives=(OBJECTIVE_1, OBJECTIVE_2),
    sections=("Pencarian linear", "Pencarian biner"),
    required_exercises=5,
)

DRAFT = ChapterDraft(
    title="Algoritma Pencarian",
    learning_objectives=(OBJECTIVE_1, OBJECTIVE_2),
    sections=(Section(heading="Pencarian linear", body="Pencarian linear memeriksa tiap elemen."),),
    summary="Bab ini membandingkan dua algoritma pencarian.",
)


def exercise_payload(n: int, **overrides: Any) -> dict[str, Any]:
    """Satu butir latihan yang sah; ``n`` membuat soalnya berbeda dari yang lain."""
    payload: dict[str, Any] = {
        "prompt": f"Soal {n}: hitung kompleksitas pencarian pada {n * 100} elemen.",
        "difficulty": "mudah",
        "objective": OBJECTIVE_1,
        "hint": "Ingat bahwa ruang pencarian dibagi dua.",
        "answer": "O(log n).",
    }
    payload.update(overrides)
    return payload


def exercises_json(items: list[dict[str, Any]], **overrides: Any) -> str:
    """Balasan ``ExerciseSet`` dari daftar butir yang sudah disusun."""
    payload: dict[str, Any] = {"exercises": items, "notes": []}
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def mix_json(levels: tuple[str, ...]) -> str:
    """Balasan dengan satu latihan per tingkat kesulitan yang diberikan."""
    items = [exercise_payload(n, difficulty=level) for n, level in enumerate(levels)]
    return exercises_json(items)


class StubProvider:
    """``ModelProvider`` palsu yang mencatat peran yang diminta."""

    def __init__(self, model: Any) -> None:
        self._model = model
        self.requested: list[str] = []

    def chat(self, role: str) -> Any:
        self.requested.append(role)
        return self._model

    def embedder(self, role: str = "embedding") -> Any:
        raise AssertionError("gate latihan tidak boleh meminta model embedding")


@pytest.fixture
def book() -> BookState:
    return BookState(
        request=BookRequest(title="Algoritma", target_chapters=1),
        spec=BookSpec(title="Algoritma", chapters=(SPEC,)),
    )


@pytest.fixture
def record() -> ChapterRecord:
    return ChapterRecord(
        number=1,
        status=ChapterStatus.EXAMPLES_WRITTEN,
        spec=SPEC,
        draft=DRAFT,
        research=ResearchPackage.empty(),
    )


def make_gate(
    model: ScriptedChatModel, prompts: FilePromptLibrary, **kwargs: Any
) -> ExerciseWriterGate:
    return ExerciseWriterGate(model=model, prompts=prompts, **kwargs)


# ---------------------------------------------------------------------------
# 1. Jalur bahagia
# ---------------------------------------------------------------------------
def test_enough_exercises_pass_with_a_perfect_score(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel([mix_json(("mudah", "sedang", "sedang", "sulit", "sulit"))])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 1
    assert result.approved is True
    assert result.score == 10
    assert result.skipped is False
    assert result.gate == "exercise_writer"
    assert any("5 latihan dihasilkan dari 5 yang diminta" in note for note in result.feedback)


def test_the_gate_hands_back_the_draft_it_enriched(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel([mix_json(("mudah",) * 5)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.enriched_draft is not None
    assert len(result.enriched_draft.exercises) == 5
    assert result.enriched_draft.sections == DRAFT.sections
    assert result.enriched_draft.title == DRAFT.title


def test_the_answer_survives_into_the_markdown_inside_a_collapsed_block(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Bahan yang dibayar token tidak dibuang; ia hanya tidak terlihat langsung."""
    model = ScriptedChatModel([mix_json(("mudah",) * 5)])

    rendered = make_gate(model, prompt_library).evaluate(record, book).enriched_draft.exercises[0]

    assert "<summary>Kunci jawaban</summary>" in rendered
    assert "O(log n)." in rendered


# ---------------------------------------------------------------------------
# 2. Sebaran tingkat kesulitan — dihitung, dilaporkan apa adanya
# ---------------------------------------------------------------------------
def test_the_difficulty_mix_is_reported_and_ordered(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel([mix_json(("sulit", "mudah", "sedang", "mudah", "sedang"))])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert any("Tingkat kesulitan: 2 mudah, 2 sedang, 1 sulit." in note for note in result.feedback)


def test_a_narrow_mix_is_reported_not_polished(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """§20 meminta jangkauan; jangkauan yang sempit adalah temuan, bukan cacat laporan."""
    model = ScriptedChatModel([mix_json(("mudah",) * 5)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.score == 10, "lima latihan mudah tetap lima latihan yang sah"
    assert any(note.startswith("Tingkat kesulitan: 5 mudah") for note in result.feedback)


def test_the_mix_line_appears_even_when_everything_failed(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Tanpa baris ini, laporan kegagalan tidak menyebut apa pun tentang sebaran."""
    model = ScriptedChatModel(
        [exercises_json([exercise_payload(0, prompt="   ")])] * 3
    )

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert any("Tingkat kesulitan: tidak ada latihan" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 3. Pemeriksaan sebelum model dipanggil lagi
# ---------------------------------------------------------------------------
def test_a_level_outside_the_known_list_triggers_repair_with_the_valid_values(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Label tingkat tercetak di buku, dan prompt sudah menyebut ketiga nilai yang sah."""
    bad = [exercise_payload(n, difficulty="medium") for n in range(5)]
    good = [exercise_payload(n, difficulty="sedang") for n in range(5)]
    model = ScriptedChatModel([exercises_json(bad), exercises_json(good)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 2
    assert result.score == 10
    assert "medium" in model.requests[1].user
    assert "sulit" in model.requests[1].user, "daftar nilai yang sah harus ikut dikirim"


def test_an_exercise_without_an_objective_triggers_repair(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Latihan tanpa ikatan tujuan tidak dapat diperiksa kesesuaiannya kelak (§23)."""
    bad = [exercise_payload(n, objective="") for n in range(5)]
    good = [exercise_payload(n) for n in range(5)]
    model = ScriptedChatModel([exercises_json(bad), exercises_json(good)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 2
    assert result.score == 10
    assert "tujuan" in model.requests[1].user


def test_a_paraphrased_objective_does_not_trigger_a_repair(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Pencocokan teks sengaja tidak dilakukan — keputusan itu dikunci di sini.

    Latihan yang memparafrase tujuan pembelajaran adalah latihan yang baik.
    Menolaknya secara mekanis berarti membakar dua panggilan model plus satu skor
    rendah yang tidak berdasar, untuk setiap bab.
    """
    paraphrased = [
        exercise_payload(n, objective="Menjelaskan cara kerja pencarian linear")
        for n in range(5)
    ]
    model = ScriptedChatModel([exercises_json(paraphrased)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 1
    assert result.score == 10


# ---------------------------------------------------------------------------
# 4. Tangga perbaikan dan batasnya
# ---------------------------------------------------------------------------
def test_a_shortfall_triggers_another_attempt_with_the_findings_in_the_prompt(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel(
        [exercises_json([exercise_payload(0)]), mix_json(("mudah",) * 5)]
    )

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 2
    assert result.score == 10
    assert "hanya 1 dari 5" in model.requests[1].user


def test_an_exhausted_ladder_reports_a_shortfall_instead_of_rejecting(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel([exercises_json([exercise_payload(0)])] * 3)

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 3
    assert result.approved is True, "gate penulisan tidak menolak bab"
    assert result.score == 5
    assert any("hanya 1 dari 5" in note for note in result.feedback)
    assert any("belum memenuhi syarat" in note for note in result.feedback)


def test_no_usable_exercise_at_all_scores_zero(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel(
        [exercises_json([exercise_payload(0, prompt="  \n ")])] * 3
    )

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.score == 0
    assert any("Tidak satu pun latihan dapat dipakai" in note for note in result.feedback)


def test_a_model_that_never_returns_json_raises_rather_than_rejecting(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel(["rusak", "rusak", "rusak"])

    with pytest.raises(AgentOutputError):
        make_gate(model, prompt_library, repair_attempts=2).evaluate(record, book)


# ---------------------------------------------------------------------------
# 5. Bab tanpa latihan, dan latihan dari penulis
# ---------------------------------------------------------------------------
def test_a_chapter_that_requests_no_exercises_skips_without_spending_a_token(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    spec = SPEC.model_copy(update={"required_exercises": 0})
    record = ChapterRecord(number=1, status=ChapterStatus.DRAFTED, spec=spec, draft=DRAFT)
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 0
    assert result.skipped is True
    assert result.enriched_draft is None


def test_exercises_from_the_writer_are_replaced_and_the_replacement_is_reported(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    draft_with_exercises = DRAFT.model_copy(
        update={"exercises": ("Latihan lama dari penulis.",)}
    )
    record = ChapterRecord(
        number=1,
        status=ChapterStatus.DRAFTED,
        spec=SPEC,
        draft=draft_with_exercises,
        research=ResearchPackage.empty(),
    )
    model = ScriptedChatModel([mix_json(("mudah",) * 5)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.enriched_draft is not None
    assert "Latihan lama dari penulis." not in result.enriched_draft.exercises
    assert any("digantikan" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 6. Prasyarat
# ---------------------------------------------------------------------------
def test_a_record_without_a_draft_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    bare = ChapterRecord(number=1, status=ChapterStatus.PLANNED, spec=SPEC)
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library).evaluate(bare, book)

    assert excinfo.value.missing == "draf"
    assert model.call_count == 0


def test_a_record_without_a_spec_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    bare = ChapterRecord(number=1, status=ChapterStatus.DRAFTED, draft=DRAFT)
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library).evaluate(bare, book)

    assert excinfo.value.missing == "spesifikasi bab"
    assert model.call_count == 0


# ---------------------------------------------------------------------------
# 7. Apa yang dikirim ke model, dan dari mana modelnya datang
# ---------------------------------------------------------------------------
def test_the_prompt_carries_the_objectives_and_the_required_count(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel([mix_json(("mudah",) * 5)])

    make_gate(model, prompt_library).evaluate(record, book)

    sent = model.requests[0].user
    assert OBJECTIVE_1 in sent
    assert OBJECTIVE_2 in sent
    assert "Algoritma Pencarian" in sent
    assert "5" in sent


def test_the_agent_asks_for_the_schema_it_declares(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel([mix_json(("mudah",) * 5)])

    make_gate(model, prompt_library).evaluate(record, book)

    schema = model.schemas()[0]
    assert schema is not None
    assert set(schema["properties"]) == {"exercises", "notes"}


def test_the_agent_uses_the_writer_role(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Latihan adalah prosa — gaya bahasanya harus sama dengan bab yang dilatihkan."""
    assert ExerciseWriterAgent.role == "writer"
    assert ExerciseWriterAgent.prompt_name == "exercise.chapter"


def test_the_gate_takes_its_model_from_the_router_by_role(
    prompt_library: FilePromptLibrary,
) -> None:
    """Satu-satunya tempat ``router.chat("writer")`` dipanggil untuk gate ini."""
    from agents.gates import build_gates  # lokal: menghindari impor melingkar

    provider = StubProvider(ScriptedChatModel([mix_json(("mudah",) * 5)]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
    )

    gates = build_gates(("exercise_writer",), context)

    assert provider.requested == ["writer"]
    assert gates[0].name == "exercise_writer"
    assert gates[0].produces is ChapterStatus.EXERCISES_WRITTEN
