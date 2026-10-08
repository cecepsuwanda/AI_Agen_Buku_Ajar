"""Tes Pedagogy Reviewer dan gate-nya (§23).

§23 adalah gate yang paling sering menolak, dan karena itu ia yang paling mahal
salahnya. Dua hal diuji berurutan, dan urutannya sendiri yang paling penting:

1. **Tangganya diperiksa lebih dulu, tanpa satu token.** Empat dari enam
   kelemahan yang §23 minta dideteksi hanya punya jawaban pada bab yang tangganya
   lengkap — "latihan tidak sesuai tujuan" pada bab tanpa tujuan bukan pertanyaan
   yang sulit, melainkan pertanyaan yang tidak punya jawaban. Tesnya memakai
   :class:`ScriptedChatModel` yang **kosong**, sehingga panggilan model apa pun
   menggagalkan tes dengan pesan yang jelas. ``skipped=False`` sama pentingnya:
   ini bukan gate yang melewati babnya, melainkan gate yang menolaknya.
2. **Kelima anak tangga dijawab, dan jawabannya mengikat.** Model yang menyetujui
   bab sambil menandai satu anak tangga `ok` bernilai salah membatalkan
   pekerjaannya sendiri; model yang melewatkan satu anak tangga meninggalkan
   bagian bab yang tidak diperiksa siapa pun. Keduanya dilaporkan — dan yang
   pertama tidak memerlukan kejujuran model untuk ketahuan.

Berbeda dari pemeriksa fakta, tidak ada kelemahan yang "sudah dinyatakan penulis"
di sini: ``unresolved_claims`` menyatakan klaim yang belum berbukti, bukan anak
tangga yang belum ditulis.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.gates import GateContext, build_gates
from agents.pedagogy_reviewer import PedagogyGate, PedagogyReviewerAgent
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, Section
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.pedagogy import LADDER
from domain.state import BookState
from domain.transitions import can_advance
from tests.fakes.chat_models import ScriptedChatModel

EXPLANATION = "Pencarian linear memeriksa tiap elemen satu per satu."

SPEC = ChapterSpec(
    number=1,
    title="Algoritma Pencarian",
    objectives=("Mahasiswa mampu menjelaskan pencarian linear.",),
    sections=("Pencarian linear",),
    required_examples=3,
    required_exercises=5,
)

#: Draf yang tangganya utuh — prasyarat masuknya penilaian ke model.
COMPLETE = ChapterDraft(
    title="Algoritma Pencarian",
    learning_objectives=("Mahasiswa mampu menjelaskan pencarian linear.",),
    sections=(Section(heading="Pencarian linear", body=EXPLANATION),),
    examples=("**Contoh 1.1**\n\n```python\nfor x in data: pass\n```",),
    exercises=("Cari 7 pada larik [3, 7, 9].",),
)


def damaged(**fields: object) -> ChapterDraft:
    """Draf lengkap dengan satu bagian dirusak."""
    return COMPLETE.model_copy(update=fields)


def rung(name: str, *, ok: bool, detail: str = "") -> dict[str, Any]:
    """Satu temuan model atas sebuah anak tangga."""
    return {"subject": name, "ok": ok, "detail": detail, "source": ""}


def every_rung_ok() -> list[dict[str, Any]]:
    """Kelima anak tangga, semuanya terpenuhi."""
    return [rung(name, ok=True, detail="terpenuhi") for name in LADDER]


def verdict_json(**overrides: Any) -> str:
    """Balasan ``CheckVerdict`` yang sah: kelima anak tangga dijawab."""
    payload: dict[str, Any] = {
        "approved": True,
        "score": 9,
        "feedback": [],
        "findings": every_rung_ok(),
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class StubProvider:
    """``ModelProvider`` palsu yang mencatat peran yang diminta.

    ``embedder`` sengaja melempar: peninjau pedagogi tidak punya urusan dengan
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
        raise AssertionError("peninjau pedagogi tidak boleh meminta model embedding")


@pytest.fixture
def book() -> BookState:
    """State buku tanpa satu pun bab yang sudah disetujui."""
    return BookState(
        request=BookRequest(title="Algoritma", target_chapters=1),
        spec=BookSpec(title="Algoritma", chapters=(SPEC,)),
    )


@pytest.fixture
def record() -> ChapterRecord:
    """Bab yang datang dari tahap latihan: tangganya utuh, belum ada yang menilainya."""
    return ChapterRecord(
        number=1,
        status=ChapterStatus.EXERCISES_WRITTEN,
        spec=SPEC,
        draft=COMPLETE,
    )


def make_gate(
    model: ScriptedChatModel, prompts: FilePromptLibrary, **kwargs: Any
) -> PedagogyGate:
    return PedagogyGate(model=model, prompts=prompts, **kwargs)


# ---------------------------------------------------------------------------
# 1. Tangga diperiksa lebih dulu — tanpa model
# ---------------------------------------------------------------------------
def test_a_missing_rung_rejects_the_chapter_without_calling_the_model(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """``ScriptedChatModel([])`` tidak punya balasan: panggilan apa pun menggagalkan tes.

    Itu inti pemeriksaannya, bukan efek sampingnya. Model yang tetap dipanggil
    akan mengeluarkan vonis tentang anak tangga yang tidak ada — dan vonis itu
    tercatat sebagai hasil pemeriksaan.
    """
    thin = ChapterRecord(
        number=1,
        status=ChapterStatus.EXERCISES_WRITTEN,
        spec=SPEC,
        draft=damaged(learning_objectives=(), examples=(), exercises=()),
    )
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library).evaluate(thin, book)

    assert model.call_count == 0
    assert result.approved is False
    assert result.skipped is False, "ini penolakan, bukan pelewatan"
    assert result.score == 0
    assert result.gate == "pedagogy_reviewer"
    assert any("Learning Objective" in note for note in result.feedback)
    assert any("Example" in note for note in result.feedback)
    assert any("Exercise" in note for note in result.feedback)


def test_the_rejection_says_the_ladder_must_be_complete_first(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Alasan penolakannya disebutkan, dan bukan "skornya rendah"."""
    thin = record.model_copy(update={"draft": damaged(sections=())})

    result = make_gate(ScriptedChatModel([]), prompt_library).evaluate(thin, book)

    assert any("Tangga §23 harus utuh" in note for note in result.feedback)
    assert any("tidak ada penilaian model" in note for note in result.feedback)


def test_the_gap_names_the_rung_so_the_writer_knows_what_to_add(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Temuan menyebut anak tangga dan alasannya — bukan "ada yang kurang"."""
    thin = record.model_copy(update={"draft": damaged(exercises=())})

    result = make_gate(ScriptedChatModel([]), prompt_library).evaluate(thin, book)

    assert any("Exercise" in note and "5 latihan" in note for note in result.feedback)


def test_a_single_duplicate_heading_rejects_before_the_model(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Pengulangan yang tidak diperlukan juga tidak butuh penilaian (§23)."""
    twice = record.model_copy(
        update={
            "draft": damaged(
                sections=(
                    Section(heading="Pencarian linear", body=EXPLANATION),
                    Section(heading="Pencarian linear", body="Materi lain."),
                )
            )
        }
    )
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library).evaluate(twice, book)

    assert model.call_count == 0
    assert result.approved is False
    assert any("Pencarian linear" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 2. Prasyarat
# ---------------------------------------------------------------------------
def test_a_record_without_a_draft_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Menilai pedagogi bab yang belum ditulis berarti menilai ketiadaan."""
    bare = ChapterRecord(number=1, status=ChapterStatus.EXERCISES_WRITTEN, spec=SPEC)

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(ScriptedChatModel([]), prompt_library).evaluate(bare, book)

    assert "draf" in str(excinfo.value)


def test_a_record_without_a_spec_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Spesifikasi membawa tujuan yang menjadi pembanding latihan."""
    bare = ChapterRecord(number=1, status=ChapterStatus.EXERCISES_WRITTEN, draft=COMPLETE)

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(ScriptedChatModel([]), prompt_library).evaluate(bare, book)

    assert "spesifikasi bab" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 3. Penilaian — kelima anak tangga dijawab, dan jawabannya mengikat
# ---------------------------------------------------------------------------
def test_a_chapter_that_satisfies_the_ladder_is_accepted(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Jalur bahagia: seluruh anak tangga terpenuhi, babnya lulus."""
    model = ScriptedChatModel([verdict_json()])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 1
    assert result.approved is True
    assert result.skipped is False
    assert result.score == 9
    assert result.gate == "pedagogy_reviewer"


def test_a_weak_rung_rejects_the_chapter(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Model menandai satu anak tangga belum terpenuhi, dan babnya ditolak."""
    findings = every_rung_ok()
    findings[1] = rung(
        "Explanation",
        ok=False,
        detail="Sub-bab melompat dari definisi ke notasi tanpa satu contoh pun.",
    )
    model = ScriptedChatModel([verdict_json(approved=False, score=4, findings=findings)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert result.score == 4
    assert any("Explanation" in note and "melompat" in note for note in result.feedback)


def test_the_verdict_is_enforced_when_the_model_contradicts_its_own_findings(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Vonis yang bertentangan dengan temuannya sendiri diturunkan sistem.

    Tanpa aturan ini, bab yang ditandai gagal oleh peninjaunya sendiri akan lolos
    tanpa satu pun tanda — dan tidak ada yang tahu peninjaunya sebenarnya
    menyetujui.
    """
    findings = every_rung_ok()
    findings[3] = rung("Practice", ok=False, detail="Tidak ada langkah terbimbing.")
    model = ScriptedChatModel([verdict_json(approved=True, score=8, findings=findings)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert any("diabaikan oleh sistem" in note for note in result.feedback)


def test_a_score_under_the_threshold_is_lowered_by_the_shared_rule(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Ambang ditegakkan sistem, bukan diserahkan kepada peninjau."""
    model = ScriptedChatModel([verdict_json(approved=True, score=6)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert result.score == 6
    assert any("di bawah ambang 7" in note for note in result.feedback)


def test_the_threshold_comes_from_the_caller(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Ambang adalah konfigurasi, bukan konstanta yang tertanam di gate."""
    model = ScriptedChatModel([verdict_json(approved=True, score=6)])

    result = make_gate(model, prompt_library, threshold=5).evaluate(record, book)

    assert result.approved is True


def test_a_rung_the_model_never_mentions_is_reported_as_unexamined(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Model yang menyebut empat dari lima anak tangga belum memeriksa yang kelima.

    Dilaporkan tetapi **tidak** menurunkan vonis: yang kurang di sini laporan,
    bukan babnya.
    """
    spoken = [item for item in every_rung_ok() if item["subject"] != "Practice"]
    model = ScriptedChatModel([verdict_json(findings=spoken)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is True
    assert any("tidak diperiksa model" in note and "Practice" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 4. Yang benar-benar dikirim ke model
# ---------------------------------------------------------------------------
def test_the_prompt_carries_the_ladder_the_draft_and_the_planned_objectives(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Ketiganya sampai ke prompt — dan tangganya sebagai daftar, bukan prosa."""
    model = ScriptedChatModel([verdict_json()])

    make_gate(model, prompt_library).evaluate(record, book)

    sent = model.requests[0].user
    assert model.requests[0].role == "reviewer"
    for name in LADDER:
        assert name in sent, f"anak tangga {name} tidak ikut terkirim"
    assert "Pencarian linear" in sent, "drafnya tidak ikut terkirim"
    assert SPEC.objectives[0] in sent, "tujuan yang direncanakan tidak ikut terkirim"


def test_the_agent_asks_for_the_schema_it_declares(prompt_library: FilePromptLibrary) -> None:
    """``format=`` tidak pernah dicabut: keluaran yang tak terikat skema tak dapat divonis.

    Skema yang diminta adalah skema yang dideklarasikan prompt — bukan yang
    dikarang agent. Keduanya dibangkitkan dari model Pydantic yang sama, dan
    itulah yang membuat kontrak OUTPUT prompt tidak dapat menyimpang dari
    ``format=`` yang menegakkan keluaran.
    """
    model = ScriptedChatModel([verdict_json()])
    agent = PedagogyReviewerAgent(model=model, prompts=prompt_library)

    agent.review(COMPLETE, spec=SPEC, book=BookState(request=BookRequest(title="B")))

    schema = model.schemas()[0]
    assert schema is not None
    assert set(schema["properties"]) == {"approved", "score", "feedback", "findings"}


def test_the_agent_uses_its_own_role(prompt_library: FilePromptLibrary) -> None:
    """Peran ``reviewer``: memutuskan sesuatu tentang pekerjaan yang sudah ada."""
    assert PedagogyReviewerAgent.role == "reviewer"
    assert PedagogyReviewerAgent.prompt_name == "pedagogy.chapter"


# ---------------------------------------------------------------------------
# 5. Gate di rantai §27
# ---------------------------------------------------------------------------
def test_the_gate_produces_the_status_the_chain_expects() -> None:
    """``produces`` harus memajukan rantai dari status yang mendahuluinya."""
    assert PedagogyGate.name == "pedagogy_reviewer"
    assert PedagogyGate.produces is ChapterStatus.PEDAGOGY_REVIEWED
    assert can_advance(ChapterStatus.EXERCISES_WRITTEN, PedagogyGate.produces)


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

    gates = build_gates(("pedagogy_reviewer",), context)

    assert provider.requested == ["reviewer"]
    assert gates[0].name == "pedagogy_reviewer"
    assert gates[0].produces is ChapterStatus.PEDAGOGY_REVIEWED
