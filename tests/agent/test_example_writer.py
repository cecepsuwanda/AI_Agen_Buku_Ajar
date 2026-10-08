"""Tes Example Agent dan gate-nya (§19).

Yang paling mudah salah di sini bukan penulisan contohnya, melainkan **siapa
yang memutuskan**. Gate ini adalah tahap penulisan: ia selalu meloloskan drafnya
dan menyerahkan hasil kerjanya lewat ``enriched_draft``. Kekurangan yang tersisa
dilaporkan dengan catatan yang keras dan skor rendah, lalu diteruskan kepada
peninjau — satu-satunya pihak yang berwenang menolak bab. Tes di bawah mengunci
pembagian wewenang itu, termasuk saat model gagal total.

Yang kedua: **tangga perbaikan harus memakai temuan**. Percobaan ulang yang
mengirim prompt yang sama persis adalah token yang dibakar untuk jawaban yang
sama; karena itu tesnya memeriksa bahwa temuan benar-benar sampai ke permintaan
berikutnya, bukan sekadar bahwa jumlah panggilannya bertambah.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.example_writer import ExampleWriterAgent, ExampleWriterGate
from agents.gates import GateContext
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ResearchPackage, Section
from domain.enums import ChapterStatus
from domain.errors import AgentOutputError, GatePreconditionError
from domain.state import BookState
from tests.fakes.chat_models import ScriptedChatModel

OBJECTIVE = "Mahasiswa mampu mengenali pencarian linear pada larik"

SPEC = ChapterSpec(
    number=1,
    title="Algoritma Pencarian",
    objectives=(OBJECTIVE,),
    sections=("Pencarian linear", "Pencarian biner"),
    required_examples=3,
)

DRAFT = ChapterDraft(
    title="Algoritma Pencarian",
    learning_objectives=(OBJECTIVE,),
    sections=(Section(heading="Pencarian linear", body="Pencarian linear memeriksa tiap elemen."),),
    summary="Bab ini membandingkan dua algoritma pencarian.",
)


def example_payload(n: int, **overrides: Any) -> dict[str, Any]:
    """Satu contoh yang sah; ``n`` membuat kodenya berbeda dari contoh lain."""
    payload: dict[str, Any] = {
        "title": f"Pencarian contoh {n}",
        "language": "python",
        "code": f"def cari{n}(data, target):\n    return target in data",
        "expected_output": "True",
        "explanation": f"Contoh {n} memakai pencarian linear.",
    }
    payload.update(overrides)
    return payload


def examples_json(count: int, **overrides: Any) -> str:
    """Balasan ``ExampleSet`` dengan ``count`` contoh yang berbeda-beda."""
    payload: dict[str, Any] = {
        "examples": [example_payload(n) for n in range(count)],
        "notes": [],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class StubProvider:
    """``ModelProvider`` palsu yang mencatat peran yang diminta.

    ``embedder`` sengaja melempar: gate contoh tidak punya urusan dengan
    embedding, dan tes ini membuktikannya dengan gagal keras bila suatu saat ia
    memintanya.
    """

    def __init__(self, model: Any) -> None:
        self._model = model
        self.requested: list[str] = []

    def chat(self, role: str) -> Any:
        self.requested.append(role)
        return self._model

    def embedder(self, role: str = "embedding") -> Any:
        raise AssertionError("gate contoh tidak boleh meminta model embedding")


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
        status=ChapterStatus.DRAFTED,
        spec=SPEC,
        draft=DRAFT,
        research=ResearchPackage.empty(),
    )


def make_gate(
    model: ScriptedChatModel, prompts: FilePromptLibrary, **kwargs: Any
) -> ExampleWriterGate:
    return ExampleWriterGate(model=model, prompts=prompts, **kwargs)


# ---------------------------------------------------------------------------
# 1. Jalur bahagia — gate menulis, bukan menilai
# ---------------------------------------------------------------------------
def test_enough_examples_pass_with_a_perfect_score(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel([examples_json(3)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 1
    assert result.approved is True
    assert result.score == 10
    assert result.skipped is False
    assert result.gate == "example_writer"


def test_the_gate_hands_back_the_draft_it_enriched(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """``enriched_draft`` adalah satu-satunya cara gate ini menyerahkan kerjanya.

    :class:`~domain.rules.with_gate_result` yang memasangnya ke record; karena
    itu field ini harus ada dan harus berisi draf **yang sama** dengan yang
    diterima, bukan draf kosong.
    """
    model = ScriptedChatModel([examples_json(3)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.enriched_draft is not None
    assert len(result.enriched_draft.examples) == 3
    assert result.enriched_draft.sections == DRAFT.sections
    assert result.enriched_draft.title == DRAFT.title


def test_the_rendered_examples_are_markdown_not_raw_json(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """``ChapterDraft.examples`` bertipe ``tuple[str, ...]``; gate-nya penerjemah.

    Yang tersimpan di draf adalah Markdown yang siap dirender — judulnya tebal,
    kodenya berpagar, penjelasannya utuh. Kalau yang tersimpan adalah JSON,
    ``render_chapter_markdown`` akan mencetaknya apa adanya ke buku.
    """
    model = ScriptedChatModel([examples_json(3)])

    example = make_gate(model, prompt_library).evaluate(record, book).enriched_draft.examples[0]

    assert example.startswith("**Pencarian contoh 0**")
    assert "```python" in example
    assert "memakai pencarian linear" in example
    assert not example.startswith("{")


def test_the_writer_notes_are_carried_into_the_feedback(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Catatan model adalah tempat ia melaporkan kekurangan **bahan**.

    Menelannya akan membuat "spesifikasi tidak menyebut operasi apa pun yang
    dapat dicontohkan" terlihat seperti "contohnya kebetulan sedikit".
    """
    note = "Spesifikasi bab tidak menyebut satu pun operasi yang dapat dicontohkan."
    model = ScriptedChatModel([examples_json(3, notes=[note])])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert note in result.feedback
    assert result.score == 10, "catatan bukan kekurangan yang dihasilkan gate ini"


def test_examples_from_the_writer_are_replaced_and_the_replacement_is_reported(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """§19 menaruh Example Agent **sesudah** penulis, jadi hasilnya yang dipakai.

    Menggantinya diam-diam akan menyembunyikan bahwa penulis membayar token
    untuk bahan yang tidak terpakai — dan itu justru hal yang perlu terlihat
    saat biaya token sedang ditinjau.
    """
    draft_with_examples = DRAFT.model_copy(update={"examples": ("Contoh lama dari penulis.",)})
    record = ChapterRecord(
        number=1,
        status=ChapterStatus.DRAFTED,
        spec=SPEC,
        draft=draft_with_examples,
        research=ResearchPackage.empty(),
    )
    model = ScriptedChatModel([examples_json(3)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.enriched_draft is not None
    assert "Contoh lama dari penulis." not in result.enriched_draft.examples
    assert any("digantikan" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 2. Bab yang tidak meminta contoh
# ---------------------------------------------------------------------------
def test_a_chapter_that_requests_no_examples_skips_without_spending_a_token(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Bab teori murni bukan bab yang gagal memuat contoh."""
    spec = SPEC.model_copy(update={"required_examples": 0})
    record = ChapterRecord(
        number=1, status=ChapterStatus.DRAFTED, spec=spec, draft=DRAFT
    )
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 0
    assert result.skipped is True
    assert result.approved is True
    assert result.enriched_draft is None, "tidak ada yang diperkaya kalau tidak ada yang diminta"


# ---------------------------------------------------------------------------
# 3. Tangga perbaikan — temuan harus sampai, bukan sekadar diulang
# ---------------------------------------------------------------------------
def test_a_shortfall_triggers_another_attempt_with_the_findings_in_the_prompt(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Percobaan ulang yang buta adalah token yang dibakar untuk jawaban yang sama."""
    model = ScriptedChatModel([examples_json(1), examples_json(3)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 2
    assert result.score == 10
    second_prompt = model.requests[1].user
    assert "hanya 1 dari 3" in second_prompt, "temuan percobaan pertama harus dikirim"


def test_the_findings_are_not_in_the_first_prompt(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Percobaan pertama tidak punya temuan; mengarangnya akan menyesatkan model."""
    model = ScriptedChatModel([examples_json(3)])

    make_gate(model, prompt_library).evaluate(record, book)

    assert "contoh yang diminta dapat dipakai" not in model.requests[0].user


def test_an_exhausted_ladder_reports_a_shortfall_instead_of_rejecting(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Menolak bab karena contohnya kurang berarti mengembalikan bab yang sudah benar.

    Penulis tidak membuat contoh, jadi ia tidak dapat memperbaikinya. Yang benar:
    laporkan, beri skor rendah, teruskan ke peninjau — yang memang berwenang.
    """
    model = ScriptedChatModel([examples_json(1), examples_json(1), examples_json(1)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 3
    assert result.approved is True, "gate penulisan tidak menolak bab"
    assert result.score == 5, "nol akan menyamakan 'kurang' dengan 'tidak ada'"
    assert any("hanya 1 dari 3" in note for note in result.feedback)
    assert any("belum memenuhi syarat" in note for note in result.feedback)


def test_no_usable_example_at_all_scores_zero_not_shortfall(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """"Tidak ada" dan "kurang" adalah dua keadaan yang berbeda, dan bedanya terlihat."""
    model = ScriptedChatModel(
        [examples_json(1, examples=[example_payload(0, code="", explanation="")])] * 3
    )

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.score == 0
    assert any("Tidak satu pun contoh dapat dipakai" in note for note in result.feedback)


def test_repair_attempts_zero_means_exactly_one_call(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Batas percobaan dari ``config.yaml`` harus benar-benar mengikat."""
    model = ScriptedChatModel([examples_json(1)])

    result = make_gate(model, prompt_library, repair_attempts=0).evaluate(record, book)

    assert model.call_count == 1
    assert result.score == 5


def test_a_model_that_never_returns_json_raises_rather_than_rejecting(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Model yang tidak dapat di-parse bukan "bab ditolak" — itu kegagalan lain.

    Menelannya menjadi ``approved=False`` akan mengirim bab ke revisi tanpa satu
    pun catatan untuk dikerjakan penulis.
    """
    model = ScriptedChatModel(["rusak", "rusak", "rusak"])

    with pytest.raises(AgentOutputError):
        make_gate(model, prompt_library, repair_attempts=2).evaluate(record, book)


# ---------------------------------------------------------------------------
# 4. Prasyarat — gagal sebelum satu token pun dibakar
# ---------------------------------------------------------------------------
def test_a_record_without_a_draft_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Contoh untuk bab yang belum ditulis berarti menebak isi babnya."""
    bare = ChapterRecord(number=1, status=ChapterStatus.PLANNED, spec=SPEC)
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library).evaluate(bare, book)

    assert excinfo.value.missing == "draf"
    assert model.call_count == 0


def test_a_record_without_a_spec_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Tanpa spesifikasi tidak ada ``required_examples`` yang dapat dipenuhi."""
    bare = ChapterRecord(number=1, status=ChapterStatus.DRAFTED, draft=DRAFT)
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library).evaluate(bare, book)

    assert excinfo.value.missing == "spesifikasi bab"
    assert model.call_count == 0


# ---------------------------------------------------------------------------
# 5. Apa yang dikirim ke model
# ---------------------------------------------------------------------------
def test_the_prompt_carries_the_draft_the_objectives_and_the_required_count(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Contoh harus nyambung dengan penjelasan bab — jadi babnya ikut dikirim."""
    model = ScriptedChatModel([examples_json(3)])

    make_gate(model, prompt_library).evaluate(record, book)

    sent = model.requests[0].user
    assert OBJECTIVE in sent
    assert "Algoritma Pencarian" in sent
    assert "Pencarian linear memeriksa tiap elemen." in sent
    assert "3" in sent


def test_the_agent_asks_for_the_schema_it_declares(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Tanpa ``format_schema``, ``--dry-run`` tidak dapat mensintesis balasan."""
    model = ScriptedChatModel([examples_json(3)])

    make_gate(model, prompt_library).evaluate(record, book)

    schema = model.schemas()[0]
    assert schema is not None
    assert set(schema["properties"]) == {"examples", "notes"}


def test_the_agent_uses_the_code_role(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Contoh butuh ketepatan sintaks dan kelakuan program, bukan prosa."""
    assert ExampleWriterAgent.role == "code"
    assert ExampleWriterAgent.prompt_name == "example.chapter"


# ---------------------------------------------------------------------------
# 6. Perakitan dari registri
# ---------------------------------------------------------------------------
def test_the_gate_takes_its_model_from_the_router_by_role(
    prompt_library: FilePromptLibrary,
) -> None:
    """Satu-satunya tempat ``router.chat("code")`` dipanggil untuk gate ini."""
    from agents.gates import build_gates  # lokal: menghindari impor melingkar

    provider = StubProvider(ScriptedChatModel([examples_json(3)]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
    )

    gates = build_gates(("example_writer",), context)

    assert provider.requested == ["code"]
    assert gates[0].name == "example_writer"
    assert gates[0].produces is ChapterStatus.EXAMPLES_WRITTEN
