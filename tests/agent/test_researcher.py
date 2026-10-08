"""Tes Research Agent (§17) — dua implementasi, dua perilaku yang harus jujur.

``NullResearcher`` tidak meneliti, jadi yang diuji padanya adalah **batasnya**:
ia tidak boleh mengaku punya sumber, dan ia harus tetap memenuhi kontrak port
sehingga penggantinya dapat dipasang tanpa menyentuh satu pun pemanggil.

``RagResearcher`` meneliti, dan yang diuji padanya adalah **pembagian tugas**:
retriever yang menyediakan bukti, model yang menyaringnya. Bukti beserta halaman
dan nama berkasnya tidak boleh melewati model — model yang boleh menuliskan
nomor halaman dapat mengarangnya, dan halaman yang salah adalah kesalahan yang
tidak akan pernah ditemukan pembaca.

Seluruh berkas ini berjalan tanpa jaringan: retriever-nya palsu, dan modelnya
berskrip.
"""

from __future__ import annotations

import pytest

from agents.researcher import DEFAULT_TOP_K, NullResearcher, RagResearcher
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, ChapterSpec
from domain.chapter import Evidence, ResearchPackage
from domain.errors import AgentOutputError
from domain.ports import Researcher
from domain.state import BookState
from tests.fakes.chat_models import ScriptedChatModel, json_draft

SPEC = ChapterSpec(
    number=2,
    title="Analisis Kompleksitas",
    references=("Cormen, Introduction to Algorithms, 4th ed.",),
    source_weeks=("Minggu 2",),
)

PLANNED_SPEC = ChapterSpec(
    number=2,
    title="Analisis Kompleksitas",
    objectives=("Mahasiswa mampu menghitung kompleksitas waktu",),
    sections=("Pengantar", "Notasi Big-O"),
)


def _book() -> BookState:
    return BookState(request=BookRequest(title="Buku", target_chapters=1))


def test_null_researcher_conforms_to_the_port() -> None:
    """Kesesuaian ditegakkan tipe, bukan ``isinstance`` — port ini bukan runtime_checkable.

    Anotasi di bawah adalah tesnya: ``mypy`` menolak berkas ini bila
    ``NullResearcher`` berhenti memenuhi ``Researcher``. Itu jauh lebih kuat
    daripada pemeriksaan saat jalan, karena kegagalannya muncul sebelum kode
    dijalankan.
    """
    researcher: Researcher = NullResearcher()

    assert researcher.collect(SPEC, _book()) is not None


def test_null_researcher_marks_its_package_degraded() -> None:
    """``degraded`` adalah seluruh isi kontraknya — prompt penulis bercabang dari sini."""
    package = NullResearcher().collect(SPEC, _book())

    assert package.degraded is True


def test_null_researcher_claims_no_sources_even_when_the_spec_assigns_them() -> None:
    """Rujukan yang ditugaskan **bukan** bahan yang sudah diambil.

    Ini invarian terpenting di berkas ini. Mengisi ``sources`` dari
    ``spec.references`` akan menghasilkan paket yang sekaligus mengaku punya
    sumber dan mengaku terdegradasi; penulis lalu akan mengutip buku yang belum
    pernah dibuka siapa pun.
    """
    package = NullResearcher().collect(SPEC, _book())

    assert package.sources == ()
    assert package.evidence == ()


def test_null_researcher_returns_an_entirely_empty_package() -> None:
    package = NullResearcher().collect(SPEC, _book())

    assert package == ResearchPackage.empty()


def test_collect_does_not_mutate_the_book() -> None:
    """Pemanggilan berulang harus menghasilkan paket identik — tidak ada state tersembunyi."""
    book = _book()
    snapshot = book.model_dump()
    researcher = NullResearcher()

    first = researcher.collect(SPEC, book)
    second = researcher.collect(SPEC, book)

    assert first == second
    assert book.model_dump() == snapshot


def test_null_researcher_works_without_a_book_spec() -> None:
    """Bab pertama dapat direncanakan sebelum ``BookSpec`` tersimpan; itu harus tetap sah."""
    book = BookState(request=BookRequest(title="Buku", target_chapters=1))

    assert NullResearcher().collect(SPEC, book).degraded is True


# ---------------------------------------------------------------------------
# RagResearcher — pembagian tugas antara retriever dan model (§13, §17)
# ---------------------------------------------------------------------------
class StubRetriever:
    """Retriever palsu: buktinya sudah ditentukan, pertanyaannya dicatat.

    Yang diuji di sini bukan kualitas pencariannya — itu urusan ``rag/`` —
    melainkan **apa yang dilakukan peneliti dengan hasilnya**, dan pertanyaan
    seperti apa yang ia ajukan.
    """

    def __init__(self, evidence: tuple[Evidence, ...] = ()) -> None:
        self.evidence = evidence
        self.queries: list[tuple[str, int]] = []

    def retrieve(self, query: str, *, limit: int = 8) -> tuple[Evidence, ...]:
        self.queries.append((query, limit))
        return self.evidence


def _evidence() -> tuple[Evidence, ...]:
    """Tiga bukti dari dua berkas — satu berkas muncul dua kali, seperti sungguhan."""
    return (
        Evidence(
            source="compiler.pdf",
            page=42,
            section="Notasi Big-O",
            text="Notasi asimtotik menggambarkan laju pertumbuhan.",
            score=0.91,
        ),
        Evidence(
            source="compiler.pdf",
            page=88,
            section="Graf Berarah",
            text="Graf berarah memuat pasangan terurut.",
            score=0.72,
        ),
        Evidence(
            source="rosen.pdf",
            page=12,
            section="Himpunan",
            text="Himpunan adalah kumpulan objek.",
            score=0.61,
        ),
    )


#: Balasan model yang sah: tiga daftar, tanpa ``source`` maupun ``page``.
FINDINGS = json_draft(
    concepts=["notasi Big-O", "graf berarah"],
    definitions=["O(n) berarti waktu eksekusi tumbuh linear terhadap masukan."],
    examples=[],
)


def _researcher(
    model: ScriptedChatModel,
    prompts: FilePromptLibrary,
    retriever: StubRetriever,
    *,
    top_k: int = DEFAULT_TOP_K,
    max_repair_attempts: int = 2,
) -> RagResearcher:
    """Peneliti di atas retriever palsu — tanpa menulis ulang argumen di setiap tes."""
    return RagResearcher(
        model=model,
        prompts=prompts,
        retriever=retriever,
        top_k=top_k,
        max_repair_attempts=max_repair_attempts,
    )


def test_rag_researcher_conforms_to_the_port(prompt_library: FilePromptLibrary) -> None:
    """Kesesuaian ditegakkan tipe: ``mypy`` menolak berkas ini bila ia berhenti memenuhinya."""
    researcher: Researcher = RagResearcher(
        model=ScriptedChatModel(()),
        prompts=prompt_library,
        retriever=StubRetriever(),
    )

    assert researcher.collect(SPEC, _book()).degraded is True


def test_a_package_built_from_evidence_is_not_degraded(
    prompt_library: FilePromptLibrary,
) -> None:
    """``degraded=False`` adalah seluruh gunanya: hanya dengan itu penulis boleh mengutip."""
    package = _researcher(
        ScriptedChatModel([FINDINGS]), prompt_library, StubRetriever(_evidence())
    ).collect(SPEC, _book())

    assert package.degraded is False
    assert package.concepts == ("notasi Big-O", "graf berarah")
    assert package.definitions == ("O(n) berarti waktu eksekusi tumbuh linear terhadap masukan.",)
    assert package.examples == ()


def test_the_evidence_reaches_the_package_with_its_pages_intact(
    prompt_library: FilePromptLibrary,
) -> None:
    """§13: bukti wajib memuat asalnya, dan halaman itu tidak boleh melewati model.

    Halaman yang hilang di tengah jalan tidak menghasilkan galat apa pun — yang
    dihasilkan adalah bab yang mengutip halaman yang tidak memuat kalimatnya.
    """
    package = _researcher(
        ScriptedChatModel([FINDINGS]), prompt_library, StubRetriever(_evidence())
    ).collect(SPEC, _book())

    assert [item.page for item in package.evidence] == [42, 88, 12]
    assert [item.source for item in package.evidence] == [
        "compiler.pdf",
        "compiler.pdf",
        "rosen.pdf",
    ]
    assert [item.score for item in package.evidence] == [0.91, 0.72, 0.61]


def test_sources_are_unique_and_follow_relevance_order(
    prompt_library: FilePromptLibrary,
) -> None:
    """Satu PDF menghasilkan puluhan potongan tetapi hanya satu entri daftar pustaka.

    Urutannya mengikuti relevansi, bukan abjad: daftar pustaka yang mendahulukan
    bahan paling relevan lebih berguna daripada yang mendahulukan huruf awal.
    """
    package = _researcher(
        ScriptedChatModel([FINDINGS]), prompt_library, StubRetriever(_evidence())
    ).collect(SPEC, _book())

    assert package.sources == ("compiler.pdf", "rosen.pdf")


def test_a_blank_source_never_reaches_the_bibliography(
    prompt_library: FilePromptLibrary,
) -> None:
    """Entri daftar pustaka tanpa nama berkas tidak dapat ditelusuri siapa pun."""
    evidence = (
        Evidence(source="   ", page=1, text="tanpa asal", score=0.5),
        Evidence(source="rosen.pdf", page=12, section="Himpunan", text="Himpunan.", score=0.4),
    )

    package = _researcher(
        ScriptedChatModel([FINDINGS]), prompt_library, StubRetriever(evidence)
    ).collect(SPEC, _book())

    assert package.sources == ("rosen.pdf",)


def test_no_evidence_means_no_model_call_at_all(prompt_library: FilePromptLibrary) -> None:
    """Memanggil peneliti pada bahan kosong hanya akan menghasilkan konsep karangan.

    Antrean model yang **kosong** di bawah ini bukan hiasan: ``ScriptedChatModel``
    gagal keras pada setiap panggilan, sehingga tes ini sekaligus membuktikan
    bahwa jalur ini benar-benar tidak memanggil model.
    """
    model = ScriptedChatModel(())

    package = _researcher(model, prompt_library, StubRetriever(())).collect(SPEC, _book())

    assert package == ResearchPackage.empty()
    assert package.degraded is True
    assert model.call_count == 0


def test_the_query_is_built_from_what_the_chapter_already_has(
    prompt_library: FilePromptLibrary,
) -> None:
    """Judul, tujuan, dan rencana sub-bab — tiga hal yang memang dimiliki bab ini.

    Meng-embed seluruh RPS akan menghasilkan pencarian yang terlalu umum:
    potongan yang cocok dengan rata-rata bab tidak berguna bagi bab mana pun.
    """
    retriever = StubRetriever(())

    _researcher(ScriptedChatModel(()), prompt_library, retriever).collect(
        PLANNED_SPEC, _book()
    )

    assert retriever.queries[0][0] == (
        "Analisis Kompleksitas Mahasiswa mampu menghitung kompleksitas waktu "
        "Pengantar Notasi Big-O"
    )


def test_a_chapter_without_objectives_or_sections_searches_by_title(
    prompt_library: FilePromptLibrary,
) -> None:
    retriever = StubRetriever(())

    _researcher(ScriptedChatModel(()), prompt_library, retriever).collect(SPEC, _book())

    assert retriever.queries[0][0] == "Analisis Kompleksitas"


def test_the_retriever_is_asked_for_top_k_items(prompt_library: FilePromptLibrary) -> None:
    retriever = StubRetriever(())

    _researcher(ScriptedChatModel(()), prompt_library, retriever, top_k=3).collect(
        SPEC, _book()
    )

    assert retriever.queries[0][1] == 3


def test_a_top_k_below_one_falls_back_to_a_single_item(
    prompt_library: FilePromptLibrary,
) -> None:
    """``rag.top_k: 0`` tidak boleh berarti "bab tanpa bahan sama sekali"."""
    retriever = StubRetriever(())

    _researcher(ScriptedChatModel(()), prompt_library, retriever, top_k=0).collect(
        SPEC, _book()
    )

    assert retriever.queries[0][1] == 1


def test_the_default_top_k_matches_the_configured_one() -> None:
    """Angka bawaan di sini harus sama dengan ``rag.top_k`` di ``config.yaml``."""
    assert DEFAULT_TOP_K == 8


def test_the_model_is_called_in_the_researcher_role_with_a_schema(
    prompt_library: FilePromptLibrary,
) -> None:
    """Pemanggil tidak memilih modelnya — yang dipakai adalah peran ``researcher``."""
    model = ScriptedChatModel([FINDINGS])

    _researcher(model, prompt_library, StubRetriever(_evidence())).collect(SPEC, _book())

    request = model.requests[0]
    assert request.role == "researcher"
    assert request.format_schema is not None
    assert request.prompt_version.startswith("1+")


def test_the_model_sees_every_piece_with_its_file_and_page(
    prompt_library: FilePromptLibrary,
) -> None:
    """Bukti yang tidak menyebut asalnya tidak dapat diperiksa.

    Yang menyaring adalah model, dan ia hanya dapat menyaring apa yang
    ditunjukkan kepadanya — karena itu berkas dan halamannya harus ada di prompt,
    bukan hanya di dalam tipe.
    """
    model = ScriptedChatModel([FINDINGS])

    _researcher(model, prompt_library, StubRetriever(_evidence())).collect(SPEC, _book())

    user = model.requests[0].user
    assert "compiler.pdf" in user
    assert "hlm. 42" in user
    assert "Notasi asimtotik menggambarkan laju pertumbuhan." in user


def test_the_model_cannot_add_a_source_or_a_page_of_its_own(
    prompt_library: FilePromptLibrary,
) -> None:
    """``ResearchFindings`` menolak field asing, jadi ``source``/``page`` tidak punya jalan masuk.

    Yang diuji bukan kerapian skema, melainkan satu-satunya jalan bagi model untuk
    mengarang halaman. Tangga perbaikan dimatikan supaya yang diperiksa benar-benar
    penolakan skemanya, bukan habisnya percobaan perbaikan.
    """
    model = ScriptedChatModel(
        [
            json_draft(
                concepts=[], definitions=[], examples=[], source="karangan.pdf", page=999
            )
        ]
    )
    researcher = _researcher(
        model, prompt_library, StubRetriever(_evidence()), max_repair_attempts=0
    )

    with pytest.raises(AgentOutputError) as excinfo:
        researcher.collect(SPEC, _book())

    assert excinfo.value.agent == "researcher"
