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
    PLACEHOLDER_GATES,
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


def test_every_uninhabited_chain_stage_has_a_placeholder() -> None:
    """Rantai §27 tetap dapat dilalui utuh meski baru satu gate yang berpenghuni."""
    registered = registered_gate_names()

    assert all(name in registered for name, _ in PLACEHOLDER_GATES)


def test_placeholder_names_are_sorted_in_the_report() -> None:
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
    names = ("fact_checked", "reviewer", "consistency_checked")

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
    gate = PassThroughGate(name="fact_checked", produces=ChapterStatus.FACT_CHECKED)

    result = gate.evaluate(ChapterRecord(number=1), book)

    assert result.skipped is True
    assert any("belum diimplementasikan" in note for note in result.feedback)


def test_placeholder_gate_produces_the_status_it_stands_for(context: GateContext) -> None:
    """Nama placeholder dan status yang dicapainya harus sepadan.

    Kalau tidak, rantai §27 tetap "terlalui" tetapi di tempat yang salah — dan
    ``validate_gates`` tidak akan menangkapnya, karena ia hanya memeriksa arah,
    bukan maksud.
    """
    gates = build_gates(tuple(name for name, _ in PLACEHOLDER_GATES), context)

    for (expected_name, expected_status), gate in zip(PLACEHOLDER_GATES, gates, strict=True):
        assert gate.name == expected_name
        assert gate.produces is expected_status


#: Seluruh gate yang menutup rantai §27 pada hari ini, dalam urutan rantai.
#:
#: Daftarnya sengaja literal, seperti ``EXPECTED_PROMPTS`` di
#: ``tests/unit/test_prompting.py``: setiap kali sebuah placeholder digantikan
#: agent sungguhan (Tahap 4 menggantikan ``citation_checked`` dengan
#: ``citation_checker``), daftar ini harus disunting — dan suntingan itu adalah
#: keputusan sadar, bukan pembiaran. Menurunkannya dari ``PLACEHOLDER_GATES``
#: justru akan menyembunyikan pergantian itu.
CHAIN_GATES: tuple[str, ...] = (
    "fact_checked",
    "citation_checker",
    "pedagogy_reviewed",
    "consistency_checked",
    "reviewer",
    "latex_generated",
    "latex_compiled",
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
    """Seluruh rantai §27 — dari DRAFTED sampai APPROVED — punya gate yang mencapainya.

    Inilah bukti bahwa MVP tidak menciutkan rantai: yang belum ada hanyalah
    isinya, bukan bentuknya. Ketika agent sungguhnya tiba, yang berubah hanya
    kelas di balik satu nama gate.
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
