"""Rencana Pembelajaran Semester sebagai data (§12) — MURNI.

Tipe di sini adalah **hasil uraian** RPS, bukan RPS itu sendiri. Pembedaan itu
penting: berkas ``rps.tex`` adalah dokumen yang ditulis manusia untuk dibaca
manusia, sedangkan yang dibutuhkan pipeline adalah daftar minggu yang dapat
dihitung, kode CPMK yang dapat dirujuk, dan bobot penilaian yang dapat
dijumlah. Penguraiannya sendiri ada di :mod:`ingestion.rps_loader` — ia memakai
``re``, dan ``re`` dilarang di ``domain/``.

Modul ini memuat dua hal: tipe datanya, dan **perenderan ringkasannya** —
:func:`render_course_plan`. Ringkasan itu yang dikirim ke Book Planner sebagai
pengganti teks mentah: ia memuat isi yang sama dalam bentuk yang menyebut nomor
minggu secara eksplisit, sehingga perencana tidak perlu menebaknya dari kalimat.

Cacat pada RPS tidak pernah menjadi pengecualian. Bidang yang tidak ditemukan
bernilai kosong dan dilaporkan sebagai catatan, karena RPS yang bentuknya lain
sama sekali tetap harus dapat menghasilkan buku — hanya dengan catatan yang
jujur tentang apa yang tidak terbaca.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Sequence

from pydantic import Field

from domain.base import FrozenModel


class OutcomeKind(StrEnum):
    """Jenis capaian pembelajaran.

    Ketiganya sengaja berada dalam satu daftar bertanda jenis, bukan dalam tiga
    bidang terpisah di :class:`CoursePlan`. Dua bidang yang saling menyaring akan
    dapat berbeda isi — dan tidak ada cara mengetahui mana yang benar ketika itu
    terjadi.
    """

    CPL = "CPL"
    CPMK = "CPMK"
    SUB_CPMK = "SUB_CPMK"


#: Judul blok ringkasan, dipakai :func:`render_course_plan`.
KIND_TITLES: dict[OutcomeKind, str] = {
    OutcomeKind.CPL: "Capaian Pembelajaran Lulusan (CPL)",
    OutcomeKind.CPMK: "Capaian Pembelajaran Mata Kuliah (CPMK)",
    OutcomeKind.SUB_CPMK: "Sub-Capaian Pembelajaran Mata Kuliah (Sub-CPMK)",
}


class CourseIdentity(FrozenModel):
    """Identitas mata kuliah — baris-baris tabel bagian "Identitas".

    Seluruh bidang berdefault kosong: RPS yang tidak memuat identitas tetap sah,
    dan kekosongan itu dilaporkan sebagai catatan alih-alih menghentikan
    penguraian. ``extra`` menyimpan label yang tidak dikenali tabel label, supaya
    RPS yang memuat "Beban Praktikum" atau "Kode Kelas" tidak kehilangan isinya
    hanya karena modul ini belum mengenal namanya.
    """

    name: str = ""
    code: str = ""
    credits: str = ""
    semester: str = ""
    programme: str = ""
    prerequisites: str = ""
    lecturer: str = ""
    extra: tuple[tuple[str, str], ...] = ()

    def rows(self) -> tuple[tuple[str, str], ...]:
        """Seluruh baris identitas sebagai ``(label, nilai)``, berurutan."""
        known = (
            ("Mata Kuliah", self.name),
            ("Kode", self.code),
            ("SKS", self.credits),
            ("Semester", self.semester),
            ("Program Studi", self.programme),
            ("Prasyarat", self.prerequisites),
            ("Dosen", self.lecturer),
        )
        return tuple((label, value) for label, value in known if value) + self.extra

    def is_empty(self) -> bool:
        """Tidak satu pun baris identitas terbaca."""
        return not self.rows()


class LearningOutcome(FrozenModel):
    """Satu butir capaian pembelajaran.

    ``code`` boleh kosong: CPL pada RPS yang lazim ditulis sebagai daftar biasa
    tanpa kode. Butir tanpa kode tetap berguna sebagai konteks bagi perencana —
    tetapi ia tidak dapat dirujuk, dan itu dicatat oleh pengurainya.
    """

    code: str = ""
    statement: str = Field(min_length=1)
    kind: OutcomeKind

    def label(self) -> str:
        """``code`` bila ada, jika tidak potongan awal pernyataannya."""
        return self.code or self.statement


class WeekPlan(FrozenModel):
    """Satu minggu perkuliahan (§12).

    ``is_assessment`` memisahkan minggu ujian dari minggu kuliah. Minggu ujian
    **tidak boleh** menjadi bab: bab yang isinya "Ujian Tengah Semester" adalah
    bab yang tidak dapat ditulis siapa pun.
    """

    number: int = Field(ge=1)
    topic: str = ""
    is_assessment: bool = False


class AssessmentItem(FrozenModel):
    """Satu komponen penilaian dari bagian "Metode Penilaian".

    ``weight`` disimpan apa adanya sebagai teks, bukan diubah menjadi angka:
    RPS menuliskannya sebagai "20\\%", "20%", atau kadang "1 SKS", dan mengubah
    bentuknya di sini berarti membuang perbedaan yang mungkin justru penting.
    """

    activity: str = Field(min_length=1)
    weight: str = ""
    outcomes: tuple[str, ...] = ()


class CoursePlan(FrozenModel):
    """Seluruh RPS yang berhasil diuraikan (§12).

    Bidang ``other_sections`` menyimpan bagian yang tidak dikenali modul ini —
    misalnya "Catatan Penyusunan Buku" pada RPS contoh, yang justru memuat
    ketentuan penting tentang isi tiap bab. Pengurai yang hanya menyimpan apa
    yang dikenalnya akan menjatuhkan ketentuan itu tanpa satu pun tanda.
    """

    identity: CourseIdentity = Field(default_factory=CourseIdentity)
    description: str = ""
    outcomes: tuple[LearningOutcome, ...] = ()
    weeks: tuple[WeekPlan, ...] = ()
    assessments: tuple[AssessmentItem, ...] = ()
    references: tuple[str, ...] = ()
    other_sections: tuple[tuple[str, str], ...] = ()

    # -- pandangan turunan (murni) -----------------------------------------
    def by_kind(self, kind: OutcomeKind) -> tuple[LearningOutcome, ...]:
        """Butir capaian bertanda ``kind``, berurutan."""
        return tuple(outcome for outcome in self.outcomes if outcome.kind is kind)

    def cpl(self) -> tuple[LearningOutcome, ...]:
        """Butir CPL."""
        return self.by_kind(OutcomeKind.CPL)

    def cpmk(self) -> tuple[LearningOutcome, ...]:
        """Butir CPMK."""
        return self.by_kind(OutcomeKind.CPMK)

    def sub_cpmk(self) -> tuple[LearningOutcome, ...]:
        """Butir Sub-CPMK."""
        return self.by_kind(OutcomeKind.SUB_CPMK)

    def teaching_weeks(self) -> tuple[WeekPlan, ...]:
        """Minggu kuliah — minggu ujian dibuang."""
        return tuple(week for week in self.weeks if not week.is_assessment)

    def assessment_weeks(self) -> tuple[WeekPlan, ...]:
        """Minggu ujian."""
        return tuple(week for week in self.weeks if week.is_assessment)

    def is_empty(self) -> bool:
        """Tidak satu pun bagian RPS yang dikenali.

        Keadaan ini bukan kesalahan: berkas yang bentuknya sama sekali lain tetap
        dapat diserahkan ke perencana sebagai teks mentah. Yang penting adalah
        pemanggilnya **tahu** bahwa itu yang terjadi, karena ringkasan kosong
        tidak boleh dikirim sebagai pengganti RPS.
        """
        return not any(
            (
                not self.identity.is_empty(),
                self.description,
                self.outcomes,
                self.weeks,
                self.assessments,
                self.references,
                self.other_sections,
            )
        )


def week_label(numbers: Sequence[int]) -> str:
    """Label minggu dari nomor-nomornya (MURNI).

    Nomor yang berurutan diringkas menjadi rentang, dan kelompok yang terputus
    oleh minggu ujian ditulis sebagai beberapa rentang: ``[7, 9]`` menjadi
    ``"Minggu 7, 9"``, dan ``[1, 2, 3, 4, 5, 6, 7, 9, 10]`` menjadi
    ``"Minggu 1-7, 9-10"``. Bentuk itu persis §12 ("Chapter 1 → Week 1-2"), dan
    yang lebih penting: ia **tidak pernah** menyatakan cakupan yang lebih luas
    daripada kenyataannya. Menuliskan ``[7, 9]`` sebagai ``"Minggu 7-9"`` akan
    menyatakan bahwa bab itu memuat minggu 8, padahal minggu 8 adalah ujian
    tengah semester.

    Urutan dipakai apa adanya, tidak diurutkan ulang di sini: penyortiran
    menyembunyikan minggu yang tidak berurutan, dan itu justru hal yang harus
    terlihat.
    """
    ordered = list(numbers)
    if not ordered:
        return ""

    spans: list[str] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        spans.append(_span(start, previous))
        start = previous = number
    spans.append(_span(start, previous))

    return "Minggu " + ", ".join(spans)


def _span(first: int, last: int) -> str:
    """Satu rentang minggu: ``"3"`` atau ``"1-2"`` (MURNI)."""
    return str(first) if first == last else f"{first}-{last}"


def render_course_plan(plan: CoursePlan) -> str:
    """Ringkas RPS terurai menjadi teks untuk Book Planner (MURNI, §12).

    Ringkasan ini **menggantikan** teks mentah RPS di dalam prompt, dan dua alasan
    membuatnya lebih baik daripada teks mentah:

    * ia menyebut nomor minggu secara eksplisit, sehingga perencana tidak perlu
      menyimpulkannya dari "Minggu 9 - Pohon seimbang: AVL, ..." yang terpotong
      baris demi baris;
    * ia menandai minggu ujian, sehingga bab "Ujian Akhir Semester" tidak muncul
      hanya karena barisnya ada di daftar.

    Tidak ada isi yang dibuang: bagian yang tidak dikenali modul ini tetap
    dirender di bawah judulnya sendiri.
    """
    blocks: list[str] = []

    rows = plan.identity.rows()
    if rows:
        lines = ["## Identitas Mata Kuliah", ""]
        lines += [f"- {label}: {value}" for label, value in rows]
        blocks.append("\n".join(lines))

    if plan.description:
        blocks.append(f"## Deskripsi Mata Kuliah\n\n{plan.description}")

    for kind in (OutcomeKind.CPL, OutcomeKind.CPMK, OutcomeKind.SUB_CPMK):
        items = plan.by_kind(kind)
        if not items:
            continue
        lines = [f"## {KIND_TITLES[kind]}", ""]
        lines += [f"- {item.code}: {item.statement}" if item.code else f"- {item.statement}"
                  for item in items]
        blocks.append("\n".join(lines))

    if plan.weeks:
        teaching = plan.teaching_weeks()
        assessment = plan.assessment_weeks()
        lines = [
            "## Kalender Mingguan",
            "",
            f"{len(teaching)} minggu kuliah, {len(assessment)} minggu penilaian.",
        ]
        lines += [""]
        for week in plan.weeks:
            marker = "  [MINGGU PENILAIAN — bukan bahan bab]" if week.is_assessment else ""
            lines.append(f"- Minggu {week.number}: {week.topic}{marker}")
        blocks.append("\n".join(lines))

    if plan.assessments:
        lines = ["## Metode Penilaian", ""]
        for item in plan.assessments:
            detail = f" — bobot {item.weight}" if item.weight else ""
            outcomes = f" — {', '.join(item.outcomes)}" if item.outcomes else ""
            lines.append(f"- {item.activity}{detail}{outcomes}")
        blocks.append("\n".join(lines))

    if plan.references:
        lines = ["## Referensi", ""]
        lines += [f"{index}. {reference}" for index, reference in enumerate(plan.references, 1)]
        blocks.append("\n".join(lines))

    for title, body in plan.other_sections:
        blocks.append(f"## {title}\n\n{body}")

    if not blocks:
        return "(RPS tidak memuat bagian yang dapat dikenali.)"

    return "\n\n".join(blocks)


__all__ = [
    "AssessmentItem",
    "CourseIdentity",
    "CoursePlan",
    "LearningOutcome",
    "OutcomeKind",
    "WeekPlan",
    "render_course_plan",
    "week_label",
]
