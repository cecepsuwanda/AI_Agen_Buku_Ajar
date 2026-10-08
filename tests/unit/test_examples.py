"""Tes materi ajar terstruktur — contoh dan latihan (§19, §20) — MURNI.

Berkas ini menguji satu hal yang mudah terlewat karena bentuknya sepele:
**pemeriksaan yang dijalankan sebelum model dipanggil**. Setiap temuan yang
dihasilkan di sini menghemat satu putaran tangga perbaikan, dan setiap temuan
yang salah menolak bahan yang sebenarnya baik — sekaligus membakar dua
panggilan model plus satu skor rendah yang tidak berdasar.

Karena itu dua sisi diuji bersama-sama:

1. yang **harus** ditolak (jumlah kurang, label tingkat di luar daftar, kode
   tanpa penjelasan, contoh kembar), dan
2. yang **tidak boleh** ditolak — contoh tanpa kode (bab teori), latihan tanpa
   ``objective`` yang sudah dilaporkan sebagai temuan tersendiri, dan yang
   paling penting: latihan yang hanya **memparafrase** tujuan pembelajaran.
   Pemeriksaan teks terhadap daftar tujuan sengaja **tidak** ada di
   :func:`~domain.examples.inspect_exercises`; §23 menugaskan penilaian itu
   kepada Pedagogy Reviewer, dan tes terakhir di berkas ini mengunci pilihan itu
   supaya ia tidak "diperbaiki" kembali menjadi pencocokan string.
"""

from __future__ import annotations

from domain.examples import (
    DIFFICULTY_LEVELS,
    CodeExample,
    Exercise,
    describe_mix,
    difficulty_mix,
    inspect_examples,
    inspect_exercises,
    is_usable_example,
    is_usable_exercise,
    render_example,
    render_examples,
    render_exercise,
    render_exercises,
)

OBJECTIVE = "Mahasiswa mampu menghitung kompleksitas waktu algoritma sederhana"


def example(**overrides: object) -> CodeExample:
    """Contoh yang sah, dengan bagian yang dapat diatur per tes."""
    payload: dict[str, object] = {
        "title": "Pencarian linear",
        "language": "python",
        "code": "for item in data:\n    if item == target:\n        return True",
        "expected_output": "True",
        "explanation": "Pencarian linear memeriksa setiap elemen satu per satu.",
    }
    payload.update(overrides)
    return CodeExample.model_validate(payload)


def exercise(**overrides: object) -> Exercise:
    """Latihan yang sah, dengan bagian yang dapat diatur per tes."""
    payload: dict[str, object] = {
        "prompt": "Hitung kompleksitas waktu pencarian biner pada 1.000 elemen.",
        "difficulty": "sedang",
        "objective": OBJECTIVE,
        "hint": "Ingat bahwa ruang pencarian dibagi dua setiap langkah.",
        "answer": "O(log n).",
    }
    payload.update(overrides)
    return Exercise.model_validate(payload)


# ---------------------------------------------------------------------------
# is_usable_* — "ada isinya", bukan "ada kodenya"
# ---------------------------------------------------------------------------
def test_an_example_with_only_explanation_is_still_usable() -> None:
    """Bab teori dan matematika menghasilkan contoh tanpa kode sama sekali.

    Memeriksa keberadaan ``code`` akan menolak setiap contoh dari buku
    non-pemrograman — dan buku ajar yang tidak memuat satu pun contoh justru
    yang paling butuh ditolong.
    """
    assert is_usable_example(example(code="", expected_output="", language="")) is True


def test_an_example_with_only_whitespace_is_not_usable() -> None:
    assert is_usable_example(example(code="  \n", expected_output="", explanation="\t")) is False


def test_an_exercise_without_a_prompt_is_not_usable() -> None:
    """Soal yang tidak menanyakan apa pun bukan soal yang sulit — ia soal yang kosong."""
    assert is_usable_exercise(exercise(prompt="   \n ")) is False


def test_an_exercise_with_a_prompt_is_usable() -> None:
    assert is_usable_exercise(exercise()) is True


# ---------------------------------------------------------------------------
# inspect_examples — hanya hal yang dapat dipastikan
# ---------------------------------------------------------------------------
def test_enough_examples_pass_without_findings() -> None:
    enough, findings = inspect_examples([example(code=f"x = {n}") for n in range(3)], required=3)

    assert enough is True
    assert findings == ()


def test_a_shortfall_names_both_numbers() -> None:
    """Pesan "hanya 1 dari 3" dapat dikerjakan model; "contoh kurang" tidak."""
    enough, findings = inspect_examples([example()], required=3)

    assert enough is False
    assert any("1" in finding and "3" in finding for finding in findings)


def test_zero_required_examples_never_produce_a_shortfall() -> None:
    """Bab yang tidak meminta contoh tidak dapat "kekurangan" contoh."""
    enough, findings = inspect_examples([], required=0)

    assert enough is True
    assert findings == ()


def test_unusable_examples_do_not_count_towards_the_required_number() -> None:
    """Lima contoh kosong bukan lima contoh."""
    enough, _ = inspect_examples([example(code="", expected_output="", explanation="")], required=1)

    assert enough is False


def test_code_without_an_explanation_is_reported() -> None:
    """§19 meminta penjelasan: kode yang berdiri sendiri tidak mengajarkan apa pun."""
    enough, findings = inspect_examples(
        [example(explanation=""), example(code="x = 2", explanation="")], required=1
    )

    assert enough is False
    assert any("penjelasan" in finding for finding in findings)


def test_an_explanation_only_example_cannot_be_unexplained() -> None:
    """Tanpa kode, "tidak ada penjelasan" tidak punya arti — pemeriksaannya bicara soal kode."""
    enough, findings = inspect_examples(
        [example(code="", expected_output="", explanation="Algoritma adalah urutan langkah.")],
        required=1,
    )

    assert enough is True
    assert not any("penjelasan" in finding for finding in findings)


def test_identical_examples_are_reported_as_repeated() -> None:
    """Dua salinan persis adalah pengulangan yang membayar token dua kali."""
    twin = example()
    enough, findings = inspect_examples([twin, twin.model_copy()], required=2)

    assert enough is False
    assert any("berulang" in finding for finding in findings)


def test_examples_that_differ_only_in_title_are_still_repeated() -> None:
    """Judul bukan isi — yang dibandingkan adalah kode atau penjelasannya."""
    enough, _ = inspect_examples(
        [example(title="Contoh A"), example(title="Contoh B")], required=2
    )

    assert enough is False


# ---------------------------------------------------------------------------
# inspect_exercises — jumlah dan label, bukan penilaian
# ---------------------------------------------------------------------------
def test_difficulty_outside_the_known_levels_is_reported_with_the_valid_ones() -> None:
    """Label ini tercetak di buku, dan prompt sudah menyebut ketiga nilai yang sah.

    Pesannya harus memuat daftar yang sah: tanpa itu, model hanya tahu labelnya
    salah, bukan apa yang benar.
    """
    enough, findings = inspect_exercises([exercise(difficulty="medium")], required=1)

    assert enough is False
    finding = next(f for f in findings if "tingkat" in f)
    assert "medium" in finding
    for level in DIFFICULTY_LEVELS:
        assert level in finding


def test_the_known_levels_are_accepted_verbatim() -> None:
    """Termasuk ``sulit``, yang tidak muncul di contoh mana pun di berkas ini."""
    enough, findings = inspect_exercises(
        [exercise(difficulty=level) for level in DIFFICULTY_LEVELS], required=3
    )

    assert enough is True
    assert findings == ()


def test_an_exercise_without_an_objective_is_reported() -> None:
    """Ikatan ke tujuan itulah yang membuat §23 dapat memeriksa kesesuaiannya kelak."""
    enough, findings = inspect_exercises([exercise(objective="")], required=1)

    assert enough is False
    assert any("tujuan" in finding for finding in findings)


def test_a_shortfall_of_exercises_names_both_numbers() -> None:
    enough, findings = inspect_exercises([exercise()], required=5)

    assert enough is False
    assert any("1" in finding and "5" in finding for finding in findings)


def test_zero_required_exercises_never_produce_a_shortfall() -> None:
    enough, findings = inspect_exercises([], required=0)

    assert enough is True
    assert findings == ()


def test_a_paraphrased_objective_is_not_a_finding() -> None:
    """Keputusan yang sengaja, dan tes ini yang menjaganya tetap begitu.

    Membandingkan teks ``objective`` dengan daftar tujuan secara mekanis akan
    menolak latihan yang baik hanya karena model memparafrase tujuannya. Setiap
    penolakan seperti itu berharga dua panggilan model dan satu skor rendah yang
    tidak berdasar. §23 menugaskan penilaian ini kepada Pedagogy Reviewer.
    """
    paraphrased = exercise(objective="Menghitung Big-O untuk algoritma sederhana")

    enough, findings = inspect_exercises([paraphrased], required=1)

    assert enough is True, findings


# ---------------------------------------------------------------------------
# difficulty_mix — dihitung, bukan ditanyakan
# ---------------------------------------------------------------------------
def test_the_mix_is_ordered_from_easiest_to_hardest() -> None:
    """Urutannya mengikuti :data:`DIFFICULTY_LEVELS`, bukan urutan kemunculan."""
    mix = difficulty_mix(
        [
            exercise(prompt="a", difficulty="sulit"),
            exercise(prompt="b", difficulty="mudah"),
            exercise(prompt="c", difficulty="sedang"),
        ]
    )

    assert mix == (("mudah", 1), ("sedang", 1), ("sulit", 1))


def test_unusable_exercises_are_absent_from_the_mix() -> None:
    """Menghitung latihan kosong di sebaran akan melaporkan jangkauan yang tidak ada."""
    mix = difficulty_mix([exercise(prompt="a", difficulty="mudah"), exercise(prompt="  ")])

    assert mix == (("mudah", 1),)


def test_a_missing_level_is_collected_under_its_own_name() -> None:
    """"Tidak disebutkan" dilaporkan apa adanya, bukan disembunyikan atau ditebak."""
    mix = difficulty_mix([exercise(difficulty="")])

    assert mix == (("tidak disebutkan", 1),)


def test_unknown_levels_come_after_the_known_ones_sorted_by_name() -> None:
    """Hasilnya deterministik meski tingkatnya tidak dikenali."""
    mix = difficulty_mix(
        [
            exercise(prompt="a", difficulty="zeta"),
            exercise(prompt="b", difficulty="alpha"),
            exercise(prompt="c", difficulty="mudah"),
        ]
    )

    assert mix == (("mudah", 1), ("alpha", 1), ("zeta", 1))


def test_the_mix_is_stable_across_input_order() -> None:
    """Dua urutan masukan yang berbeda harus menghasilkan ringkasan yang sama."""
    first = exercise(prompt="a", difficulty="mudah")
    second = exercise(prompt="b", difficulty="sulit")

    assert difficulty_mix([first, second]) == difficulty_mix([second, first])


def test_describe_mix_reads_as_sentences_and_survives_an_empty_mix() -> None:
    assert describe_mix((("mudah", 3), ("sedang", 2))) == "3 mudah, 2 sedang"
    assert describe_mix(()) == "tidak ada latihan yang dapat dipakai"


# ---------------------------------------------------------------------------
# Rendering contoh
# ---------------------------------------------------------------------------
def test_a_render_includes_title_code_output_and_explanation() -> None:
    text = render_example(example())

    assert "**Pencarian linear**" in text
    assert "```python" in text
    assert "for item in data:" in text
    assert "Keluaran yang diharapkan:" in text
    assert "Pencarian linear memeriksa setiap elemen satu per satu." in text


def test_the_sections_appear_in_the_order_the_blueprint_sets() -> None:
    """§23: judul, kode, keluaran, penjelasan — sama di seluruh buku."""
    text = render_example(example())

    assert text.index("Pencarian linear") < text.index("for item in data:")
    assert text.index("for item in data:") < text.index("Keluaran yang diharapkan:")
    assert text.index("Keluaran yang diharapkan:") < text.index("memeriksa setiap elemen")


def test_code_that_contains_a_fence_gets_a_longer_fence() -> None:
    """Kode yang memuat ``` akan menutup bloknya lebih awal tanpa ini.

    Akibatnya bukan kesalahan kosmetik: sisa kode tumpah ke prosa, dan LaTeX
    yang dihasilkan kelak memuat teks yang tidak seharusnya ada di sana.
    """
    text = render_example(example(code="```\nnested\n```", expected_output=""))

    assert "````python" in text
    assert "\n````" in text


def test_a_language_with_extra_words_keeps_only_the_marker() -> None:
    """Model kadang menulis "python 3" atau "java pseudocode"."""
    assert "```python\n" in render_example(example(language="python 3"))
    assert "```java\n" in render_example(example(language="java pseudocode"))


def test_a_language_token_with_stray_punctuation_is_dropped() -> None:
    """Penanda yang rusak lebih buruk daripada tidak ada penanda."""
    text = render_example(example(language="python3.11"))

    assert "```python3" not in text
    assert "```\n" in text


def test_an_empty_example_renders_to_nothing_and_is_dropped_by_the_batch() -> None:
    """Judul ``Contoh 3.4`` yang menggantung tanpa isi lebih buruk daripada tidak ada."""
    empty = example(title="", code="", expected_output="", explanation="", language="")

    assert render_example(empty) == ""
    assert render_examples([empty, example()]) == (render_example(example()),)


# ---------------------------------------------------------------------------
# Rendering latihan
# ---------------------------------------------------------------------------
def test_the_answer_is_kept_inside_a_collapsed_block() -> None:
    """Bahan yang dibayar token tidak boleh dibuang, tetapi juga tidak boleh terlihat.

    Blok ``<details>`` menyelesaikan keduanya: isinya ada di sumber — dan
    terlihat oleh dosen yang meninjau LaTeX-nya — tetapi tertutup di penampil
    Markdown yang lazim.
    """
    text = render_exercise(exercise())

    assert "<details>" in text
    assert "<summary>Kunci jawaban</summary>" in text
    assert "O(log n)." in text
    assert text.index("<details>") < text.index("O(log n).")


def test_an_exercise_without_an_answer_has_no_details_block() -> None:
    """Blok kosong adalah janji kunci jawaban yang tidak ditepati."""
    text = render_exercise(exercise(answer=""))

    assert "<details>" not in text


def test_the_metadata_line_carries_level_and_objective() -> None:
    text = render_exercise(exercise())

    assert "Tingkat: sedang" in text
    assert f"menguji: {OBJECTIVE}" in text


def test_the_metadata_line_is_omitted_when_nothing_can_be_said() -> None:
    """Baris ``*; *`` adalah artefak, bukan keterangan."""
    text = render_exercise(exercise(difficulty="", objective=""))

    assert "*Tingkat:" not in text
    assert "menguji:" not in text


def test_the_hint_is_rendered_but_absent_when_empty() -> None:
    assert "*Petunjuk:* Ingat bahwa" in render_exercise(exercise())
    assert "Petunjuk" not in render_exercise(exercise(hint=""))


def test_an_exercise_without_a_prompt_is_dropped_by_the_batch() -> None:
    """Latihan kosong tidak pernah sampai ke buku.

    ``render_exercise`` tidak mengembalikan string kosong untuk butir seperti itu
    — ia hanya merender apa yang ada. Yang membuangnya adalah
    :func:`~domain.examples.render_exercises`, sebelum satu pun nomor latihan
    diberikan.
    """
    empty = exercise(prompt="   \n")

    assert is_usable_exercise(empty) is False
    assert render_exercises([empty, exercise()]) == (render_exercise(exercise()),)
