"""Aturan bisnis atas record bab — MURNI (hanya stdlib + pydantic).

Inilah tempat "perilaku" tinggal, terpisah dari tipe data di ``domain/chapter``.
Setiap fungsi mengembalikan **record baru**; tidak ada yang memutasi apa pun.
"""

from __future__ import annotations

from typing import Sequence

from domain.book import BookSpec, ChapterSpec
from domain.chapter import (
    ChapterDraft,
    ChapterRecord,
    ResearchPackage,
    ReviewResult,
    ReviewVerdict,
)
from domain.enums import ChapterEvent, ChapterStatus, is_approved
from domain.errors import ChapterNotPlannedError, InvalidGateError
from domain.ports import ReviewGate
from domain.transitions import can_advance, transition


# ---------------------------------------------------------------------------
# Pencarian
# ---------------------------------------------------------------------------
def find_chapter(spec: BookSpec, number: int) -> ChapterSpec:
    """Spesifikasi bab ``number`` dari ``spec``.

    :raises ChapterNotPlannedError: bila bab tersebut tidak ada.
    """
    for chapter in spec.chapters:
        if chapter.number == number:
            return chapter
    raise ChapterNotPlannedError(number, spec.chapter_numbers())


def pending_numbers(
    spec: BookSpec,
    records: Sequence[ChapterRecord],
    *,
    force: bool = False,
) -> tuple[int, ...]:
    """Nomor bab yang masih perlu dikerjakan, terurut.

    Yang dilewati adalah bab yang sudah **selesai** (:func:`~domain.enums.is_approved`),
    bukan sekadar yang statusnya terminal — inilah inti resume (§28). Bab
    berstatus ``FAILED`` juga terminal, tetapi "berhenti karena rusak" justru
    keadaan yang paling perlu dikerjakan ulang; menyamakan keduanya membuat
    ``run`` yang dijalankan dua kali melewati tepat bab-bab yang gagal.
    """
    done = {r.number for r in records if is_approved(r.status)} if not force else set()
    return tuple(n for n in spec.chapter_numbers() if n not in done)


# ---------------------------------------------------------------------------
# Transisi yang membawa data
# ---------------------------------------------------------------------------
def with_research(record: ChapterRecord, package: ResearchPackage) -> ChapterRecord:
    """Lampirkan paket riset dan majukan status ke ``RESEARCHED``."""
    return record.model_copy(
        update={
            "research": package,
            "status": transition(record.status, ChapterEvent.RESEARCH),
            "error": None,
        }
    )


def with_draft(record: ChapterRecord, draft: ChapterDraft) -> ChapterRecord:
    """Lampirkan draf dan majukan status ke ``DRAFTED``.

    Dipanggil baik untuk draf pertama maupun untuk draf ulang setelah revisi —
    keduanya sah dari ``RESEARCHED`` maupun ``REVISION`` (lihat tabel §27).
    """
    return record.model_copy(
        update={
            "draft": draft,
            "status": transition(record.status, ChapterEvent.DRAFT),
            "error": None,
        }
    )


def with_gate_result(
    record: ChapterRecord,
    result: ReviewResult,
    *,
    produces: ChapterStatus,
) -> ChapterRecord:
    """Terapkan vonis sebuah gate (MURNI).

    Gate lulus → status naik ke ``produces``; gate gagal → ``FAILED_REVIEW``
    dan penghitung revisi bertambah.

    :raises InvalidGateError: bila ``produces`` bukan kemajuan yang sah.
    """
    if result.approved:
        if not can_advance(record.status, produces):
            raise InvalidGateError(result.gate, record.status, produces)
        return record.model_copy(
            update={
                "status": produces,
                "reviews": (*record.reviews, result),
                "error": None,
            }
        )

    return record.model_copy(
        update={
            "status": transition(record.status, ChapterEvent.REVIEW_FAIL),
            "reviews": (*record.reviews, result),
            "revision": record.revision + 1,
        }
    )


def with_failure(record: ChapterRecord, message: str) -> ChapterRecord:
    """Tandai bab gagal keras, simpan alasannya, jangan hilangkan pekerjaan."""
    return record.model_copy(
        update={
            "status": transition(record.status, ChapterEvent.FAIL),
            "error": message,
        }
    )


def reset_for_rerun(record: ChapterRecord) -> ChapterRecord:
    """Kembalikan record ke ``PLANNED`` untuk ``--force`` (MURNI).

    ``spec`` dipertahankan — ia berasal dari BookSpec, bukan hasil kerja bab.
    """
    return record.model_copy(
        update={
            "status": ChapterStatus.PLANNED,
            "revision": 0,
            "research": None,
            "draft": None,
            "reviews": (),
            "markdown_path": None,
            "error": None,
        }
    )


def progress_status(record: ChapterRecord) -> ChapterStatus:
    """Status paling maju yang **konsisten dengan data** yang dimiliki ``record``.

    Bukan "status yang seharusnya" — hanya data yang diperiksa di sini, bukan
    vonis gate. Sebuah record yang membawa draf hasil penilaian yang ditolak
    (``REVISION``) maupun yang diterima (``REVIEWED``) sama-sama dijawab
    ``DRAFTED`` di sini, karena keduanya memang layak melanjutkan dari draf.

    Itu bukan kekurangan yang tersisa: setelah dibuka kembali, gerbang dijalankan
    lagi, dan gerbang yang sudah pernah meloloskan bab ini akan meloloskannya lagi.
    Yang tidak dapat dipulihkan hanyalah **nomor** tahap yang sudah dilewati, dan
    itu tidak pernah disimpan di record — ``ReviewResult`` tidak membawa status
    yang dihasilkannya. Mengulang penilaian jauh lebih murah daripada mengulang
    penulisan, jadi inilah pertukaran yang dipilih.
    """
    if record.draft is not None:
        return ChapterStatus.DRAFTED
    if record.research is not None:
        return ChapterStatus.RESEARCHED
    return ChapterStatus.PLANNED


def reopen_failed(record: ChapterRecord) -> ChapterRecord:
    """Buka kembali bab yang gagal agar dapat dikerjakan ulang (§28, §35) (MURNI).

    ``FAILED`` berarti "percobaan **ini** ditinggalkan", bukan "bab ini tidak akan
    pernah bisa dikerjakan". :func:`pending_numbers` sudah memasukkan bab yang gagal
    ke dalam daftar kerja — itulah gunanya, dan itu pula yang membuat ``run`` yang
    dijalankan dua kali tidak melewati tepat bab-bab yang rusak. Tetapi tabel §27
    tidak punya baris yang keluar dari ``FAILED``, dan memang tidak seharusnya:
    tidak ada *langkah pipeline* yang membawa ke sana, jadi tidak ada langkah
    pipeline yang membawanya pergi.

    Membuka kembali bukan langkah pipeline, melainkan keputusan untuk memulai
    percobaan **baru** — dan karena itu statusnya ditulis langsung, sebagaimana
    :func:`reset_for_rerun` juga melakukannya. Yang membedakan keduanya adalah apa
    yang dibuang:

    * ``reset_for_rerun`` (``--force``) membuang **seluruh** pekerjaan dan mulai
      dari ``PLANNED``.
    * ``reopen_failed`` (otomatis) **mempertahankan** riset, spesifikasi, dan draf.
      Ketiganya tidak pernah menjadi tidak berlaku hanya karena langkah
      *sesudahnya* gagal, dan membuangnya berarti membayar beberapa panggilan model
      untuk menggantinya dengan hasil yang berbeda.

    Statusnya dipulihkan lewat :func:`progress_status` supaya **sesuai** dengan data
    itu. Tanpa itu, ``with_draft`` menolak ``(FAILED, DRAFT)`` dan bab yang gagal
    tidak akan pernah dapat diselesaikan: ``run`` pertama menandainya gagal, dan
    setiap ``run`` berikutnya mati dengan ``IllegalTransitionError``. Itu terjadi
    sungguhan (2026-10-07) — satu-satunya jalan keluarnya ketika itu adalah
    ``--force``, yang membuang pekerjaan yang masih baik.
    """
    if record.status is not ChapterStatus.FAILED:
        return record

    return record.model_copy(
        update={
            "status": progress_status(record),
            # Alasan kegagalan percobaan sebelumnya dibuang, seperti pada setiap
            # tahap lain: record ini sekarang mewakili percobaan yang **baru**, dan
            # ``error`` yang tertinggal akan terbaca sebagai kegagalan percobaan
            # yang sedang berjalan. Sebabnya sudah dilaporkan di laporan run yang
            # menemukannya.
            "error": None,
        }
    )


# ---------------------------------------------------------------------------
# Validasi konfigurasi (gagal di baris pertama, bukan di tengah proses)
# ---------------------------------------------------------------------------
def validate_gates(gates: Sequence[ReviewGate]) -> None:
    """Pastikan setiap gate memajukan rantai §27 secara sah.

    Dipanggil composition root saat start. Konfigurasi gate yang salah lebih
    baik gagal sebelum satu token pun dibakar.

    :raises InvalidGateError: bila ada gate yang melompat mundur atau keluar rantai.
    """
    for gate in gates:
        if not can_advance(ChapterStatus.DRAFTED, gate.produces):
            raise InvalidGateError(gate.name, ChapterStatus.DRAFTED, gate.produces)


# ---------------------------------------------------------------------------
# Rekonsiliasi keluaran Planner
# ---------------------------------------------------------------------------
def reconcile_book_spec(
    spec: BookSpec,
    *,
    target_chapters: int,
) -> tuple[BookSpec, tuple[str, ...]]:
    """Rapikan keluaran Book Planner menjadi spesifikasi yang dapat dijalankan (MURNI).

    Dua hal yang **selalu** perlu dirapikan pada keluaran LLM, dan keduanya
    tidak dapat dicegah oleh skema JSON mana pun:

    * **Nomor bab tidak berurutan atau berlompatan.** Skema hanya menuntut
      ``number >= 1``; ia tidak dapat menyatakan "1, 2, 3, … tanpa bolong".
      Nomor yang berlompatan merusak nama berkas (``chapter03.md`` tanpa
      ``chapter02.md``) dan membuat resume menanyakan bab yang tidak ada.
      Karena itu bab **dinomori ulang menurut urutannya** — urutan itulah yang
      bermakna, bukan angka yang kebetulan ditulis model.
    * **Bab tanpa judul.** ``title`` kosong menghasilkan bab yang tidak dapat
      ditulis maupun dirujuk; ia dibuang, bukan dibiarkan menggantung.

    Penomoran ulang tidak menyembunyikan masalah: setiap perubahan dilaporkan
    sebagai catatan, dan jumlah bab yang tidak sesuai target adalah catatan
    yang paling penting.

    :returns: ``(spesifikasi bersih, catatan)``.
    """
    notes: list[str] = []

    kept = [chapter for chapter in spec.chapters if chapter.title.strip()]
    dropped = len(spec.chapters) - len(kept)
    if dropped:
        notes.append(f"{dropped} bab dibuang karena tidak memiliki judul")

    renumbered = tuple(
        chapter.model_copy(update={"number": index})
        for index, chapter in enumerate(kept, start=1)
    )

    if [c.number for c in kept] != list(range(1, len(kept) + 1)):
        notes.append("nomor bab dirapikan menjadi berurutan mulai dari 1")

    if len(renumbered) != target_chapters:
        notes.append(
            f"perencana menghasilkan {len(renumbered)} bab, "
            f"sedangkan {target_chapters} diminta"
        )

    if not renumbered:
        notes.append("tidak ada bab yang dapat dijalankan")

    return spec.model_copy(update={"chapters": renumbered}), tuple(notes)


# ---------------------------------------------------------------------------
# Rekonsiliasi keluaran Chapter Planner
# ---------------------------------------------------------------------------
def reconcile_chapter_spec(
    produced: ChapterSpec,
    *,
    base: ChapterSpec,
) -> tuple[ChapterSpec, tuple[str, ...]]:
    """Kunci keluaran Chapter Planner ke batas yang sudah ditetapkan (MURNI).

    Chapter Planner bekerja **di dalam** keputusan Book Planner, bukan
    menggantikannya. Tiga hal dipulihkan di sini, dan tiap pemulihan dilaporkan:

    * **``number``** — nomor bab menentukan nama berkas dan urutan resume. Model
      yang mengembalikan nomor lain akan menulis ``chapter05.md`` untuk bab 3,
      dan ``chapter03.md`` tidak akan pernah ada.
    * **``objectives``** — tujuan pembelajaran mengalir dari RPS lewat Book
      Planner. Menulisnya ulang di sini memutus rantai itu, sehingga penilai
      kelak tidak punya acuan untuk menilai draf. Prompt memang memerintahkan
      "pertahankan apa adanya", tetapi perintah di prompt adalah permintaan,
      bukan jaminan — dan inilah jaminannya.
    * **``sections`` yang kosong** — rencana tanpa sub-bab tidak memberi penulis
      apa pun. Kerangka dari Book Planner dipakai sebagai gantinya; lebih baik
      daripada tidak ada, dan tetap dicatat supaya tidak terlihat seperti
      rencana yang disengaja.

    :returns: ``(spesifikasi terkunci, catatan)``.
    """
    notes: list[str] = []

    sections = tuple(section.strip() for section in produced.sections if section.strip())
    if not sections:
        fallback = tuple(section.strip() for section in base.sections if section.strip())
        notes.append(
            "perencana bab tidak menghasilkan sub-bab; memakai kerangka perencana buku"
            if fallback
            else "bab ini tidak memiliki rencana sub-bab"
        )
        sections = fallback

    if produced.number != base.number:
        notes.append(f"nomor bab dikembalikan ke {base.number} (model menulis {produced.number})")

    if produced.objectives != base.objectives:
        notes.append("tujuan pembelajaran dikembalikan ke versi perencana buku")

    if not produced.title.strip():
        notes.append("judul bab kosong; memakai judul dari perencana buku")

    fixed = produced.model_copy(
        update={
            "number": base.number,
            "title": produced.title.strip() or base.title,
            "objectives": base.objectives,
            "sections": sections,
            # Rujukan tidak boleh diciptakan di tingkat bab. Chapter Planner
            # tidak melakukan retrieval apa pun, jadi setiap judul buku yang ia
            # tulis adalah karangan — persis kelas halusinasi yang dilarang §34.
            "references": base.references,
            "source_weeks": base.source_weeks,
        }
    )
    return fixed, tuple(notes)


# ---------------------------------------------------------------------------
# Sitasi & kontrak draf (§34, §36)
# ---------------------------------------------------------------------------
def citations_allowed_by(
    research: ResearchPackage,
    *,
    inherited: Sequence[str] = (),
) -> frozenset[str]:
    """Sumber yang **boleh** muncul di ``ChapterDraft.citations`` (MURNI).

    Aturannya berbeda menurut kondisi bahan, dan perbedaannya disengaja:

    * **Paket terdegradasi (RAG belum ada).** Tidak ada sumber yang boleh
      dikutip — himpunan kosong, ditambah ``inherited`` bila pemanggilnya
      membawa sitasi yang sudah ada sebelumnya. Ini yang menegakkan aturan
      prompt penulis "jangan mencantumkan satu pun rujukan" secara mekanis.
    * **Paket terisi.** Yang boleh dikutip adalah sumber yang benar-benar
      tercantum di dalam paket itu. Judul yang tidak ada di sana adalah
      karangan, sekalipun bentuknya masuk akal.

    Sitasi disimpan sebagai ``str`` bebas, bukan id, sehingga pencocokannya
    harus persis. Itu memang kasar — dan lebih baik kasar daripada longgar:
    kelonggaran di sini berarti membiarkan rujukan karangan lolos ke dalam buku.

    :param inherited: sitasi yang sudah ada di draf sebelumnya. Dipakai jalur
        revisi: penulis tidak boleh **menambah** rujukan, tetapi tidak juga
        dihukum karena mempertahankan yang sudah lolos sebelumnya.
    """
    if research.degraded:
        return frozenset(inherited)

    from_evidence = {item.source for item in research.evidence if item.source.strip()}
    return frozenset(from_evidence | set(research.sources) | set(inherited))


def enforce_draft_contract(
    draft: ChapterDraft,
    *,
    spec: ChapterSpec,
    allowed_citations: frozenset[str],
) -> tuple[ChapterDraft, tuple[str, ...]]:
    """Kunci draf pada dua hal yang tidak boleh diserahkan kepada model (MURNI).

    **Tujuan pembelajaran.** Prompt memerintahkan "sama persis dengan yang di
    input", tetapi perintah di prompt adalah permintaan. Tujuan yang ditulis
    ulang di sini akan membuat draf tidak lagi dapat dinilai terhadap RPS —
    dan itu baru terlihat jauh kemudian, saat seluruh bab sudah ditulis.

    **Sitasi.** Ini permukaan halusinasi yang paling berbahaya di seluruh
    sistem: rujukan yang tidak ada tampak persis seperti rujukan yang ada.
    Penyaringan di sini menegakkan §34 secara mekanis, bukan dengan harapan.

    Keduanya dilaporkan sebagai catatan — draf yang "dibersihkan" diam-diam
    akan menyembunyikan bahwa modelnya tidak menaati kontraknya.

    :returns: ``(draf terkunci, catatan)``.
    """
    notes: list[str] = []

    if draft.learning_objectives != spec.objectives:
        notes.append("tujuan pembelajaran pada draf dikembalikan ke versi perencana")

    seen: set[str] = set()
    kept: list[str] = []
    dropped = 0
    for citation in draft.citations:
        normalized = citation.strip()
        if not normalized or normalized not in allowed_citations:
            dropped += 1
            continue
        if normalized in seen:  # duplikat: tidak salah, tetapi tidak berguna
            continue
        seen.add(normalized)
        kept.append(normalized)

    if dropped:
        notes.append(f"{dropped} sitasi dibuang karena tidak didukung bahan rujukan")

    if not draft.title.strip():
        notes.append("judul draf kosong; memakai judul dari spesifikasi bab")

    fixed = draft.model_copy(
        update={
            "title": draft.title.strip() or spec.title,
            "learning_objectives": spec.objectives,
            "citations": tuple(kept),
        }
    )
    return fixed, tuple(notes)


# ---------------------------------------------------------------------------
# Vonis gate
# ---------------------------------------------------------------------------
def decide_review(
    verdict: ReviewVerdict,
    *,
    gate: str,
    threshold: int,
) -> ReviewResult:
    """Ubah vonis model menjadi vonis sistem (MURNI).

    Ambang skor ditegakkan **di sini**, bukan hanya di prompt. Prompt peninjau
    memang menyatakan "``approved`` benar hanya bila skor >= 7", tetapi perintah
    di prompt adalah permintaan: model yang menulis "sebenarnya sudah cukup
    baik, skor 6" lalu tetap menyetujui akan meloloskan bab di bawah ambang tanpa
    satu pun tanda. Bab yang lolos karena kelelahan peninjau adalah bab yang
    tidak akan pernah ditinjau lagi.

    Ketidaksepakatan itu **dilaporkan**, bukan disembunyikan: catatan tambahan
    menjelaskan bahwa vonis model diabaikan dan mengapa. Menelannya akan membuat
    laporan akhir tampak seperti penolakan biasa, dan tidak ada yang tahu bahwa
    peninjau sebenarnya menyetujui.

    :returns: :class:`~domain.chapter.ReviewResult` dengan identitas gate kita —
        model tidak pernah diminta mengarang nama gate-nya sendiri.
    """
    approved = verdict.approved and verdict.score >= threshold

    feedback = list(verdict.feedback)
    if verdict.approved and not approved:
        feedback.append(
            f"Skor {verdict.score} berada di bawah ambang {threshold}; "
            "persetujuan peninjau diabaikan oleh sistem."
        )

    return ReviewResult(
        gate=gate,
        approved=approved,
        score=verdict.score,
        feedback=tuple(feedback),
        skipped=False,
    )
