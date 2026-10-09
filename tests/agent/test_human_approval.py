"""Gate persetujuan manusia (§44) — dan vonis "menunggu" yang membawanya.

Berkas ini menjaga satu hal yang mudah hilang tanpa disadari: **gate ini tidak
boleh pernah meloloskan bab dengan sendirinya.** Gate yang dapat menyetujui tanpa
manusia tidak menambahkan apa pun di atas rantai tanpa gate — ia hanya
menghabiskan satu baris konfigurasi. Karena itu yang diuji di sini adalah
kebalikannya: setiap kali dipanggil, ia berhenti.

Yang juga diuji: perintah yang harus dijalankan tercantum di catatannya, dengan
nomor babnya sudah terisi. Pesan "menunggu persetujuan" tanpa perintah akan
membuat pembacanya mencari sendiri, dan pesan yang harus dijelaskan lebih lanjut
belum menyelesaikan tugasnya.
"""

from __future__ import annotations

from typing import Any

import pytest

from agents.gates import GateContext, build_gates, registered_gate_names
from agents.human_approval import HUMAN_APPROVAL_GATE, HumanApprovalGate
from domain.book import BookRequest
from domain.chapter import ChapterRecord
from domain.enums import ChapterStatus
from domain.state import BookState


class _RefusingProvider:
    """``ModelProvider`` yang menolak dipanggil sama sekali.

    Gate §44 tidak menilai apa pun — manusianya yang membaca. Provider yang
    melempar membuat janji itu dapat diperiksa: satu panggilan model di jalur ini
    akan menggagalkan tes ini alih-alih lolos diam-diam.
    """

    def chat(self, role: str) -> Any:
        raise AssertionError(f"gate §44 tidak boleh memanggil model (peran {role!r})")

    def embedder(self, role: str = "embedding") -> Any:
        raise AssertionError("gate §44 tidak boleh meminta model embedding")


@pytest.fixture
def gate() -> HumanApprovalGate:
    return HumanApprovalGate()


@pytest.fixture
def record() -> ChapterRecord:
    """Bab yang sudah selesai dikerjakan dan tinggal menunggu pembacanya."""
    return ChapterRecord(number=3, status=ChapterStatus.LATEX_COMPILED)


@pytest.fixture
def book() -> BookState:
    return BookState(request=BookRequest(title="Buku", target_chapters=1))


@pytest.fixture
def context(prompt_library: Any) -> GateContext:
    return GateContext(router=_RefusingProvider(), prompts=prompt_library, reporter=object())


def test_the_gate_is_registered_under_its_configured_name() -> None:
    """Tanpa ini, satu baris di ``config.yaml`` akan menjadi kesalahan konfigurasi."""
    assert HUMAN_APPROVAL_GATE in registered_gate_names()


def test_it_points_at_the_final_status_of_the_chain() -> None:
    """Yang dicapai gate ini bila manusianya menyetujui (§27)."""
    assert HumanApprovalGate.produces is ChapterStatus.APPROVED


def test_it_never_approves_on_its_own(gate: HumanApprovalGate, record: ChapterRecord,
                                      book: BookState) -> None:
    """Inti gate ini: keputusan penerbitan tidak dapat dibuat model.

    Ia juga **tidak** menolak: ``approved=False`` akan mengirim bab yang tidak
    dikeluhkan siapa pun ke antrean revisi.
    """
    verdict = gate.evaluate(record, book)

    assert verdict.approved is False
    assert verdict.blocked is True
    assert verdict.skipped is False
    assert verdict.gate == HUMAN_APPROVAL_GATE


def test_the_note_says_which_chapter_is_waiting(gate: HumanApprovalGate) -> None:
    """Nomor babnya harus terisi — pesan yang menyuruh mencari sendiri tidak menolong."""
    verdict = gate.evaluate(ChapterRecord(number=7, status=ChapterStatus.LATEX_COMPILED),
                            BookState(request=BookRequest(title="Buku")))

    assert any("Bab 7" in line for line in verdict.feedback)


def test_the_note_names_the_command_that_continues_the_book(
    gate: HumanApprovalGate, record: ChapterRecord, book: BookState
) -> None:
    """Kedua jalan keluar disebut, masing-masing dengan nomor babnya.

    Yang membaca catatan ini adalah dosen di terminal pada pukul sebelas malam;
    ia tidak perlu membuka dokumentasi untuk hal yang dapat ditulis di sini.
    """
    verdict = gate.evaluate(record, book)
    note = "\n".join(verdict.feedback)

    assert "approve 3" in note
    assert "reject 3" in note


def test_the_factory_needs_no_model(context: GateContext) -> None:
    """Gate ini dapat didaftarkan pada profil mana pun, termasuk yang termurah.

    ``_RefusingProvider`` melempar bila dipanggil; gate yang dapat dibangun
    darinya berarti tidak satu pun peran model dibutuhkan untuk mengaktifkan §44.
    """
    (built,) = build_gates((HUMAN_APPROVAL_GATE,), context)

    assert isinstance(built, HumanApprovalGate)
