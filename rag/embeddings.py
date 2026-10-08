"""Adapter embedding untuk RAG (§10, §13).

:class:`~domain.ports.EmbeddingModel` sudah ada di ``models/`` dan sudah
berbicara HTTP. Yang ditambahkan berkas ini **bukan** pembungkus kosong,
melainkan dua hal yang tidak boleh dimiliki adapter HTTP mana pun:

**Pemotongan batch.** Indeks pertama dibangun dari ratusan potongan sekaligus.
Mengirim seluruhnya dalam satu permintaan membuat satu kegagalan transien
membuang pekerjaan seluruh korpus, dan membuat permintaan yang gagal tidak dapat
diulang sebagian. Batch berukuran tetap mengubah satu permintaan besar yang rapuh
menjadi beberapa permintaan kecil yang masing-masing dapat diulang.

**Pemeriksaan jumlah vektor.** Ini yang paling penting, dan alasannya bukan
kerapian: vektor dikaitkan ke potongan **berdasarkan urutan**. Model yang
mengembalikan 11 vektor untuk 12 teks akan menggeser seluruh kaitan setelahnya —
potongan 12 memakai vektor 11, dan seterusnya. Tidak ada satu pun galat yang
muncul; yang muncul adalah indeks yang mengutip halaman yang salah, dan itu
persis jenis kerusakan yang tidak dapat ditemukan manusia yang membaca buku
jadi. Karena itu jumlahnya diperiksa, dan ketidakcocokan menggagalkan jalankan
alih-alih menghasilkan indeks yang tampak baik.
"""

from __future__ import annotations

from typing import Sequence

from domain.errors import EmbeddingShapeError
from domain.ports import EmbeddingModel

#: Jumlah teks per permintaan embedding. ``nomic-embed-text`` memotong pada 2048
#: token per teks; 16 potongan sekaligus membuat setiap permintaan tetap di bawah
#: batas waktu adapter, tanpa membuat jumlah permintaan meledak pada korpus besar.
DEFAULT_BATCH_SIZE = 16


class BatchingEmbedder:
    """ :class:`domain.ports.EmbeddingModel` dengan batch dan pemeriksaan bentuk.

    Menerima port, bukan nama model: ``BatchingEmbedder`` tidak tahu model apa
    yang dipakai, dan menggantinya adalah urusan ``config.yaml`` (§6).
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size harus >= 1")
        self._model = model
        self._batch_size = batch_size

    @property
    def batch_size(self) -> int:
        """Potongan per permintaan — dipakai laporan ``ingest``."""
        return self._batch_size

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Kembalikan satu vektor untuk setiap teks, urutannya dipertahankan.

        :raises EmbeddingShapeError: bila model mengembalikan jumlah vektor yang
            berbeda dari jumlah teks.
        """
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            produced = self._model.embed(batch)
            if len(produced) != len(batch):
                raise EmbeddingShapeError(
                    expected=len(batch), produced=len(produced), offset=start
                )
            vectors.extend(list(vector) for vector in produced)
        return vectors

    def embed_one(self, text: str) -> list[float]:
        """Vektor untuk satu teks — jalur yang dipakai setiap pencarian.

        Ada supaya pemanggil tidak menuliskan ``embed([text])[0]`` dan tidak
        perlu memutuskan apa yang harus dilakukan bila daftarnya kosong.
        """
        produced = self.embed([text])
        if not produced:
            raise EmbeddingShapeError(expected=1, produced=0, offset=0)
        return produced[0]


__all__ = ["DEFAULT_BATCH_SIZE", "BatchingEmbedder"]
