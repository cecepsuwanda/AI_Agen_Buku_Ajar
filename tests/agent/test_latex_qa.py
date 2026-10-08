"""Tes LaTeX QA dan gate-nya (§26).

Empat hal yang dikunci di sini, dan keempatnya adalah hal yang paling mudah
salah pada §26:

1. **Yang dikompilasi adalah berkas yang sudah ditulis, bukan bab yang dihasilkan
   ulang.** Kalau gate ini meminta model sekali lagi, ia mengompilasi bab yang
   mungkin berbeda dari yang akan dicetak — dan tes ini membuktikannya dengan
   potongan yang disemai ke ``tmp_path`` lebih dulu.
2. **Berkasnya hanya ditimpa oleh percobaan yang lolos.** Potongan yang sudah
   pernah dihasilkan lebih baik daripada potongan yang lebih buruk, dan tidak ada
   satu pun cara mengetahui bahwa yang di disk sudah bukan yang terbaik.
3. **Tangga perbaikan berjalan sebelum vonis.** §26 berbunyi "Errors? yes →
   LaTeX Agent → retry", dan urutan itu bukan hiasan: satu ``&`` yang belum
   diloloskan tidak boleh membuang seluruh bab.
4. **Yang tetap gagal ditolak, dengan log-nya.** Bab yang tidak dapat dicetak
   adalah bab yang tidak ada, dan penerima catatannya adalah penulis bab — yang
   harus tahu apa yang dikatakan perkakas LaTeX, bukan hanya bahwa "gagal".

Tidak satu pun tes di sini menjalankan LaTeX: kompilasinya adalah
:class:`StubCompiler`, hasilnya disiapkan tes. Kompilasi sungguhan diuji terpisah
di ``tests/live/`` dan ditandai ``live``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import pytest

from agents.gates import GateContext, PassThroughGate, build_gates
from agents.latex_qa import LatexQAGate, LatexRepairAgent
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, Section
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.latex import (
    LatexBuildResult,
    LatexChapter,
    citation_key,
    render_chapter_latex,
)
from domain.state import BookState
from latex.artifacts import FileLatexArtifacts, atomic_write_text
from latex.dry_run import DryRunLatexCompiler
from tests.fakes.chat_models import ScriptedChatModel

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

#: Potongan seperti yang ditulis gate §25 — sudah lengkap dengan ``\\chapter``,
#: label bab, dan blok tujuan pembelajaran.
FRAGMENT = (
    f"\\chapter{{Algoritma Pencarian}}\n"
    f"\\label{{chap:1}}\n"
    f"\n"
    f"\\section*{{Tujuan Pembelajaran}}\n"
    f"\\begin{{itemize}}\n"
    f"  \\item {OBJECTIVE_1}\n"
    f"\\end{{itemize}}\n"
    f"\n"
    f"\\section{{Pencarian linear}}\n"
    f"Pencarian linear memeriksa tiap elemen \\cite{{{CORMEN_KEY}}}.\n"
    f"\\label{{sec:cari}}\n"
)

#: Kompilasi yang gagal karena satu ``&`` yang belum diloloskan.
FAILED = LatexBuildResult(
    ok=False,
    errors=("Misplaced alignment tab character &.",),
    log_excerpt="! Misplaced alignment tab character &.\nl.3 Notasi $O(n)$ & temannya.",
)


def chapter_json(
    *,
    number: int = 1,
    title: str = "Algoritma Pencarian",
    body: str | None = None,
    labels: tuple[str, ...] = ("sec:cari",),
    cite_keys: tuple[str, ...] = (CORMEN_KEY,),
) -> str:
    """Balasan ``LatexChapter`` yang sah, dengan ``citations`` mengikuti ``cite_keys``."""
    cite = f" \\cite{{{','.join(cite_keys)}}}" if cite_keys else ""
    default_body = (
        f"\\section{{Pencarian linear}}\n"
        f"Pencarian linear memeriksa tiap elemen{cite}.\n"
        f"\\label{{sec:cari}}\n"
    )
    payload = {
        "number": number,
        "title": title,
        "body_tex": body if body is not None else default_body,
        "labels": list(labels),
        "citations": list(cite_keys),
    }
    return json.dumps(payload, ensure_ascii=False)


class StubCompiler:
    """Kompilasi palsu: hasil yang sudah disiapkan, berurutan, lalu gagal keras.

    Ketat seperti :class:`~tests.fakes.chat_models.ScriptedChatModel`: memanggil
    lebih banyak daripada hasil yang disiapkan adalah kesalahan tes, bukan
    kesempatan mengembalikan hasil bawaan. Hasil bawaan yang longgar akan
    menyembunyikan kompilasi yang tidak seharusnya terjadi.
    """

    def __init__(self, *results: LatexBuildResult) -> None:
        self._results: list[LatexBuildResult] = list(results)
        self.fragments: list[str] = []
        self.sources: list[tuple[str, ...]] = []

    def compile_fragment(self, fragment: str, *, sources: Sequence[str] = ()) -> LatexBuildResult:
        """Catat apa yang dikompilasi, lalu kembalikan hasil berikutnya."""
        self.fragments.append(fragment)
        self.sources.append(tuple(sources))
        if not self._results:
            raise AssertionError(
                f"StubCompiler kehabisan hasil pada kompilasi ke-{len(self.fragments)}. "
                "Tes menyiapkan terlalu sedikit hasil — atau gate mengompilasi lebih "
                "sering daripada yang seharusnya."
            )
        return self._results.pop(0)

    @property
    def calls(self) -> int:
        """Berapa kali kompilasi diminta."""
        return len(self.fragments)


class CountingArtifacts(FileLatexArtifacts):
    """Artifacts yang menghitung berapa kali berkas potongan ditulis ulang."""

    def __init__(self, latex_dir: Path) -> None:
        super().__init__(latex_dir)
        self.saves = 0

    def save_chapter(self, number: int, text: str) -> str:
        self.saves += 1
        return super().save_chapter(number, text)


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
        status=ChapterStatus.LATEX_GENERATED,
        spec=SPEC,
        draft=DRAFT,
    )


@pytest.fixture
def artifacts(tmp_path: Path) -> CountingArtifacts:
    """Direktori LaTeX di ``tmp_path``, **sudah** berisi potongan gate §25.

    Disemai lewat :func:`~latex.artifacts.atomic_write_text` dan bukan lewat
    ``save_chapter``: yang dihitung ``saves`` adalah penulisan yang dilakukan
    gate §26, bukan penyiapan tesnya.
    """
    made = CountingArtifacts(tmp_path / "latex")
    atomic_write_text(made.chapter_path(1), FRAGMENT)
    return made


def make_gate(
    model: Any,
    prompts: FilePromptLibrary,
    artifacts: FileLatexArtifacts,
    compiler: Any,
    **kwargs: Any,
) -> LatexQAGate:
    return LatexQAGate(
        model=model,
        prompts=prompts,
        artifacts=artifacts,
        compiler=compiler,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. Jalur bahagia — bersih pada percobaan pertama
# ---------------------------------------------------------------------------
def test_a_clean_fragment_passes_without_ever_calling_the_model(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Kompilasi yang bersih tidak butuh model — dan tidak boleh memanggilnya."""
    compiler = StubCompiler(LatexBuildResult(ok=True))
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    assert model.call_count == 0
    assert compiler.calls == 1
    assert result.gate == "latex_qa"
    assert result.approved is True
    assert result.score == 10
    assert result.skipped is False


def test_the_feedback_says_the_fragment_was_clean_and_names_the_file(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    compiler = StubCompiler(LatexBuildResult(ok=True))

    result = make_gate(
        ScriptedChatModel([]), prompt_library, artifacts, compiler
    ).evaluate(record, book)

    assert any("dikompilasi bersih" in note for note in result.feedback)
    assert any("chapter01.tex" in note for note in result.feedback)


def test_a_clean_compile_leaves_the_file_the_writer_wrote_alone(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Menulis ulang berkas yang sudah benar adalah diff palsu di ``output/`` (§39)."""
    compiler = StubCompiler(LatexBuildResult(ok=True))

    make_gate(ScriptedChatModel([]), prompt_library, artifacts, compiler).evaluate(record, book)

    assert artifacts.saves == 0
    assert artifacts.load_chapter(1) == FRAGMENT


def test_the_notes_never_paste_the_chapter_source_into_the_state(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Catatan adalah catatan, bukan isi bab.

    Teks potongannya sudah tersimpan sekali di ``output/latex/chapters/`` dan
    sekali lagi di dalam record; menyalinnya ke dalam setiap catatan berarti
    menggandakannya untuk ketiga kalinya di ``state/chapterNN.json``.
    """
    compiler = StubCompiler(LatexBuildResult(ok=True))

    result = make_gate(
        ScriptedChatModel([]), prompt_library, artifacts, compiler
    ).evaluate(record, book)

    assert all("Pencarian linear memeriksa tiap elemen" not in note for note in result.feedback)


def test_advisories_are_reported_without_holding_the_chapter_back(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Rujukan menggantung lintas bab dan kotak yang melebar bukan alasan menolak.

    Bab dikompilasi sebagai potongan, sehingga ``\\ref`` ke bab lain akan selalu
    tampak menggantung di sini meski tidak ada yang salah. Yang benar-benar tahu
    jawabannya adalah kompilasi tingkat buku.
    """
    compiler = StubCompiler(
        LatexBuildResult(
            ok=True,
            undefined_refs=("chap:2",),
            overfull_boxes=("Overfull \\hbox (9.5pt too wide) in paragraph at lines 22--23",),
        )
    )

    result = make_gate(
        ScriptedChatModel([]), prompt_library, artifacts, compiler
    ).evaluate(record, book)

    assert result.approved is True
    assert result.score == 10
    assert any("chap:2" in note for note in result.feedback)
    assert any("melebihi lebar halaman" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 2. Yang dikompilasi, dan dari mana asalnya
# ---------------------------------------------------------------------------
def test_the_gate_compiles_the_file_that_is_on_disk(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Bukan bab yang dihasilkan ulang: satu panggilan LLM lebih murah dihemat."""
    compiler = StubCompiler(LatexBuildResult(ok=True))

    make_gate(ScriptedChatModel([]), prompt_library, artifacts, compiler).evaluate(record, book)

    assert compiler.fragments[0] == FRAGMENT


def test_the_compiler_receives_the_sources_the_chapter_cites(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Tanpa daftar ini, setiap ``\\cite`` dilaporkan menggantung meski kuncinya benar."""
    compiler = StubCompiler(LatexBuildResult(ok=True))

    make_gate(ScriptedChatModel([]), prompt_library, artifacts, compiler).evaluate(record, book)

    assert compiler.sources[0] == (CORMEN,)


def test_sources_remembered_by_the_book_are_passed_too(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """``\\cite`` bab ini yang menunjuk sumber bab 1 harus tetap menemukan entrinya."""
    book = book.model_copy(update={"citations": {SEDGEWICK_KEY: SEDGEWICK}})
    compiler = StubCompiler(LatexBuildResult(ok=True))

    make_gate(ScriptedChatModel([]), prompt_library, artifacts, compiler).evaluate(record, book)

    assert compiler.sources[0] == (SEDGEWICK, CORMEN)


# ---------------------------------------------------------------------------
# 3. Tangga perbaikan (§26: "Errors? yes → LaTeX Agent → retry")
# ---------------------------------------------------------------------------
def test_a_failing_compile_triggers_a_repair_and_a_second_compile(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    compiler = StubCompiler(FAILED, LatexBuildResult(ok=True))
    model = ScriptedChatModel([chapter_json()])

    result = make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    assert model.call_count == 1
    assert compiler.calls == 2
    assert result.approved is True
    assert result.score == 10
    assert any("setelah 1 perbaikan" in note for note in result.feedback)


def test_the_repaired_candidate_is_what_gets_compiled_and_written(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Yang lolos kompilasi adalah potongan **baru** — dan itulah yang tersimpan."""
    compiler = StubCompiler(FAILED, LatexBuildResult(ok=True))
    model = ScriptedChatModel([chapter_json()])

    make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    repaired = LatexChapter.model_validate_json(chapter_json())
    expected = render_chapter_latex(repaired, number=1, spec=SPEC)
    assert compiler.fragments[1] == expected
    assert artifacts.load_chapter(1) == expected
    assert artifacts.saves == 1


def test_the_repair_prompt_carries_the_findings_the_log_and_the_fragment(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Pesan perkakas dikirim apa adanya: menerjemahkannya menambah satu tempat salah."""
    compiler = StubCompiler(FAILED, LatexBuildResult(ok=True))
    model = ScriptedChatModel([chapter_json()])

    make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    sent = model.requests[0].user
    assert "Misplaced alignment tab character" in sent
    assert "l.3 Notasi $O(n)$ & temannya." in sent
    assert "Pencarian linear memeriksa tiap elemen" in sent
    assert CORMEN_KEY in sent


def test_the_repair_agent_asks_for_the_schema_it_declares(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    compiler = StubCompiler(FAILED, LatexBuildResult(ok=True))
    model = ScriptedChatModel([chapter_json()])

    make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    schema = model.schemas()[0]
    assert schema is not None
    assert set(schema["properties"]) == {"number", "title", "body_tex", "labels", "citations"}


def test_a_wrong_chapter_number_in_the_repair_is_locked_and_reported(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    compiler = StubCompiler(FAILED, LatexBuildResult(ok=True))
    model = ScriptedChatModel([chapter_json(number=7)])

    result = make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    assert result.approved is True
    assert any("dikunci menjadi 1" in note for note in result.feedback)
    assert "\\label{chap:1}" in artifacts.load_chapter(1)


def test_a_structurally_broken_repair_is_never_compiled(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Potongan yang bentuknya sudah salah tidak menghasilkan log yang lebih berguna.

    Percobaan pertama dijawab dengan ``body_tex`` kosong; gate harus mengenalinya
    dari bentuknya saja, mengirim temuan itu ke model, dan baru mengompilasi
    jawaban berikutnya.
    """
    compiler = StubCompiler(FAILED, LatexBuildResult(ok=True))
    model = ScriptedChatModel([chapter_json(title="Potongan Rusak", body="   "), chapter_json()])

    result = make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    assert model.call_count == 2
    assert compiler.calls == 2
    assert "Potongan Rusak" not in compiler.fragments[1]
    assert "body_tex" in model.requests[1].user, "temuan bentuknya harus ikut dikirim"
    assert result.approved is True


def test_an_invented_citation_key_in_the_repair_is_rejected_before_compiling(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """§34 ditegakkan pada tingkat kunci: perbaikan tidak boleh menyelundupkan kunci baru."""
    invented = citation_key("Buku yang Tidak Pernah Ada")
    compiler = StubCompiler(FAILED, LatexBuildResult(ok=True))
    model = ScriptedChatModel([chapter_json(cite_keys=(invented,)), chapter_json()])

    result = make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    assert invented in model.requests[1].user
    assert invented not in artifacts.load_chapter(1)
    assert result.approved is True


# ---------------------------------------------------------------------------
# 4. Yang tetap gagal ditolak — dengan log-nya
# ---------------------------------------------------------------------------
def test_an_exhausted_ladder_rejects_the_chapter(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Bab yang tidak dapat dicetak adalah bab yang tidak ada."""
    compiler = StubCompiler(FAILED, FAILED, FAILED)
    model = ScriptedChatModel([chapter_json(), chapter_json()])

    result = make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    assert model.call_count == 2
    assert compiler.calls == 3
    assert result.approved is False
    assert result.score == 2, "bukan nol: potongannya ada, yang gagal hanyalah kompilasinya"
    assert result.skipped is False


def test_the_rejection_carries_what_the_latex_tool_actually_said(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Penerima catatan ini adalah penulis bab, bukan orang yang membaca kode."""
    compiler = StubCompiler(FAILED, FAILED, FAILED)
    model = ScriptedChatModel([chapter_json(), chapter_json()])

    result = make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    joined = "\n".join(result.feedback)
    assert "tidak dapat dikompilasi" in joined
    assert "galat LaTeX: Misplaced alignment tab character &." in joined
    assert "l.3 Notasi $O(n)$ & temannya." in joined


def test_a_rejected_chapter_keeps_the_fragment_that_was_already_written(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Menimpa potongan yang setidaknya pernah dihasilkan adalah kerugian permanen."""
    compiler = StubCompiler(FAILED, FAILED, FAILED)
    model = ScriptedChatModel([chapter_json(), chapter_json()])

    make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    assert artifacts.saves == 0
    assert artifacts.load_chapter(1) == FRAGMENT


def test_without_repair_attempts_a_failure_is_rejected_immediately(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    compiler = StubCompiler(FAILED)
    model = ScriptedChatModel([])

    result = make_gate(
        model, prompt_library, artifacts, compiler, repair_attempts=0
    ).evaluate(record, book)

    assert model.call_count == 0
    assert compiler.calls == 1
    assert result.approved is False
    assert result.score == 2


def test_a_tool_failure_without_any_error_line_is_still_a_problem(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Perkakas yang tidak ada dan batas waktu yang lewat tidak menulis baris ``!``."""
    compiler = StubCompiler(LatexBuildResult(ok=False))
    model = ScriptedChatModel([])

    result = make_gate(
        model, prompt_library, artifacts, compiler, repair_attempts=0
    ).evaluate(record, book)

    assert result.approved is False
    assert any("tidak menghasilkan PDF" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 5. Prasyarat
# ---------------------------------------------------------------------------
def test_a_chapter_that_was_never_written_in_latex_is_refused(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    tmp_path: Path,
) -> None:
    """Tidak ada yang dapat dikompilasi bukan berarti kompilasinya bersih."""
    empty = FileLatexArtifacts(tmp_path / "latex")
    compiler = StubCompiler()
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library, empty, compiler).evaluate(record, book)

    assert "sumber LaTeX" in excinfo.value.missing
    assert compiler.calls == 0
    assert model.call_count == 0


def test_a_record_without_a_spec_is_refused(
    prompt_library: FilePromptLibrary,
    book: BookState,
    artifacts: CountingArtifacts,
) -> None:
    bare = ChapterRecord(number=1, status=ChapterStatus.LATEX_GENERATED, draft=DRAFT)
    compiler = StubCompiler()

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(ScriptedChatModel([]), prompt_library, artifacts, compiler).evaluate(bare, book)

    assert excinfo.value.missing == "spesifikasi bab"
    assert compiler.calls == 0


def test_a_record_without_a_draft_is_refused(
    prompt_library: FilePromptLibrary,
    book: BookState,
    artifacts: CountingArtifacts,
) -> None:
    bare = ChapterRecord(number=1, status=ChapterStatus.LATEX_GENERATED, spec=SPEC)
    compiler = StubCompiler()

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(ScriptedChatModel([]), prompt_library, artifacts, compiler).evaluate(bare, book)

    assert excinfo.value.missing == "draf"
    assert compiler.calls == 0


# ---------------------------------------------------------------------------
# 6. Dari mana modelnya datang — dan apa yang terjadi bila kompilasi mustahil
# ---------------------------------------------------------------------------
def test_the_gate_declares_the_status_that_closes_the_latex_stage() -> None:
    assert LatexQAGate.name == "latex_qa"
    assert LatexQAGate.produces is ChapterStatus.LATEX_COMPILED


def test_the_repair_agent_uses_the_latex_role() -> None:
    """Peran yang sama dengan penulisnya: ia yang sudah memegang konteks babnya."""
    assert LatexRepairAgent.role == "latex"
    assert LatexRepairAgent.prompt_name == "latex.repair"


def test_the_gate_takes_its_model_from_the_router_by_role(
    prompt_library: FilePromptLibrary, artifacts: CountingArtifacts
) -> None:
    provider = StubProvider(ScriptedChatModel([]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
        latex=artifacts,
        compiler=StubCompiler(LatexBuildResult(ok=True)),
    )

    gates = build_gates(("latex_qa",), context)

    assert provider.requested == ["latex"]
    assert isinstance(gates[0], LatexQAGate)
    assert gates[0].produces is ChapterStatus.LATEX_COMPILED


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

    gates = build_gates(("latex_qa",), context)

    assert isinstance(gates[0], PassThroughGate)
    assert gates[0].produces is ChapterStatus.LATEX_COMPILED
    assert provider.requested == [], "peran latex tidak boleh dipanggil saat dimatikan"


def test_a_machine_without_latex_tools_yields_a_pass_through_gate(
    prompt_library: FilePromptLibrary, artifacts: CountingArtifacts
) -> None:
    """Sumbernya tetap ditulis, tetapi tidak ada yang mengompilasinya di sini."""
    provider = StubProvider(ScriptedChatModel([]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
        latex=artifacts,
        compiler=None,
    )

    gates = build_gates(("latex_qa",), context)

    assert isinstance(gates[0], PassThroughGate)
    assert provider.requested == []


def test_the_pass_through_gate_explains_why_nothing_was_compiled(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    provider = StubProvider(ScriptedChatModel([]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
        latex=None,
    )
    gate = build_gates(("latex_qa",), context)[0]

    result = gate.evaluate(ChapterRecord(number=1), book)

    assert result.skipped is True
    assert any("latex.enabled" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 7. Jalur ``--dry-run``: rangkaiannya diperiksa tanpa menjalankan LaTeX
# ---------------------------------------------------------------------------
def test_the_dry_run_compiler_carries_the_gate_through_without_a_model_call(
    prompt_library: FilePromptLibrary,
    book: BookState,
    record: ChapterRecord,
    artifacts: CountingArtifacts,
) -> None:
    """Berbeda dari pass-through: gate-nya benar-benar berjalan, jawabannya yang palsu."""
    compiler = DryRunLatexCompiler()
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library, artifacts, compiler).evaluate(record, book)

    assert compiler.calls == 1
    assert model.call_count == 0
    assert result.approved is True
    assert result.skipped is False, "gate-nya berjalan; yang palsu hanyalah jawabannya"
