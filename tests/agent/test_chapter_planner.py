"""Tes Chapter Planner — pola tiga skenario yang sama dengan Book Planner.

Yang khas untuk agent ini adalah **penguncian**: keluarannya tidak diterima
apa adanya, melainkan direkonsiliasi terhadap spesifikasi Book Planner. Tes di
bagian bawah berkas ini menguji penguncian itu secara langsung, karena di
situlah letak nilainya — model yang patuh dan model yang tidak harus
menghasilkan rencana yang sama.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.chapter_planner import ChapterPlannerAgent
from agents.context import summaries_before, terminology_lines
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.errors import AgentOutputError
from domain.rules import reconcile_chapter_spec
from domain.state import BookState
from tests.fakes.chat_models import SchemaEchoChatModel, ScriptedChatModel

OBJECTIVE_1 = "Mahasiswa mampu menghitung kompleksitas waktu algoritma sederhana"
OBJECTIVE_2 = "Mahasiswa mampu membandingkan dua algoritma berdasarkan notasi Big-O"

BASE_SPEC = ChapterSpec(
    number=2,
    title="Analisis Kompleksitas",
    objectives=(OBJECTIVE_1, OBJECTIVE_2),
    sections=("Pengantar", "Notasi Big-O"),
    required_examples=3,
    required_exercises=5,
    references=("Cormen, Introduction to Algorithms, 4th ed.",),
    source_weeks=("Minggu 2",),
)


def chapter_json(**overrides: Any) -> str:
    """Keluaran model yang sah, dengan kemungkinan penimpaan per field."""
    payload: dict[str, Any] = {
        "number": 2,
        "title": "Analisis Kompleksitas",
        "objectives": [OBJECTIVE_1, OBJECTIVE_2],
        "sections": ["Pengantar", "Notasi Big-O", "Kasus Terburuk"],
        "required_examples": 4,
        "required_exercises": 6,
        "references": [],
        "source_weeks": [],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


@pytest.fixture
def book() -> BookState:
    """State buku dengan tiga ringkasan — dua sebelum bab 2, satu sesudahnya."""
    return BookState(
        request=BookRequest(title="Algoritma dan Struktur Data", target_chapters=2),
        spec=BookSpec(title="Algoritma dan Struktur Data", chapters=(BASE_SPEC,)),
        summaries={1: "Bab 1 membahas pengertian algoritma.", 2: "Ringkasan bab dua.", 3: "Bab 3."},
        terminology={
            "kompleksitas waktu": "laju pertumbuhan waktu eksekusi",
            "algoritma": "urutan langkah penyelesaian masalah",
        },
    )


def _agent(model: Any, prompts: FilePromptLibrary, **kwargs: Any) -> ChapterPlannerAgent:
    return ChapterPlannerAgent(model=model, prompts=prompts, **kwargs)


# ---------------------------------------------------------------------------
# 1. Jalur bahagia
# ---------------------------------------------------------------------------
def test_valid_output_on_first_attempt_calls_model_once(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    model = ScriptedChatModel([chapter_json()])

    spec, notes = _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    assert model.call_count == 1
    assert notes == ()
    assert spec.sections == ("Pengantar", "Notasi Big-O", "Kasus Terburuk")
    assert spec.required_examples == 4, "jumlah contoh adalah keputusan perencana bab"


def test_schema_is_sent_on_every_call(prompt_library: FilePromptLibrary, book: BookState) -> None:
    """``format=`` selalu dikirim — kontrak keluaran tidak pernah dicabut."""
    model = ScriptedChatModel([chapter_json()])

    _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    schema = model.schemas()[0]
    assert schema is not None
    assert "sections" in schema["properties"]


def test_prompt_receives_objectives_and_planned_sections(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    model = ScriptedChatModel([chapter_json()])

    _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    sent = model.requests[0].user
    assert OBJECTIVE_1 in sent
    assert "Notasi Big-O" in sent
    assert "Analisis Kompleksitas" in sent


# ---------------------------------------------------------------------------
# 2. Konteks buku: ringkasan dan istilah
# ---------------------------------------------------------------------------
def test_previous_summaries_exclude_this_chapter_and_later_ones(book: BookState) -> None:
    """Hanya bab **sebelum** bab ini yang dikirim.

    Menyertakan ringkasan bab yang sedang direncanakan akan membuat perencana
    mengulang isinya sendiri; menyertakan bab sesudahnya berarti menyuruhnya
    merencanakan bab yang belum menjadi urusannya.
    """
    lines = summaries_before(book, 2)

    assert len(lines) == 1
    assert "Bab 1" in lines[0]
    assert "Bab 2" not in "".join(lines)
    assert "Bab 3" not in "".join(lines)


def test_terminology_is_sorted_so_the_prompt_is_deterministic(book: BookState) -> None:
    """State yang sama harus merender prompt yang sama, apa pun urutan dict-nya.

    Kalau tidak, ``prompt_digest`` kehilangan gunanya sebagai alat pembanding
    kualitas antar-jalankan (§38).
    """
    assert terminology_lines(book)[0].startswith("algoritma:")


def test_prompt_carries_previous_summaries_and_terminology(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    model = ScriptedChatModel([chapter_json()])

    _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    sent = model.requests[0].user
    assert "Bab 1 membahas pengertian algoritma." in sent
    assert "kompleksitas waktu: laju pertumbuhan waktu eksekusi" in sent


def test_empty_summaries_omit_the_section_entirely(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Bab pertama tidak memunculkan blok ringkasan sama sekali."""
    empty = book.model_copy(update={"summaries": {}})
    model = ScriptedChatModel([chapter_json()])

    _agent(model, prompt_library).detail(BASE_SPEC, book=empty)

    assert "Ringkasan bab-bab sebelumnya" not in model.requests[0].user


# ---------------------------------------------------------------------------
# 3. Penguncian terhadap Book Planner
# ---------------------------------------------------------------------------
def test_chapter_number_is_locked_to_the_book_planner_decision(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Model yang menomori ulang bab tidak boleh menulis berkas dengan nama salah."""
    model = ScriptedChatModel([chapter_json(number=5)])

    spec, notes = _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    assert spec.number == 2
    assert any("nomor bab" in note for note in notes)


def test_rewritten_objectives_are_reverted(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Tujuan pembelajaran mengalir dari RPS; menulisnya ulang memutus rantai itu."""
    model = ScriptedChatModel([chapter_json(objectives=["Tujuan karangan model"])])

    spec, notes = _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    assert spec.objectives == (OBJECTIVE_1, OBJECTIVE_2)
    assert any("tujuan pembelajaran" in note for note in notes)


def test_invented_references_are_dropped(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Chapter Planner tidak melakukan retrieval — rujukan apa pun darinya adalah karangan."""
    invented = ["Buku Yang Tidak Pernah Ada, 2019"]
    model = ScriptedChatModel([chapter_json(references=invented)])

    spec, _ = _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    assert spec.references == BASE_SPEC.references


def test_empty_sections_fall_back_to_the_book_planner_outline(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Rencana tanpa sub-bab tidak memberi penulis apa pun; kerangka buku dipakai."""
    model = ScriptedChatModel([chapter_json(sections=["   ", ""])])

    spec, notes = _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    assert spec.sections == ("Pengantar", "Notasi Big-O")
    assert any("kerangka perencana buku" in note for note in notes)


def test_conforming_output_produces_no_notes(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Catatan hanya muncul saat ada yang dipulihkan — bukan sebagai kebisingan."""
    model = ScriptedChatModel([chapter_json(sections=["Pengantar", "Notasi Big-O"])])

    _, notes = _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    assert notes == ()


# ---------------------------------------------------------------------------
# 4. Tangga perbaikan
# ---------------------------------------------------------------------------
def test_broken_output_is_repaired_with_zero_temperature(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    model = ScriptedChatModel(["ini bukan json sama sekali", chapter_json()])

    spec, _ = _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    assert model.call_count == 2
    assert model.temperatures() == (None, 0.0)
    assert spec.number == 2


def test_repair_prompt_keeps_the_original_instructions(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Instruksi asli ikut dikirim ulang, bukan diganti oleh pesan perbaikan."""
    model = ScriptedChatModel(["bukan json", chapter_json()])

    _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    repair = model.requests[1].user
    assert "bukan json" in repair
    assert "PERBAIKAN KELUARAN" in repair
    assert OBJECTIVE_1 in repair


def test_persistent_failure_raises_with_the_raw_text(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Percobaan habis: ``AgentOutputError`` membawa teks terakhir, bukan menelannya."""
    model = ScriptedChatModel(["rusak pertama", "rusak kedua", "rusak ketiga"])
    agent = _agent(model, prompt_library, max_repair_attempts=2)

    with pytest.raises(AgentOutputError) as excinfo:
        agent.detail(BASE_SPEC, book=book)

    assert model.call_count == 3
    assert excinfo.value.attempts == 3
    assert excinfo.value.raw == "rusak ketiga"


# ---------------------------------------------------------------------------
# 5. Model echo skema
# ---------------------------------------------------------------------------
def test_schema_echo_model_satisfies_the_chapter_planner_contract(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Echo model mengikuti ``ChapterSpec`` secara otomatis, termasuk field baru."""
    model = SchemaEchoChatModel()

    spec, notes = _agent(model, prompt_library).detail(BASE_SPEC, book=book)

    assert isinstance(spec, ChapterSpec)
    assert model.call_count == 1
    # Echo menghasilkan ``number`` default; penguncian mengembalikannya, dan itu
    # memang harus terlihat sebagai catatan.
    assert spec.number == BASE_SPEC.number


# ---------------------------------------------------------------------------
# reconcile_chapter_spec (murni)
# ---------------------------------------------------------------------------
def test_reconcile_is_pure() -> None:
    """Spesifikasi masukan tidak berubah."""
    produced = ChapterSpec(number=9, title="X", sections=("A",))
    snapshot = produced.model_dump()

    reconcile_chapter_spec(produced, base=BASE_SPEC)

    assert produced.model_dump() == snapshot


def test_reconcile_trims_blank_sections() -> None:
    """Judul sub-bab yang hanya berisi spasi tidak dianggap sub-bab."""
    produced = ChapterSpec(number=2, title="X", sections=("  A  ", "", "   "))

    fixed, _ = reconcile_chapter_spec(produced, base=BASE_SPEC)

    assert fixed.sections == ("A",)


def test_reconcile_reports_when_there_is_no_outline_at_all() -> None:
    """Tanpa kerangka cadangan sekalipun, itu dikatakan terang-terangan."""
    base = BASE_SPEC.model_copy(update={"sections": ()})
    produced = ChapterSpec(number=2, title="X", sections=())

    _, notes = reconcile_chapter_spec(produced, base=base)

    assert any("tidak memiliki rencana sub-bab" in note for note in notes)


def test_reconcile_restores_a_blank_title() -> None:
    """Judul yang hanya berisi spasi lolos ``min_length``, jadi harus dijaga di sini."""
    produced = ChapterSpec(number=2, title="   ")

    fixed, notes = reconcile_chapter_spec(produced, base=BASE_SPEC)

    assert fixed.title == BASE_SPEC.title
    assert any("judul bab kosong" in note for note in notes)
