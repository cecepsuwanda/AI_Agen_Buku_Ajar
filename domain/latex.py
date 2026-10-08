"""Sumber LaTeX bab, daftar pustaka, dan pemeriksaannya (§25) — MURNI.

Blueprint §25 meminta "LaTeX Agent" berperan ``latex``. Tetapi meminta model
menyalin seluruh isi bab ke LaTeX adalah cara paling andal untuk **kehilangan
separuh isi bab** — dan kehilangan itu tidak menimbulkan satu pun galat, hanya
PDF yang lebih pendek daripada Markdown-nya. Karena itu pembagiannya tegas, dan
sama persis dengan pasangan yang sudah ada untuk Markdown
(:func:`~domain.rendering.render_chapter_markdown`):

* **Model memutuskan isi dan struktur** — :class:`LatexChapter`. Ia yang memilih
  environment (``definition``, ``example``, ``lstlisting``), meletakkan
  ``\\label{}`` di tempat yang berguna, dan meloloskan karakter khusus di dalam
  prosa yang ditulisnya. Itu pekerjaan yang memang butuh model.
* **Program merakit dokumen** — :func:`render_chapter_latex` dan
  :func:`render_bibliography`. Keduanya murni, dan keluarannya deterministik
  byte-per-byte.

Seluruh fungsi di sini murni: masuk string, keluar string. Tidak ada berkas,
tidak ada ``subprocess``, dan tidak ada ``latexmk`` — menulis ``.tex`` adalah
pekerjaan adapter ``latex/``, dan mengompilasinya baru ada di Tahap 7 (§26).
Bahkan ``re`` terlarang di ``domain/``, sehingga seluruh pemindaian
``\\cite{}``/``\\label{}`` di bawah ditulis dengan ``str.find``. Itu bukan
kerugian: pemindaian braket yang ditulis tangan jauh lebih jernih daripada
ekspresi reguler yang berusaha menutup kurung bersarang, dan di sini braketnya
tidak pernah bersarang.

**Kunci sitasi dihitung program, bukan dipilih model.** Model menerima daftar
``\\cite`` yang sah sebagai bagian dari prompt (lihat ``prompts/latex.chapter.md``)
dan dilarang menulis kunci lain. Dengan begitu ``.bib`` yang dihasilkan
:func:`render_bibliography` pasti memuat setiap kunci yang dikutip, dan §34
("jangan mengarang sitasi") ditegakkan pada tingkat **kunci**: satu-satunya cara
model menyimpang adalah mengubah kunci yang sudah diberikan, dan itu justru yang
ditangkap :func:`inspect_latex` sebelum berkasnya ditulis.

Isi entri ``.bib`` tetap jujur tentang apa yang diketahui program. Yang diketahui
program tentang sebuah sumber adalah **namanya sendiri** — itulah isi
``BookState.citations``, dan itulah yang tertulis di entri sebagai ``title``.
Menambahkan penulis dan tahun berarti mengarang keduanya dari nama berkas; hal
itu menuntut metadata dokumen, dan sampai metadata itu ada, entri ``@misc``
dengan judul yang benar lebih baik daripada entri ``@book`` dengan penulis yang
salah.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from pydantic import Field

from domain.base import FrozenModel
from domain.book import ChapterSpec
from domain.rendering import strip_chapter_prefix

#: Karakter yang bermakna bagi LaTeX, dan penggantinya di dalam **teks biasa**.
#:
#: Diterapkan dalam **satu lintasan**, bukan sebagai rangkaian ``str.replace``.
#: Penggantian berurutan akan meloloskan ulang garis miring balik yang baru saja
#: dihasilkan sendiri — ``\\textbackslash{}`` berubah menjadi omong kosong yang
#: jauh lebih sulit dilacak daripada karakter aslinya.
_ESCAPE_MAP: Mapping[str, str] = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "^": r"\textasciicircum{}",
    "_": r"\_",
    "%": r"\%",
    "~": r"\textasciitilde{}",
}

#: Panjang maksimum bagian slug dari kunci sitasi. Kunci BibTeX yang panjang
#: membuat ``references.bib`` tidak terbaca manusia, dan bagian yang panjang
#: itu selalu ekornya — edisi, tahun, nama penerbit — yang sudah diwakili
#: sidik jari di ujung kunci.
_SLUG_MAX = 40

#: Slug yang dipakai bila nama sumber tidak menyisakan satu pun karakter aman
#: (mis. sumber berbahasa non-Latin). Bukan string kosong: kunci BibTeX yang
#: dimulai tanda hubung adalah kunci yang tidak sah.
_FALLBACK_SLUG = "sumber"

# FNV-1a 32-bit.
#
# Bukan ``hash()`` bawaan Python: nilainya **diacak per proses** untuk ``str``
# (PYTHONHASHSEED), sehingga kunci yang sama akan berbeda di setiap jalankan dan
# ``output/latex/references.bib`` — yang di-track git (§39) — berubah setiap
# kali. Bukan ``hashlib`` pula: modul itu tidak ada di daftar putih impor
# ``domain/``, dan tidak perlu ada, sebab yang dibutuhkan hanyalah sidik jari
# yang stabil dan pendek, bukan fungsi hash yang tahan serangan.
_FNV_OFFSET_BASIS = 0x811C9DC5
_FNV_PRIME = 0x01000193
_FNV_MASK = 0xFFFFFFFF


class LatexChapter(FrozenModel):
    """Potongan LaTeX satu bab — keluaran Latex Agent (§25, §36).

    Ini **bukan** dokumen yang berdiri sendiri: tidak ada ``\\documentclass``,
    tidak ada preamble, dan tidak ada ``\\begin{document}``. Bab-bab disertakan
    ke dalam ``main.tex`` oleh perintah ``export`` (Tahap 7), dan model yang
    mengirimkan dokumen utuh akan menggandakan preamble sebanyak jumlah bab —
    kompilasi yang gagal dengan pesan yang menunjuk ke tempat yang salah.

    ``body_tex`` adalah **isi bab saja**, tanpa judul: judul, penomoran, label
    bab, dan tujuan pembelajaran dirakit :func:`render_chapter_latex` dari
    spesifikasi bab. Pembagian itu disengaja — judul yang ditulis dua kali
    (oleh model dan oleh perender) adalah judul yang akan berbeda.
    """

    number: int = Field(
        ge=1,
        description="Nomor bab, disalin apa adanya dari header di atas.",
    )
    title: str = Field(
        min_length=1,
        description=(
            "Judul bab saja, TANPA awalan 'Bab N:' dan tanpa perintah LaTeX "
            "apa pun — perender yang menambahkan judulnya."
        ),
    )
    body_tex: str = Field(
        default="",
        description=(
            "Isi bab sebagai potongan LaTeX: sub-bab (\\section, \\subsection), "
            "prosa, contoh, dan latihan. Tanpa preamble, tanpa \\begin{document}, "
            "tanpa judul bab, dan tanpa \\bibliography."
        ),
    )
    labels: tuple[str, ...] = Field(
        default=(),
        description=(
            "Nama setiap \\label{} yang benar-benar ditulis di dalam body_tex, "
            "tanpa \\label itu sendiri dan tanpa kurung kurawal."
        ),
    )
    citations: tuple[str, ...] = Field(
        default=(),
        description=(
            "Kunci sitasi yang benar-benar dipakai di dalam body_tex, disalin "
            "persis dari daftar kunci yang diberikan di atas. Bukan nama sumber."
        ),
    )


# ---------------------------------------------------------------------------
# Pelolosan & kunci sitasi
# ---------------------------------------------------------------------------
def escape_latex(text: str) -> str:
    """Loloskan karakter yang bermakna bagi LaTeX di dalam teks biasa (MURNI).

    Dipakai perender untuk **teks yang datang dari program** — judul bab dan
    tujuan pembelajaran, yang keduanya berasal dari planner. Teks yang ditulis
    model tidak melewati sini: meloloskannya dua kali akan mencetak
    ``\\&`` secara harfiah di PDF, dan model sudah diminta meloloskan tulisannya
    sendiri di dalam prompt.
    """
    return "".join(_ESCAPE_MAP.get(char, char) for char in text)


def citation_key(source: str) -> str:
    """Kunci BibTeX yang stabil untuk sebuah nama sumber (MURNI).

    Bentuknya ``slug-sidikjari``. Keduanya diperlukan:

    * **Slug** membuat ``references.bib`` dan ``.tex`` dapat dibaca manusia —
      ``\\cite{cormen-introduction-to-algorithms-4th-ed-...}`` setidaknya
      memberi tahu pembaca apa yang dirujuk.
    * **Sidik jari** membuat dua sumber dengan nama panjang yang sama di 40
      karakter pertama tetap mendapat kunci berbeda. Tanpa itu, dua edisi buku
      yang berjudul sama akan bertabrakan, dan sitasi bab 5 diam-diam menunjuk
      pustaka yang dikutip bab 1 — persis jenis kesalahan yang tidak dapat
      ditemukan manusia yang membaca PDF-nya.

    Fungsinya murni: nama sumber yang sama selalu menghasilkan kunci yang sama,
    di proses mana pun, di mesin mana pun.
    """
    return f"{_slug(source)}-{_fingerprint(source)}"


def bibliography_entries(sources: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Pasangan ``(kunci, sumber)`` untuk daftar pustaka, terurut menurut kunci (MURNI).

    Sumber yang sama tidak pernah menghasilkan dua entri, dan urutannya
    deterministik — dua hal yang diperlukan agar ``references.bib`` tidak
    berubah setiap kali perintah dijalankan (§39: ``output/`` di-track git).

    Daftar ini dipakai **dua kali** untuk hal yang harus sepakat: prompt
    menerimanya sebagai daftar kunci yang sah untuk dikutip, dan
    :func:`render_bibliography` menuliskannya sebagai isi ``.bib``. Dihitung
    sekali di sini supaya keduanya tidak dapat berbeda.
    """
    keyed: dict[str, str] = {}
    for source in sorted({item.strip() for item in sources if item.strip()}):
        key = citation_key(source)
        candidate = key
        index = 2
        while candidate in keyed and keyed[candidate] != source:
            # Hanya mungkin bila dua nama berbeda bentrok di 32 bit sidik jari.
            # Ditangani — bukan diabaikan — karena mengabaikannya berarti satu
            # entri pustaka menghilang tanpa satu pun tanda.
            candidate = f"{key}-{index}"
            index += 1
        keyed[candidate] = source
    return tuple(sorted(keyed.items()))


def chapter_latex_filename(number: int) -> str:
    """Nama berkas LaTeX untuk bab ``number`` (mis. ``chapter01.tex``) (MURNI).

    Kembar dari :func:`~domain.rendering.chapter_filename`. Keduanya sengaja
    memakai pola penomoran yang sama supaya ``output/chapters/chapter01.md`` dan
    ``output/latex/chapters/chapter01.tex`` selalu berbicara tentang bab yang
    sama.
    """
    return f"chapter{number:02d}.tex"


# ---------------------------------------------------------------------------
# Pemindaian potongan LaTeX
# ---------------------------------------------------------------------------
def strip_tex_comments(tex: str) -> str:
    """Buang komentar LaTeX (``%`` sampai akhir baris) (MURNI).

    Diperlukan sebelum **memindai**, bukan sebelum menulis: ``\\cite{kunci}``
    yang dikomentari tidak pernah sampai ke BibTeX, jadi menghitungnya sebagai
    sitasi berarti melaporkan bab yang memakai rujukan yang tidak akan pernah
    tercetak. ``\\%`` tetap dipertahankan — ia adalah persen yang sesungguhnya,
    dan memperlakukannya sebagai awal komentar akan memotong sisa baris.

    Berkas yang ditulis ke ``output/latex/`` tetap memuat komentarnya: LaTeX
    tidak berkeberatan, dan membuangnya di sana berarti menyunting tulisan model
    tanpa memberitahunya.
    """
    lines: list[str] = []
    for line in tex.split("\n"):
        cut = len(line)
        index = 0
        while index < len(line):
            char = line[index]
            if char == "\\":
                index += 2  # karakter berikutnya diloloskan, apa pun isinya
                continue
            if char == "%":
                cut = index
                break
            index += 1
        lines.append(line[:cut])
    return "\n".join(lines)


def _braced_arguments(tex: str, command: str) -> tuple[str, ...]:
    """Isi setiap ``{...}`` yang mengikuti ``command``, urut kemunculan (MURNI).

    Argumen opsional ``[...]`` dilewati, dan huruf yang menyambung nama perintah
    ikut dimakan sehingga ``\\citep{...}`` tertangkap oleh ``\\cite`` sementara
    ``\\endinput`` tidak tertangkap oleh ``\\end``. Pemindaiannya sengaja
    "gagal dengan tenang": braket yang tidak ditutup tidak melempar, hanya
    berhenti menghasilkan — bab yang braketnya rusak akan ditolak kompilasi di
    Tahap 7, dan di sini tugasnya hanya mengumpulkan apa yang **dapat**
    dikumpulkan.
    """
    found: list[str] = []
    index = 0
    while True:
        start = tex.find(command, index)
        if start < 0:
            return tuple(found)

        cursor = start + len(command)
        while cursor < len(tex) and (tex[cursor].isalpha() or tex[cursor] == "*"):
            cursor += 1
        while cursor < len(tex) and tex[cursor].isspace():
            cursor += 1
        while cursor < len(tex) and tex[cursor] == "[":
            close = tex.find("]", cursor)
            if close < 0:
                break
            cursor = close + 1
            while cursor < len(tex) and tex[cursor].isspace():
                cursor += 1

        if cursor < len(tex) and tex[cursor] == "{":
            close = tex.find("}", cursor)
            if close < 0:
                index = cursor + 1
                continue
            found.append(tex[cursor + 1 : close])
            index = close + 1
        else:
            index = cursor

        if index <= start:  # pragma: no cover - jaring pengaman anti-lintasan-buntu
            index = start + 1


def extract_cite_keys(tex: str) -> tuple[str, ...]:
    """Kunci di dalam setiap ``\\cite{...}``, urut kemunculan tanpa duplikat (MURNI).

    Satu perintah dapat memuat beberapa kunci (``\\cite{a,b}``), dan kunci yang
    dikutip dua kali hanya dihitung sekali: yang diperiksa di sini adalah
    **himpunan** rujukan yang dipakai bab, bukan berapa kali masing-masing
    disebut.
    """
    keys: dict[str, None] = {}
    for argument in _braced_arguments(strip_tex_comments(tex), "\\cite"):
        for key in argument.split(","):
            normalized = key.strip()
            if normalized:
                keys.setdefault(normalized, None)
    return tuple(keys)


def extract_labels(tex: str) -> tuple[str, ...]:
    """Nama di dalam setiap ``\\label{...}``, urut kemunculan (MURNI)."""
    return tuple(
        label.strip()
        for argument in _braced_arguments(strip_tex_comments(tex), "\\label")
        if (label := argument.strip())
    )


def unbalanced_environments(tex: str) -> tuple[str, ...]:
    """Environment yang jumlah ``\\begin`` dan ``\\end``-nya tidak sama (MURNI).

    Ini pemeriksaan yang paling sering menyelamatkan satu kali kompilasi:
    environment yang tidak ditutup menghasilkan galat yang pesannya menunjuk
    ke akhir berkas, bukan ke tempat kesalahannya, dan model yang memperbaiki
    berdasarkan pesan itu akan memperbaiki hal yang salah.
    """
    counts: dict[str, int] = {}
    for name in _braced_arguments(strip_tex_comments(tex), "\\begin"):
        counts[name.strip()] = counts.get(name.strip(), 0) + 1
    for name in _braced_arguments(strip_tex_comments(tex), "\\end"):
        counts[name.strip()] = counts.get(name.strip(), 0) - 1
    return tuple(sorted(name for name, delta in counts.items() if delta))


def inspect_latex(
    chapter: LatexChapter,
    *,
    allowed_citations: Sequence[str],
) -> tuple[bool, tuple[str, ...]]:
    """Periksa potongan LaTeX sebuah bab, tanpa memanggil model (MURNI).

    Yang diperiksa hanya hal-hal yang **dapat dipastikan** — dan itu bukan
    daftar yang pendek, karena seluruh kesalahan LaTeX yang lazim adalah
    kesalahan bentuk, bukan kesalahan penilaian. Pertanyaan "apakah prosa ini
    baik" tidak ada di sini; itu pekerjaan peninjau, dan menebaknya secara
    mekanis hanya akan menolak bab yang baik karena alasan yang salah.

    :returns: ``(bersih, temuan)``. ``bersih`` bernilai True bila tidak ada
        temuan. Temuan ditulis sebagai kalimat yang dapat dikerjakan penulis,
        sama seperti temuan pemeriksa lain (§21–§24): "apa yang salah", bukan
        "ada yang salah".
    """
    if not strip_tex_comments(chapter.body_tex).strip():
        # Satu temuan saja, dan berhenti. Isi yang kosong membuat seluruh
        # pemeriksaan lain di bawah menjadi hampa — melaporkan "kunci sitasi
        # dinyatakan tetapi tidak dikutip" di atas bab yang tidak berisi apa pun
        # hanya mengubur temuan yang sebenarnya harus dikerjakan.
        return (False, ("isi bab (body_tex) kosong",))

    body = strip_tex_comments(chapter.body_tex)
    findings: list[str] = []

    if "\\documentclass" in body or "\\begin{document}" in body:
        findings.append(
            "isi bab memuat preamble atau \\begin{document}; bab adalah potongan "
            "yang disertakan buku, bukan dokumen tersendiri"
        )

    if any(line.strip().startswith("\\chapter") for line in body.split("\n")):
        findings.append(
            "isi bab memuat \\chapter; judul dan nomor bab ditambahkan perender, "
            "sehingga judul di sini akan tercetak dua kali"
        )

    if "\\label{chap:" in body:
        findings.append(
            "isi bab memuat label bab (\\label{chap:...}); label itu ditulis "
            "perender, dan label ganda membuat \\ref menunjuk yang terakhir"
        )

    used = extract_cite_keys(body)
    known = {key.strip() for key in allowed_citations if key.strip()}
    unknown = tuple(key for key in used if key not in known)
    if unknown:
        findings.append(
            "kunci sitasi yang tidak ada di daftar kunci yang diberikan (jangan "
            "mengarang kunci): " + ", ".join(unknown)
        )

    declared = tuple(key.strip() for key in chapter.citations if key.strip())
    unquoted = tuple(key for key in dict.fromkeys(declared) if key not in used)
    if unquoted:
        findings.append(
            "kunci sitasi dinyatakan tetapi tidak dipakai di isi bab: " + ", ".join(unquoted)
        )

    labels = extract_labels(body)
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    duplicated = tuple(label for label in dict.fromkeys(labels) if counts[label] > 1)
    if duplicated:
        findings.append(
            "label ganda di dalam satu bab (\\ref akan menunjuk yang terakhir): "
            + ", ".join(duplicated)
        )

    declared_labels = tuple(label.strip() for label in chapter.labels if label.strip())
    absent = tuple(label for label in dict.fromkeys(declared_labels) if label not in counts)
    if absent:
        findings.append(
            "label dinyatakan tetapi tidak ditemukan di isi bab: " + ", ".join(absent)
        )

    unbalanced = unbalanced_environments(body)
    if unbalanced:
        findings.append(
            "environment yang \\begin dan \\end-nya tidak berpasangan: "
            + ", ".join(unbalanced)
        )

    # Apa yang **tidak** diperiksa di sini: apakah setiap environment benar-benar
    # didefinisikan preamble. Pemeriksaan itu menuntut daftar environment yang
    # dijaga sinkron dengan ``latex/templates/preamble.tex``, dan daftar yang
    # dipelihara di dua tempat adalah daftar yang akan menyimpang. Yang benar-benar
    # membuktikannya adalah kompilasi — dan ia ada di Tahap 7 (§26), di mana galat
    # "Environment X undefined" datang dari LaTeX sendiri, bukan dari tebakan kita.

    return (not findings, tuple(findings))


# ---------------------------------------------------------------------------
# Rendering — materi terstruktur menjadi sumber LaTeX (§25)
# ---------------------------------------------------------------------------
def render_chapter_latex(
    chapter: LatexChapter,
    *,
    number: int,
    spec: ChapterSpec,
) -> str:
    """Rakit satu bab menjadi potongan LaTeX yang siap di-``\\include`` (MURNI).

    Keluarannya deterministik byte-per-byte: masukan yang sama selalu
    menghasilkan berkas yang sama, sehingga ``output/latex/chapters/*.tex`` yang
    di-track git (§39) hanya berubah ketika isinya memang berubah.

    Nomor bab diambil dari ``number``, **bukan** dari ``chapter.number``.
    Nomor yang benar adalah nomor yang diberikan sistem, dan judul yang salah
    nomor lebih baik kehilangan nomornya daripada mempertahankannya — aturan
    yang sama dengan :func:`~domain.rendering.strip_chapter_prefix`. Selisih
    antara keduanya tetap dilaporkan oleh gate, bukan ditelan diam-diam.

    Tujuan pembelajaran diambil dari **spesifikasi**, bukan dari draf:
    spesifikasi adalah yang sudah dikunci ``enforce_draft_contract``, sedangkan
    draf dapat memuatnya kembali dengan kata-katanya sendiri. Perender tidak
    menerima draf justru supaya pilihan itu tidak dapat dibuat dua kali.
    """
    heading = escape_latex(strip_chapter_prefix(chapter.title))
    lines: list[str] = [
        f"\\chapter{{{heading}}}",
        f"\\label{{chap:{number}}}",
        "",
    ]

    if spec.objectives:
        lines.extend(["\\section*{Tujuan Pembelajaran}", "\\begin{itemize}"])
        lines.extend(f"  \\item {escape_latex(objective)}" for objective in spec.objectives)
        lines.extend(["\\end{itemize}", ""])

    body = chapter.body_tex.strip()
    if body:
        lines.extend([body, ""])

    return "\n".join(lines).rstrip() + "\n"


def render_bibliography(sources: Sequence[str]) -> str:
    """Rakit daftar pustaka BibTeX dari nama-nama sumber (MURNI).

    Setiap entri adalah ``@misc`` dengan **judul** saja. Itu memang bentuk yang
    paling sederhana, dan bentuk itu dipilih karena itu satu-satunya hal yang
    benar-benar diketahui program: ``BookState.citations`` memetakan kunci
    rujukan ke nama sumbernya, dan menuliskan penulis serta tahun dari nama
    berkas berarti mengarang keduanya (lihat docstring modul).

    Entri ``@misc`` tanpa penulis tetap **sah** bagi BibTeX dan tetap tercetak
    di daftar pustaka. Yang tidak sah adalah entri yang menyebut penulis yang
    tidak ada — pembaca yang menelusurinya tidak akan menemukan bukunya, dan
    tidak ada satu pun cara mengetahui bahwa nama itu karangan.
    """
    entries = [
        "@misc{" + key + ",\n  title = {" + escape_latex(source) + "},\n}"
        for key, source in bibliography_entries(sources)
    ]
    if not entries:
        return ""
    return "\n\n".join(entries) + "\n"


# ---------------------------------------------------------------------------
# Pencocokan nomor bab
# ---------------------------------------------------------------------------
def lock_chapter_number(
    chapter: LatexChapter,
    *,
    number: int,
) -> tuple[LatexChapter, tuple[str, ...]]:
    """Kunci nomor bab pada nomor yang diberikan sistem (MURNI).

    Sama seperti :func:`~domain.rules.reconcile_chapter_spec` untuk keluaran
    planner: yang salah **diperbaiki**, dan perbaikannya **dilaporkan**. Model
    yang menuliskan nomor bab lain bukan alasan membuang bab yang sudah benar
    isinya — tetapi membiarkannya tanpa catatan berarti menyembunyikan bahwa
    model tidak mengikuti header yang diberikan kepadanya.
    """
    if chapter.number == number:
        return (chapter, ())
    return (
        chapter.model_copy(update={"number": number}),
        (f"nomor bab pada keluaran model ({chapter.number}) dikunci menjadi {number}.",),
    )


# ---------------------------------------------------------------------------
# Pembantu
# ---------------------------------------------------------------------------
def _slug(source: str) -> str:
    """Bagian kunci sitasi yang dapat dibaca manusia (MURNI)."""
    cleaned: list[str] = []
    for char in source.strip().casefold():
        # Hanya ASCII. Kunci BibTeX yang memuat huruf non-ASCII membuat
        # ``bibtex`` klasik gagal membacanya, dan kegagalan itu muncul sebagai
        # "entri pustaka tidak ditemukan" — pesan yang menunjuk ke tempat yang
        # salah.
        cleaned.append(char if char.isascii() and char.isalnum() else "-")
    trimmed = "-".join(part for part in "".join(cleaned).split("-") if part)
    return trimmed[:_SLUG_MAX].rstrip("-") or _FALLBACK_SLUG


def _fingerprint(text: str) -> str:
    """Sidik jari heksadesimal stabil untuk sebuah string (MURNI)."""
    value = _FNV_OFFSET_BASIS
    for byte in text.encode("utf-8"):
        value = ((value ^ byte) * _FNV_PRIME) & _FNV_MASK
    return format(value, "08x")


__all__ = [
    "LatexChapter",
    "bibliography_entries",
    "chapter_latex_filename",
    "citation_key",
    "escape_latex",
    "extract_cite_keys",
    "extract_labels",
    "inspect_latex",
    "lock_chapter_number",
    "render_bibliography",
    "render_chapter_latex",
    "strip_tex_comments",
    "unbalanced_environments",
]
