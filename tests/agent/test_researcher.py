"""Tes Research Agent MVP — yang diuji adalah **batasnya**, bukan kemampuannya.

``NullResearcher`` tidak meneliti, jadi tidak ada retrieval yang bisa diuji.
Yang bisa dan harus diuji adalah invarian kejujurannya: ia tidak boleh mengaku
punya sumber, dan ia harus tetap memenuhi kontrak port sehingga penggantinya
nanti dapat dipasang tanpa menyentuh satu pun pemanggil.
"""

from __future__ import annotations

from agents.researcher import NullResearcher
from domain.book import BookRequest, ChapterSpec
from domain.chapter import ResearchPackage
from domain.ports import Researcher
from domain.state import BookState

SPEC = ChapterSpec(
    number=2,
    title="Analisis Kompleksitas",
    references=("Cormen, Introduction to Algorithms, 4th ed.",),
    source_weeks=("Minggu 2",),
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
