"""Pencarian bukti dari indeks (§13).

Berkas ini kecil dengan sengaja, dan yang ada di dalamnya adalah **kebijakan
pencarian** — bukan penyimpanan (itu ``rag/vector_store.py``) dan bukan
penyaringan isi (itu model ``researcher``). Tiga hal yang ditetapkan di sini,
dan ketiganya harus diuji tanpa ChromaDB maupun jaringan:

**Jarak menjadi skor.** ChromaDB mengembalikan jarak kosinus (0 = identik),
sedangkan :class:`~domain.chapter.Evidence` menyimpan skor kemiripan yang lebih
besar berarti lebih baik — dan itulah yang dibaca manusia pada temuan pemeriksa
fakta. Penerjemahannya satu tempat, sehingga tidak ada dua definisi skor di
dalam satu jalankan.

**Ambang minimum.** Bukti yang **anti**-mirip tidak lebih berguna daripada tidak
ada bukti: ia akan dikutip penulis sebagai rujukan, dan pemeriksa fakta akan
menemukan klaim yang tidak didukung oleh potongan yang justru membantahnya.
Potongan dengan skor di bawah ambang dibuang di sini, sebelum ia sempat menjadi
kutipan.

**Port ``Retriever`` dipenuhi secara struktural.** Tidak ada ``isinstance`` dan
tidak ada pewarisan — ``agents/researcher.py`` hanya menerima sesuatu yang
berbentuk :class:`~domain.ports.Retriever`, dan yang membuktikannya adalah
``mypy``.
"""

from __future__ import annotations

from domain.chapter import Evidence
from rag.vector_store import ChromaVectorStore, VectorHit

#: Batas bawah skor kemiripan. Nol berarti "tegak lurus" — potongan yang tidak
#: lebih dekat ke pertanyaan daripada arah acak. Menurunkan ambang ini berarti
#: menerima bukti yang tidak berhubungan; menaikkannya terlalu tinggi berarti
#: mengembalikan paket riset kosong pada korpus yang sebenarnya memadai.
DEFAULT_MIN_SCORE = 0.0

#: Batas jumlah bukti bila pemanggil tidak menyebutkannya. Angka yang sama
#: dengan bawaan ``rag.top_k`` di ``config.yaml`` — bukan kebetulan: keduanya
#: adalah "berapa potongan yang dapat dibaca penulis sebelum promptnya terlalu
#: panjang", dan perbedaan di antara keduanya hanya akan membingungkan.
DEFAULT_LIMIT = 8


def similarity(distance: float) -> float:
    """Ubah jarak kosinus menjadi skor kemiripan (MURNI).

    ``score = 1 - distance``. Pada ruang kosinus jarak berada di ``[0, 2]``,
    sehingga skornya di ``[-1, 1]``: 1 identik, 0 tegak lurus, -1 berlawanan.
    Nilainya **tidak** dijepit ke ``[0, 1]``: skor negatif adalah informasi yang
    benar — potongan itu memang berlawanan arah dengan pertanyaannya — dan
    menjepitnya menjadi 0 akan menyamarkannya sebagai sekadar tidak relevan.
    """
    return 1.0 - float(distance)


def _to_evidence(hit: VectorHit) -> Evidence:
    """ :class:`VectorHit` menjadi :class:`Evidence` (MURNI).

    ``source`` jatuh ke ``chunk_id`` bila metadata berkasnya kosong. Bukan
    kehati-hatian berlebihan: ``Evidence.source`` wajib ada, dan bukti tanpa
    asal akan dicetak sebagai kutipan tanpa sumber — lebih baik menampilkan
    identitas potongan, yang setidaknya dapat ditelusuri kembali ke indeks,
    daripada string kosong yang tampak seperti kelalaian.
    """
    return Evidence(
        source=hit.source or hit.chunk_id,
        page=hit.page,
        section=hit.section,
        text=hit.text,
        score=similarity(hit.distance),
    )


class VectorRetriever:
    """ :class:`domain.ports.Retriever` di atas :class:`ChromaVectorStore`.

    Menerima **store**, bukan direktori: retriever tidak membuka indeks, tidak
    membangunnya, dan tidak dapat menghapusnya. Yang boleh menulis indeks hanya
    ``ingest`` lewat port :class:`~domain.ports.CorpusIndexer`.
    """

    def __init__(
        self,
        store: ChromaVectorStore,
        *,
        default_limit: int = DEFAULT_LIMIT,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        self._store = store
        self._default_limit = max(default_limit, 1)
        self._min_score = min_score

    def retrieve(self, query: str, *, limit: int = DEFAULT_LIMIT) -> tuple[Evidence, ...]:
        """Bukti terbaik untuk ``query``, terbaik lebih dulu (§13).

        ``limit`` yang tidak positif diganti dengan batas bawaan alih-alih
        menghasilkan pencarian kosong: pemanggil yang tidak menyebut batas
        berarti tidak memikirkannya, bukan berarti menginginkan nol hasil.
        """
        effective = self._default_limit if limit <= 0 else limit
        hits = self._store.query(query, limit=effective)
        return tuple(
            _to_evidence(hit)
            for hit in hits
            if similarity(hit.distance) > self._min_score
        )

    @property
    def min_score(self) -> float:
        """Ambang yang berlaku, untuk dilaporkan ``ingest`` dan ``status``."""
        return self._min_score


__all__ = ["DEFAULT_LIMIT", "DEFAULT_MIN_SCORE", "VectorRetriever", "similarity"]
