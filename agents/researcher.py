"""Research Agent (§17) — dua implementasi, satu port.

:class:`NullResearcher` **tidak meneliti**, dan ia mengatakannya:
``degraded=True``, tanpa sumber, tanpa bukti. Ia tidak mengisi ``sources`` dari
``spec.references`` karena rujukan itu belum pernah diambil — dan paket yang
mengaku punya sumber sekaligus mengaku terdegradasi adalah kontradiksi yang akan
membuat penulis mengutip bahan yang tidak pernah ada. Bendera ``degraded``
itulah yang dipakai prompt penulis untuk memutuskan: tanpa bahan, ia dilarang
mencantumkan kutipan sama sekali dan wajib menandai setiap klaim faktual di
``unresolved_claims``. Jadi degradasi ini **terlihat di dalam buku**, bukan
disembunyikan.

:class:`RagResearcher` mengambil bukti dari indeks (§13), lalu meminta model
peran ``researcher`` memilahnya menjadi konsep, definisi, dan contoh.

Yang **tidak** dilakukan :class:`RagResearcher`, dan itu keputusan yang
disengaja: ia tidak meminta model mengembalikan ``source`` atau ``page``.
Bukti yang dikembalikannya adalah **objek bukti asli** dari retriever, apa
adanya, sehingga halaman yang tertulis di dalam bab tidak pernah melewati mulut
model. Model yang boleh menuliskan nomor halaman dapat mengarangnya, dan
halaman yang salah adalah kesalahan yang tidak akan pernah ditemukan pembaca.

Ketika retriever tidak menemukan apa pun, jalurnya kembali ke
:class:`NullResearcher`: paket terdegradasi, tanpa satu pun panggilan model.
Memanggil peneliti pada bahan kosong hanya akan menghasilkan konsep karangan —
dan konsep karangan yang lolos ke draf lebih berbahaya daripada bab yang jujur
mengaku belum berbukti.
"""

from __future__ import annotations

from typing import Sequence

from domain.book import ChapterSpec
from domain.chapter import Evidence, ResearchFindings, ResearchPackage
from domain.ports import ChatModel, PromptLibrary, Retriever
from domain.state import BookState

from agents.base import StructuredAgent
from agents.context import (
    book_language,
    book_title,
    evidence_lines,
    evidence_ref,
    summaries_before,
    terminology_lines,
)

#: Banyaknya bukti yang diminta dari retriever. Angka ini datang dari
#: ``config.rag.top_k`` lewat composition root; bawaan di sini hanya agar kelas
#: ini tetap dapat dipakai tanpa konfigurasi (mis. di tes).
DEFAULT_TOP_K = 8


class NullResearcher:
    """Researcher yang tidak meneliti — keadaan jujur ketika RAG mati atau indeksnya kosong.

    Kesesuaiannya dengan port ditegakkan oleh tipe, bukan oleh ``isinstance``:
    ``Researcher`` adalah ``Protocol`` tanpa ``runtime_checkable``, sehingga
    satu-satunya pemeriksaan yang bermakna adalah anotasi tipe di composition
    root dan ``mypy`` yang membacanya.
    """

    def collect(self, spec: ChapterSpec, book: BookState) -> ResearchPackage:
        """Kembalikan paket riset kosong yang menandai dirinya terdegradasi.

        Kedua argumen sengaja tidak dipakai. Signature-nya tetap lengkap supaya
        :class:`RagResearcher` dapat memakainya tanpa mengubah satu pun pemanggil.
        """
        del spec, book
        return ResearchPackage.empty()


class RagResearcher(StructuredAgent[ResearchFindings]):
    """Researcher berbasis retrieval (§13, §17).

    Menerima ``ChatModel`` dan ``Retriever``, bukan router: dari sini tidak
    terlihat model mana yang menjawab, dan tidak ada jalan bagi agent ini untuk
    memilih modelnya sendiri.
    """

    role = "researcher"
    prompt_name = "researcher.chapter"

    def __init__(
        self,
        *,
        model: ChatModel,
        prompts: PromptLibrary,
        retriever: Retriever,
        top_k: int = DEFAULT_TOP_K,
        max_repair_attempts: int = 2,
    ) -> None:
        super().__init__(model=model, prompts=prompts, max_repair_attempts=max_repair_attempts)
        self._retriever = retriever
        self._top_k = max(top_k, 1)

    def collect(self, spec: ChapterSpec, book: BookState) -> ResearchPackage:
        """Ambil bukti untuk ``spec``, saring dengan model, rakit paketnya (§17).

        Bukti asli ikut ke dalam paket **seluruhnya**, termasuk yang tidak
        dipakai model untuk menyusun konsep. Yang menentukan apa yang boleh
        dikutip adalah ``research.evidence`` dan ``sources`` — bukan hasil
        penyaringan model — karena pemeriksa sitasi (§22) bekerja atas daftar
        itu.
        """
        evidence = self._retriever.retrieve(self._query(spec), limit=self._top_k)
        if not evidence:
            return ResearchPackage.empty()

        findings = self.generate(
            {
                "book_title": book_title(book),
                "language": book_language(book),
                "number": spec.number,
                "chapter_title": spec.title,
                "objectives": spec.objectives,
                "section_plan": spec.sections,
                "previous_summaries": summaries_before(book, spec.number),
                "terminology": terminology_lines(book),
                "evidence": evidence_lines(evidence),
            },
            # §38: catatan panggilan menyebut bahan yang benar-benar diambil.
            # Inilah satu-satunya panggilan model di pipeline yang punya daftar
            # itu, dan karena itu satu-satunya yang mengisinya.
            sources=tuple(evidence_ref(item) for item in evidence),
        )

        return ResearchPackage(
            concepts=findings.concepts,
            definitions=findings.definitions,
            examples=findings.examples,
            evidence=evidence,
            sources=_sources(evidence),
            degraded=False,
        )

    @staticmethod
    def _query(spec: ChapterSpec) -> str:
        """Kalimat pencarian untuk satu bab (MURNI).

        Disusun dari judul, tujuan pembelajaran, dan rencana sub-bab — tiga hal
        yang **memang dimiliki** bab ini sebelum apa pun ditulis. Meng-embed
        seluruh RPS akan menghasilkan pencarian yang terlalu umum: potongan yang
        cocok dengan rata-rata bab tidak berguna bagi bab mana pun.
        """
        parts = [spec.title, *spec.objectives, *spec.sections]
        return " ".join(part.strip() for part in parts if part.strip()) or spec.title


def _sources(evidence: Sequence[Evidence]) -> tuple[str, ...]:
    """Nama berkas unik dari bukti, urut kemunculan (MURNI).

    Diurutkan menurut kemunculan, bukan di-``sorted``: urutan bukti sudah
    menyatakan relevansi, dan daftar pustaka yang mengikuti relevansi lebih
    berguna daripada yang mengikuti abjad. Duplikat dibuang karena satu PDF
    menghasilkan puluhan potongan tetapi hanya satu entri daftar pustaka.
    """
    seen: dict[str, None] = {}
    for item in evidence:
        source = item.source.strip()
        if source:
            seen.setdefault(source, None)
    return tuple(seen)


__all__ = ["DEFAULT_TOP_K", "NullResearcher", "RagResearcher"]
