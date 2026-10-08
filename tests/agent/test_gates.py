"""Tes registri gate — jembatan antara rantai §27 dan MVP.

Registri ini adalah tempat OCP proyek ini diuji secara langsung: menambahkan
tahap baru harus cukup dengan mendaftarkan namanya, dan mengganti model harus
cukup dengan mengedit konfigurasi. Tes di bawah memeriksa keduanya, plus satu hal
yang lebih mudah dilanggar tanpa sadar: bahwa gate yang **dilewati** mengatakan
dirinya dilewati.
"""

from __future__ import annotations

from typing import Any

import pytest

from agents.gates import (
    GateContext,
    PassThroughGate,
    build_gates,
    register_gate,
    registered_gate_names,
)
from domain.chapter import ChapterRecord
from domain.book import BookRequest
from domain.enums import ChapterStatus
from domain.errors import ConfigError
from domain.rules import validate_gates
from domain.state import BookState
from tests.fakes.chat_models import SchemaEchoChatModel


class StubProvider:
    """``ModelProvider`` palsu yang mencatat peran; ``embedder`` sengaja melempar."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    def chat(self, role: str) -> Any:
        self.requested.append(role)
        return SchemaEchoChatModel()

    def embedder(self, role: str = "embedding") -> Any:
        raise AssertionError("gate tidak boleh meminta model embedding")


@pytest.fixture
def context(prompt_library: Any) -> GateContext:
    return GateContext(router=StubProvider(), prompts=prompt_library, reporter=object())


@pytest.fixture
def book() -> BookState:
    """State buku minimal — cukup untuk apa pun yang membaca ``request``."""
    return BookState(request=BookRequest(title="Buku", target_chapters=1))


# ---------------------------------------------------------------------------
# 1. Isi registri
# ---------------------------------------------------------------------------
def test_the_reviewer_gate_is_registered() -> None:
    assert "reviewer" in registered_gate_names()


#: Nama gate yang **pernah** ada sebagai placeholder dan kini sudah tidak.
#:
#: Daftarnya dipertahankan justru karena perannya sudah habis: ``register_gate``
#: menolak nama yang terdaftar dua kali, jadi setiap agent sungguhan **wajib**
#: menghapus placeholder-nya lebih dulu. Nama yang tertinggal di registri berarti
#: penghapusan itu belum terjadi — dan gate yang terdaftar dengan nama lama akan
#: dilewati ``build_gates`` hanya bila ada yang menuliskan nama itu di
#: ``config.yaml``.
RETIRED_GATE_NAMES: tuple[str, ...] = (
    "citation_checked",
    "consistency_checked",
    "fact_checked",
    "latex_compiled",
    "latex_generated",
    "pedagogy_reviewed",
)


def test_every_chain_stage_is_inhabited_by_a_real_gate() -> None:
    """Tidak ada lagi tahap yang dipegang placeholder.

    Sejak Tahap 9, setiap status di rantai §27 punya gate yang benar-benar
    memeriksa sesuatu. Yang tersisa hanyalah nama lamanya, dan nama itu harus
    benar-benar hilang — bukan sekadar tidak dipakai.
    """
    registered = registered_gate_names()

    stale = [name for name in RETIRED_GATE_NAMES if name in registered]
    assert not stale, f"nama placeholder yang belum dihapus: {', '.join(stale)}"


def test_gate_names_are_sorted_in_the_report() -> None:
    """``registered_gate_names`` dipakai di pesan kesalahan — ia harus deterministik."""
    names = registered_gate_names()

    assert names == tuple(sorted(names))


# ---------------------------------------------------------------------------
# 2. Pendaftaran ganda
# ---------------------------------------------------------------------------
def test_registering_an_existing_name_is_refused() -> None:
    """Dua gate bernama sama berarti salah satunya tidak akan pernah dijalankan.

    Kegagalan seperti itu tidak terlihat sampai seseorang bertanya mengapa
    babnya tidak pernah diperiksa — jadi lebih baik gagal di sini.

    Tes ini memakai nama yang **sudah ada**, bukan nama baru: dengan begitu ia
    tidak meninggalkan jejak apa pun di registri.
    """
    before = registered_gate_names()

    with pytest.raises(ConfigError) as excinfo:
        register_gate("reviewer")(lambda ctx: None)  # type: ignore[arg-type,return-value]

    assert "sudah terdaftar" in str(excinfo.value)
    assert registered_gate_names() == before, "registri tidak boleh berubah"


# ---------------------------------------------------------------------------
# 3. Perakitan dari nama
# ---------------------------------------------------------------------------
def test_build_gates_preserves_the_configured_order(context: GateContext) -> None:
    """Urutan di ``pipeline.gates`` adalah urutan tahap — bukan detail kosmetik."""
    names = ("fact_checker", "reviewer", "consistency_checker")

    gates = build_gates(names, context)

    assert tuple(gate.name for gate in gates) == names


def test_an_unknown_gate_name_fails_before_any_token_is_spent(
    context: GateContext,
) -> None:
    """Konfigurasi yang salah harus gagal di baris pertama, bukan di bab ke-7."""
    with pytest.raises(ConfigError) as excinfo:
        build_gates(("reviewer", "gate_yang_tidak_ada"), context)

    message = str(excinfo.value)
    assert "gate_yang_tidak_ada" in message
    assert "reviewer" in message, "pesan harus menyebut gate yang tersedia"


def test_an_empty_gate_list_is_allowed(context: GateContext) -> None:
    """Pipeline tanpa gate adalah konfigurasi yang sah — bab langsung disetujui."""
    assert build_gates((), context) == ()


# ---------------------------------------------------------------------------
# 4. PassThroughGate — jujur tentang apa yang tidak dikerjakannya
# ---------------------------------------------------------------------------
def test_pass_through_gate_advances_without_calling_a_model(book: BookState) -> None:
    gate = PassThroughGate(name="pedagogy_reviewed", produces=ChapterStatus.PEDAGOGY_REVIEWED)

    result = gate.evaluate(ChapterRecord(number=1), book)

    assert result.approved is True
    assert result.gate == "pedagogy_reviewed"


def test_pass_through_gate_marks_itself_as_skipped(book: BookState) -> None:
    """Tanpa bendera ini, laporan akhir akan menyatakan bab "diperiksa" padahal tidak.

    Bab yang lolos tanpa pemeriksaan harus terlihat sebagai bab yang lolos tanpa
    pemeriksaan — itulah bedanya melaporkan dan menyembunyikan.
    """
    gate = PassThroughGate(name="pedagogy_reviewed", produces=ChapterStatus.PEDAGOGY_REVIEWED)

    result = gate.evaluate(ChapterRecord(number=1), book)

    assert result.skipped is True
    assert any("tidak dikerjakan pada jalankan ini" in note for note in result.feedback)


def test_pass_through_gate_reports_the_reason_its_caller_gave(book: BookState) -> None:
    """Pemanggil yang tahu alasannya mengirim catatannya sendiri.

    "LaTeX dimatikan" jauh lebih berguna bagi pembaca ``state/chapterNN.json``
    enam bulan kemudian daripada catatan bawaan yang hanya mengatakan bahwa
    tahapnya tidak dikerjakan.
    """
    gate = PassThroughGate(
        name="latex_qa",
        produces=ChapterStatus.LATEX_COMPILED,
        note="latexmk tidak ditemukan di PATH.",
    )

    result = gate.evaluate(ChapterRecord(number=1), book)

    assert result.feedback == ("latexmk tidak ditemukan di PATH.",)


#: Seluruh gate yang menutup rantai §27 pada hari ini, dalam urutan rantai.
#:
#: Daftarnya sengaja literal, seperti ``EXPECTED_PROMPTS`` di
#: ``tests/unit/test_prompting.py``: setiap kali sebuah tahap berganti pemilik
#: (Tahap 4 menggantikan ``citation_checked`` dengan ``citation_checker``, Tahap 5
#: menggantikan ``fact_checked`` dengan ``fact_checker``, Tahap 6 menggantikan
#: ``latex_generated`` dengan ``latex_writer``, Tahap 7 menggantikan
#: ``latex_compiled`` dengan ``latex_qa``, Tahap 8 menggantikan
#: ``pedagogy_reviewed`` dengan ``pedagogy_reviewer``, Tahap 9 menggantikan
#: ``consistency_checked`` dengan ``consistency_checker``), daftar ini harus
#: disunting — dan suntingan itu adalah keputusan sadar, bukan pembiaran.
#: Menurunkannya dari registri justru akan menyembunyikan pergantian itu.
#:
#: ``latex_writer`` dan ``latex_qa`` di sini dibangun dengan ``latex=None``,
#: sehingga yang terpasang adalah pass-through keduanya. Yang diuji oleh tes di
#: bawah adalah **rantai**, bukan isi gate-nya; perilaku ``LatexWriterGate``
#: sungguhan diuji di ``tests/agent/test_latex_writer.py`` dan ``LatexQAGate``
#: di ``tests/agent/test_latex_qa.py``.
CHAIN_GATES: tuple[str, ...] = (
    "fact_checker",
    "citation_checker",
    "pedagogy_reviewer",
    "consistency_checker",
    "reviewer",
    "latex_writer",
    "latex_qa",
)


# ---------------------------------------------------------------------------
# 5. Integrasi dengan gerbang validasi domain
# ---------------------------------------------------------------------------
def test_every_built_gate_advances_the_chain_legally(context: GateContext) -> None:
    """``validate_gates`` dipanggil composition root saat start.

    Registri yang berisi gate dengan ``produces`` yang melompat mundur akan
    merusak state di tengah proses — dan itu jauh lebih mahal daripada gagal
    sebelum satu token pun dibakar.
    """
    validate_gates(build_gates(CHAIN_GATES, context))  # tidak boleh melempar


def test_the_full_chain_can_be_walked_by_gates_alone(context: GateContext) -> None:
    """Seluruh tahap pemeriksaan §27 punya gate yang mencapainya.

    Inilah bukti bahwa rantainya utuh dan tidak diciutkan: yang diuji bukan
    apakah gate-nya pintar, melainkan apakah setiap statusnya benar-benar
    dihasilkan oleh sesuatu. ``latex_generated`` dan ``latex_compiled`` di sini
    datang dari pass-through, karena ``context.latex`` bernilai ``None`` — dan
    itu memang yang terjadi pada mesin tanpa LaTeX.
    """
    reachable = {gate.produces for gate in build_gates(CHAIN_GATES, context)}

    assert reachable == {
        ChapterStatus.FACT_CHECKED,
        ChapterStatus.CITATION_CHECKED,
        ChapterStatus.PEDAGOGY_REVIEWED,
        ChapterStatus.CONSISTENCY_CHECKED,
        ChapterStatus.LATEX_GENERATED,
        ChapterStatus.LATEX_COMPILED,
        ChapterStatus.REVIEWED,
    }
