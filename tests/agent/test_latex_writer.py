"""Tes LaTeX Agent dan gate-nya (§25).

Tiga hal yang dikunci di sini, dan ketiganya adalah hal yang paling mudah salah
pada §25:

1. **Kunci sitasi dihitung program, bukan dipilih model.** Tes-tes di bawah
   selalu menurunkan kunci yang diharapkan dari :func:`~domain.latex.citation_key`
   — tidak ada satu pun kunci yang ditulis tangan. Kunci yang ditulis tangan di
   dalam tes akan tetap hijau meskipun ``references.bib`` berisi kunci yang
   berbeda dari ``\\cite`` di ``.tex``.
2. **Gate ini menulis berkas.** Karena itu berkasnya benar-benar diperiksa di
   ``tmp_path``: bukan hanya "port-nya dipanggil", melainkan "isinya ada dan
   benar". Gate yang melaporkan sukses sambil menulis berkas kosong adalah
   kegagalan yang baru terlihat di Tahap 7, saat kompilasi gagal.
3. **Gate ini selalu meloloskan drafnya.** Ia tahap **penulisan**, sama seperti
   gate contoh dan latihan (§19, §20): kekurangan pada potongan LaTeX adalah
   kekurangan bahan, dan menolak bab karenanya mengembalikan bab kepada penulis
   yang tidak menulis LaTeX.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agents.gates import GateContext, PassThroughGate, build_gates
from agents.latex_writer import LatexWriterAgent, LatexWriterGate
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ResearchPackage, Section
from domain.enums import ChapterStatus
from domain.errors import AgentOutputError, GatePreconditionError
from domain.latex import citation_key
from domain.state import BookState
from latex.artifacts import CHAPTERS_DIRNAME, FileLatexArtifacts
from tests.fakes.chat_models import SchemaEchoChatModel, ScriptedChatModel

CORMEN = "Cormen, Introduction to Algorithms, 4th ed."
SEDGEWICK = "Sedgewick, Algorithms, 4th ed."

#: Kunci yang **seharusnya** muncul — diturunkan, bukan ditulis tangan.
CORMEN_KEY = citation_key(CORMEN)
SEDGEWICK_KEY = citation_key(SEDGEWICK)

OBJECTIVE_1 = "Mahasiswa mampu menghitung kompleksitas waktu"
OBJECTIVE_2 = "Mahasiswa mampu membandingkan dua algoritma"

SPEC = ChapterSpec(
    number=1,
    title="Algoritma Pencarian",
    objectives=(OBJECTIVE_1, OBJECTIVE_2),
    sections=("Pencarian linear", "Pencarian biner"),
)

DRAFT = ChapterDraft(
    title="Algoritma Pencarian",
    learning_objectives=(OBJECTIVE_1, OBJECTIVE_2),
    sections=(Section(heading="Pencarian linear", body="Pencarian linear memeriksa tiap elemen."),),
    summary="Bab ini membandingkan dua algoritma pencarian.",
    citations=(CORMEN,),
)


def body_tex(*cite_keys: str, label: str = "sec:cari") -> str:
    """Isi bab yang sah; ``cite_keys`` menentukan sitasi yang benar-benar dipakai."""
    cite = f" \\cite{{{','.join(cite_keys)}}}" if cite_keys else ""
    return (
        f"\\section{{Pencarian linear}}\n"
        f"Pencarian linear memeriksa tiap elemen{cite}.\n"
        f"\\label{{{label}}}\n"
    )


def latex_json(
    *,
    cite_keys: tuple[str, ...] = (CORMEN_KEY,),
    labels: tuple[str, ...] = ("sec:cari",),
    number: int = 1,
    title: str = "Algoritma Pencarian",
    body: str | None = None,
) -> str:
    """Balasan ``LatexChapter`` yang sah, dengan `citations` mengikuti `cite_keys`."""
    payload = {
        "number": number,
        "title": title,
        "body_tex": body if body is not None else body_tex(*cite_keys),
        "labels": list(labels),
        "citations": list(cite_keys),
    }
    return json.dumps(payload, ensure_ascii=False)


class StubProvider:
    """``ModelProvider`` palsu yang mencatat peran yang diminta."""

    def __init__(self, model: Any) -> None:
        self._model = model
        self.requested: list[str] = []

    def chat(self, role: str) -> Any:
        self.requested.append(role)
        return self._model

    def embedder(self, role: str = "embedding") -> Any:
        raise AssertionError("gate LaTeX tidak boleh meminta model embedding")


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
        status=ChapterStatus.REVIEWED,
        spec=SPEC,
        draft=DRAFT,
        research=ResearchPackage.empty(),
    )


@pytest.fixture
def artifacts(tmp_path: Path) -> FileLatexArtifacts:
    return FileLatexArtifacts(tmp_path / "latex")


def make_gate(
    model: Any, prompts: FilePromptLibrary, artifacts: FileLatexArtifacts, **kwargs: Any
) -> LatexWriterGate:
    return LatexWriterGate(model=model, prompts=prompts, artifacts=artifacts, **kwargs)


# ---------------------------------------------------------------------------
# 1. Jalur bahagia — dan berkas yang benar-benar tertulis
# ---------------------------------------------------------------------------
def test_a_clean_chapter_passes_with_a_perfect_score(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    model = ScriptedChatModel([latex_json()])

    result = make_gate(model, prompt_library, artifacts).evaluate(record, book)

    assert model.call_count == 1
    assert result.approved is True
    assert result.score == 10
    assert result.skipped is False
    assert result.gate == "latex_writer"


def test_the_chapter_file_lands_where_the_book_includes_chapters_from(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    model = ScriptedChatModel([latex_json()])

    make_gate(model, prompt_library, artifacts).evaluate(record, book)

    written = artifacts.chapters_dir / "chapter01.tex"
    assert written.is_file()
    assert written.parent.name == CHAPTERS_DIRNAME


def test_the_written_chapter_is_assembled_by_the_renderer_not_by_the_model(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """Judul, nomor, label bab, dan tujuan datang dari perender — bukan dari model."""
    model = ScriptedChatModel([latex_json()])

    make_gate(model, prompt_library, artifacts).evaluate(record, book)

    text = artifacts.chapter_path(1).read_text(encoding="utf-8")
    assert text.startswith("\\chapter{Algoritma Pencarian}\n")
    assert "\\label{chap:1}" in text
    assert "\\section*{Tujuan Pembelajaran}" in text
    assert f"\\item {OBJECTIVE_1}" in text
    assert "\\section{Pencarian linear}" in text


def test_the_chapter_file_carries_no_preamble(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """Bab adalah potongan yang di-``\\include``; preamble ada di ``preamble.tex``."""
    model = ScriptedChatModel([latex_json()])

    make_gate(model, prompt_library, artifacts).evaluate(record, book)

    text = artifacts.chapter_path(1).read_text(encoding="utf-8")
    assert "\\documentclass" not in text
    assert "\\begin{document}" not in text


def test_the_bibliography_carries_the_source_the_chapter_cites(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """Tanpa entri ini, ``\\cite`` menggantung dan PDF memuat tanda tanya."""
    model = ScriptedChatModel([latex_json()])

    make_gate(model, prompt_library, artifacts).evaluate(record, book)

    bib = artifacts.bibliography_path().read_text(encoding="utf-8")
    assert f"@misc{{{CORMEN_KEY}," in bib
    assert CORMEN in bib


def test_sources_remembered_by_the_book_are_written_too(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """``\\cite`` bab ini yang menunjuk sumber bab 1 harus tetap menemukan entrinya."""
    book = book.model_copy(update={"citations": {SEDGEWICK_KEY: SEDGEWICK}})
    model = ScriptedChatModel([latex_json()])

    make_gate(model, prompt_library, artifacts).evaluate(record, book)

    bib = artifacts.bibliography_path().read_text(encoding="utf-8")
    assert f"@misc{{{SEDGEWICK_KEY}," in bib
    assert f"@misc{{{CORMEN_KEY}," in bib


def test_the_feedback_names_the_files_that_were_written(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    model = ScriptedChatModel([latex_json()])

    result = make_gate(model, prompt_library, artifacts).evaluate(record, book)

    assert any("Daftar pustaka (1 entri)" in note for note in result.feedback)
    assert any("chapter01.tex" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 2. Kunci sitasi — daftar tertutup, dan apa yang terjadi bila dilanggar
# ---------------------------------------------------------------------------
def test_the_prompt_carries_the_closed_list_of_allowed_keys(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """Model harus tahu kunci mana yang ada di ``.bib`` — dan hanya itu."""
    model = ScriptedChatModel([latex_json()])

    make_gate(model, prompt_library, artifacts).evaluate(record, book)

    sent = model.requests[0].user
    assert CORMEN_KEY in sent
    assert CORMEN in sent
    assert OBJECTIVE_1 in sent
    assert "Algoritma Pencarian" in sent


def test_a_chapter_with_no_sources_is_told_not_to_cite_anything(
    prompt_library: FilePromptLibrary,
    book: BookState,
    artifacts: FileLatexArtifacts,
) -> None:
    """Bab tanpa rujukan tidak boleh mengarang satu pun ``\\cite``."""
    bare_draft = DRAFT.model_copy(update={"citations": ()})
    record = ChapterRecord(number=1, status=ChapterStatus.REVIEWED, spec=SPEC, draft=bare_draft)
    model = ScriptedChatModel([latex_json(cite_keys=(), labels=())])

    result = make_gate(model, prompt_library, artifacts).evaluate(record, book)

    assert model.call_count == 1
    assert "tidak ada" in model.requests[0].user
    assert result.score == 10
    assert any("tidak mengutip satu pun sumber" in note for note in result.feedback)
    assert artifacts.bibliography_path().read_text(encoding="utf-8") == ""


def test_an_invented_key_triggers_a_repair_with_the_findings_in_the_prompt(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """§34 pada tingkat kunci: satu-satunya cara menyimpang adalah mengubah kunci."""
    invented = citation_key("Buku yang Tidak Pernah Ada")
    model = ScriptedChatModel([latex_json(cite_keys=(invented,)), latex_json()])

    result = make_gate(model, prompt_library, artifacts).evaluate(record, book)

    assert model.call_count == 2
    assert result.score == 10
    assert invented in model.requests[1].user, "temuannya harus ikut dikirim"
    assert invented not in artifacts.chapter_path(1).read_text(encoding="utf-8")


def test_a_declared_key_that_is_never_used_triggers_a_repair(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """``citations`` yang menyatakan lebih banyak daripada yang dikutip adalah klaim palsu."""
    # Percobaan pertama mengaku memakai kunci, padahal isinya tidak mengutipnya.
    model = ScriptedChatModel(
        [
            latex_json(cite_keys=(CORMEN_KEY,), labels=("sec:cari",), body=body_tex()),
            latex_json(),
        ]
    )

    result = make_gate(model, prompt_library, artifacts).evaluate(record, book)

    assert model.call_count == 2
    assert result.score == 10
    assert CORMEN_KEY in model.requests[1].user


# ---------------------------------------------------------------------------
# 3. Tangga perbaikan dan batasnya
# ---------------------------------------------------------------------------
def test_an_exhausted_ladder_reports_the_findings_instead_of_rejecting(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """Gate penulisan tidak menolak bab; temuan yang tersisa diteruskan berlabel."""
    invented = citation_key("Buku yang Tidak Pernah Ada")
    model = ScriptedChatModel([latex_json(cite_keys=(invented,))] * 3)

    result = make_gate(model, prompt_library, artifacts).evaluate(record, book)

    assert model.call_count == 3
    assert result.approved is True, "gate penulisan tidak menolak bab"
    assert result.score == 5
    assert any("masih memuat temuan" in note for note in result.feedback)


def test_even_a_shortfall_still_writes_the_files(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """Potongan yang cacat tetap lebih berguna daripada tidak ada potongan sama sekali."""
    invented = citation_key("Buku yang Tidak Pernah Ada")
    model = ScriptedChatModel([latex_json(cite_keys=(invented,))] * 3)

    result = make_gate(model, prompt_library, artifacts).evaluate(record, book)

    assert result.score == 5
    assert artifacts.chapter_path(1).is_file()


def test_a_model_that_never_returns_json_raises_rather_than_rejecting(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """Kegagalan total adalah kesalahan yang ditahan per bab (§35), bukan vonis."""
    model = ScriptedChatModel(["rusak", "rusak", "rusak"])

    with pytest.raises(AgentOutputError):
        make_gate(model, prompt_library, artifacts, repair_attempts=2).evaluate(record, book)

    assert not artifacts.chapter_path(1).exists()


# ---------------------------------------------------------------------------
# 4. Nomor bab yang dikunci
# ---------------------------------------------------------------------------
def test_a_wrong_chapter_number_is_locked_and_reported(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """Nomor yang benar adalah nomor yang diberikan sistem, bukan yang ditulis model."""
    model = ScriptedChatModel([latex_json(number=7)])

    result = make_gate(model, prompt_library, artifacts).evaluate(record, book)

    assert result.score == 10
    assert any("dikunci menjadi 1" in note for note in result.feedback)
    assert "\\label{chap:1}" in artifacts.chapter_path(1).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. Prasyarat
# ---------------------------------------------------------------------------
def test_a_record_without_a_draft_is_refused(
    prompt_library: FilePromptLibrary,
    book: BookState,
    artifacts: FileLatexArtifacts,
) -> None:
    """Menulis LaTeX untuk bab yang belum ditulis berarti mengarang isi bab."""
    bare = ChapterRecord(number=1, status=ChapterStatus.REVIEWED, spec=SPEC)
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library, artifacts).evaluate(bare, book)

    assert excinfo.value.missing == "draf"
    assert model.call_count == 0


def test_a_record_without_a_spec_is_refused(
    prompt_library: FilePromptLibrary,
    book: BookState,
    artifacts: FileLatexArtifacts,
) -> None:
    bare = ChapterRecord(number=1, status=ChapterStatus.REVIEWED, draft=DRAFT)
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library, artifacts).evaluate(bare, book)

    assert excinfo.value.missing == "spesifikasi bab"
    assert model.call_count == 0


# ---------------------------------------------------------------------------
# 6. Apa yang dikirim ke model, dan dari mana modelnya datang
# ---------------------------------------------------------------------------
def test_the_agent_asks_for_the_schema_it_declares(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    model = ScriptedChatModel([latex_json()])

    make_gate(model, prompt_library, artifacts).evaluate(record, book)

    schema = model.schemas()[0]
    assert schema is not None
    assert set(schema["properties"]) == {"number", "title", "body_tex", "labels", "citations"}


def test_the_agent_uses_the_latex_role(
    prompt_library: FilePromptLibrary,
) -> None:
    """Peran tersendiri di §6 — bukan peran ``writer``, dan bukan ``reviewer``."""
    assert LatexWriterAgent.role == "latex"
    assert LatexWriterAgent.prompt_name == "latex.chapter"


def test_the_gate_takes_its_model_from_the_router_by_role(
    prompt_library: FilePromptLibrary, artifacts: FileLatexArtifacts
) -> None:
    """Satu-satunya tempat ``router.chat("latex")`` dipanggil untuk gate ini."""
    provider = StubProvider(ScriptedChatModel([latex_json()]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
        latex=artifacts,
    )

    gates = build_gates(("latex_writer",), context)

    assert provider.requested == ["latex"]
    assert gates[0].name == "latex_writer"
    assert gates[0].produces is ChapterStatus.LATEX_GENERATED


def test_a_disabled_latex_configuration_yields_a_pass_through_gate(
    prompt_library: FilePromptLibrary,
) -> None:
    """LaTeX yang dimatikan harus terlihat **dilewati**, bukan hilang dari rantai."""
    provider = StubProvider(ScriptedChatModel([]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
        latex=None,
    )

    gates = build_gates(("latex_writer",), context)

    assert isinstance(gates[0], PassThroughGate)
    assert gates[0].produces is ChapterStatus.LATEX_GENERATED
    assert provider.requested == [], "peran latex tidak boleh dipanggil saat dimatikan"


def test_the_pass_through_gate_explains_why_nothing_was_written(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    provider = StubProvider(ScriptedChatModel([]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
        latex=None,
    )
    gate = build_gates(("latex_writer",), context)[0]

    result = gate.evaluate(ChapterRecord(number=1), book)

    assert result.skipped is True
    assert any("latex.enabled" in note for note in result.feedback)


def test_the_dry_run_path_writes_files_and_never_rejects(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: FileLatexArtifacts,
) -> None:
    """``--dry-run`` menempuh gate ini tanpa satu token, dan tidak boleh menggagalkannya.

    :class:`~tests.fakes.chat_models.SchemaEchoChatModel` menyintesis bab dari
    skema saja — ia tidak melihat isi draf, jadi kunci sitasinya pasti tidak ada
    di daftar yang diizinkan. Tangga perbaikan karena itu berjalan sampai habis
    dan skornya rendah. Itu **perilaku yang benar** untuk jalur tanpa token:
    gate penulisan tidak menolak bab karena kekurangan bahan, dan berkasnya tetap
    ditulis sehingga Tahap 7 punya sesuatu untuk dikompilasi.
    """
    model = SchemaEchoChatModel()

    result = make_gate(model, prompt_library, artifacts).evaluate(record, book)

    assert model.call_count == 3, "tangga perbaikan berjalan sampai habis"
    assert result.approved is True
    assert result.score == 5
    assert artifacts.chapter_path(1).is_file()
    assert artifacts.bibliography_path().is_file()
