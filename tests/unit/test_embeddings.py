"""Tes pembungkus embedding (§10, §13) — tanpa jaringan, tanpa model.

Dua hal yang diuji di sini, dan keduanya adalah kerusakan yang **tidak
menghasilkan galat** di jalur biasa:

1. **Pemotongan batch.** Ia tidak boleh mengubah urutan maupun jumlah vektor.
   Batch yang "menghilangkan" satu teks akan menggeser kaitan vektor dengan
   potongan untuk seluruh sisa korpus.
2. **Pemeriksaan jumlah vektor.** Ia harus gagal, dan gagalnya harus menyebut
   batch mana yang salah — bukan sekadar "jumlahnya tidak cocok". Tanpa nomor
   batch, pesan itu tidak menolong siapa pun yang sedang menelusuri korpus
   berisi ratusan potongan.
"""

from __future__ import annotations

from typing import Sequence

import pytest

from domain.errors import EmbeddingShapeError
from rag.embeddings import DEFAULT_BATCH_SIZE, BatchingEmbedder


class CountingEmbedder:
    """``EmbeddingModel`` palsu: mengembalikan ``count`` vektor per panggilan.

    ``count=None`` berarti "sebanyak masukannya" — perilaku model yang benar.
    Angka lain mensimulasikan model yang mengembalikan lebih sedikit (atau lebih
    banyak) vektor daripada teks yang dikirim, yang justru satu-satunya kesalahan
    yang tidak terlihat tanpa pemeriksaan.
    """

    def __init__(self, *, count: int | None = None, dimensions: int = 3) -> None:
        self.batches: list[list[str]] = []
        self._count = count
        self._dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        size = len(texts) if self._count is None else self._count
        return [[float(index)] * self._dimensions for index in range(size)]


class EchoingEmbedder:
    """Vektor yang isinya panjang teks — sehingga kaitan vektor↔teks dapat diperiksa."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


def embedder(model: object, *, batch_size: int = DEFAULT_BATCH_SIZE) -> BatchingEmbedder:
    """Pembungkus atas model palsu, tanpa memaksa tes menulis anotasi port."""
    return BatchingEmbedder(model, batch_size=batch_size)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------
def test_an_empty_input_calls_no_model_at_all() -> None:
    """Korpus kosong tidak boleh menghasilkan satu pun permintaan embedding."""
    model = CountingEmbedder()

    assert embedder(model).embed([]) == []
    assert model.batches == []


def test_texts_are_split_into_batches_of_the_configured_size() -> None:
    """Lima teks dengan batch 2 menjadi 2+2+1 — batch terakhir tidak dibuang."""
    model = CountingEmbedder()

    assert len(embedder(model, batch_size=2).embed(["a", "b", "c", "d", "e"])) == 5
    assert [len(batch) for batch in model.batches] == [2, 2, 1]


def test_the_number_of_batches_follows_the_batch_size() -> None:
    """Ukuran batch yang lebih kecil menghasilkan lebih banyak permintaan, bukan lebih sedikit
    vektor."""
    model = CountingEmbedder()

    assert len(embedder(model, batch_size=1).embed(["a", "b", "c"])) == 3
    assert len(model.batches) == 3


def test_vectors_stay_aligned_with_their_texts_across_batches() -> None:
    """Ini invarian yang paling penting di berkas ini.

    Vektor dikaitkan ke potongan **berdasarkan urutan**. Model yang mengembalikan
    jumlah yang benar tetapi urutan yang tertukar tidak akan pernah ketahuan dari
    panjangnya daftar — yang ketahuan adalah kutipan yang menunjuk halaman lain.
    Karena itu tesnya memakai vektor yang isinya adalah identitas teksnya.
    """
    texts = ["a", "bbb", "cc", "ddddd"]

    vectors = embedder(EchoingEmbedder(), batch_size=2).embed(texts)

    assert vectors == [[1.0], [3.0], [2.0], [5.0]]


def test_the_default_batch_size_is_the_documented_one() -> None:
    """Angka bawaan ikut dilaporkan ``ingest``; ia tidak boleh berubah diam-diam."""
    assert embedder(CountingEmbedder()).batch_size == DEFAULT_BATCH_SIZE


def test_a_batch_size_below_one_is_rejected_at_construction() -> None:
    """``batch_size=0`` akan menyebabkan perulangan tak berujung — ditolak lebih dulu."""
    with pytest.raises(ValueError, match="batch_size"):
        embedder(CountingEmbedder(), batch_size=0)


# ---------------------------------------------------------------------------
# Pemeriksaan bentuk
# ---------------------------------------------------------------------------
def test_a_short_batch_fails_instead_of_shifting_the_alignment() -> None:
    """Model yang mengembalikan lebih sedikit vektor menggagalkan jalankan, bukan indeks."""
    model = CountingEmbedder(count=1)

    with pytest.raises(EmbeddingShapeError) as excinfo:
        embedder(model, batch_size=4).embed(["a", "b", "c", "d"])

    assert excinfo.value.expected == 4
    assert excinfo.value.produced == 1


class ShortSecondBatch:
    """Batch pertama benar, batch kedua kurang satu vektor.

    Model yang rusak tidak selalu rusak sejak permintaan pertama — dan justru
    kerusakan yang muncul di tengah korpus yang paling sulit ditemukan, karena
    ia tidak menggagalkan apa pun pada berkas-berkas pertama.
    """

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        size = len(texts) if self.calls == 1 else len(texts) - 1
        return [[0.0] for _ in range(size)]


def test_the_error_says_which_batch_was_wrong() -> None:
    """Batch pertama baik-baik saja, batch kedua yang rusak — dan itu yang disebut.

    Tanpa ``offset``, pesan ini hanya memberi tahu bahwa *ada* batch yang salah.
    Pada korpus sungguhan, yang berguna adalah **mulai dari potongan ke berapa**
    hasilnya tidak dapat dipercaya.
    """
    with pytest.raises(EmbeddingShapeError) as excinfo:
        embedder(ShortSecondBatch(), batch_size=3).embed(["a", "b", "c", "d", "e", "f", "g"])

    assert excinfo.value.expected == 3
    assert excinfo.value.produced == 2
    assert excinfo.value.offset == 3
    assert "teks ke-3" in str(excinfo.value)


def test_a_long_batch_is_also_rejected() -> None:
    """Kelebihan vektor sama berbahayanya dengan kekurangan: kaitannya bergeser juga."""
    model = CountingEmbedder(count=5)

    with pytest.raises(EmbeddingShapeError):
        embedder(model, batch_size=2).embed(["a", "b"])


def test_the_error_never_mentions_the_model_name() -> None:
    """Pemanggil tidak memilih modelnya, jadi menyebut nama model tidak menolongnya.

    Yang dapat ia lakukan adalah memperbaiki atau mengganti model embedding lewat
    ``config.yaml`` — dan pesannya mengarahkan ke sana tanpa berpura-pura tahu
    model mana yang menjawab.
    """
    with pytest.raises(EmbeddingShapeError) as excinfo:
        embedder(CountingEmbedder(count=0), batch_size=2).embed(["a", "b"])

    assert "nomic" not in str(excinfo.value)
    assert "indeks tidak dibangun" in str(excinfo.value)


# ---------------------------------------------------------------------------
# embed_one
# ---------------------------------------------------------------------------
def test_embed_one_returns_a_single_vector() -> None:
    """Jalur yang dipakai setiap pencarian: satu teks, satu vektor."""
    assert embedder(EchoingEmbedder()).embed_one("kata") == [4.0]


def test_embed_one_rejects_a_model_that_returns_nothing() -> None:
    """Model yang mengembalikan nol vektor tidak boleh menjadi "pencarian tanpa hasil"."""
    with pytest.raises(EmbeddingShapeError) as excinfo:
        embedder(CountingEmbedder(count=0)).embed_one("kata")

    assert excinfo.value.expected == 1
    assert excinfo.value.produced == 0
