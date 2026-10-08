"""Materi ajar terstruktur — contoh dan latihan (§19, §20) — MURNI.

**Mengapa tipe terstruktur, bukan ``str``.** §19 tidak berhenti pada "hasilkan
contoh": ia menaruh *Code Reviewer* sesudah Example Agent. Memeriksa contoh
berarti bertanya "apakah contoh ini benar-benar memakai konsep yang dimaksud",
"apakah keluarannya sesuai", "apakah ada contoh yang sama dua kali" — dan
pertanyaan itu hanya dapat diajukan bila contohnya punya bagian yang bernama.
Teks bebas memaksa setiap pemeriksaan menjadi tebak-tebakan atas prosa.

**Mengapa hasilnya tetap dirender menjadi ``str``.** ``ChapterDraft.examples``
bertipe ``tuple[str, ...]``, dan itu **tidak** diubah di sini. Alasannya
mekanis: skema keluaran penulis dibangkitkan otomatis dari tipe itu
(``render_output_contract`` + ``strict_schema``, yang menandai **semua**
properti sebagai wajib). Mengubahnya menjadi tipe terstruktur berarti
memaksa Chapter Writer (§18) ikut menghasilkan ``language``, ``code``,
``expected_output``, dan ``explanation`` untuk setiap contoh — pekerjaan yang
justru menjadi tanggung jawab Example Agent (§19). Jadi gate-nya yang
menerjemahkan: materi terstruktur masuk, Markdown jadi, draf diperkaya.

**Mengapa tingkat kesulitan dihitung, bukan ditanyakan.** §20 meminta latihan
menjangkau beberapa tingkat kesulitan. Model yang mengisi sendiri field
``difficulty_mix`` akan menuliskannya sebagai *ringkasan* — dan ringkasan yang
dihitung dari data yang sama selalu lebih benar daripada ringkasan yang
dikarang. Karena itu :func:`difficulty_mix` adalah fungsi murni atas butir
latihan yang benar-benar dihasilkan, dan hasilnya **dilaporkan sebagai temuan**
oleh gate — bukan disimpan sebagai field yang bisa berbohong.
"""

from __future__ import annotations

from typing import Sequence

from domain.base import FrozenModel

#: Tingkat kesulitan yang dikenali, dari termudah.
#:
#: Daftar ini dipakai untuk dua hal sekaligus: memeriksa keluaran model
#: (:func:`inspect_exercises`) dan mengurutkan :func:`difficulty_mix`. Karena itu
#: ia berurutan — "mudah" lebih dulu — dan bukan ``frozenset``.
DIFFICULTY_LEVELS: tuple[str, ...] = ("mudah", "sedang", "sulit")


class CodeExample(FrozenModel):
    """Satu contoh beserta penjelasannya (§19).

    ``title`` ada di luar daftar field yang disebut blueprint karena perender
    membutuhkannya: ``render_chapter_markdown`` sudah mencetak ``Contoh 3.2``
    dari nomor bab dan urutannya, tetapi ia tidak tahu contoh itu **tentang
    apa** — dan "Contoh 3.2" tanpa keterangan adalah judul yang tidak memberi
    informasi apa pun kepada mahasiswa.
    """

    title: str = ""
    language: str = ""
    code: str = ""
    expected_output: str = ""
    explanation: str = ""


class ExampleSet(FrozenModel):
    """Keluaran Example Agent (§19).

    ``notes`` adalah tempat model melaporkan apa yang **tidak** dapat ia
    kerjakan — misalnya karena spesifikasi bab tidak menyebut satu pun operasi
    yang dapat dicontohkan. Catatan itu diteruskan apa adanya ke ``feedback``
    gate, sehingga kekurangan bahan terlihat sebagai kekurangan bahan, bukan
    sebagai contoh yang kebetulan sedikit.
    """

    examples: tuple[CodeExample, ...] = ()
    notes: tuple[str, ...] = ()


class Exercise(FrozenModel):
    """Satu butir latihan (§20).

    ``objective`` mengikat latihan pada tujuan pembelajaran yang dilayaninya.
    Ikatan itu yang membuat §23 ("latihan tidak sesuai tujuan") dapat diperiksa
    di kemudian hari tanpa menebak dari teks soal.
    """

    prompt: str = ""
    difficulty: str = ""
    objective: str = ""
    hint: str = ""
    answer: str = ""


class ExerciseSet(FrozenModel):
    """Keluaran Exercise Agent (§20)."""

    exercises: tuple[Exercise, ...] = ()
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Pemeriksaan — deterministik, tanpa model (§keputusan 7)
# ---------------------------------------------------------------------------
def is_usable_example(example: CodeExample) -> bool:
    """True bila contoh ini memuat sesuatu yang dapat dicetak (MURNI).

    Contoh **tanpa kode tetap sah**: bab teori atau matematika menghasilkan
    contoh yang seluruhnya berupa penjelasan. Karena itu yang diukur di sini
    adalah "ada isinya", bukan "ada kodenya" — memeriksa kode akan menolak
    setiap contoh dari buku non-pemrograman.
    """
    return bool(example.code.strip() or example.explanation.strip())


def is_usable_exercise(exercise: Exercise) -> bool:
    """True bila butir latihan ini benar-benar menanyakan sesuatu (MURNI)."""
    return bool(exercise.prompt.strip())


def difficulty_mix(
    exercises: Sequence[Exercise],
) -> tuple[tuple[str, int], ...]:
    """Sebaran tingkat kesulitan, terurut menurut :data:`DIFFICULTY_LEVELS` (MURNI).

    Tingkat yang tidak dikenali dikumpulkan di bawah namanya sendiri, apa
    adanya, alih-alih dibuang: menyembunyikannya akan membuat laporan
    "3 mudah, 2 sedang" terlihat rapi padahal ada butir yang tingkatnya tidak
    dapat dipercaya.
    """
    counts: dict[str, int] = {}
    for exercise in exercises:
        if not is_usable_exercise(exercise):
            continue
        level = exercise.difficulty.strip().lower()
        if not level:
            level = "tidak disebutkan"
        counts[level] = counts.get(level, 0) + 1

    def order(level: str) -> tuple[int, str]:
        # Tingkat yang dikenal lebih dulu, berurutan; sisanya di belakang,
        # diurutkan menurut namanya agar hasilnya deterministik.
        if level in DIFFICULTY_LEVELS:
            return (DIFFICULTY_LEVELS.index(level), "")
        return (len(DIFFICULTY_LEVELS), level)

    return tuple(sorted(counts.items(), key=lambda item: order(item[0])))


def inspect_examples(
    examples: Sequence[CodeExample],
    *,
    required: int,
) -> tuple[bool, tuple[str, ...]]:
    """Periksa sekumpulan contoh, tanpa memanggil model (MURNI).

    Yang diperiksa hanya hal-hal yang **dapat dipastikan**: jumlah, contoh
    berkode tanpa penjelasan, dan kode yang muncul dua kali. Pertanyaan "apakah
    contoh ini benar" tidak ada di sini — itu pekerjaan peninjau, dan menebaknya
    secara mekanis akan menolak contoh yang baik karena alasan yang salah.

    :returns: ``(cukup, temuan)``. ``cukup`` bernilai True bila tidak ada temuan.
    """
    findings: list[str] = []
    usable = [example for example in examples if is_usable_example(example)]

    if required > 0 and len(usable) < required:
        findings.append(
            f"hanya {len(usable)} dari {required} contoh yang diminta dapat dipakai"
        )

    unexplained = sum(
        1 for example in usable if example.code.strip() and not example.explanation.strip()
    )
    if unexplained:
        findings.append(f"{unexplained} contoh kode tidak memiliki penjelasan")

    seen: set[str] = set()
    repeated = 0
    for example in usable:
        key = example.code.strip() or example.explanation.strip()
        if key in seen:
            repeated += 1
        seen.add(key)
    if repeated:
        findings.append(f"{repeated} contoh berulang persis dengan contoh sebelumnya")

    return (not findings, tuple(findings))


def inspect_exercises(
    exercises: Sequence[Exercise],
    *,
    required: int,
) -> tuple[bool, tuple[str, ...]]:
    """Periksa sekumpulan latihan, tanpa memanggil model (MURNI).

    **Yang tidak diperiksa di sini: apakah latihan benar-benar sesuai dengan
    tujuannya.** Itu pertanyaan penilaian, bukan pemeriksaan — menebaknya
    secara mekanis (mis. dengan membandingkan teks ``objective`` terhadap
    daftar tujuan) akan menolak latihan yang baik hanya karena model
    memparafrase tujuannya, dan setiap penolakan seperti itu berharga dua
    panggilan model serta satu skor rendah yang tidak berdasar. §23 menugaskan
    penilaian itu kepada Pedagogy Reviewer, dan di sanalah ia akan diperiksa.

    Yang tersisa di sini adalah hal-hal yang **tidak ambigu**: jumlah latihan
    yang dapat dipakai, dan tingkat kesulitan yang berada di luar daftar
    :data:`DIFFICULTY_LEVELS`. Yang kedua memang tampak sepele, tetapi label
    itulah yang tercetak di buku, dan prompt sudah menyebutkan ketiga nilai
    yang sah secara harfiah.

    :returns: ``(cukup, temuan)``.
    """
    findings: list[str] = []
    usable = [exercise for exercise in exercises if is_usable_exercise(exercise)]

    if required > 0 and len(usable) < required:
        findings.append(
            f"hanya {len(usable)} dari {required} latihan yang diminta dapat dipakai"
        )

    unknown_levels = sorted(
        {
            exercise.difficulty.strip().lower()
            for exercise in usable
            if exercise.difficulty.strip()
            and exercise.difficulty.strip().lower() not in DIFFICULTY_LEVELS
        }
    )
    if unknown_levels:
        findings.append(
            "tingkat kesulitan di luar daftar yang dikenal: "
            + ", ".join(unknown_levels)
            + " (yang sah: "
            + ", ".join(DIFFICULTY_LEVELS)
            + ")"
        )

    unlabelled = sum(1 for exercise in usable if not exercise.objective.strip())
    if unlabelled:
        findings.append(f"{unlabelled} latihan tidak menyebut tujuan pembelajaran yang diuji")

    return (not findings, tuple(findings))


# ---------------------------------------------------------------------------
# Rendering — materi terstruktur menjadi Markdown (§19, §20)
# ---------------------------------------------------------------------------
def _fence(code: str) -> str:
    """Pagar kode yang aman untuk ``code`` (MURNI).

    Blok kode Markdown ditutup oleh deretan backtick; kode yang **memuat**
    deretan seperti itu akan menutup bloknya lebih awal dan menumpahkan sisa
    kode ke prosa. Karena itu pagarnya selalu satu backtick lebih panjang
    daripada deretan terpanjang di dalam kode — bukan selalu tiga.
    """
    longest = 0
    run = 0
    for char in code:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _fence_language(language: str) -> str:
    """Nama bahasa untuk pagar kode, dibersihkan dari karakter yang merusaknya (MURNI).

    Model kadang menulis ``"Python 3"`` atau ``"java, pseudocode"``. Yang
    diambil hanya kata pertama, dan hanya bila ia tersusun dari karakter yang
    lazim dipakai penanda bahasa.
    """
    token = language.strip().split()[0] if language.strip() else ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+#-")
    return token if token and set(token) <= allowed else ""


def render_example(example: CodeExample) -> str:
    """Render satu contoh menjadi Markdown (MURNI).

    Urutannya mengikuti §23 — judul, kode, keluarannya, baru penjelasan —
    sehingga contoh yang sama selalu terbaca dengan cara yang sama di seluruh
    buku. Bab yang menyajikan contohnya dengan urutan berbeda-beda adalah bab
    yang membuat pembacanya berhenti membaca.
    """
    lines: list[str] = []

    if example.title.strip():
        lines.extend([f"**{example.title.strip()}**", ""])

    if example.code.strip():
        fence = _fence(example.code)
        lines.extend(
            [
                f"{fence}{_fence_language(example.language)}",
                example.code.strip("\n"),
                fence,
                "",
            ]
        )

    if example.expected_output.strip():
        fence = _fence(example.expected_output)
        lines.extend(
            [
                "Keluaran yang diharapkan:",
                "",
                f"{fence}text",
                example.expected_output.strip("\n"),
                fence,
                "",
            ]
        )

    if example.explanation.strip():
        lines.extend([example.explanation.strip(), ""])

    return "\n".join(lines).strip()


def render_examples(examples: Sequence[CodeExample]) -> tuple[str, ...]:
    """Render seluruh contoh yang dapat dipakai menjadi Markdown (MURNI).

    Contoh yang tidak menghasilkan apa pun dibuang di sini — bukan karena
    "dibersihkan", tetapi karena merendernya akan mencetak judul ``Contoh 3.4``
    yang menggantung tanpa isi. Jumlah yang dibuang tetap terlihat dari
    :func:`inspect_examples`, yang melaporkannya sebelum gate memutuskan.
    """
    rendered = (render_example(example) for example in examples if is_usable_example(example))
    return tuple(text for text in rendered if text)


def render_exercise(exercise: Exercise) -> str:
    """Render satu butir latihan menjadi Markdown (MURNI).

    **Kunci jawaban disertakan, di dalam blok ``<details>``.** Membuangnya akan
    menghilangkan bahan yang benar-benar dihasilkan §20 — dan bahan itu berbayar
    dalam token. Menampilkannya terbuka akan membuat bagian Latihan tidak lagi
    dapat dipakai sebagai latihan. Blok ``<details>`` menyelesaikan keduanya:
    isinya tetap ada di sumber (dan tetap terlihat oleh dosen yang meninjau
    LaTeX-nya), tetapi tertutup di setiap penampil Markdown yang lazim dipakai.
    Versi LaTeX kelak dapat memindahkannya ke kunci jawaban tersendiri.
    """
    lines: list[str] = [exercise.prompt.strip()]

    meta: list[str] = []
    if exercise.difficulty.strip():
        meta.append(f"Tingkat: {exercise.difficulty.strip().lower()}")
    if exercise.objective.strip():
        meta.append(f"menguji: {exercise.objective.strip()}")
    if meta:
        lines.extend(["", f"*{'; '.join(meta)}*"])

    if exercise.hint.strip():
        lines.extend(["", f"*Petunjuk:* {exercise.hint.strip()}"])

    if exercise.answer.strip():
        lines.extend(
            [
                "",
                "<details>",
                "<summary>Kunci jawaban</summary>",
                "",
                exercise.answer.strip(),
                "",
                "</details>",
            ]
        )

    return "\n".join(lines).strip()


def render_exercises(exercises: Sequence[Exercise]) -> tuple[str, ...]:
    """Render seluruh latihan yang dapat dipakai menjadi Markdown (MURNI)."""
    rendered = (
        render_exercise(exercise) for exercise in exercises if is_usable_exercise(exercise)
    )
    return tuple(text for text in rendered if text)


def describe_mix(mix: Sequence[tuple[str, int]]) -> str:
    """Ringkas sebaran tingkat kesulitan menjadi satu kalimat (MURNI)."""
    if not mix:
        return "tidak ada latihan yang dapat dipakai"
    return ", ".join(f"{count} {level}" for level, count in mix)


__all__ = [
    "DIFFICULTY_LEVELS",
    "CodeExample",
    "ExampleSet",
    "Exercise",
    "ExerciseSet",
    "describe_mix",
    "difficulty_mix",
    "inspect_examples",
    "inspect_exercises",
    "is_usable_example",
    "is_usable_exercise",
    "render_example",
    "render_examples",
    "render_exercise",
    "render_exercises",
]
