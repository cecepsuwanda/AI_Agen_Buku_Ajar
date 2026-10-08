"""Tes Fact Checker dan gate-nya (§21).

§21 meminta satu alur, dan berkas ini mengujinya berurutan sebagaimana gate
menjalankannya:

> Claim → Retrieved Evidence → Supported?

Tiga hal diuji di sini yang tidak akan terlihat dari sekadar "babnya ditolak":

1. **Gate berhenti tanpa memanggil model bila tidak ada bahan.** Tanpa bukti,
   satu-satunya cara model menjawab adalah dari ingatannya sendiri — dan vonis
   yang keluar dari ingatan tidak dapat dibedakan dari karangan. Tesnya memakai
   :class:`ScriptedChatModel` yang **kosong**, sehingga panggilan model apa pun
   menggagalkan tes dengan pesan yang jelas. ``skipped=True`` sama pentingnya:
   bab yang lolos tanpa pemeriksaan harus terlihat sebagai bab yang lolos tanpa
   pemeriksaan.
2. **Klaim yang salah ditolak, dan yang benar tidak.** Klaim yang sengaja
   dibuat bertentangan dengan bukti halaman 46 harus ditolak; klaim yang sesuai
   dengannya harus lulus. Menolak keduanya sama tidak bergunanya dengan
   meluluskan keduanya.
3. **Kejujuran tidak dihukum.** Penulis *diwajibkan* mencatat klaim yang tidak
   dapat didukungnya (``writer.chapter.md``), jadi gate ini membedakan klaim
   yang **disembunyikan** dari klaim yang **dinyatakan**. Perbedaan itu hanya
   dapat dibuat program — ia membandingkan draf dengan dirinya sendiri — dan
   tanpa itu §21 tidak dapat berhenti: penulis dan pemeriksa membaca bahan yang
   sama, sehingga setiap revisi akan menghasilkan penandaan yang sama.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.fact_checker import FactChecker, FactCheckerAgent
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

#: Klaim yang **bertentangan** dengan bukti halaman 46. Sengaja salah, bukan
#: sekadar tidak berbukti: klaim yang salah adalah bentuk paling tegas dari
#: "tidak didukung", dan ia yang paling mudah luput bila gate hanya mencari
#: klaim yang tidak disebut sama sekali.
FALSE_CLAIM = "O(n) berarti waktu eksekusi tumbuh kuadratik terhadap masukan."

#: Klaim yang penulis sendiri sudah tandai belum berbukti di ``unresolved_claims``.
DECLARED_CLAIM = "Pencarian biner selalu selesai dalam paling banyak 20 langkah."

SPEC = ChapterSpec(
    number=1,
    title="Analisis Kompleksitas",
    sections=("Notasi Big-O",),
    objectives=("Mahasiswa mampu menghitung kompleksitas waktu algoritma.",),
)

DRAFT = ChapterDraft(
    title="Analisis Kompleksitas",
    sections=(Section(heading="Notasi Big-O", body=FALSE_CLAIM),),
    citations=(CORMEN,),
)


def declaring(*claims: str) -> ChapterDraft:
    """Draf yang sama, dengan klaim yang penulis sendiri tandai belum berbukti."""
    return DRAFT.model_copy(update={"unresolved_claims": claims})


def verdict_json(**overrides: Any) -> str:
    """Balasan ``CheckVerdict`` yang sah: satu temuan untuk klaim di draf."""
    payload: dict[str, Any] = {
        "approved": True,
        "score": 9,
        "feedback": [],
        "findings": [
            {
                "subject": FALSE_CLAIM,
                "ok": False,
                "detail": "Bahan halaman 46 menyatakan linear, bukan kuadratik.",
                "source": CORMEN,
            }
        ],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def claimed(
    claim: str, *, ok: bool, detail: str = "", source: str = CORMEN
) -> dict[str, Any]:
    """Satu temuan model atas sebuah klaim."""
    return {"subject": claim, "ok": ok, "detail": detail, "source": source}


class StubProvider:
    """``ModelProvider`` palsu yang mencatat peran yang diminta.

    ``embedder`` sengaja melempar: pemeriksa fakta tidak punya urusan dengan
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
        raise AssertionError("pemeriksa fakta tidak boleh meminta model embedding")


@pytest.fixture
def book() -> BookState:
    """Buku tanpa satu pun bab yang sudah disetujui."""
    return BookState(
        request=BookRequest(title="Algoritma", target_chapters=1),
        spec=BookSpec(title="Algoritma", chapters=(SPEC,)),
    )


@pytest.fixture
def record(full_research: ResearchPackage) -> ChapterRecord:
    """Bab yang datang dari penulis: klaimnya ada, dan belum ada yang menandainya."""
    return ChapterRecord(
        number=1,
        status=ChapterStatus.EXERCISES_WRITTEN,
        spec=SPEC,
        draft=DRAFT,
        research=full_research,
    )


def make_gate(
    model: ScriptedChatModel, prompts: FilePromptLibrary, **kwargs: Any
) -> FactChecker:
    return FactChecker(model=model, prompts=prompts, **kwargs)


# ---------------------------------------------------------------------------
# 1. Tanpa bahan, gate berhenti — bukan menebak dari ingatan model
# ---------------------------------------------------------------------------
def test_a_degraded_package_stops_the_gate_without_calling_the_model(
    prompt_library: FilePromptLibrary, book: BookState, empty_research: ResearchPackage
) -> None:
    """Tanpa retrieval, tidak ada yang dapat diverifikasi — dan gate mengatakannya.

    ``ScriptedChatModel([])`` tidak punya satu balasan pun. Bila gate tetap
    memanggil model, tes ini gagal — dan kegagalannya adalah inti pemeriksaan
    ini, bukan efek sampingnya.

    ``skipped=True`` adalah bagian terpentingnya: bab ini **bukan** bab yang
    faktanya sudah diperiksa.
    """
    record = ChapterRecord(
        number=1,
        status=ChapterStatus.EXERCISES_WRITTEN,
        spec=SPEC,
        draft=DRAFT,
        research=empty_research,
    )
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 0
    assert result.skipped is True
    assert result.approved is True
    assert result.score == 10
    assert result.gate == "fact_checker"
    assert any("tidak dijalankan" in note for note in result.feedback)
    assert any("tidak ada bahan untuk memeriksa" in note for note in result.feedback)


def test_a_search_that_found_nothing_stops_the_gate_too(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    """RAG hidup tetapi indeksnya kosong: sama saja tidak ada bahan.

    Membedakan keduanya di dalam prompt berarti membiarkan model menilai klaim
    dari ingatannya pada salah satu cabang — dan cabang itu akan menjadi cabang
    yang dipakai.
    """
    record = ChapterRecord(
        number=1,
        status=ChapterStatus.EXERCISES_WRITTEN,
        spec=SPEC,
        draft=DRAFT,
        research=ResearchPackage(degraded=False),
    )
    model = ScriptedChatModel([])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 0
    assert result.skipped is True
    assert any("tidak menemukan" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 2. Prasyarat — gagal sebelum satu token pun dibakar
# ---------------------------------------------------------------------------
def test_a_record_without_a_draft_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    bare = ChapterRecord(number=1, status=ChapterStatus.EXERCISES_WRITTEN, spec=SPEC)
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library).evaluate(bare, book)

    assert excinfo.value.gate == "fact_checker"
    assert excinfo.value.missing == "draf"
    assert model.call_count == 0


def test_a_record_without_a_spec_is_refused(
    prompt_library: FilePromptLibrary, book: BookState
) -> None:
    bare = ChapterRecord(number=1, status=ChapterStatus.EXERCISES_WRITTEN, draft=DRAFT)
    model = ScriptedChatModel([])

    with pytest.raises(GatePreconditionError) as excinfo:
        make_gate(model, prompt_library).evaluate(bare, book)

    assert excinfo.value.missing == "spesifikasi bab"
    assert model.call_count == 0


# ---------------------------------------------------------------------------
# 3. Didukung atau tidak — di sinilah model baru dipanggil
# ---------------------------------------------------------------------------
def test_a_claim_that_contradicts_the_material_is_rejected(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Verifikasi rencana Tahap 5: fakta yang sengaja salah harus ditolak (§21).

    Bahan halaman 46 menyatakan pertumbuhan **linear**; draf menyatakan
    **kuadratik**. Ini pertentangan langsung dengan bahan, dan gate tidak boleh
    meloloskannya sekalipun model menuliskan `approved` bernilai benar.
    """
    model = ScriptedChatModel([verdict_json()])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert model.call_count == 1
    assert result.approved is False
    assert result.gate == "fact_checker"


def test_a_claim_the_material_supports_is_accepted(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Yang benar harus lulus. Gate yang menolak segalanya sama tidak bergunanya.

    Tanpa tes ini, aturan "tolak klaim tak didukung" dapat dipenuhi dengan
    menolak setiap klaim — dan tidak satu pun tes lain di berkas ini akan
    memperlihatkannya.
    """
    model = ScriptedChatModel(
        [verdict_json(findings=[claimed(FALSE_CLAIM, ok=True, detail="Didukung hlm. 46.")])]
    )

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is True
    assert result.score == 9


def test_the_failing_finding_reaches_the_writer_with_its_source_and_page(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Sumber dan halaman diisi program dari bukti — bukan dikarang model.

    Halaman 46 adalah halaman bukti **pertama** untuk sumber itu pada daftar
    ``full_research``… dan justru karena itu ia membuktikan pencocokannya
    benar-benar terjadi: nomor itu tidak pernah muncul di draf maupun di vonis.
    """
    model = ScriptedChatModel([verdict_json()])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert any("hlm. 45" in note for note in result.feedback)
    assert any(FALSE_CLAIM in note for note in result.feedback)
    assert any("kuadratik" in note for note in result.feedback)


def test_a_score_under_the_threshold_is_lowered_by_the_shared_rule(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Ambang tetap ditegakkan di :func:`~domain.rules.decide_review`, bukan di sini."""
    model = ScriptedChatModel([verdict_json(approved=True, score=6, findings=[])])

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert result.score == 6
    assert any("ambang" in note for note in result.feedback)


def test_the_threshold_comes_from_the_caller(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """``review_threshold`` dari ``config.yaml`` harus benar-benar mengikat."""
    model = ScriptedChatModel([verdict_json(approved=True, score=6, findings=[])])

    result = make_gate(model, prompt_library, threshold=5).evaluate(record, book)

    assert result.approved is True


# ---------------------------------------------------------------------------
# 4. Klaim yang sudah dinyatakan — dilaporkan, tidak dihukum
# ---------------------------------------------------------------------------
def test_a_claim_the_writer_already_flagged_is_reported_but_not_punished(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Kejujuran bukan kegagalan — dan gate inilah satu-satunya yang dapat menilainya.

    Penulis diwajibkan mencatat klaim yang tidak dapat didukungnya. Menghukum
    catatan itu membuatnya menyembunyikan klaim alih-alih menandainya, yaitu
    kebalikan dari yang diminta `writer.chapter.md`. Temuannya tetap dilaporkan
    — pembaca laporan berhak melihatnya — tetapi tidak membatalkan persetujuan.
    """
    dishonest = record.model_copy(update={"draft": declaring(DECLARED_CLAIM)})
    model = ScriptedChatModel(
        [
            verdict_json(
                findings=[claimed(DECLARED_CLAIM, ok=False, detail="Bahan tidak menyebut angka.")]
            )
        ]
    )

    result = make_gate(model, prompt_library).evaluate(dishonest, book)

    assert result.approved is True
    assert any("sudah dinyatakan bab ini" in note for note in result.feedback)
    assert not any("diabaikan oleh sistem" in note for note in result.feedback)


def test_a_claim_the_writer_never_flagged_is_punished(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Klaim yang sama, tanpa dinyatakan: di sinilah §21 menolak bab.

    Perbedaan antara kedua tes ini adalah seluruh isi pengecualian yang
    dijelaskan di docstring modul: yang menentukan hukuman bukan isi temuannya,
    melainkan apakah babnya sudah mengaku.
    """
    model = ScriptedChatModel(
        [
            verdict_json(
                findings=[claimed(DECLARED_CLAIM, ok=False, detail="Bahan tidak menyebut angka.")]
            )
        ]
    )

    result = make_gate(model, prompt_library).evaluate(record, book)

    assert result.approved is False
    assert result.score == 9, "angkanya tidak diubah; yang diturunkan adalah vonisnya"
    assert any("tidak dinyatakan bab ini" in note for note in result.feedback)


def test_a_declared_claim_the_model_never_mentions_is_reported_as_unexamined(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """"Tidak ada temuan" bukan "sudah diperiksa".

    Daftar `unresolved_claims` adalah daftar yang paling mungkin dibiarkan model,
    justru karena isinya sudah ditandai belum berbukti. Ia tidak menurunkan
    vonis — yang dilaporkan di sini adalah kekurangan **laporan**, bukan
    kekurangan bab — tetapi ia harus terlihat.
    """
    declared = record.model_copy(update={"draft": declaring(DECLARED_CLAIM)})
    model = ScriptedChatModel([verdict_json(findings=[])])

    result = make_gate(model, prompt_library).evaluate(declared, book)

    assert any("tidak diperiksa model" in note for note in result.feedback)
    assert any(DECLARED_CLAIM in note for note in result.feedback)
    assert result.approved is True, "laporan yang kurang bukan bab yang kurang"


def test_the_threshold_still_binds_when_every_failing_claim_was_declared(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Tidak dihukum oleh temuannya bukan berarti bebas dari ambangnya.

    Bab yang seluruh klaimnya sudah dinyatakan memang tidak boleh dibatalkan
    oleh aturan temuan — tetapi ia tetap harus lulus angkanya, dan yang
    memutuskan itu tetap :func:`~domain.rules.decide_review`.
    """
    declared = record.model_copy(update={"draft": declaring(DECLARED_CLAIM)})
    model = ScriptedChatModel(
        [
            verdict_json(
                approved=True,
                score=5,
                findings=[claimed(DECLARED_CLAIM, ok=False, detail="Bahan tidak menyebut angka.")],
            )
        ]
    )

    result = make_gate(model, prompt_library).evaluate(declared, book)

    assert result.approved is False
    assert result.score == 5
    assert any("ambang" in note for note in result.feedback)


# ---------------------------------------------------------------------------
# 5. Apa yang dikirim ke model
# ---------------------------------------------------------------------------
def test_the_prompt_carries_the_flagged_claims_and_the_evidence_with_its_page(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """Model hanya dapat menilai klaim bila ia melihat klaimnya dan bahannya.

    Klaim yang ditandai penulis dikirim sebagai daftar tersendiri **dan** apa
    adanya: itulah yang membuat perintah "salin persis" di dalam prompt masuk
    akal dibaca, dan itulah yang membuat penandaan ``declared`` mungkin.
    """
    declared = record.model_copy(update={"draft": declaring(DECLARED_CLAIM)})
    model = ScriptedChatModel([verdict_json(findings=[])])

    make_gate(model, prompt_library).evaluate(declared, book)

    sent = model.requests[0].user
    assert DECLARED_CLAIM in sent
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


def test_the_agent_uses_its_own_role(
    prompt_library: FilePromptLibrary, book: BookState, record: ChapterRecord
) -> None:
    """§6 mencantumkan ``fact_checker`` tersendiri, terpisah dari ``reviewer``.

    Menilai apakah sebuah klaim **benar** menurut bahan adalah pertanyaan yang
    berbeda dari menilai apakah babnya **baik**, dan keduanya boleh saja
    dijawab model yang berbeda.
    """
    assert FactCheckerAgent.role == "fact_checker"
    assert FactCheckerAgent.prompt_name == "factcheck.chapter"


# ---------------------------------------------------------------------------
# 6. Tempatnya di rantai §27 dan di registri
# ---------------------------------------------------------------------------
def test_the_gate_produces_the_status_the_chain_expects() -> None:
    assert FactChecker.produces is ChapterStatus.FACT_CHECKED
    assert can_advance(ChapterStatus.EXERCISES_WRITTEN, FactChecker.produces)


def test_the_gate_takes_its_model_from_the_router_by_role(
    prompt_library: FilePromptLibrary,
) -> None:
    """Satu-satunya tempat ``router.chat("fact_checker")`` dipanggil untuk gate ini."""
    from agents.gates import build_gates  # lokal: menghindari impor melingkar

    provider = StubProvider(ScriptedChatModel([verdict_json()]))
    context = GateContext(
        router=provider,
        prompts=prompt_library,
        reporter=object(),  # type: ignore[arg-type]
    )

    gates = build_gates(("fact_checker",), context)

    assert provider.requested == ["fact_checker"]
    assert gates[0].name == "fact_checker"
    assert gates[0].produces is ChapterStatus.FACT_CHECKED
