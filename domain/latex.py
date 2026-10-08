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
pekerjaan adapter ``latex/``, dan mengompilasinya adalah pekerjaan
``latex/compiler.py`` (§26). Yang tinggal di sini justru keputusan yang memang
milik program, bukan milik perkakas: menilai apakah hasil kompilasi dapat
diterima (:func:`build_problems`), memilih bagian log yang berguna untuk
memperbaiki bab (:func:`log_excerpt`), mencari rujukan silang yang menggantung
(:func:`crossref_findings`), dan merakit ``main.tex`` dari template
(:func:`render_main_tex`). Parser log-nya sendiri ada di ``latex/validator.py``
— ia boleh memakai ``re``, dan ``domain/`` tidak.

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
from domain.errors import ConfigError
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

#: Pola nama berkas potongan bab, dalam dua arah.
#:
#: Ditulis sebagai konstanta, bukan disebar sebagai f-string dan potongan
#: literal: :func:`chapter_latex_filename` dan
#: :func:`chapter_number_from_filename` adalah kebalikan satu sama lain, dan
#: keduanya harus berbicara tentang pola yang sama persis.
_CHAPTER_PREFIX = "chapter"
_CHAPTER_SUFFIX = ".tex"

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


class LatexBuildResult(FrozenModel):
    """Hasil satu kali kompilasi, sebagaimana dilaporkan LaTeX sendiri (§26).

    Objek ini **hanya memuat fakta**, bukan vonis: ``ok`` berarti perkakas LaTeX
    melaporkan sukses, dan kedelapan jenis masalah §26 yang terbaca dari log
    dikelompokkan ke field-nya masing-masing. Yang memutuskan apakah hasil ini
    dapat diterima adalah :func:`build_problems` — pemisahan itu disengaja,
    karena "latexmk keluar dengan kode 0" dan "buku ini tidak punya sitasi
    menggantung" adalah dua hal yang berbeda, dan menyatukannya menjadi satu
    boolean berarti salah satunya akan hilang.

    ``log_excerpt`` ikut dibawa karena ia satu-satunya hal yang dapat dikerjakan
    model: potongan log yang menyebut galatnya, bukan dua ratus baris terakhir
    yang sebagian besar berisi daftar berkas yang dibaca.
    """

    ok: bool = Field(
        default=False,
        description=(
            "True bila perkakas LaTeX selesai dengan kode keluar 0 dan "
            "menghasilkan PDF. Bukan vonis akhir — lihat build_problems()."
        ),
    )
    errors: tuple[str, ...] = Field(
        default=(),
        description="Baris galat LaTeX (baris log yang dimulai '!'), urut kemunculan.",
    )
    warnings: tuple[str, ...] = Field(
        default=(),
        description=(
            "Peringatan LaTeX yang tidak termasuk jenis lain, tanpa duplikat, "
            "dipotong pada 10 entri pertama."
        ),
    )
    undefined_refs: tuple[str, ...] = Field(
        default=(),
        description="Kunci \\ref yang belum punya \\label, urut kemunculan.",
    )
    undefined_citations: tuple[str, ...] = Field(
        default=(),
        description="Kunci \\cite yang tidak ditemukan di references.bib.",
    )
    duplicate_labels: tuple[str, ...] = Field(
        default=(),
        description="Label yang terdefinisi lebih dari sekali di seluruh dokumen.",
    )
    missing_figures: tuple[str, ...] = Field(
        default=(),
        description="Nama berkas gambar yang dirujuk tetapi tidak ditemukan.",
    )
    overfull_boxes: tuple[str, ...] = Field(
        default=(),
        description="Kotak yang melebihi lebar halaman (kosmetik, urut kemunculan).",
    )
    log_excerpt: str = Field(
        default="",
        description="Potongan log yang menyebut galatnya — bahan perbaikan bagi model.",
    )
    pdf_path: str = Field(
        default="",
        description="Jalur PDF yang dihasilkan; kosong bila kompilasi gagal.",
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


def bibliography_sources(
    book_sources: Sequence[str],
    draft_sources: Sequence[str],
) -> tuple[str, ...]:
    """Seluruh sumber yang harus ada di ``references.bib`` satu bab (MURNI).

    Gabungan dua himpunan, dan keduanya diperlukan:
    ``BookState.citations`` memuat sumber yang sudah lolos pemeriksaan sitasi
    pada bab-bab sebelumnya — tanpa itu, ``\\cite`` bab ini yang menunjuk sumber
    dari bab 1 akan menggantung, sedangkan entri buku baru diperbarui *sesudah*
    bab ini selesai. ``draft.citations`` memuat sumber yang barusan dipakai bab
    ini.

    Dihitung di ``domain/`` alih-alih di dalam gate karena **dua** gate
    membutuhkannya: penulis sumber LaTeX (§25) menulis daftar pustakanya, dan
    pemeriksa kompilasi (§26) memeriksa ``\\cite`` bab terhadap daftar yang sama.
    Dua salinan aturan ini adalah dua salinan yang akan menyimpang, dan
    penyimpangannya berbentuk sitasi menggantung yang baru terlihat di PDF.
    """
    return tuple(dict.fromkeys((*book_sources, *(source.strip() for source in draft_sources))))


def chapter_citations(
    sources: Sequence[str],
    draft_sources: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    """Pasangan ``(kunci, sumber)`` yang **boleh** dikutip satu bab (MURNI).

    Hanya sumber yang benar-benar dipakai draf — daftar pustaka boleh memuat
    sumber bab lain, tetapi daftar kunci yang dikirim ke model adalah daftar
    tertutup untuk bab ini, dan daftar yang lebih pendek lebih mudah dipatuhi.
    """
    key_by_source = {source: key for key, source in bibliography_entries(sources)}
    used = dict.fromkeys(item.strip() for item in draft_sources)
    return tuple(
        (key_by_source[source], source) for source in used if source and source in key_by_source
    )


def chapter_latex_filename(number: int) -> str:
    """Nama berkas LaTeX untuk bab ``number`` (mis. ``chapter01.tex``) (MURNI).

    Kembar dari :func:`~domain.rendering.chapter_filename`. Keduanya sengaja
    memakai pola penomoran yang sama supaya ``output/chapters/chapter01.md`` dan
    ``output/latex/chapters/chapter01.tex`` selalu berbicara tentang bab yang
    sama.
    """
    return f"{_CHAPTER_PREFIX}{number:02d}{_CHAPTER_SUFFIX}"


def chapter_number_from_filename(filename: str) -> int | None:
    """Nomor bab dari nama berkas ``chapterNN.tex``, atau ``None`` (MURNI).

    Kebalikan dari :func:`chapter_latex_filename`, dan diletakkan tepat di
    sebelahnya supaya kedua arah penamaan itu tidak dapat menyimpang tanpa
    terlihat: menambahkan nol di depan di satu arah saja akan membuat berkasnya
    ditemukan oleh ``export`` tetapi tidak dikenali lagi oleh pembacanya.

    ``None`` adalah jawaban yang benar untuk berkas yang bukan potongan bab —
    ``main.tex``, ``preamble.tex``, atau bahan rujukan apa pun. Mengembalikan
    nomor tebakan untuk berkas seperti itu akan menempelkan konsep-konsepnya pada
    bab yang tidak pernah memuatnya.

    :param filename: nama berkas saja (``Path.name``), bukan jalurnya. Nama yang
        memuat pemisah direktori tidak dikenali — membiarkannya lolos berarti
        satu-satunya pemanggil yang memakai jalur penuh akan mendapat nomor yang
        benar secara kebetulan, lalu salah pada bentuk jalur yang lain.
    """
    name = filename.strip()
    if not name.endswith(_CHAPTER_SUFFIX):
        return None
    stem = name[: -len(_CHAPTER_SUFFIX)]
    if not stem.startswith(_CHAPTER_PREFIX):
        return None
    digits = stem[len(_CHAPTER_PREFIX) :]
    if not digits.isascii() or not digits.isdigit():
        return None
    number = int(digits)
    return number if number >= 1 else None


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
# Menilai hasil kompilasi (§26)
# ---------------------------------------------------------------------------
def build_problems(result: LatexBuildResult) -> tuple[str, ...]:
    """Temuan yang membuat hasil kompilasi **belum dapat diterima** (MURNI).

    Delapan jenis masalah yang diminta §26 dipetakan ke sini, dan pemetaannya
    perlu dinyatakan terang karena dua di antaranya bergabung:

    1. galat kompilasi → ``errors``,
    2. rujukan menggantung (``\\ref`` tanpa ``\\label``) → ``undefined_refs``,
       **tetapi tidak menahan** — lihat catatan di bawah,
    3. sitasi menggantung → ``undefined_citations``,
    4. gambar tidak ditemukan → ``missing_figures``,
    5. label hilang → sama dengan (2): ``\\ref`` yang tidak menemukan ``\\label``
       adalah label yang hilang, dibaca dari sisi yang memakainya,
    6. persamaan rusak → ``errors``; LaTeX melaporkannya sebagai galat
       (``! Missing $ inserted.``) dan bukan sebagai peringatan,
    7. kotak melebihi halaman → ``overfull_boxes``, **tidak menahan**,
    8. label ganda → ``duplicate_labels``.

    **Mengapa rujukan menggantung tidak masuk sini.** Bab dikompilasi sebagai
    potongan; ``\\ref`` yang menunjuk bab lain akan selalu tampak menggantung di
    situ meski tidak ada yang salah — dan menolak setiap bab yang merujuk bab
    sebelumnya berarti tidak ada satu pun buku yang dapat lolos. Yang benar-benar
    tahu jawabannya adalah kompilasi tingkat buku (``export --latex``), tempat
    seluruh bab sudah dirakit; di sanalah ``undefined_refs`` dilaporkan sebagai
    temuan, dan di sana pula ia tidak lagi punya alasan untuk muncul.
    """
    problems: list[str] = []
    if result.errors:
        problems.append("galat LaTeX: " + "; ".join(result.errors))
    elif not result.ok:
        # Perkakas gagal tanpa satu pun baris galat yang terbaca: perkakasnya
        # tidak ada, atau kehabisan waktu. Dua sebab itu punya pesan sendiri di
        # adapter, dan di sini yang tersisa hanyalah kenyataan bahwa tidak ada
        # PDF yang dihasilkan.
        problems.append("kompilasi tidak menghasilkan PDF")
    if result.missing_figures:
        problems.append("berkas gambar tidak ditemukan: " + ", ".join(result.missing_figures))
    if result.undefined_citations:
        problems.append(
            "sitasi yang tidak punya entri daftar pustaka: "
            + ", ".join(result.undefined_citations)
        )
    if result.duplicate_labels:
        problems.append("label ganda: " + ", ".join(result.duplicate_labels))
    return tuple(problems)


def build_advisories(result: LatexBuildResult) -> tuple[str, ...]:
    """Temuan yang **dilaporkan tetapi tidak menahan** bab (MURNI).

    Dipisah dari :func:`build_problems` karena keduanya masuk ke tempat yang
    berbeda: temuan menahan bab dan menggerakkan tangga perbaikan, sedangkan
    catatan hanya menemani vonis supaya manusia yang membaca
    ``state/chapterNN.json`` tahu apa yang belum rapi. Menaruh keduanya di satu
    daftar berarti kotak yang terlalu lebar akan memicu perbaikan yang tidak akan
    pernah memperbaikinya.
    """
    advisories: list[str] = []
    if result.undefined_refs:
        advisories.append(
            "rujukan \\ref yang belum terdefinisi pada bab ini (dapat terisi setelah "
            "seluruh bab dirakit): " + ", ".join(result.undefined_refs)
        )
    if result.overfull_boxes:
        advisories.append(
            f"{len(result.overfull_boxes)} kotak melebihi lebar halaman; "
            "kosmetik, tidak menahan bab"
        )
    advisories.extend(f"peringatan LaTeX: {warning}" for warning in result.warnings)
    return tuple(advisories)


def log_excerpt(text: str, *, limit: int = 40) -> str:
    """Potongan log LaTeX yang paling berguna untuk memperbaiki bab (MURNI).

    Bukan "40 baris terakhir": ekor log hampir selalu berisi daftar berkas yang
    dibaca dan ringkasan yang tidak menyebut tempat kesalahannya. Yang dicari
    adalah baris pertama yang benar-benar menunjuk masalah — galat (``!``) atau
    peringatan yang salah satu jenisnya dibaca :func:`build_problems` — lalu
    jendela ``limit`` baris dari dua baris sebelumnya.

    Bila tidak ada satu pun baris seperti itu, potongan diambil dari awal log:
    pada kompilasi yang gagal sebelum LaTeX sempat menulis apa pun (perkakas
    tidak ditemukan, berkas template hilang), justru bagian itulah yang berbicara.
    """
    lines = text.split("\n")
    interesting = next((index for index, line in enumerate(lines) if _is_log_signal(line)), None)
    if interesting is None:
        return "\n".join(lines[:limit]).strip()
    return "\n".join(lines[max(interesting - 2, 0) : interesting + limit]).strip()


def _is_log_signal(line: str) -> bool:
    """True bila baris log menunjuk masalah yang dapat dikerjakan (MURNI)."""
    stripped = line.strip()
    if stripped.startswith("!"):
        return True
    if "Overfull \\hbox" in stripped or "Overfull \\vbox" in stripped:
        return True
    if not stripped.startswith("LaTeX Warning:"):
        return False
    return any(
        marker in stripped
        for marker in ("Reference", "Citation", "multiply defined", "not found")
    )


# ---------------------------------------------------------------------------
# Rujukan silang (§26)
# ---------------------------------------------------------------------------
#: Perintah yang memakai ``\label`` bab ini atau bab lain. Daftarnya tertutup
#: dan sengaja pendek: perintah yang tidak ada di sini tidak akan pernah
#: dilaporkan menggantung, dan menambahkan perintah paket baru ke sini berarti
#: memutuskan bahwa paketnya memang dipakai buku ini.
_REF_COMMANDS: frozenset[str] = frozenset(
    {"\\ref", "\\eqref", "\\pageref", "\\autoref", "\\cref", "\\Cref"}
)


def extract_ref_keys(tex: str) -> tuple[str, ...]:
    """Argumen setiap perintah rujukan (``\\ref``, ``\\eqref``, …), tanpa duplikat (MURNI).

    Kembar dari :func:`extract_cite_keys` untuk sisi yang lain: keduanya
    mengumpulkan **himpunan** hal yang disebut, bukan berapa kali masing-masing
    disebut.
    """
    keys: dict[str, None] = {}
    for argument in _command_arguments(strip_tex_comments(tex), _REF_COMMANDS):
        normalized = argument.strip()
        if normalized:
            keys.setdefault(normalized, None)
    return tuple(keys)


def _command_arguments(tex: str, commands: frozenset[str]) -> tuple[str, ...]:
    """Argumen ``{...}`` dari salah satu ``commands``, urut kemunculan (MURNI).

    Berbeda dari :func:`_braced_arguments` yang mencari satu perintah pada satu
    waktu: di sini urutannya harus urutan dokumen, bukan urutan perintah. Bab
    yang memakai ``\\eqref`` sebelum ``\\ref`` harus melaporkan keduanya dalam
    urutan itu, karena laporan yang urutannya berubah-ubah membuat catatan yang
    seharusnya sama terlihat berbeda setiap kali dijalankan.
    """
    found: list[str] = []
    index = 0
    while True:
        start = tex.find("\\", index)
        if start < 0:
            return tuple(found)

        cursor = start + 1
        while cursor < len(tex) and (tex[cursor].isalpha() or tex[cursor] == "*"):
            cursor += 1
        name = tex[start:cursor]
        index = cursor  # selalu maju: nama perintah minimal sepanjang satu karakter
        if name not in commands:
            continue

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
                continue
            found.append(tex[cursor + 1 : close])
            index = close + 1
        elif cursor > start:
            index = cursor


def crossref_findings(
    tex: str,
    *,
    bibliography_keys: Sequence[str],
) -> tuple[str, ...]:
    """Rujukan silang yang menggantung di dalam teks LaTeX (MURNI).

    Dua hal, dan keduanya dapat diketahui **tanpa menjalankan LaTeX sama sekali**:
    ``\\ref`` yang tidak menemukan ``\\label`` di dokumen mana pun, dan ``\\cite``
    yang tidak menemukan entrinya di ``references.bib``.

    Itulah alasan pemeriksaan ini ada meski log kompilasi juga melaporkan
    keduanya: yang ini tetap bekerja di mesin tanpa LaTeX, dan justru di situlah
    isyaratnya paling dibutuhkan — pesan "sitasi ini tidak punya entri" jauh
    lebih berguna daripada kegagalan kompilasi yang tidak dapat dijalankan.
    """
    body = strip_tex_comments(tex)
    labels = {label.strip() for label in extract_labels(body)}
    used_refs = (
        ref.strip() for ref in _command_arguments(body, _REF_COMMANDS) if ref.strip()
    )
    dangling = tuple(ref for ref in dict.fromkeys(used_refs) if ref not in labels)

    known = {key.strip() for key in bibliography_keys if key.strip()}
    missing = tuple(key for key in extract_cite_keys(body) if key not in known)

    findings: list[str] = []
    if dangling:
        findings.append(
            "rujukan \\ref yang tidak punya \\label di seluruh buku: " + ", ".join(dangling)
        )
    if missing:
        findings.append(
            "sitasi yang tidak punya entri daftar pustaka: " + ", ".join(missing)
        )
    return tuple(findings)


# ---------------------------------------------------------------------------
# Perakitan ``main.tex`` dari template (§42)
# ---------------------------------------------------------------------------
#: Penanda blok daftar bab di dalam ``latex/templates/main.tex``.
#:
#: Template bawaan bukan program, jadi ia tidak dapat memanggil perender. Yang
#: dapat dilakukan adalah menyepakati dua baris penanda, dan itulah bentuk
#: kesepakatan yang paling tidak mungkin rusak: template yang lupa memuatnya
#: gagal dengan pesan yang menyebut penandanya, bukan menghasilkan buku tanpa
#: satu pun bab.
CHAPTER_BLOCK_BEGIN = "% BUKUAJAR-BAB-MULAI"
CHAPTER_BLOCK_END = "% BUKUAJAR-BAB-SELESAI"


def render_main_tex(
    template: str,
    *,
    title: str,
    chapter_numbers: Sequence[int],
) -> str:
    """Isi ``main.tex`` dari template: judul buku dan daftar ``\\include`` (MURNI).

    Yang disunting hanya dua tempat — baris ``\\title`` dan blok di antara kedua
    penanda — sehingga template pengguna (``--latex-template``, §42) tetap utuh
    di luar keduanya. Menyalin ulang seluruh isi template dari nol akan berarti
    buku prodi yang punya preamble sendiri kehilangan preamblenya.

    Bab disertakan lewat ``\\include`` **tanpa ekstensi**, karena itulah yang
    diharapkan ``\\include``; nama berkasnya sendiri diambil dari
    :func:`chapter_latex_filename` supaya ``main.tex`` dan berkas yang benar-benar
    ditulis tidak dapat menyimpang.

    :raises ConfigError: bila template tidak memuat penanda blok atau baris
        ``\\title``. Template yang tidak lengkap adalah kesalahan konfigurasi,
        dan menemukannya di sini jauh lebih murah daripada menemukannya sebagai
        PDF yang tidak memuat satu pun bab.
    """
    lines = template.split("\n")
    begin = _marker_index(lines, CHAPTER_BLOCK_BEGIN)
    end = _marker_index(lines, CHAPTER_BLOCK_END)
    if end < begin:
        raise ConfigError(
            f"Template main.tex menaruh {CHAPTER_BLOCK_END} sebelum "
            f"{CHAPTER_BLOCK_BEGIN}; blok daftar bab tidak dapat diisi."
        )

    #: `\\include` menambahkan `.tex` sendiri, jadi ekstensinya dibuang di sini.
    includes = [
        f"\\include{{chapters/{chapter_latex_filename(number)[: -len('.tex')]}}}"
        for number in chapter_numbers
    ]
    filled = [*lines[: begin + 1], *includes, *lines[end:]]

    escaped = escape_latex(title)
    titled = [
        f"\\title{{{escaped}}}" if line.strip().startswith("\\title{") else line
        for line in filled
    ]
    return "\n".join(titled)


def _marker_index(lines: Sequence[str], marker: str) -> int:
    """Indeks baris yang memuat ``marker`` (MURNI).

    :raises ConfigError: bila penandanya tidak ada.
    """
    index = next((i for i, line in enumerate(lines) if line.strip().startswith(marker)), None)
    if index is None:
        raise ConfigError(
            f"Template main.tex tidak memuat penanda {marker!r}; berkas itu harus "
            "berasal dari `latex/templates/main.tex` atau memuat kedua penandanya sendiri."
        )
    return index


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
    "CHAPTER_BLOCK_BEGIN",
    "CHAPTER_BLOCK_END",
    "LatexBuildResult",
    "LatexChapter",
    "bibliography_entries",
    "bibliography_sources",
    "build_advisories",
    "build_problems",
    "chapter_citations",
    "chapter_latex_filename",
    "chapter_number_from_filename",
    "citation_key",
    "crossref_findings",
    "escape_latex",
    "extract_cite_keys",
    "extract_labels",
    "extract_ref_keys",
    "inspect_latex",
    "lock_chapter_number",
    "log_excerpt",
    "render_bibliography",
    "render_chapter_latex",
    "render_main_tex",
    "strip_tex_comments",
    "unbalanced_environments",
]
