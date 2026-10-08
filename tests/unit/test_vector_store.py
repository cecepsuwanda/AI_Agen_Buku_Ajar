"""Tes indeks vektor ChromaDB di direktori sementara (§13).

Berkas ini adalah **satu-satunya** tempat indeks sungguhan disentuh pada suite
default, dan ia tidak mengimpor ``chromadb`` sama sekali — ditegakkan
``tests/unit/test_architecture.py``. Yang diuji karena itu bukan ChromaDB,
melainkan **terjemahannya**: jenis metadata yang ditulis, apa yang terjadi pada
potongan kosong dan kembar, dan apakah halaman yang dikembalikan pencarian
benar-benar halaman potongan itu.

``tmp_path`` dipakai untuk seluruh tes. Indeks sungguhan di ``knowledge/`` tidak
pernah disentuh: ia dipegang proses ini juga saat ``ingest`` berjalan, dan
ChromaDB mengunci berkasnya di Windows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.document import Document, SourceType
from rag.vector_store import ChromaVectorStore, VectorHit
from tests.fakes.embeddings import KeywordEmbedder


def document(text: str, **overrides: object) -> Document:
    """Satu potongan yang sah; metadata apa pun dapat ditimpa per tes."""
    payload: dict[str, object] = {
        "document_id": "compiler.pdf",
        "filename": "compiler.pdf",
        "source_type": SourceType.PDF,
        "text": text,
        "page": 42,
        "section": "Finite Automata",
        "paragraph": 0,
    }
    payload.update(overrides)
    return Document.model_validate(payload)


@pytest.fixture
def store(tmp_path: Path) -> ChromaVectorStore:
    """Indeks kosong di direktori sekali pakai."""
    return ChromaVectorStore(tmp_path / "vector_db", embedder=KeywordEmbedder())


# ---------------------------------------------------------------------------
# Menanam
# ---------------------------------------------------------------------------
def test_indexing_stores_every_chunk(store: ChromaVectorStore) -> None:
    written = store.index(
        [document("algoritma", page=1), document("graf", page=2), document("pohon", page=3)]
    )

    assert written == 3
    assert store.count() == 3


def test_indexing_the_same_corpus_twice_does_not_duplicate_it(store: ChromaVectorStore) -> None:
    """Identitas potongan diturunkan dari metadata §9, bukan nomor urut.

    Inilah yang membuat ``ingest`` dapat diulang setelah bahan rujukan diganti:
    potongan yang sama menimpa dirinya sendiri. Tanpa itu, setiap ``ingest``
    menanam salinan kedua di sebelah yang lama — dan penulis akan menerima bukti
    kembar, dengan biaya token dua kali untuk isi yang sama.
    """
    corpus = [document("algoritma", page=1), document("graf", page=2)]

    store.index(corpus)
    store.index(corpus)

    assert store.count() == 2


def test_chunks_with_empty_text_are_dropped(store: ChromaVectorStore) -> None:
    """Teks kosong tidak dapat di-embed secara bermakna — dan tidak akan dikutip siapa pun."""
    written = store.index([document("algoritma"), document("   "), document("\n\n")])

    assert written == 1
    assert store.count() == 1


def test_indexing_nothing_at_all_does_not_even_open_the_index(tmp_path: Path) -> None:
    """Korpus yang seluruhnya kosong tidak boleh membuat berkas indeks.

    Yang diuji bukan penghematan: ``PersistentClient`` mengunci direktori indeks
    di Windows. Membukanya untuk menanam nol potongan berarti memegang kunci itu
    sampai proses berakhir — untuk sesuatu yang tidak menghasilkan apa-apa.
    """
    store = ChromaVectorStore(tmp_path / "vector_db", embedder=KeywordEmbedder())

    assert store.index([]) == 0
    assert store.index([document("   ")]) == 0
    assert not store.directory.exists()


def test_two_chunks_with_the_same_identity_are_stored_once(store: ChromaVectorStore) -> None:
    """Dua potongan beridentitas sama — mis. berkas bernama sama di dua subdirektori.

    ChromaDB **menolak** ``upsert`` berisi id ganda, dan menolak seluruh
    panggilannya. Membuang yang kembar di sini membuat satu berkas kembar tidak
    membatalkan ``ingest`` bahan yang selebihnya baik-baik saja.
    """
    same = document("algoritma", page=7, paragraph=0, document_id="compiler.pdf")

    written = store.index([same, same])

    assert written == 1
    assert store.count() == 1


def test_a_chunk_without_a_page_keeps_it_empty(store: ChromaVectorStore) -> None:
    """``page=None`` harus tetap ``None`` — bukan 0, yang akan menunjuk halaman pertama."""
    store.index([document("matriks", page=None, source_type=SourceType.MARKDOWN)])

    hit = store.query("matriks", limit=1)[0]

    assert hit.page is None


# ---------------------------------------------------------------------------
# Mencari
# ---------------------------------------------------------------------------
def test_query_returns_the_chunk_that_matches_the_question_first(
    store: ChromaVectorStore,
) -> None:
    store.index(
        [
            document("graf dan pohon", page=10),
            document("kompleksitas waktu algoritma", page=42),
        ]
    )

    hits = store.query("kompleksitas algoritma", limit=2)

    assert hits[0].page == 42
    assert hits[0].text == "kompleksitas waktu algoritma"


def test_query_keeps_the_source_and_section_of_each_hit(store: ChromaVectorStore) -> None:
    """§13: bukti tidak boleh berupa teks saja.

    Tanpa ``source`` dan ``section``, temuan pemeriksa fakta tidak dapat
    ditelusuri manusia — dan pemeriksaan yang tidak dapat ditelusuri sama saja
    dengan tidak ada pemeriksaan.
    """
    store.index(
        [document("graf", document_id="rosen.pdf", filename="rosen.pdf", section="Graf Berarah")]
    )

    hit = store.query("graf", limit=1)[0]

    assert hit.source == "rosen.pdf"
    assert hit.section == "Graf Berarah"
    assert hit.chunk_id == "rosen.pdf#p42#c0"


def test_query_on_an_empty_index_returns_nothing_instead_of_failing(
    store: ChromaVectorStore,
) -> None:
    """Indeks yang belum dibangun adalah keadaan yang sah, bukan galat.

    ChromaDB sendiri mengembalikan balasan kosong; yang penting adalah jalur ini
    tidak menuntut ``ingest`` dijalankan lebih dulu hanya untuk dapat membaca
    status buku.
    """
    assert store.count() == 0
    assert store.query("apa pun") == ()


def test_query_with_a_limit_below_one_returns_nothing(store: ChromaVectorStore) -> None:
    store.index([document("algoritma")])

    assert store.query("algoritma", limit=0) == ()


def test_query_embeds_the_question_with_the_same_model(tmp_path: Path) -> None:
    """Pencarian memakai embedder yang sama dengan penanaman.

    Kalau tidak, jarak yang dihitung tidak bermakna apa pun.
    """
    embedder = KeywordEmbedder()
    store = ChromaVectorStore(tmp_path / "vector_db", embedder=embedder)
    store.index([document("algoritma")])

    store.query("algoritma")

    assert embedder.calls[-1] == ["algoritma"]


# ---------------------------------------------------------------------------
# Mengosongkan
# ---------------------------------------------------------------------------
def test_reset_empties_the_index(store: ChromaVectorStore) -> None:
    """``ingest`` mengosongkan lebih dulu: bahan yang diganti tidak boleh meninggalkan
    potongan lama."""
    store.index([document("algoritma", page=1), document("graf", page=2)])

    store.reset()

    assert store.count() == 0


def test_reset_on_an_index_that_was_never_built_is_harmless(store: ChromaVectorStore) -> None:
    store.reset()

    assert store.count() == 0


# ---------------------------------------------------------------------------
# _hits — terjemahan balasan ChromaDB (murni)
# ---------------------------------------------------------------------------
def test_hits_reads_the_first_result_of_a_single_query() -> None:
    """ChromaDB mengembalikan daftar **per-query**; kita selalu mengirim satu."""
    hits = ChromaVectorStore._hits(
        {
            "ids": [["a#p1#c0"]],
            "documents": [["teks"]],
            "metadatas": [[{"source": "a.pdf", "page": 1, "section": "S"}]],
            "distances": [[0.25]],
        }
    )

    assert hits == (
        VectorHit(
            chunk_id="a#p1#c0",
            text="teks",
            source="a.pdf",
            page=1,
            section="S",
            distance=0.25,
        ),
    )


def test_hits_tolerates_an_empty_answer() -> None:
    """Balasan kosong berarti tidak ada hasil — bukan galat, dan bukan ``IndexError``."""
    empty = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    assert ChromaVectorStore._hits(empty) == ()
    assert ChromaVectorStore._hits({}) == ()
