"""Pengurai RPS (§12) — deterministik, tanpa model, tanpa berkas.

Rencana Pembelajaran Semester punya struktur yang **tegas**: bagian bernama,
butir bernomor, tabel berlabel. Mengurai struktur seperti itu dengan model
bahasa berarti menukar kepastian dengan kemungkinan: kode CPMK yang dikarang
tidak dapat dibedakan dari yang benar oleh siapa pun yang membaca keluarannya,
sedangkan parser yang salah **gagal dengan cara yang terlihat**. Karena itu
seluruh modul ini deterministik.

Ia murni atas teks — tidak membuka berkas, tidak memanggil model — tetapi ia
tetap tinggal di ``ingestion/`` dan bukan di ``domain/``: penguraian teks
memerlukan ``re``, dan ``re`` termasuk daftar terlarang ``domain/``. Yang ada di
``domain/rps`` adalah hasilnya, bukan cara memperolehnya.

Berkas ini **tidak pernah melempar** karena bentuk RPS yang tidak dikenal.
Serahkan padanya berkas apa pun: yang kembali adalah :class:`~domain.rps.CoursePlan`
yang mungkin nyaris kosong, beserta catatan yang menyebut apa yang tidak terbaca.
Keputusan "masih berguna atau tidak" ada di pemanggil, dan pemanggil punya jalan
keluar: teks mentahnya. Parser yang menghentikan seluruh pipeline karena satu
berkas RPS berformat lain adalah kemunduran, bukan perbaikan.
"""

from __future__ import annotations

import re

from domain.rps import (
    AssessmentItem,
    CourseIdentity,
    CoursePlan,
    LearningOutcome,
    OutcomeKind,
    WeekPlan,
)

#: ``\section*{Judul}`` dan variannya. Isi judul diasumsikan tanpa kurung kurawal
#: bersarang — asumsi yang aman untuk judul bagian, dan yang gagal dengan tenang
#: bila dilanggar (judulnya tinggal tidak dikenali dan dirender apa adanya).
_SECTION = re.compile(r"\\section\*?\{([^{}]*)\}")

#: ``\item`` beserta spasi sesudahnya.
_ITEM = re.compile(r"\\item\b\s*")

#: ``Minggu 9 - Pohon seimbang`` / ``Minggu 9: Pohon seimbang`` / ``Minggu 9 — ...``.
_WEEK = re.compile(r"^Minggu\s+(\d+)\s*(?:[-–—:]|\s)\s*(.*)$", re.IGNORECASE)

#: Kode butir capaian: ``CPMK-1: …``, ``Sub-CPMK-2.1: …``, ``CPL-3: …``.
_CODE = re.compile(r"^((?:Sub-)?CPMK-\d+(?:\.\d+)?|CPL-\d+)\s*:\s*(.+)$", re.IGNORECASE)

#: Perintah sebaris yang isinya justru yang kita inginkan.
_INLINE_WRAP = re.compile(r"\\(?:emph|textbf|textit|texttt|text)\{([^{}]*)\}")

#: Perintah tanpa isi yang hanya mengatur tampilan.
_INLINE_DROP = re.compile(r"\\(?:noindent|centering|small|large|bfseries|itshape|newline)\b")

#: Pelolosan karakter khusus LaTeX → karakter aslinya.
_ESCAPES = (
    (r"\&", "&"),
    (r"\%", "%"),
    (r"\_", "_"),
    (r"\#", "#"),
    (r"\$", "$"),
    (r"\{", "{"),
    (r"\}", "}"),
)

#: Akhiran baris LaTeX: ``\\`` dan ``\\[3pt]``.
_ROW_BREAK = re.compile(r"\\\\\s*(?:\[[^\]]*\])?")

#: ``\begin{...}{...}`` / ``\end{...}`` / ``\hline`` — struktur, bukan isi.
#:
#: Argumen kedua ikut dibuang karena di sanalah spesifikasi kolom berada:
#: ``\begin{tabular}{ll}`` yang tersisa sebagai ``{ll}`` akan muncul di ringkasan
#: sebagai baris pertama tabel, dan pada tabel penilaian ia terbaca sebagai
#: komponen bernama "{ll}".
_STRUCTURAL = re.compile(
    r"\\(?:begin|end)\{[^{}]*\}(?:\{[^{}]*\})?|\\(?:hline|toprule|midrule|bottomrule)\b"
)

#: Kata yang menandai baris **judul tabel** pada "Metode Penilaian".
#:
#: Baris seperti ``Aktivitas & Bobot & CPMK`` bukan komponen penilaian; bila ikut
#: terbaca, ia muncul di ringkasan sebagai komponen bernama "Aktivitas" dengan
#: bobot "Bobot". Daftar ini sengaja sempit: baris yang salah dibuang lebih mahal
#: daripada baris yang salah diterima, jadi hanya kata yang **tidak mungkin**
#: menjadi nama komponen yang masuk ke sini.
_TABLE_HEADER_WORDS = frozenset(
    {
        "aktivitas",
        "kegiatan",
        "komponen",
        "bobot",
        "nilai",
        "cpmk",
        "cpl",
        "penilaian",
        "deskripsi",
    }
)

#: Kata yang menandai minggu **penilaian**, bukan minggu kuliah (§12).
#:
#: Sengaja sesempit satu kata. "Kuis", "presentasi", dan "responsi" tetap minggu
#: kuliah — minggu yang memuat keduanya masih punya bahan untuk ditulis, sedangkan
#: "Ujian Tengah Semester" tidak punya sama sekali.
_ASSESSMENT_MARKERS = ("ujian",)

#: Label tabel identitas → nama bidang :class:`~domain.rps.CourseIdentity`.
#:
#: Kunci sudah dinormalkan: huruf kecil, spasi tunggal. Label yang tidak ada di
#: sini tidak hilang — ia masuk ke ``CourseIdentity.extra``.
_IDENTITY_LABELS: dict[str, str] = {
    "mata kuliah": "name",
    "nama mata kuliah": "name",
    "nama mk": "name",
    "kode": "code",
    "kode mata kuliah": "code",
    "kode mk": "code",
    "sks": "credits",
    "bobot sks": "credits",
    "kredit": "credits",
    "semester": "semester",
    "program studi": "programme",
    "prodi": "programme",
    "jurusan": "programme",
    "prasyarat": "prerequisites",
    "prasyarat mata kuliah": "prerequisites",
    "dosen": "lecturer",
    "dosen pengampu": "lecturer",
    "pengampu": "lecturer",
}

#: Judul bagian → perannya. Diperiksa berurutan, yang pertama cocok menang.
_SECTION_KINDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("sub-cpmk", "sub cpmk"), "sub_cpmk"),
    (("cpmk",), "cpmk"),
    (("capaian pembelajaran lulusan", "cpl"), "cpl"),
    (("identitas",), "identity"),
    (("deskripsi",), "description"),
    (("rencana mingguan", "kalender", "mingguan", "jadwal"), "weeks"),
    (("metode penilaian", "penilaian", "assessment"), "assessments"),
    (("referensi", "daftar pustaka", "rujukan"), "references"),
)


# ---------------------------------------------------------------------------
# Pembersihan teks LaTeX (murni)
# ---------------------------------------------------------------------------
def strip_comments(text: str) -> str:
    """Buang komentar ``%`` sampai akhir baris, tanpa menyentuh ``\\%`` (MURNI).

    Komentar RPS contoh memuat catatan penulis berkas — dan salah satunya justru
    menyatakan bahwa parser terstruktur "menyusul di tahap berikutnya". Catatan itu
    tidak boleh ikut masuk ke prompt perencana sebagai isi RPS.
    """
    lines: list[str] = []
    for line in text.splitlines():
        cut = len(line)
        index = 0
        while index < len(line):
            if line[index] == "%" and (index == 0 or line[index - 1] != "\\"):
                cut = index
                break
            index += 1
        lines.append(line[:cut])
    return "\n".join(lines)


def clean_latex(text: str) -> str:
    """Ubah penggalan LaTeX menjadi teks biasa (MURNI).

    Perintah yang isinya bermakna (``\\emph``, ``\\textbf``) dipertahankan isinya
    dan dibuang pembungkusnya; perintah yang hanya mengatur tampilan dibuang
    seluruhnya; struktur ``\\begin``/``\\end``/``\\hline`` hilang; pelolosan
    karakter (``\\&``, ``\\%``) dikembalikan ke karakter aslinya. Spasi berlebih
    dirapikan menjadi satu spasi, karena hasilnya dibaca sebagai kalimat, bukan
    sebagai sumber LaTeX.

    ``\\\\`` (akhir baris tabel) diganti baris baru, bukan spasi: tanpa itu,
    seluruh isi tabel identitas menjadi satu baris tunggal dan penguraian
    barisnya kehilangan batas.
    """
    cleaned = _INLINE_DROP.sub(" ", text)
    cleaned = _STRUCTURAL.sub(" ", cleaned)
    # Dua kali: ``\emph{\textbf{x}}`` bersarang.
    for _ in range(2):
        cleaned = _INLINE_WRAP.sub(r"\1", cleaned)
    for escaped, plain in _ESCAPES:
        cleaned = cleaned.replace(escaped, plain)
    cleaned = _ROW_BREAK.sub("\n", cleaned)
    return normalize_spaces(cleaned)


def normalize_spaces(text: str) -> str:
    """Rapikan spasi dan baris kosong tanpa mengubah urutan kata (MURNI)."""
    kept = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(kept).strip()


# ---------------------------------------------------------------------------
# Penggalan per bagian (murni)
# ---------------------------------------------------------------------------
def split_sections(text: str) -> tuple[tuple[str, str], ...]:
    """Potong RPS menjadi ``(judul, isi)`` menurut ``\\section`` (MURNI).

    Teks sebelum bagian pertama — prakata — dibuang. Pada RPS yang lazim, bagian
    itu hanya berisi komentar; bila ia ternyata memuat kalimat sungguhan, kalimat
    itu tetap terlihat karena ia akan terbaca oleh penguraian bagian yang
    bersangkutan... kecuali bila RPS-nya memang tidak punya bagian sama sekali,
    dan dalam hal itu :func:`parse_rps` melaporkannya sebagai catatan.
    """
    matches = list(_SECTION.finditer(text))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(1).strip(), text[match.end():end]))
    return tuple(sections)


def clean_items(body: str) -> tuple[str, ...]:
    """Butir-butir ``\\item`` beserta lanjutannya, digabung (MURNI).

    Butir RPS lazim terpotong di tengah kalimat karena lebar halaman. Mengambil
    baris pertama saja akan memenggal setiap CPMK pada kata ke-delapan, dan
    perencana akan menyusun buku dari separuh kalimat.
    """
    parts = _ITEM.split(body)
    items: list[str] = []
    for part in parts[1:]:  # teks sebelum \item pertama bukan butir
        joined = " ".join(line.strip() for line in clean_latex(part).splitlines())
        item = " ".join(joined.split()).strip()
        if item:
            items.append(item)
    return tuple(items)


def table_rows(body: str) -> tuple[tuple[str, ...], ...]:
    """Baris tabel LaTeX sebagai daftar sel, tanpa baris kosong dan judul (MURNI).

    Baris dipisah menurut **baris teks**, bukan dengan mencari ``\\\\``: pemisah
    itu sudah menjadi baris baru oleh :func:`clean_latex`, dan mencarinya lagi di
    sini berarti tidak menemukan apa pun — seluruh tabel lalu terbaca sebagai satu
    baris raksasa, dan identitas mata kuliah muncul sebagai satu bidang panjang
    alih-alih tujuh.
    """
    rows: list[tuple[str, ...]] = []
    for raw_row in clean_latex(body).splitlines():
        cells = tuple(" ".join(cell.split()) for cell in raw_row.split("&"))
        if not any(cells):
            continue
        rows.append(cells)
    return tuple(rows)


def _is_header_row(cells: tuple[str, ...]) -> bool:
    """Baris yang seluruh selnya kata judul tabel."""
    words = {cell.strip().lower() for cell in cells if cell.strip()}
    return bool(words) and words <= _TABLE_HEADER_WORDS


def _section_kind(title: str) -> str | None:
    """Peran sebuah bagian, atau ``None`` bila tidak dikenali."""
    haystack = " ".join(title.lower().split())
    for needles, kind in _SECTION_KINDS:
        if any(needle in haystack for needle in needles):
            return kind
    return None


# ---------------------------------------------------------------------------
# Pengurai per bagian (murni)
# ---------------------------------------------------------------------------
def parse_identity(body: str) -> CourseIdentity:
    """Tabel identitas → :class:`~domain.rps.CourseIdentity` (MURNI).

    Sel yang memuat lebih dari satu nilai (``Key & Nilai`` pada satu baris) sudah
    terpisah oleh pemotongan ``&``; baris yang tidak memuat ``&`` sama sekali
    diabaikan karena ia tidak menyatakan pasangan apa pun.
    """
    known: dict[str, str] = {}
    extra: list[tuple[str, str]] = []

    for cells in table_rows(body):
        if len(cells) < 2 or _is_header_row(cells):
            continue
        label = cells[0].strip()
        value = " ".join(cell for cell in cells[1:] if cell).strip()
        if not label or not value:
            continue
        field = _IDENTITY_LABELS.get(" ".join(label.lower().split()))
        if field is None:
            extra.append((label, value))
        elif field not in known:
            known[field] = value

    return CourseIdentity(
        name=known.get("name", ""),
        code=known.get("code", ""),
        credits=known.get("credits", ""),
        semester=known.get("semester", ""),
        programme=known.get("programme", ""),
        prerequisites=known.get("prerequisites", ""),
        lecturer=known.get("lecturer", ""),
        extra=tuple(extra),
    )


def parse_outcomes(
    body: str,
    *,
    kind: OutcomeKind,
) -> tuple[tuple[LearningOutcome, ...], tuple[str, ...]]:
    """Butir capaian → :class:`~domain.rps.LearningOutcome` beserta catatannya (MURNI).

    Butir yang tidak memakai pola ``CPMK-N:`` tetap diterima dengan ``code=""``
    dan **dilaporkan**: ia masih berguna sebagai konteks, tetapi ia tidak dapat
    dirujuk, dan perencana yang mengira dapat merujuknya akan menulis bab yang
    menunjuk capaian yang tidak ada.
    """
    outcomes: list[LearningOutcome] = []
    notes: list[str] = []
    uncoded = 0

    for item in clean_items(body):
        match = _CODE.match(item)
        if match is None:
            uncoded += 1
            outcomes.append(LearningOutcome(statement=item, kind=kind))
            continue
        outcomes.append(
            LearningOutcome(
                code=match.group(1).strip(), statement=match.group(2).strip(), kind=kind
            )
        )

    if uncoded:
        notes.append(
            f"{uncoded} butir di bagian {kind.value} tidak memakai pola "
            f"'{kind.value}-N:' sehingga tidak dapat dirujuk"
        )
    return tuple(outcomes), tuple(notes)


def parse_weeks(body: str) -> tuple[tuple[WeekPlan, ...], tuple[str, ...]]:
    """Daftar minggu → :class:`~domain.rps.WeekPlan` beserta catatannya (MURNI).

    Urutan sumber **dipertahankan**, bahkan ketika nomornya melompat atau
    mundur. Mengurutkannya akan menyembunyikan kekacauan yang justru perlu
    dilihat manusia yang menyusun RPS itu.
    """
    weeks: list[WeekPlan] = []
    notes: list[str] = []
    seen: set[int] = set()
    unparsed = 0
    duplicates: list[int] = []

    for item in clean_items(body):
        match = _WEEK.match(item)
        if match is None:
            unparsed += 1
            continue
        number = int(match.group(1))
        topic = match.group(2).strip()
        if number in seen:
            duplicates.append(number)
            continue
        seen.add(number)
        weeks.append(
            WeekPlan(
                number=number,
                topic=topic,
                is_assessment=any(marker in topic.lower() for marker in _ASSESSMENT_MARKERS),
            )
        )

    if unparsed:
        notes.append(
            f"{unparsed} butir di bagian rencana mingguan tidak memakai pola "
            "'Minggu N - topik' dan diabaikan"
        )
    if duplicates:
        listed = ", ".join(str(number) for number in duplicates)
        notes.append(f"minggu {listed} disebut lebih dari sekali; yang pertama dipakai")

    numbers = [week.number for week in weeks]
    out_of_order = [
        f"{earlier} → {later}"
        for earlier, later in zip(numbers, numbers[1:])
        if later != earlier + 1
    ]
    if out_of_order:
        notes.append(f"nomor minggu melompat atau mundur: {', '.join(out_of_order)}")

    return tuple(weeks), tuple(notes)


def parse_assessments(body: str) -> tuple[AssessmentItem, ...]:
    """Tabel penilaian → :class:`~domain.rps.AssessmentItem` (MURNI)."""
    items: list[AssessmentItem] = []
    for cells in table_rows(body):
        if len(cells) < 2 or _is_header_row(cells):
            continue
        activity = cells[0].strip()
        if not activity:
            continue
        weight = cells[1].strip() if len(cells) > 1 else ""
        outcomes = _split_outcomes(cells[2]) if len(cells) > 2 else ()
        items.append(AssessmentItem(activity=activity, weight=weight, outcomes=outcomes))
    return tuple(items)


def _split_outcomes(cell: str) -> tuple[str, ...]:
    """``"CPMK-1, CPMK-2"`` → ``("CPMK-1", "CPMK-2")`` (MURNI)."""
    return tuple(part.strip() for part in re.split(r"[,;]", cell) if part.strip())


def _total_weight(items: tuple[AssessmentItem, ...]) -> int | None:
    """Jumlah bobot bila seluruhnya berupa persentase, ``None`` bila tidak (MURNI).

    Hanya dipakai untuk satu catatan: tabel penilaian yang jumlahnya bukan 100%
    biasanya berarti ada baris yang tidak terbaca, dan itu lebih berguna
    diketahui sekarang daripada setelah buku selesai ditulis.
    """
    percentages: list[int] = []
    for item in items:
        digits = item.weight.rstrip("% \t").replace("\\", "").strip()
        if not digits.isdigit():
            return None
        percentages.append(int(digits))
    return sum(percentages) if percentages else None


# ---------------------------------------------------------------------------
# Titik masuk
# ---------------------------------------------------------------------------
def parse_rps(text: str) -> tuple[CoursePlan, tuple[str, ...]]:
    """Uraikan teks RPS menjadi :class:`~domain.rps.CoursePlan` (MURNI).

    Selalu berhasil: hasilnya dapat berupa rencana hampir kosong, dan itulah
    yang dilaporkan. Tidak ada pengecualian yang dilempar dari sini — keputusan
    untuk berhenti bukan milik parser, melainkan milik pemanggil yang tahu apa
    yang masih dapat dikerjakan tanpa RPS yang terbaca.

    :returns: ``(rencana, catatan)``.
    """
    sections = split_sections(strip_comments(text))
    if not sections:
        return (
            CoursePlan(),
            (
                "berkas RPS tidak memuat satu pun \\section{} sehingga tidak ada "
                "bagian yang dapat dikenali",
            ),
        )

    identity = CourseIdentity()
    description = ""
    outcomes: list[LearningOutcome] = []
    weeks: tuple[WeekPlan, ...] = ()
    assessments: tuple[AssessmentItem, ...] = ()
    references: tuple[str, ...] = ()
    others: list[tuple[str, str]] = []
    notes: list[str] = []
    seen_kinds: set[str] = set()

    for title, body in sections:
        kind = _section_kind(title)
        if kind is None:
            others.append((title, normalize_spaces(clean_latex(body))))
            continue
        if kind in seen_kinds:
            notes.append(f"bagian '{title}' muncul lebih dari sekali; yang pertama dipakai")
            continue
        seen_kinds.add(kind)

        if kind == "identity":
            identity = parse_identity(body)
        elif kind == "description":
            description = normalize_spaces(clean_latex(body))
        elif kind in {"cpl", "cpmk", "sub_cpmk"}:
            parsed, outcome_notes = parse_outcomes(body, kind=OutcomeKind(kind.upper()))
            outcomes.extend(parsed)
            notes.extend(outcome_notes)
        elif kind == "weeks":
            weeks, week_notes = parse_weeks(body)
            notes.extend(week_notes)
        elif kind == "assessments":
            assessments = parse_assessments(body)
        elif kind == "references":
            references = clean_items(body)

    if identity.is_empty():
        notes.append("identitas mata kuliah tidak terbaca dari RPS")
    if not weeks:
        notes.append("rencana mingguan tidak terbaca; bab tidak dapat dipetakan ke minggu")
    if not outcomes:
        notes.append("tidak ada CPMK/Sub-CPMK yang terbaca dari RPS")
    if not references:
        notes.append("daftar referensi RPS kosong atau tidak terbaca")

    notes.extend(_orphan_notes(outcomes))
    notes.extend(_weight_notes(assessments))

    return (
        CoursePlan(
            identity=identity,
            description=description,
            outcomes=tuple(outcomes),
            weeks=weeks,
            assessments=assessments,
            references=references,
            other_sections=tuple(others),
        ),
        tuple(notes),
    )


def _orphan_notes(outcomes: list[LearningOutcome]) -> tuple[str, ...]:
    """Sub-CPMK yang CPMK induknya tidak ada di RPS (MURNI).

    Pemetaan yang menggantung tidak menghasilkan galat apa pun — ia menghasilkan
    bab yang mengaku menutup Sub-CPMK-3.2 padahal CPMK-3 tidak pernah ditulis.
    """
    codes = {outcome.code.upper() for outcome in outcomes if outcome.code}
    orphans: list[str] = []
    for outcome in outcomes:
        if outcome.kind is not OutcomeKind.SUB_CPMK or not outcome.code:
            continue
        parent = outcome.code.split(".")[0].replace("Sub-CPMK", "CPMK").strip()
        if parent.upper() not in codes:
            orphans.append(outcome.code)
    if not orphans:
        return ()
    return (f"Sub-CPMK tanpa CPMK induk di RPS: {', '.join(orphans)}",)


def _weight_notes(assessments: tuple[AssessmentItem, ...]) -> tuple[str, ...]:
    """Bobot penilaian yang jumlahnya bukan 100% (MURNI)."""
    total = _total_weight(assessments)
    if total is None or total == 100:
        return ()
    return (f"bobot penilaian berjumlah {total}%, bukan 100% — periksa tabel penilaian",)


__all__ = [
    "clean_items",
    "clean_latex",
    "normalize_spaces",
    "parse_assessments",
    "parse_identity",
    "parse_outcomes",
    "parse_rps",
    "parse_weeks",
    "split_sections",
    "strip_comments",
    "table_rows",
]
