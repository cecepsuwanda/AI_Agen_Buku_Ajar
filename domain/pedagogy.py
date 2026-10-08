"""Tangga pedagogi §23, dan anak tangga yang **terbukti** absen — MURNI.

§23 menggambar satu tangga, bukan satu daftar:

```text
Learning Objective
       ↓
Explanation
       ↓
Example
       ↓
Practice
       ↓
Exercise
```

Tangga itu bukan hiasan tata letak. Ia menyatakan **ketergantungan**: anak
tangga yang di bawah hanya dapat dinilai bila anak tangga di atasnya ada.
"Latihan tidak sesuai tujuan pembelajaran" — kelemahan keempat yang harus
dideteksi §23 — tidak dapat dijawab oleh siapa pun, model sekalipun, pada bab
yang tidak punya tujuan pembelajaran. Pertanyaan itu tidak sulit dijawab; ia
tidak punya jawaban.

**Itulah batas yang ditarik berkas ini.** Yang tinggal di sini hanyalah
pertanyaan-pertanyaan yang jawabannya sudah diketahui program sebelum model
dipanggil: apakah anak tangganya **ada**. Sesudah itu ada, apakah isinya **baik**
adalah pekerjaan model — dan hanya itu yang sampai ke sana (§keputusan 7
rencana). Pemisahan tersebut bukan penghematan token belaka: bab yang ditolak
karena satu anak tangga hilang tidak perlu dibayar dua kali, dan yang lebih
penting, ia tidak akan **lolos** hanya karena model kebetulan memaafkannya.

**Catatan tentang anak tangga pertama.** Tujuan pembelajaran pada draf dikunci
ke tujuan yang direncanakan :func:`~domain.rules.enforce_draft_contract`, jadi
dalam pipeline sungguhan temuan "Learning Objective" yang muncul di sini berarti
yang tidak menetapkan tujuan adalah **perencana**, bukan penulis. Fungsi ini
tetap memeriksa drafnya dan bukan spesifikasinya, karena itulah yang
sesungguhnya akan dibaca mahasiswa — dan pemanggil yang melewati penguncian itu
belum tentu tidak ada.

**Mengapa jumlah tidak diperiksa di sini.** ``spec.required_examples`` dan
``spec.required_exercises`` sudah diperiksa gate contoh (§19) dan gate latihan
(§20) — dengan temuan yang lebih tepat daripada yang dapat dirumuskan di sini,
karena di sanalah bahan terstrukturnya masih utuh. Memeriksanya kembali di sini
akan menciptakan dua tempat yang memutuskan hal yang sama tentang angka yang
sama, dan dua tempat seperti itu akan menyimpang. Yang diperiksa di sini adalah
hal yang tidak diperiksa siapa pun: apakah anak tangganya **kosong sama sekali**,
dan apakah babnya mengulang dirinya sendiri.
"""

from __future__ import annotations

from typing import Sequence

from domain.book import ChapterSpec
from domain.chapter import ChapterDraft
from domain.checking import CheckFinding

#: Anak tangga §23, dari atas ke bawah.
#:
#: Ditulis dalam bahasa blueprint, bukan diterjemahkan: ``subject`` temuan yang
#: diminta kepada model disalin dari daftar ini, dan menerjemahkannya berarti
#: menambah satu tempat lagi yang dapat menyimpang dari §23. Prompt yang
#: menjelaskannya kepada model, dan prompt itu sudah berbahasa Indonesia.
LADDER: tuple[str, ...] = (
    "Learning Objective",
    "Explanation",
    "Example",
    "Practice",
    "Exercise",
)


def _blank(parts: Sequence[str]) -> bool:
    """True bila tidak satu pun bagian ini memuat sesuatu (MURNI)."""
    return not any(part.strip() for part in parts)


def missing_rungs(draft: ChapterDraft, *, spec: ChapterSpec) -> tuple[CheckFinding, ...]:
    """Anak tangga §23 yang **terbukti** absen dari draf, urut tangga (MURNI).

    Yang dikembalikan hanya yang dapat dipastikan dari struktur draf itu
    sendiri. Tidak ada penilaian, tidak ada tebakan, dan tidak ada pemeriksaan
    yang bergantung pada mutu tulisan — hanya "ada" atau "tidak ada".

    :returns: temuan dengan ``ok=False``; **kosong** berarti tangganya utuh dan
        penilaian boleh dilanjutkan ke model.
    """
    gaps: list[CheckFinding] = []

    if _blank(draft.learning_objectives):
        gaps.append(
            CheckFinding(
                subject=LADDER[0],
                ok=False,
                detail=(
                    "Bab ini tidak menyebut satu pun tujuan pembelajaran. Tanpa tujuan, "
                    "tidak ada yang dapat dibandingkan dengan latihannya — dan "
                    "kelemahan yang paling sering §23 minta dideteksi justru itu."
                ),
            )
        )

    if _blank([section.body for section in draft.sections]):
        gaps.append(
            CheckFinding(
                subject=LADDER[1],
                ok=False,
                detail=(
                    "Tidak satu pun sub-bab memuat penjelasan; yang ada hanya judulnya. "
                    "Tangga §23 berhenti di anak tangga kedua."
                ),
            )
        )

    if spec.required_examples > 0 and _blank(draft.examples):
        gaps.append(
            CheckFinding(
                subject=LADDER[2],
                ok=False,
                detail=(
                    f"Spesifikasi bab meminta {spec.required_examples} contoh, dan draf "
                    "ini tidak memuat satu pun."
                ),
            )
        )

    if spec.required_exercises > 0 and _blank(draft.exercises):
        gaps.append(
            CheckFinding(
                subject=LADDER[4],
                ok=False,
                detail=(
                    f"Spesifikasi bab meminta {spec.required_exercises} latihan, dan draf "
                    "ini tidak memuat satu pun."
                ),
            )
        )

    gaps.extend(_repetitions(draft))
    return tuple(gaps)


def repeated_headings(draft: ChapterDraft) -> tuple[str, ...]:
    """Judul sub-bab yang muncul lebih dari sekali, urut kemunculan (MURNI).

    Pencocokannya mengabaikan besar-kecil huruf dan spasi tepi: ``"Notasi Big-O"``
    dan ``"notasi big-o "`` adalah sub-bab yang sama bagi pembaca, dan
    pemeriksaan yang menuntut kesamaan byte akan melaporkannya sebagai dua
    sub-bab yang berbeda.
    """
    seen: set[str] = set()
    repeated: dict[str, None] = {}
    for section in draft.sections:
        key = section.heading.strip().casefold()
        if not key:
            continue
        if key in seen:
            repeated.setdefault(section.heading.strip(), None)
        seen.add(key)
    return tuple(repeated)


def _repetitions(draft: ChapterDraft) -> tuple[CheckFinding, ...]:
    """Pengulangan yang tidak diperlukan — kelemahan keenam §23 (MURNI).

    Dua bentuk, dan keduanya dapat dipastikan: judul sub-bab yang sama muncul
    dua kali, dan dua sub-bab yang isinya identik persis. Yang kedua sengaja
    membandingkan **seluruh badan** sub-bab, bukan paragrafnya: paragraf yang
    berulang di dalam satu sub-bab seringkali memang disengaja penulisnya
    (penegasan, rangkuman), sedangkan sub-bab yang seluruh isinya kembar tidak
    punya pembacaan lain.

    Subjek temuannya adalah **judul** sub-bab yang mengulang, bukan kalimat
    penjelas: judul itulah yang dapat dicari penulis di dalam drafnya.
    """
    gaps: list[CheckFinding] = []

    for heading in repeated_headings(draft):
        gaps.append(
            CheckFinding(
                subject=heading,
                ok=False,
                detail="Judul sub-bab ini muncul lebih dari sekali di dalam bab yang sama.",
            )
        )

    seen: set[str] = set()
    reported: set[str] = set()
    for section in draft.sections:
        body = section.body.strip().casefold()
        if not body:
            continue
        if body in seen and section.heading.strip() not in reported:
            reported.add(section.heading.strip())
            gaps.append(
                CheckFinding(
                    subject=section.heading.strip() or section.heading,
                    ok=False,
                    detail=(
                        "Isi sub-bab ini identik dengan sub-bab sebelumnya; "
                        "pengulangan yang tidak diperlukan (§23)."
                    ),
                )
            )
        seen.add(body)

    return tuple(gaps)


__all__ = ["LADDER", "missing_rungs", "repeated_headings"]
