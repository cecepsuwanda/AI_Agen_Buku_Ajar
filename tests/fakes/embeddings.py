"""``EmbeddingModel`` palsu yang deterministik.

Embedder palsu harus **deterministik dan dapat dibaca**: yang diuji pada jalur
RAG bukan mutu vektornya, melainkan apakah potongan yang benar muncul lebih dulu
dan apakah halaman yang dikembalikan benar-benar halaman potongan itu. Vektor
acak akan membuat tes gagal kadang-kadang — jenis tes yang lebih buruk daripada
tidak ada tes sama sekali.

Karena itu :class:`KeywordEmbedder` menghitung kata pada kosakata tetap. Ia
memberi jarak yang bermakna (pertanyaan yang menyebut "kompleksitas" lebih dekat
ke potongan yang memuat kata itu), dan hasilnya sama di setiap mesin.
"""

from __future__ import annotations

from typing import Sequence

#: Kosakata tetap. Kecil dengan sengaja: cukup untuk membedakan potongan, dan
#: masih dapat dibaca manusia saat sebuah tes gagal.
VOCABULARY = ("algoritma", "kompleksitas", "graf", "pohon", "matriks")


class KeywordEmbedder:
    """Vektor = hitungan kata kosakata, dengan baseline agar tidak ada vektor nol.

    Baseline ``1.0`` penting: jarak kosinus terhadap vektor nol tidak
    terdefinisi, dan ChromaDB mengembalikan angka yang tidak bermakna untuknya —
    "tidak ada galat, tetapi hasilnya salah", persis jenis kerusakan yang membuat
    tes ini ada.
    """

    def __init__(self, vocabulary: Sequence[str] = VOCABULARY) -> None:
        self._vocabulary = tuple(vocabulary)
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Satu vektor per teks, urutannya dipertahankan."""
        self.calls.append(list(texts))
        return [
            [1.0 + 10.0 * text.lower().count(word) for word in self._vocabulary]
            for text in texts
        ]


__all__ = ["VOCABULARY", "KeywordEmbedder"]
