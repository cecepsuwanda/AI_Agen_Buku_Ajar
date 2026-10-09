"""Persetujuan manusia (§44) — gate yang berhenti dan menunggu.

§44 menutup rantai §41 dengan sesuatu yang tidak dapat dikerjakan model mana
pun: **keputusan untuk menerbitkan**. Blueprint menamakannya dengan tegas — "AI
co-author, bukan generator buku otonom penuh" — dan §27 mencerminkan sikap yang
sama: ``APPROVED`` adalah status tersendiri, bukan hasil sampingan dari kompilasi
LaTeX yang berhasil. Bab yang dapat dicetak belum tentu bab yang boleh diajarkan.

**Gate ini mati secara bawaan, dan itu keputusan.** Ia tidak terdaftar di
``pipeline.gates`` milik ``config.yaml``, sehingga bab-bab yang dikerjakan tanpa
mengubah konfigurasi tetap berakhir ``APPROVED`` seperti sebelumnya. Alasannya
praktis: mengaktifkannya berarti setiap ``run`` berhenti dan menunggu seseorang —
dan itu bukan perilaku yang pantas didapat seseorang yang baru menjalankan
perintahnya untuk pertama kali. Menyalakannya adalah satu baris:

.. code-block:: yaml

    pipeline:
      gates: [..., latex_writer, latex_qa, human_approval]

**Bila diaktifkan, ia selalu memblokir.** Tidak ada konfigurasi yang membuatnya
meloloskan bab dengan sendirinya, karena gate yang dapat meloloskan dirinya
sendiri tidak menambahkan apa pun di atas jalur tanpa gate. Yang membuat
babnya berlanjut adalah keputusan manusia yang tercatat di
``state/chapterNN.json`` — lewat ``ai-book approve N``.

**Perintah manualnya tetap berguna meski gate ini mati.** ``ai-book reject N
--reason ...`` mengembalikan bab yang sudah ``APPROVED`` ke penulis; itu jalur
yang tidak dimiliki gate ini, karena gate hanya berjalan pada bab yang belum
disetujui. Karena itu keduanya tinggal di ``app/commands.py``, bukan di sini.
"""

from __future__ import annotations

from domain.chapter import ChapterRecord, ReviewResult
from domain.enums import ChapterStatus
from domain.ports import ReviewGate
from domain.state import BookState

from agents.gates import GateContext, register_gate

#: Nama gate di ``config.yaml``.
#:
#: Dipakai dua tempat, dan keduanya harus menyebut nama yang sama: di sini, dan
#: di :class:`~agents.book_director.BookDirector` yang menulis vonis manusia ke
#: dalam ``reviews`` ketika ``approve``/``reject`` dijalankan. Vonis yang
#: namanya berbeda dari gate-nya akan terbaca sebagai dua pemeriksa yang berbeda.
HUMAN_APPROVAL_GATE = "human_approval"


class HumanApprovalGate:
    """Gate §44: berhenti di ``LATEX_COMPILED`` sampai manusia memutuskan.

    Ia memakai vonis ``blocked`` — bukan ``approved=False`` — karena babnya tidak
    ditolak dan tidak akan direvisi. Perbedaan itu ditegakkan
    :func:`~domain.rules.with_gate_result`: vonis yang menunggu tidak mengubah
    status dan tidak menghabiskan anggaran revisi, sehingga bab yang baik tidak
    dihukum hanya karena belum sempat dibaca.
    """

    #: Nama gate di ``config.yaml``.
    name = HUMAN_APPROVAL_GATE

    #: Status yang dicapai bila manusia menyetujui (§27).
    produces = ChapterStatus.APPROVED

    def evaluate(self, record: ChapterRecord, book: BookState) -> ReviewResult:
        """Kembalikan vonis "menunggu keputusan" — bukan lulus, bukan tolak.

        Catatannya sengaja memuat perintah yang harus dijalankan, dengan nomor
        babnya sudah terisi. Pesan yang berbunyi "menunggu persetujuan" saja akan
        membuat pembacanya mencari tahu sendiri perintah apa yang dimaksud, dan
        pesan yang harus dijelaskan lebih lanjut belum menyelesaikan tugasnya.
        """
        del book
        number = record.number
        return ReviewResult(
            gate=self.name,
            approved=False,
            blocked=True,
            feedback=(
                f"Bab {number} selesai dikerjakan dan menunggu persetujuan manusia (§44).",
                f"Periksa keluarannya, lalu jalankan: ai-book approve {number}",
                "Bila belum layak diajarkan, kembalikan ke penulis: "
                f'ai-book reject {number} --reason "..."',
            ),
        )


@register_gate(HumanApprovalGate.name)
def _build_human_approval(context: GateContext) -> ReviewGate:
    """Bangun gate §44 dari konteksnya — tanpa model, dan itu memang maksudnya.

    Satu-satunya gate yang tidak memanggil ``router.chat(...)`` sama sekali.
    Konfigurasi karena itu dapat memuatnya meski tidak ada model yang tersedia —
    dan justru itulah keadaannya pada mesin yang memakai profil paling murah:
    yang dibutuhkan gate ini adalah pembaca, bukan model.
    """
    del context
    return HumanApprovalGate()


__all__ = ["HUMAN_APPROVAL_GATE", "HumanApprovalGate"]
