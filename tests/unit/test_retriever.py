"""Tes kebijakan pencarian (§13).

Yang diuji di sini bukan ChromaDB — ia tidak diimpor sama sekali — melainkan
**kebijakan** yang hidup di ``rag/retriever.py``: bagaimana jarak menjadi skor,
bukti seperti apa yang dibuang, dan berapa banyak yang dikembalikan. Ketiganya
menentukan apa yang boleh dikutip penulis, dan ketiganya dapat diuji tanpa
indeks, tanpa berkas, dan tanpa jaringan.

Satu tes di bagian akhir memakai indeks sungguhan di ``tmp_path`` justru untuk
satu hal saja: membuktikan bahwa ``page`` potongan benar-benar sampai ke
:class:`~domain.chapter.Evidence`. Nomor halaman yang hilang di tengah jalan
tidak menghasilkan galat apa pun — yang dihasilkan adalah bab yang mengutip
halaman yang tidak memuat kalimatnya.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from domain.document import Document, SourceType
from rag.retriever import DEFAULT_LIMIT, DEFAULT_MIN_SCORE, VectorRetriever, similarity
from rag.vector_store import ChromaVectorStore, VectorHit
from tests.fakes.embeddings import KeywordEmbedder


class StubStore:
    """Store dengan hasil yang sudah ditentukan — untuk menguji kebijakan, bukan indeks.

    Mencatat ``limit`` yang diminta karena itulah satu-satunya hal yang dilakukan
    retriever sebelum menyaring: ia meneruskan batas yang **berlaku**, bukan batas
    mentah dari pemanggil.
    """

    def __init__(self, hits: tuple[VectorHit, ...] = ()) -> None:
        self.hits = hits
        self.limits: list[int] = []

    def query(self, text: str, *, limit: int = 8) -> tuple[VectorHit, ...]:
        self.limits.append(limit)
        return self.hits


def hit(distance: float, **overrides: Any) -> VectorHit:
    """Satu hasil pencarian; ``distance`` yang menentukan skornya."""
    payload: dict[str, Any] = {
        "chunk_id": "compiler.pdf#p42#c0",
        "text": "Notasi asimtotik menggambarkan laju pertumbuhan.",
        "source": "compiler.pdf",
        "page": 42,
        "section": "Notasi Big-O",
        "distance": distance,
    }
    payload.update(overrides)
    return VectorHit(**payload)


def retriever(store: StubStore, **overrides: Any) -> VectorRetriever:
    """Retriever di atas store palsu, tanpa memaksa tes menulis anotasi tipe store."""
    return VectorRetriever(store, **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# similarity — jarak menjadi skor (murni)
# ---------------------------------------------------------------------------
def test_identical_vectors_score_one() -> None:
    assert similarity(0.0) == 1.0


def test_orthogonal_vectors_score_zero() -> None:
    """Nol berarti "tidak lebih dekat daripada arah acak" — ambang bawaannya."""
    assert similarity(1.0) == 0.0


def test_opposite_vectors_score_minus_one() -> None:
    """Pada ruang kosinus jarak mencapai 2, dan skornya -1."""
    assert similarity(2.0) == -1.0


def test_negative_scores_are_not_clamped() -> None:
    """Skor negatif adalah informasi yang benar — potongan itu berlawanan arah.

    Menjepitnya menjadi nol akan menyamarkannya sebagai "sekadar tidak relevan",
    padahal ia **membantah** pertanyaannya. Perbedaan itu penting bagi pemeriksa
    fakta, dan menghapusnya berarti menghapus bukti bahwa korpus memuat bahan
    yang bertentangan.
    """
    assert similarity(1.5) == -0.5


def test_the_score_is_a_float_even_for_an_integer_distance() -> None:
    """ChromaDB kadang mengembalikan jarak sebagai bilangan bulat; skornya tetap float."""
    assert isinstance(similarity(0), float)


# ---------------------------------------------------------------------------
# retrieve
# ---------------------------------------------------------------------------
def test_retrieve_returns_evidence_in_the_order_of_the_index() -> None:
    """Urutan store sudah menyatakan relevansi — retriever tidak mengurutkan ulang."""
    store = StubStore((hit(0.1, page=1), hit(0.3, page=2), hit(0.5, page=3)))

    evidence = retriever(store).retrieve("notasi")

    assert [item.page for item in evidence] == [1, 2, 3]
    assert [item.score for item in evidence] == [0.9, 0.7, 0.5]


def test_retrieve_carries_the_source_section_and_text() -> None:
    """§13: teks saja tidak cukup — bukti harus menyebut asalnya."""
    evidence = retriever(StubStore((hit(0.2),))).retrieve("notasi")[0]

    assert evidence.source == "compiler.pdf"
    assert evidence.section == "Notasi Big-O"
    assert evidence.text == "Notasi asimtotik menggambarkan laju pertumbuhan."
    assert evidence.page == 42


def test_evidence_without_a_source_falls_back_to_the_chunk_id() -> None:
    """Bukti wajib punya asal; identitas potongan lebih berguna daripada string kosong."""
    store = StubStore((hit(0.2, source=""),))

    evidence = retriever(store).retrieve("notasi")[0]

    assert evidence.source == "compiler.pdf#p42#c0"


def test_evidence_without_a_page_keeps_it_empty() -> None:
    """``None`` bukan 0: halaman yang tidak diketahui tidak boleh menjadi halaman pertama."""
    store = StubStore((hit(0.2, page=None),))

    assert retriever(store).retrieve("notasi")[0].page is None


def test_an_empty_index_yields_no_evidence() -> None:
    assert retriever(StubStore()).retrieve("notasi") == ()


def test_irrelevant_hits_are_dropped_by_the_default_threshold() -> None:
    """Potongan yang tegak lurus atau berlawanan tidak lebih berguna daripada tidak ada.

    Ia akan dikutip sebagai rujukan, lalu pemeriksa fakta menemukan klaim yang
    tidak didukung oleh potongan yang justru membantahnya.
    """
    store = StubStore((hit(0.4), hit(1.0), hit(1.7)))

    evidence = retriever(store).retrieve("notasi")

    assert [item.score for item in evidence] == [0.6]


def test_a_higher_threshold_drops_more() -> None:
    """Ambang yang lebih tinggi membuang bukti yang lemah — dan ambang itu ``>``, bukan ``>=``."""
    store = StubStore((hit(0.1), hit(0.4), hit(0.8)))

    evidence = retriever(store, min_score=0.6).retrieve("notasi")

    assert [item.score for item in evidence] == [0.9]


def test_a_score_exactly_at_the_threshold_is_dropped() -> None:
    """Batasnya eksklusif, dan itu disengaja: skor 0.0 berarti "tegak lurus"."""
    store = StubStore((hit(1.0),))

    assert retriever(store, min_score=0.0).retrieve("notasi") == ()


def test_the_threshold_is_reported() -> None:
    """``status`` dan ``ingest`` melaporkan ambang yang berlaku; ia harus dapat dibaca."""
    assert retriever(StubStore()).min_score == DEFAULT_MIN_SCORE
    assert retriever(StubStore(), min_score=0.3).min_score == 0.3


def test_a_limit_below_one_falls_back_to_the_default() -> None:
    """Pemanggil yang tidak menyebut batas tidak menginginkan nol hasil.

    ``researcher`` menyampaikan ``top_k`` dari konfigurasi; nilai yang tidak
    masuk akal di sana harus berakhir pada batas bawaan, bukan pada bab tanpa
    bahan sama sekali.
    """
    store = StubStore((hit(0.1),))

    retriever(store, default_limit=5).retrieve("notasi", limit=0)

    assert store.limits == [5]


def test_the_requested_limit_is_passed_through() -> None:
    store = StubStore((hit(0.1),))

    retriever(store, default_limit=5).retrieve("notasi", limit=2)

    assert store.limits == [2]


def test_the_default_limit_matches_the_configured_top_k() -> None:
    """Angka bawaan di sini harus sama dengan ``rag.top_k`` di ``config.yaml``."""
    assert DEFAULT_LIMIT == 8


# ---------------------------------------------------------------------------
# Bukti bersumber halaman, lewat indeks sungguhan
# ---------------------------------------------------------------------------
def document(text: str, *, page: int) -> Document:
    """Potongan satu halaman di dalam PDF yang sama."""
    return Document(
        document_id="compiler.pdf",
        filename="compiler.pdf",
        source_type=SourceType.PDF,
        text=text,
        page=page,
        section="Notasi Big-O",
        paragraph=0,
    )


@pytest.mark.parametrize("question,expected_page", [("kompleksitas", 42), ("graf", 88)])
def test_retrieve_returns_the_page_of_the_matching_chunk(
    tmp_path: Path, question: str, expected_page: int
) -> None:
    """Pertanyaan berbeda harus menemukan halaman yang berbeda — bukan halaman pertama.

    Inilah invarian yang membuat seluruh jalur sitasi berguna: §10 melarang
    membuang nomor halaman justru karena kutipan tanpa halaman tidak dapat
    diperiksa manusia. Tes ini menempuh jalur lengkapnya — embedder → indeks →
    retriever → ``Evidence`` — sehingga satu penghapusan metadata di mana pun
    sepanjang jalur itu akan menggagalkannya.
    """
    store = ChromaVectorStore(tmp_path / "vector_db", embedder=KeywordEmbedder())
    store.index(
        [
            document("kompleksitas waktu algoritma", page=42),
            document("graf berarah dan pohon", page=88),
        ]
    )

    evidence = VectorRetriever(store).retrieve(question, limit=1)

    assert len(evidence) == 1
    assert evidence[0].page == expected_page
    assert evidence[0].source == "compiler.pdf"
    assert evidence[0].score > 0.0
