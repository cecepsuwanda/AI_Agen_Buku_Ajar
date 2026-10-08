"""Tes Citation Checker dan gate-nya (§22).

§22 meminta tiga hal, dan berkas ini menguji ketiganya **berurutan sebagaimana
gate menjalankannya** — bukan sebagai satu daftar fitur:

1. Setiap kunci yang dikutip harus punya entri di knowledge base. Ini dapat
   dipastikan tanpa model, jadi tesnya memakai :class:`ScriptedChatModel` yang
   **kosong**: bila gate memanggil model, tesnya gagal dengan pesan yang jelas
   alih-alih diam-diam lulus. Itulah bentuk tes yang paling penting di sini —
   bukan sekadar "babnya ditolak", melainkan "babnya ditolak tanpa membayar"
   (keputusan 7 di rencana: pemeriksaan deterministik lebih dulu).
2. Apakah rujukan itu benar-benar menopang isi bab. Ini penilaian, dan baru di
   sini model dipanggil.
3. Model tidak boleh mengarang kunci sitasi. Karena itu `subject` disalin apa
   adanya, dan rujukan yang tidak disebut model sama sekali **dilaporkan**
   sebagai belum diperiksa — bukan dianggap lulus.

Yang kedua dari ketiganya sering dianggap remeh: vonis model yang menyetujui
babnya sementara temuannya sendiri gagal akan diturunkan sistem. Tesnya ada di
bawah, dan ia menegakkan pembagian wewenang yang sama dengan
:func:`~domain.rules.decide_review`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.citation_checker import CitationChecker, CitationCheckerAgent
from agents.gates import GateContext
from app.prompting import FilePromptLibrary
from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ChapterRecord, ResearchPackage, Section
from domain.enums import ChapterStatus
from domain.errors import GatePreconditionError
from domain.state import BookState
from domain.transitions import can_advance
from tests.fakes.chat_models import ScriptedChatModel

#: Sumber yang benar-benar ada di knowledge base — sama dengan ``full_research``.
CORMEN = "Cormen, Introduction to Algorithms, 4th ed."

#: Kunci yang tidak ada di mana pun. Bentuknya masuk akal, dan itu justru
#: alasannya: kunci halusinasi yang kelihatan salah tidak akan pernah lolos ke
#: draf, jadi yang perlu diuji adalah yang kelihatan benar.
HALLUCINATED = "Knuth, The Art of Computer Programming, 3rd ed."

SPEC = ChapterSpec(
    number=1,
    title="Analisis Kompleksitas",
    sections=("Notasi Big-O",),
    objectives=("Mahasiswa mampu menghitung kompleksitas waktu algoritma.",),
)

DRAFT = ChapterDraft(
    title="Analisis Kompleksitas",
    sections=(
        Section(heading="Notasi Big-O", body="O(n) berarti waktu eksekusi tumbuh linear."),
    ),
    citations=(CORMEN,),
)


def citing(*keys: str) -> ChapterDraft:
    """Draf yang sama, dengan daftar rujukan yang ditentukan tes."""
    return DRAFT.model_copy(update={"citations": keys})


def verdict_json(**overrides: Any) -> str:
    """Balasan ``CheckVerdict`` yang sah: satu temuan untuk setiap rujukan."""
    payload: dict[str, Any] = {
        "approved": True,
        "score": 9,
        "feedback": [],
        "findings": [
            {
                "subject": CORMEN,
                "ok": True,
                "detail": "Bahan menopang definisi notasi Big-O di sub-bab 1.1.",
            }
        ],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class StubProvider:
    """``ModelProvider`` palsu yang mencatat peran yang diminta.

    ``embedder`` sengaja melempar: pemeriksa sitasi tidak punya urusan dengan
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
        raise AssertionError("pemeriksa sitasi tidak boleh meminta model embedding")


def make_book(**overrides: Any) -> BookState:
    """Buku dengan ``citations`` yang dapat diatur — §22 bekerja lintas bab."""
    fields: dict[str, Any] = {
        "request": BookRequest(title="Algoritma", target_chapters=1),
        "spec": BookSpec(title="Algoritma", chapters=(SPEC,)),
    }
    fields.update(overrides)
    return BookState(**fields)


@pytest.fixture
def book() -> BookState:
    """Buku yang belum pernah menyetujui bab mana pun."""
    return make_book()


@pytest.fixture
def record(full_research: ResearchPackage) -> ChapterRecord:
    return ChapterRecord(
        number=1,
        status=ChapterStatus.FACT_CHECKED,
        spec=SPEC,
        draft=DRAFT,
        research=full_research,
    )


def make_gate(
    model: ScriptedChatModel, prompts: FilePromptLibrary, **kwargs: Any
) -> CitationChecker:
    return CitationChecker(model=model, prompts=prompts, **kwargs)


# ---------------------------------------------------------------------------
# 1. Lapis deterministik — ditolak tanpa satu pun panggilan model
# ---------------------------------------------------------------------------
def test_a_key_outside_the_knowledge_base_is_rejected_without_calling_the_model(
    prompt_library: FilePromptLibrary,
) -> None:
    """Kunci halusinasi adalah kegagalan pasti; menanyakannya berarti membayar untuk mengetahuinya.

    ``ScriptedChatModel([])`` tidak punya satu balasan pun. Bila gate tetap
    memanggil model, tes ini gagal — dan kegagalannya adalah inti pemeriksaan
    ini, bukan efek sampingnya.

    ``CORMEN`` diwarisi dari bab sebelumnya, sehingga ia **bukan** yang ditolak:
    yang diuji di sini harus satu kunci yang benar-benar asing.
    """
    book = make_book(citations={CORMEN: CORMEN})
    record = ChapterRecord(
        number=1,
        status=ChapterStatus.FACT_CHECKED,
        spec=SPEC,
        draft=citing(CORMEN, HALLUCINATED),
        research=ResearchPackage.empty(),
    )
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 0
    assert result.approved is False
    assert result.score == 0
    assert result.skipped is False
    assert result.gate == "citation_checker"


def test_the_offending_key_is_named_verbatim_so_it_can_be_removed(
    prompt_library: FilePromptLibrary,
) -> None:
    """"Ada rujukan yang tidak sah" membuat penulis menebak; nama kuncinya tidak.

    Yang dicari adalah kunci yang **salah** — dan ketiadaan kunci yang benar di
    dalam catatan itu sama pentingnya: menuduh rujukan yang sah akan membuat
    penulis menghapus bahan yang justru menopang babnya.
    """
    book = make_book(citations={CORMEN: CORMEN})
    record = ChapterRecord(
        number=1,
        status=ChapterStatus.FACT_CHECKED,
        spec=SPEC,
        draft=citing(CORMEN, HALLUCINATED),
        research=ResearchPackage.empty(),
    )
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert any(repr(HALLUCINATED) in note for note in result.feedback)
    assert not any(repr(CORMEN) in note for note in result.feedback)


def test_a_rejection_does_not_enrich_the_draft_it_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Bab ini menuju revisi; draf yang dipasang di sini tidak akan pernah disetujui siapa pun."""
    record = ChapterRecord(
        number=1,
        status=ChapterStatus.FACT_CHECKED,
        spec=SPEC,
        draft=citing(HALLUCINATED),
        research=ResearchPackage.empty(),
    )

    result = make_gate(ScriptedChatModel([]), prompt_library).evaluate(record, book)

    assert result.enriched_draft is None


def test_a_degraded_package_permits_nothing_when_nothing_was_ever_approved(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """Tanpa RAG, penulis memang dilarang mengutip (§17) — dan gate menegakkannya.

    Ini bukan duplikat pemeriksaan penulis: penulis **membersihkan** drafnya,
    sedangkan pemeriksa harus **menolak**. Draf bisa datang dari tempat lain —
    state lama, suntingan tangan — dan aturan yang hanya ditegakkan di satu
    tempat bukan aturan.
    """
    record = ChapterRecord(
        number=1, status=ChapterStatus.FACT_CHECKED, spec=SPEC, draft=DRAFT
    )
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 0
    assert result.approved is False


# ---------------------------------------------------------------------------
# 2. Lintas bab — sumber yang sudah disetujui tetap sah dikutip (§22)
# ---------------------------------------------------------------------------
def test_a_previously_approved_source_is_still_permitted_on_a_later_chapter(
    prompt_library: FilePromptLibrary,
) -> None:
    """Sumber yang sah dikutip bab 1 tetap sah dikutip bab 5.

    Pencarian bab 5 boleh saja tidak memunculkannya kembali; yang dituntut §22
    adalah "berasal dari knowledge base", bukan "berasal dari hasil pencarian
    bab ini". Tanpa ``book.citations``, gate ini akan menolak bab yang benar.
    """
    book = make_book(citations={CORMEN: CORMEN})
    record = ChapterRecord(
        number=1, status=ChapterStatus.FACT_CHECKED, spec=SPEC, draft=DRAFT
    )
    model = ScriptedChatModel([verdict_json()])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 1
    assert result.approved is True


def test_when_no_material_was_retrieved_the_prompt_says_so_instead_of_staying_silent(
    prompt_library: FilePromptLibrary,
) -> None:
    """Konteks hilang bukan konteks kosong: model harus tahu bahwa tidak ada bahan.

    Paket riset terdegradasi membawa ``sources`` kosong, sehingga rujukan yang
    diwarisi lolos lapis pertama sementara bahan untuk memeriksanya tidak ada.
    Prompt yang diam tentang hal itu akan membuat model menjawab dari ingatannya
    sendiri — dan rujukan yang "sepertinya benar" adalah rujukan yang tidak
    pernah diperiksa siapa pun.
    """
    book = make_book(citations={CORMEN: CORMEN})
    record = ChapterRecord(
        number=1, status=ChapterStatus.FACT_CHECKED, spec=SPEC, draft=DRAFT
    )
    model = ScriptedChatModel([verdict_json()])

    make_gate(model, prompt_library).evaluate(record, book)

    sent = model.requests[0].user
    assert "Tidak ada." in sent
    assert "bahannya tidak tersedia" in sent
    assert "Notasi asimtotik" not in sent, "tidak ada bukti yang boleh disisipkan"


# ---------------------------------------------------------------------------
# 3. Lapis penilaian — di sinilah model baru dipanggil
# ---------------------------------------------------------------------------
def test_an_approving_verdict_approves_the_chapter(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    model = ScriptedChatModel([verdict_json()])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 1
    assert result.approved is True
    assert result.score == 9
    assert result.skipped is False
    assert result.gate == "citation_checker"


def test_a_finding_the_model_itself_marks_not_ok_lowers_its_own_approval(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Pemeriksa yang menandai rujukan tak didukung lalu tetap menyetujui membatalkan kerjanya.

    Tanpa aturan ini, bab itu lolos tanpa satu pun tanda — dan laporan akhirnya
    akan menyatakan sitasinya sudah diperiksa.
    """
    model = ScriptedChatModel(
        [
            verdict_json(
                approved=True,
                score=9,
                findings=[
                    {"subject": CORMEN, "ok": False, "detail": "Bahan membahas hal lain."}
                ],
            )
        ]
    )

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert result.score == 9, "angkanya tidak diubah; yang diturunkan adalah vonisnya"
    assert any("diabaikan oleh sistem" in note for note in result.feedback)


def test_the_failing_finding_reaches_the_writer_with_its_source_and_page(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Sumber dan halaman diisi program dari bukti — bukan dikarang model.

    Halaman 45 adalah halaman bukti **pertama** untuk sumber itu, dan ia hanya
    dapat muncul di sini bila pencocokannya benar-benar terjadi. Kalau model
    yang mengisinya, tes ini akan lolos dengan angka apa pun yang kebetulan
    ditulisnya.
    """
    model = ScriptedChatModel(
        [
            verdict_json(
                approved=False,
                score=3,
                findings=[
                    {"subject": CORMEN, "ok": False, "detail": "Bahan membahas hal lain."}
                ],
            )
        ]
    )

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert any("hlm. 45" in note for note in result.feedback)
    assert any(
        CORMEN in note and "Bahan membahas hal lain." in note for note in result.feedback
    )


def test_a_citation_the_model_never_mentions_is_reported_as_unexamined(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """"Tidak ada temuan" bukan "sudah diperiksa".

    Model yang menyatakan semuanya baik tanpa menyebut satu pun kunci belum
    memeriksa apa pun. Ia tidak menurunkan vonisnya — yang dilaporkan di sini
    adalah kekurangan **laporan**, bukan kekurangan bab — tetapi ia harus
    terlihat.
    """
    model = ScriptedChatModel([verdict_json(findings=[])])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert any("tidak diperiksa model" in note for note in result.feedback)
    assert any(repr(CORMEN) in note for note in result.feedback)
    assert result.approved is True, "laporan yang kurang bukan bab yang kurang"


def test_a_score_under_the_threshold_is_lowered_by_the_shared_rule(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Ambang tetap ditegakkan di :func:`~domain.rules.decide_review`, bukan di sini.

    Bila gate ini punya ambangnya sendiri, akan ada dua tempat yang memutuskan
    lulus/tidaknya sebuah bab — dan cepat atau lambat keduanya berbeda pendapat.
    """
    model = ScriptedChatModel([verdict_json(approved=True, score=6)])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert result.score == 6
    assert any("ambang" in note for note in result.feedback)


def test_the_threshold_comes_from_the_caller(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """``review_threshold`` dari ``config.yaml`` harus benar-benar mengikat."""
    model = ScriptedChatModel([verdict_json(approved=True, score=6)])

    result = make_gate(model, prompt_library, threshold=5).evaluate(record, book)

    assert result.approved is True


# ---------------------------------------------------------------------------
# 4. Bab yang tidak punya pertanyaan untuk ditanyakan
# ---------------------------------------------------------------------------
def test_a_draft_without_citations_is_skipped_without_spending_a_token(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Bab tanpa sitasi bukan bab yang gagal — tetapi juga bukan bab yang diperiksa.

    ``skipped=True`` adalah bagian terpentingnya: bab yang lolos tanpa
    pemeriksaan harus terlihat sebagai bab yang lolos tanpa pemeriksaan.
    """
    bare = record.model_copy(update={"draft": citing()})
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library).evaluate(bare, book)

    assert model.call_count == 0
    assert result.skipped is True
    assert result.approved is True
    assert result.score == 10
    assert any("tidak memuat satu pun sitasi" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 5. Prasyarat — gagal sebelum satu token pun dibakar
# ---------------------------------------------------------------------------
def test_a_record_without_a_draft_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    bare = ChapterRecord(number=1, status=ChapterStatus.FACT_CHECKED, spec=SPEC)
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library).evaluate(bare, book)

    assert excinfo.value.gate == "citation_checker"
    assert excinfo.value.missing == "draf"
    assert model.call_count == 0


def test_a_record_without_a_spec_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    bare = ChapterRecord(number=1, status=ChapterStatus.FACT_CHECKED, draft=DRAFT)
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library).evaluate(bare, book)

    assert excinfo.value.missing == "spesifikasi bab"
    assert model.call_count == 0


# ---------------------------------------------------------------------------
# 6. Apa yang dikirim ke model
# ---------------------------------------------------------------------------
def test_the_prompt_carries_the_cited_keys_and_the_evidence_with_its_page(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Model hanya dapat menilai kesesuaian bila ia melihat keduanya."""
    model = ScriptedChatModel([verdict_json()])

    make_gate(model, prompt_library).evaluate(record, book)

    sent = model.requests[0].user
    assert CORMEN in sent
    assert "hlm. 45" in sent
    assert "Notasi asimtotik menggambarkan laju pertumbuhan." in sent
    assert "Analisis Kompleksitas" in sent


def test_the_agent_asks_for_the_schema_it_declares(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Tanpa ``format_schema``, ``--dry-run`` tidak dapat mensintesis balasan."""
    model = ScriptedChatModel([verdict_json()])

    make_gate(model, prompt_library).evaluate(record, book)

    schema = model.schemas()[0]
    assert schema is not None
    assert set(schema["properties"]) == {"approved", "score", "feedback", "findings"}


def test_the_agent_uses_the_reviewer_role(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Memutuskan sesuatu tentang pekerjaan yang sudah ada, bukan menghasilkan bahan baru."""
    assert CitationCheckerAgent.role == "reviewer"
    assert CitationCheckerAgent.prompt_name == "citation.chapter"


# ---------------------------------------------------------------------------
# 7. Tempatnya di rantai §27 dan di registri
# ---------------------------------------------------------------------------
def test_the_gate_produces_the_status_the_chain_expects() -> None:
    assert CitationChecker.produces is ChapterStatus.CITATION_CHECKED
    assert can_advance(ChapterStatus.FACT_CHECKED, CitationChecker.produces)


def test_the_gate_takes_its_model_from_the_router_by_role(
    prompt_library: FilePromptLibrary,
) -> None:
    """Satu-satunya tempat ``router.chat("reviewer")`` dipanggil untuk gate ini."""
    from agents.gates import build_gates  # lokal: menghindari impor melingkar

    provider = StubProvider(ScriptedChatModel([verdict_json()]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
    )

    gates = build_gates(("citation_checker",), context)

    assert provider.requested == ["reviewer"]
    assert gates[0].name == "citation_checker"
    assert gates[0].produces is ChapterStatus.CITATION_CHECKED
