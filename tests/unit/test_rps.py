"""Tes pengurai RPS (§12) — tanpa model, tanpa berkas buatan.

RPS contoh yang di-commit ke repo diuji **apa adanya**: yang diperiksa jalur yang
benar-benar dipakai orang, dan berkas kecil buatan tes hanya akan membuktikan lebih
sedikit tentangnya. Berkas itu memuat dua minggu ujian di tengah dan di ujung
kalender, tujuh label identitas, tabel penilaian berbobot, dan sebuah bagian yang
tidak dikenali pengurai ("Catatan Penyusunan Buku") — semuanya kasus yang harus
benar, dan semuanya sudah ada di sana.

Sisanya adalah RPS **cacat**: tanpa CPMK, minggu tidak berurutan, minggu kembar,
bobot yang tidak berjumlah 100%. Yang diuji bukan bahwa pengurainya menolak
berkas-berkas itu — ia tidak menolak apa pun — melainkan bahwa ia **melaporkan**
apa yang janggal alih-alih menebaknya. Parser yang diam tentang RPS yang tidak
terbaca adalah parser yang membuat seluruh rencana buku berdiri di atas bacaan
yang salah.
"""

from __future__ import annotations

from pathlib import Path

from domain.rps import (
    CoursePlan,
    OutcomeKind,
    render_course_plan,
    week_label,
)
from ingestion.rps_loader import (
    clean_items,
    clean_latex,
    normalize_spaces,
    parse_identity,
    parse_outcomes,
    parse_rps,
    parse_weeks,
    split_sections,
    strip_comments,
    table_rows,
)
from tests.conftest import PROJECT_ROOT

#: RPS contoh yang di-track git — bahan sungguhan, bukan berkas buatan tes.
RPS_PATH = PROJECT_ROOT / "input" / "rps" / "rps.tex"


def _real_rps() -> tuple[CoursePlan, tuple[str, ...]]:
    """RPS contoh, diuraikan sekali per pemanggilan."""
    return parse_rps(RPS_PATH.read_text(encoding="utf-8"))


def _cpmk(count: int) -> str:
    """Bagian CPMK dengan ``count`` butir bernomor."""
    items = "".join(f"  \\item CPMK-{n}: Mahasiswa mampu melakukan hal ke-{n}.\n"
                    for n in range(1, count + 1))
    title = "Capaian Pembelajaran Mata Kuliah (CPMK)"
    return f"\\section*{{{title}}}\n" + f"\\begin{{enumerate}}\n{items}\\end{{enumerate}}\n"


def _weeks(*numbers: int) -> str:
    """Bagian rencana mingguan dari daftar nomor minggu."""
    items = "".join(f"  \\item Minggu {n} - Topik minggu {n}.\n" for n in numbers)
    return f"\\section*{{Rencana Mingguan}}\n\\begin{{enumerate}}\n{items}\\end{{enumerate}}\n"


def _sub_cpmk(*codes: str) -> str:
    """Bagian Sub-CPMK dari daftar kode, mis. ``_sub_cpmk("Sub-CPMK-3.1")``."""
    items = "".join(f"  \\item {code}: Sesuatu yang dapat dinilai.\n" for code in codes)
    title = "Sub-Capaian Pembelajaran Mata Kuliah (Sub-CPMK)"
    return f"\\section*{{{title}}}\n\\begin{{enumerate}}\n{items}\\end{{enumerate}}\n"


# ---------------------------------------------------------------------------
# RPS contoh: identitas
# ---------------------------------------------------------------------------
def test_the_identity_table_is_read_field_by_field() -> None:
    """Tujuh baris tabel menjadi tujuh bidang — bukan satu bidang panjang.

    Kesalahan yang pernah terjadi di sini halus: seluruh tabel terbaca sebagai satu
    baris raksasa, dan identitasnya muncul sebagai satu string berisi semua label
    dan semua nilainya. Tidak ada galat apa pun yang dihasilkan — hanya prompt
    perencana yang berisi sampah.
    """
    plan, _ = _real_rps()

    assert plan.identity.name == "Algoritma dan Struktur Data"
    assert plan.identity.code == "IF1234"
    assert plan.identity.semester == "3"
    assert plan.identity.programme == "Informatika"
    assert plan.identity.prerequisites == "Dasar Pemrograman"
    assert plan.identity.lecturer == "Tim Pengampu"


def test_the_credits_text_is_kept_as_written() -> None:
    """``"3 SKS (2 teori, 1 praktikum)"`` tidak disederhanakan menjadi angka.

    Mengubahnya menjadi ``3`` akan membuang perbedaan teori/praktikum — yang justru
    menentukan berapa banyak bab praktikum yang perlu ditulis.
    """
    plan, _ = _real_rps()

    assert plan.identity.credits == "3 SKS (2 teori, 1 praktikum)"


def test_every_identity_row_survives_into_the_summary() -> None:
    """Ringkasan hanya boleh memuat baris yang memang terbaca — dan seluruhnya."""
    plan, _ = _real_rps()

    assert len(plan.identity.rows()) == 7


def test_an_unknown_identity_label_is_kept_in_extra() -> None:
    """Label yang tidak dikenal modul ini tidak dibuang.

    Menjatuhkannya berarti RPS yang memuat "Beban Praktikum" atau "Kode Kelas"
    kehilangan isinya hanya karena modul ini belum mengenal namanya.
    """
    text = (
        "\\section*{Identitas Mata Kuliah}\n"
        "\\begin{tabular}{ll}\n"
        "Mata Kuliah & Jaringan Komputer \\\\\n"
        "Kode Kelas & JK-A \\\\\n"
        "\\end{tabular}\n"
    )

    identity = parse_identity(split_sections(text)[0][1])

    assert identity.name == "Jaringan Komputer"
    assert identity.extra == (("Kode Kelas", "JK-A"),)


def test_an_rps_without_identity_is_reported_not_refused() -> None:
    """RPS tanpa identitas tetap diuraikan, dan kekosongan itu disebut."""
    text = _weeks(1, 2, 3)

    plan, notes = parse_rps(text)

    assert plan.identity.is_empty()
    assert any("identitas" in note for note in notes)


# ---------------------------------------------------------------------------
# RPS contoh: capaian pembelajaran
# ---------------------------------------------------------------------------
def test_the_five_cpmk_are_read_with_their_codes() -> None:
    """Kode CPMK adalah hal yang membuat capaian dapat dirujuk bab mana pun."""
    plan, _ = _real_rps()

    assert [outcome.code for outcome in plan.cpmk()] == [
        "CPMK-1",
        "CPMK-2",
        "CPMK-3",
        "CPMK-4",
        "CPMK-5",
    ]


def test_the_eight_sub_cpmk_are_read_and_kept_separate_from_cpmk() -> None:
    """Sub-CPMK dan CPMK dibedakan ``kind``, bukan oleh judul bagiannya.

    Judul keduanya memuat kata "CPMK"; pengurai yang mencocokkan judul secara
    longgar akan menggabungkan keduanya, dan perencana kehilangan pembedaan antara
    kompetensi satu semester dan kompetensi satu pertemuan.
    """
    plan, _ = _real_rps()

    assert len(plan.sub_cpmk()) == 8
    assert all(outcome.kind is OutcomeKind.SUB_CPMK for outcome in plan.sub_cpmk())
    assert plan.sub_cpmk()[0].code == "Sub-CPMK-1.1"


def test_cpl_without_codes_is_still_kept_and_reported() -> None:
    """CPL contoh tidak berkode. Ia tetap berguna sebagai konteks, tetapi tidak dapat dirujuk.

    Karena itu ia diterima dengan ``code=""`` **dan** dilaporkan — bukan dibuang
    (hilang sebagai konteks) dan bukan diberi kode karangan (tampak dapat dirujuk
    padahal tidak).
    """
    plan, notes = _real_rps()

    assert len(plan.cpl()) == 3
    assert all(not outcome.code for outcome in plan.cpl())
    assert any("CPL" in note for note in notes)


def test_a_cpmk_without_a_code_pattern_is_kept_with_an_empty_code() -> None:
    outcomes, notes = parse_outcomes(
        "\\begin{enumerate}\n  \\item Memahami sesuatu.\n  \\item CPMK-2: Lain.\n"
        "\\end{enumerate}\n",
        kind=OutcomeKind.CPMK,
    )

    assert [outcome.code for outcome in outcomes] == ["", "CPMK-2"]
    assert any("CPMK" in note for note in notes)


def test_an_item_spanning_two_lines_is_joined() -> None:
    """Butir RPS terpotong di tengah kalimat karena lebar halaman.

    Mengambil baris pertama saja akan memenggal setiap CPMK pada kata ke-delapan,
    dan perencana akan menyusun buku dari separuh kalimat.
    """
    outcomes, _ = parse_outcomes(
        "\\begin{enumerate}\n"
        "  \\item CPMK-1: Mahasiswa mampu menganalisis kompleksitas\n"
        "        waktu dan ruang sebuah algoritma.\n"
        "\\end{enumerate}\n",
        kind=OutcomeKind.CPMK,
    )

    assert outcomes[0].statement == (
        "Mahasiswa mampu menganalisis kompleksitas waktu dan ruang sebuah algoritma."
    )


# ---------------------------------------------------------------------------
# RPS contoh: kalender mingguan
# ---------------------------------------------------------------------------
def test_the_sixteen_weeks_are_read_in_source_order() -> None:
    plan, _ = _real_rps()

    assert [week.number for week in plan.weeks] == list(range(1, 17))


def test_the_two_exam_weeks_are_marked_as_assessment() -> None:
    """Minggu 8 dan 16 adalah ujian — bukan bahan bab.

    Tanpa penandaan ini, perencana yang taat pada daftar minggu akan menghasilkan
    bab "Ujian Tengah Semester", dan penulis akan menulisnya.
    """
    plan, _ = _real_rps()

    assert [week.number for week in plan.assessment_weeks()] == [8, 16]
    assert plan.assessment_weeks()[0].topic == "Ujian Tengah Semester."


def test_teaching_weeks_exclude_the_exam_weeks() -> None:
    plan, _ = _real_rps()

    assert [week.number for week in plan.teaching_weeks()] == [
        1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15,
    ]


def test_a_week_topic_keeps_its_own_text() -> None:
    """Topik minggu adalah bahan yang dibaca perencana; ia tidak boleh dipotong."""
    plan, _ = _real_rps()

    assert plan.weeks[0].topic.startswith("Kontrak kuliah; pengantar struktur data")


def test_a_quiz_week_stays_a_teaching_week() -> None:
    """Hanya "ujian" yang menandai minggu penilaian — sengaja sesempit itu.

    Minggu yang memuat kuis atau presentasi masih punya bahan untuk ditulis;
    menandainya sebagai minggu penilaian akan menghapus bahan itu dari buku.
    """
    weeks, _ = parse_weeks(
        "\\begin{enumerate}\n"
        "  \\item Minggu 1 - Kuis singkat dan pembahasan materi.\n"
        "  \\item Minggu 2 - Ujian Tengah Semester.\n"
        "\\end{enumerate}\n"
    )

    assert [week.is_assessment for week in weeks] == [False, True]


def test_a_week_out_of_order_is_reported_and_the_source_order_kept() -> None:
    """Mengurutkan ulang akan menyembunyikan kekacauan yang justru perlu dilihat."""
    weeks, notes = parse_weeks(_weeks_body("1, 2, 5, 3, 4"))

    assert [week.number for week in weeks] == [1, 2, 5, 3, 4]
    assert any("melompat" in note for note in notes)


def test_a_duplicated_week_is_reported_and_the_first_kept() -> None:
    """Minggu kembar berarti materi yang sama dijanjikan dua kali."""
    weeks, notes = parse_weeks(_weeks_body("1, 2, 2"))

    assert [week.number for week in weeks] == [1, 2]
    assert any("lebih dari sekali" in note for note in notes)


def test_a_week_item_that_does_not_match_the_pattern_is_reported() -> None:
    """Butir yang tidak terbaca tidak boleh hilang diam-diam."""
    weeks, notes = parse_weeks(
        "\\begin{enumerate}\n"
        "  \\item Pertemuan pertama: kontrak kuliah.\n"
        "  \\item Minggu 2 - Kompleksitas.\n"
        "\\end{enumerate}\n"
    )

    assert [week.number for week in weeks] == [2]
    assert any("pola" in note for note in notes)


def test_an_rps_without_weeks_is_reported() -> None:
    """Tanpa kalender, bab tidak dapat dipetakan ke minggu — dan itu disebut."""
    plan, notes = parse_rps("\\section*{Identitas Mata Kuliah}\nKode & X \\\\\n")

    assert plan.weeks == ()
    assert any("minggu" in note for note in notes)


def _weeks_body(comma_separated: str) -> str:
    """Isi bagian rencana mingguan dari daftar nomor, mis. ``"1, 2, 2"``."""
    numbers = [int(part.strip()) for part in comma_separated.split(",")]
    items = "".join(f"  \\item Minggu {n} - Topik {n}.\n" for n in numbers)
    return f"\\begin{{enumerate}}\n{items}\\end{{enumerate}}\n"


# ---------------------------------------------------------------------------
# RPS contoh: penilaian, referensi, bagian lain
# ---------------------------------------------------------------------------
def test_the_assessment_table_is_read_with_weights_and_outcomes() -> None:
    plan, _ = _real_rps()

    assert [item.activity for item in plan.assessments][0] == "Tugas dan kuis"
    assert plan.assessments[0].weight == "20%"
    assert plan.assessments[0].outcomes == ("CPMK-1", "CPMK-2")
    assert len(plan.assessments) == 5


def test_the_assessment_header_row_is_not_read_as_an_item() -> None:
    """Baris ``Aktivitas & Bobot & CPMK`` adalah judul, bukan komponen penilaian."""
    plan, _ = _real_rps()

    assert "Aktivitas" not in [item.activity for item in plan.assessments]


def test_a_weight_that_does_not_add_up_to_one_hundred_is_reported() -> None:
    """Tabel yang jumlahnya bukan 100% biasanya berarti ada baris yang tidak terbaca."""
    text = (
        "\\section*{Metode Penilaian}\n"
        "\\begin{tabular}{lll}\n"
        "Aktivitas & Bobot & CPMK \\\\\n"
        "Tugas & 20\\% & CPMK-1 \\\\\n"
        "\\end{tabular}\n"
    )

    _, notes = parse_rps(text)

    assert any("20%" in note and "100%" in note for note in notes)


def test_a_weight_that_is_not_a_percentage_produces_no_weight_note() -> None:
    """RPS yang menulis bobot sebagai "1 SKS" tidak salah — ia hanya lain bentuk."""
    text = (
        "\\section*{Metode Penilaian}\n"
        "\\begin{tabular}{lll}\n"
        "Aktivitas & Bobot & CPMK \\\\\n"
        "Praktikum & 1 SKS & CPMK-1 \\\\\n"
        "\\end{tabular}\n"
    )

    _, notes = parse_rps(text)

    assert not any("100%" in note for note in notes)


def test_the_references_are_read_without_latex_markup() -> None:
    """``\\emph{}`` dan ``\\&`` adalah markup, bukan bagian dari judul buku."""
    plan, _ = _real_rps()

    assert plan.references[0] == (
        "Cormen, T. H., Leiserson, C. E., Rivest, R. L., & Stein, C. "
        "Introduction to Algorithms. MIT Press."
    )
    assert len(plan.references) == 4


def test_an_unrecognised_section_is_kept_under_its_own_title() -> None:
    """"Catatan Penyusunan Buku" memuat ketentuan penting tentang isi tiap bab.

    Pengurai yang hanya menyimpan apa yang dikenalnya akan menjatuhkan ketentuan
    itu tanpa satu pun tanda — dan perencana kehilangan instruksi "minimal tiga
    contoh bertingkat" yang justru ditulis penyusun RPS.
    """
    plan, _ = _real_rps()

    titles = [title for title, _ in plan.other_sections]
    assert titles == ["Catatan Penyusunan Buku"]
    assert "minimal tiga contoh bertingkat" in plan.other_sections[0][1]


def test_an_unrecognised_section_is_not_a_problem() -> None:
    """Bagian yang tidak dikenal bukan cacat; ia hanya belum punya tafsir khusus."""
    plan, notes = _real_rps()

    assert plan.other_sections
    assert not any("Catatan Penyusunan Buku" in note for note in notes)


def test_a_sub_cpmk_without_its_parent_cpmk_is_reported() -> None:
    """Pemetaan menggantung menghasilkan bab yang mengaku menutup capaian yang tidak ada."""
    _, notes = parse_rps(_cpmk(1) + _weeks(1, 2) + _sub_cpmk("Sub-CPMK-3.1"))

    assert any("Sub-CPMK-3.1" in note and "induk" in note for note in notes)


def test_a_sub_cpmk_with_its_parent_present_is_not_reported() -> None:
    _, notes = parse_rps(_cpmk(3) + _weeks(1, 2) + _sub_cpmk("Sub-CPMK-3.1"))

    assert not any("induk" in note for note in notes)


# ---------------------------------------------------------------------------
# RPS cacat dan kosong
# ---------------------------------------------------------------------------
def test_a_file_without_any_section_yields_an_empty_plan_and_says_so() -> None:
    """Bukan pengecualian: pemanggil yang memutuskan, dan ia butuh tahu.

    Rencana kosong yang dikirim sebagai pengganti RPS akan membuat perencana
    menyusun buku dari nol — tanpa satu pun tanda bahwa RPS-nya tidak terbaca.
    """
    plan, notes = parse_rps("Ini hanya paragraf biasa tanpa bagian apa pun.")

    assert plan.is_empty()
    assert any("\\section" in note for note in notes)


def test_an_empty_file_is_not_a_crash() -> None:
    plan, notes = parse_rps("")

    assert plan.is_empty()
    assert notes


def test_the_preamble_comment_does_not_reach_the_summary() -> None:
    """Komentar RPS contoh menyebut "parser terstruktur menyusul di tahap berikutnya".

    Catatan penulis berkas itu bukan isi RPS, dan mengirimnya ke perencana berarti
    mengirim instruksi yang tidak dimaksudkan untuknya.
    """
    plan, _ = _real_rps()

    assert "menyusul di Tahap" not in render_course_plan(plan)


def test_a_percent_escape_is_not_mistaken_for_a_comment() -> None:
    """``\\%`` adalah persen; ``%`` adalah awal komentar. Membedakannya menentukan
    apakah bobot penilaian terbaca atau hilang."""
    assert strip_comments("Bobot 20\\% dari nilai akhir") == "Bobot 20\\% dari nilai akhir"
    assert strip_comments("Bobot 20 % komentar") == "Bobot 20 "


def test_a_trailing_comment_is_removed_but_the_code_line_survives() -> None:
    assert strip_comments("Kode & IF1234 \\\\ % ini komentar").strip() == "Kode & IF1234 \\\\"


# ---------------------------------------------------------------------------
# Pembersihan LaTeX (murni)
# ---------------------------------------------------------------------------
def test_emphasis_keeps_its_content_and_loses_its_wrapper() -> None:
    assert clean_latex("\\emph{Introduction to Algorithms}") == "Introduction to Algorithms"


def test_escaped_ampersand_becomes_a_plain_one() -> None:
    assert clean_latex("Rivest, R. L., \\& Stein, C.") == "Rivest, R. L., & Stein, C."


def test_structural_commands_and_column_specs_are_dropped() -> None:
    """``\\begin{tabular}{ll}`` yang tersisa sebagai ``{ll}`` akan menjadi komponen
    penilaian bernama "{ll}"."""
    assert clean_latex("\\begin{tabular}{ll}\nA & B\n\\end{tabular}").strip() == "A & B"


def test_row_breaks_become_line_breaks_not_spaces() -> None:
    """Tanpa itu, seluruh tabel identitas menjadi satu baris dan batas barisnya hilang."""
    lines = clean_latex("A & B \\\\\nC & D").splitlines()

    assert "A & B" in lines
    assert "C & D" in lines


def test_items_split_on_item_and_join_their_lines() -> None:
    items = clean_items(
        "\\begin{enumerate}\n"
        "  \\item Satu\n        lanjutannya.\n"
        "  \\item Dua.\n"
        "\\end{enumerate}\n"
    )

    assert items == ("Satu lanjutannya.", "Dua.")


def test_text_before_the_first_item_is_not_an_item() -> None:
    """Kalimat pengantar sebelum daftar bukan butir pertama."""
    assert clean_items("Pengantar singkat.\n\\item Hanya ini.\n") == ("Hanya ini.",)


def test_table_rows_split_on_ampersand_and_lines() -> None:
    rows = table_rows("\\begin{tabular}{lll}\nA & B & C \\\\\nD & E & F \\\\\n\\end{tabular}\n")

    assert rows == (("A", "B", "C"), ("D", "E", "F"))


def test_normalize_spaces_collapses_runs_without_touching_order() -> None:
    assert normalize_spaces("a   b\n\n  c  ") == "a b\n\nc"


def test_sections_are_split_with_their_titles() -> None:
    sections = split_sections("\\section*{Satu}\nisi satu\n\\section{Dua}\nisi dua\n")

    assert sections == (("Satu", "\nisi satu\n"), ("Dua", "\nisi dua\n"))


# ---------------------------------------------------------------------------
# render_course_plan — apa yang dibaca perencana
# ---------------------------------------------------------------------------
def test_the_summary_carries_the_week_numbers() -> None:
    """Nomor minggu adalah alasan ringkasan ini ada: perencana tidak perlu menebaknya."""
    plan, _ = _real_rps()

    summary = render_course_plan(plan)

    assert "- Minggu 9: Pohon seimbang" in summary
    assert "- Minggu 16: Ujian Akhir Semester." in summary


def test_the_summary_marks_the_assessment_weeks() -> None:
    """Penanda ini yang mencegah bab "Ujian Tengah Semester" lahir."""
    plan, _ = _real_rps()

    summary = render_course_plan(plan)

    assert "[MINGGU PENILAIAN — bukan bahan bab]" in summary
    assert summary.count("[MINGGU PENILAIAN") == 2


def test_the_summary_states_how_many_weeks_are_teaching_weeks() -> None:
    """Perencana perlu tahu berapa minggu yang harus ditutup, bukan hanya daftarnya."""
    plan, _ = _real_rps()

    assert "14 minggu kuliah, 2 minggu penilaian." in render_course_plan(plan)


def test_the_summary_carries_codes_so_the_planner_can_reference_them() -> None:
    plan, _ = _real_rps()

    summary = render_course_plan(plan)

    assert "- CPMK-1: Mahasiswa mampu menganalisis" in summary
    assert "- Sub-CPMK-1.1: Menjelaskan definisi" in summary


def test_the_summary_carries_every_section_of_the_rps() -> None:
    """Ringkasan menggantikan RPS mentah, jadi ia tidak boleh kehilangan bagian."""
    plan, _ = _real_rps()

    summary = render_course_plan(plan)

    for heading in (
        "## Identitas Mata Kuliah",
        "## Deskripsi Mata Kuliah",
        "## Capaian Pembelajaran Lulusan (CPL)",
        "## Capaian Pembelajaran Mata Kuliah (CPMK)",
        "## Sub-Capaian Pembelajaran Mata Kuliah (Sub-CPMK)",
        "## Kalender Mingguan",
        "## Metode Penilaian",
        "## Referensi",
        "## Catatan Penyusunan Buku",
    ):
        assert heading in summary, heading


def test_the_summary_of_an_empty_plan_says_it_is_empty() -> None:
    """Ringkasan kosong yang dikirim sebagai pengganti RPS jauh lebih buruk daripada
    kalimat yang mengatakannya."""
    assert render_course_plan(CoursePlan()) == "(RPS tidak memuat bagian yang dapat dikenali.)"


# ---------------------------------------------------------------------------
# week_label — label yang tidak pernah melebih-lebihkan cakupan
# ---------------------------------------------------------------------------
def test_a_single_week_is_its_own_label() -> None:
    assert week_label([3]) == "Minggu 3"


def test_consecutive_weeks_become_a_range() -> None:
    """Bentuk yang dipakai §12: "Chapter 1 → Week 1-2"."""
    assert week_label([1, 2]) == "Minggu 1-2"
    assert week_label([3, 4, 5]) == "Minggu 3-5"


def test_a_group_split_by_an_exam_week_is_not_written_as_one_range() -> None:
    """``[7, 9]`` bukan "Minggu 7-9": itu akan menyatakan minggu 8 sebagai materi bab."""
    assert week_label([7, 9]) == "Minggu 7, 9"


def test_several_runs_are_written_as_several_ranges() -> None:
    assert week_label([1, 2, 3, 4, 5, 6, 7, 9, 10]) == "Minggu 1-7, 9-10"


def test_no_weeks_means_no_label() -> None:
    assert week_label([]) == ""


def test_the_label_is_never_sorted() -> None:
    """Menyortir akan menyembunyikan minggu yang tidak berurutan — justru gejala
    yang perlu dilihat."""
    assert week_label([5, 3]) == "Minggu 5, 3"


def test_the_real_rps_path_exists() -> None:
    """Tes ini membaca berkas yang di-track git; bila berkasnya hilang, ia harus
    gagal di sini dengan pesan yang jelas, bukan di dalam pengurainya."""
    assert isinstance(RPS_PATH, Path) and RPS_PATH.is_file()
