"""Book Director (§31) — orkestrator pipeline bab-per-bab.

Inilah satu-satunya tempat di seluruh aplikasi yang tahu **urutan** pekerjaan.
Setiap agent tahu pekerjaannya sendiri dan tidak tahu apa pun tentang yang lain;
direktur ini yang menyusunnya, dan ia menyusunnya dari konfigurasi, bukan dari
pengetahuan tentang agent mana pun.

**Ia tidak menyebut satu pun nama agent.** Ia mengulang ``self._gates`` — daftar
yang datang dari ``config.pipeline.gates``. Menambahkan pedagogy reviewer besok
berarti menambah satu baris YAML; berkas ini tidak tersentuh. Itulah OCP dalam
bentuk yang dapat diperiksa mesin (lihat :mod:`agents.gates`).

Tiga keputusan di berkas ini perlu diketahui sebelum menyentuhnya:

**1. Kegagalan per-bab dikandung di :meth:`run`, bukan di :meth:`run_chapter`.**
Ini bukan pilihan sewenang-wenang. ``run_chapter`` adalah satu perintah yang
meminta satu hasil: bila bab itu gagal, kegagalannya **adalah** hasilnya, dan
menelannya akan membuat ``write-chapter 3`` keluar dengan kode 0 seolah berhasil.
Sebaliknya ``run`` memproses N bab, dan satu bab buruk tidak boleh membatalkan
N-1 sisanya (§35). Karena itu pengandungnya ada di loop, bukan di langkah.

**2. Kegagalan sistemik tidak dikandung siapa pun.** Kegagalan autentikasi atau
model-hilang berarti setiap bab sisa akan gagal dengan cara yang identik.
Mengandungnya berarti membakar anggaran untuk N bab yang sudah pasti gagal;
membatalkannya segera adalah benar. Bedanya ditegakkan oleh tipe exception di
:data:`SYSTEMIC_ERRORS`, bukan oleh tebakan.

**3. Persetujuan akhir adalah langkah direktur, bukan langkah gate.**
``ChapterReviewer`` menghasilkan ``REVIEWED`` — "bab ini baik". ``APPROVED`` —
"bab ini selesai" — adalah peristiwa tersendiri di rantai §27 (§27 punya
``LATEX_COMPILED → APPROVED`` di antaranya), dan menyamakannya akan menghapus
perbedaan itu untuk selamanya. Ketika gate LaTeX tiba, ia tidak perlu mengubah
apa pun di sini.

**Resume bekerja pada butir terkecil yang sudah selesai.** Setiap tahap
disimpan ke checkpoint segera setelah selesai, dan setiap tahap dilewati bila
hasilnya sudah ada di record:

* ``spec`` ada → Chapter Planner tidak dipanggil lagi.
* ``research`` ada → Researcher tidak dipanggil lagi.
* ``draft`` ada dan status ``REVISION`` → penulis **merevisi**, tidak menulis
  ulang dari nol. Ini yang membuat ``run`` yang terputus di tengah bab
  melanjutkan dari titik putusnya, bukan mengulang bab itu.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.book import BookRequest, BookSpec, ChapterSpec
from domain.chapter import ChapterRecord, ResearchPackage
from domain.enums import ChapterEvent, ChapterStatus, is_approved
from domain.errors import (
    AgentOutputError,
    BookNotPlannedError,
    ChapterNotPlannedError,
    ConfigError,
    GatePreconditionError,
    IllegalTransitionError,
    InvalidGateError,
    ModelAuthError,
    ModelUnavailableError,
    ServiceUnavailableError,
    StateWriteError,
    TruncatedOutputError,
)
from domain.ports import ChapterArtifacts, Reporter, Researcher, ReviewGate, StateStore
from domain.rendering import render_chapter_markdown
from domain.rules import (
    find_chapter,
    pending_numbers,
    reopen_failed,
    reset_for_rerun,
    with_draft,
    with_failure,
    with_gate_result,
    with_research,
)
from domain.state import BookState, RunReport
from domain.transitions import can_advance, status_after_gate, transition

from agents.chapter_planner import ChapterPlannerAgent
from agents.planner import BookPlanner
from agents.writer import DEFAULT_MIN_WORDS, ChapterWriter

#: Jalur berkas state tingkat buku — dipakai hanya untuk pesan kesalahan.
#:
#: Ditulis sebagai literal, bukan diimpor dari ``memory.project_state``: arah
#: dependensi melarang ``agents/`` mengenal lapisan penyimpanan. Yang dibutuhkan
#: di sini hanyalah sebuah nama untuk ditunjuk kepada pengguna, dan nama itu
#: memang bagian dari antarmuka yang terdokumentasi.
BOOK_STATE_HINT = "state/book.json"

#: Kegagalan yang **berskup satu bab**: dicatat pada bab itu, lalu proses lanjut.
#:
#: Semuanya berarti "bab ini tidak dapat diselesaikan seperti ini" — bukan "sisa
#: prosesnya akan gagal juga". Bab yang gagal disimpan sebagai ``FAILED`` beserta
#: pesannya, sehingga ``run`` berikutnya mengerjakannya kembali tanpa perlu
#: ``--force`` (lihat :func:`domain.enums.is_approved`).
CONTAINABLE_ERRORS: tuple[type[BaseException], ...] = (
    AgentOutputError,
    IllegalTransitionError,
    InvalidGateError,
    GatePreconditionError,
    TruncatedOutputError,
)

#: Kegagalan yang **sistemik**: satu bab gagal berarti semua bab sisa gagal sama.
#:
#: ``ModelAuthError`` (kredensial) dan ``ModelUnavailableError`` (model tidak
#: ada) tidak akan membaik dengan sendirinya di bab berikutnya —
#: :func:`domain.rules` dan adapter sengaja **tidak** me-retry keduanya, justru
#: supaya kegagalannya tetap jelas dan cepat. Di sini konsekuensinya ditegakkan:
#: proses dibatalkan sebelum membakar anggaran untuk bab-bab yang sudah pasti
#: bernasib sama.
SYSTEMIC_ERRORS: tuple[type[BaseException], ...] = (
    ModelAuthError,
    ModelUnavailableError,
    ServiceUnavailableError,
    ConfigError,
)


#: Status di mana ``record.draft`` sudah ada **dan sudah siap dinilai**.
#:
#: Menentukan apakah sebuah bab yang dilanjutkan perlu ditulis ulang. Sengaja
#: tidak dirumuskan sebagai ``can_advance(status, DRAFTED)``: ``REVISION`` juga
#: berarti "belum punya draf yang siap" — tetapi bukan karena drafnya tidak ada,
#: melainkan karena drafnya sudah ditolak. Keduanya sama-sama menuntut penulisan
#: ulang, dan itulah satu-satunya hal yang ditanyakan di sini.
#:
#: ``REVIEWED`` dan status LaTeX ikut dimasukkan meski gate-nya sudah lewat:
#: drafnya tetap siap dinilai, dan :meth:`BookDirector._run_gates` yang
#: memutuskan gate mana yang masih perlu dijalankan.
DRAFT_READY_STATUSES: frozenset[ChapterStatus] = frozenset(
    {
        ChapterStatus.DRAFTED,
        ChapterStatus.FACT_CHECKED,
        ChapterStatus.CITATION_CHECKED,
        ChapterStatus.PEDAGOGY_REVIEWED,
        ChapterStatus.CONSISTENCY_CHECKED,
        ChapterStatus.REVIEWED,
        ChapterStatus.LATEX_GENERATED,
        ChapterStatus.LATEX_COMPILED,
    }
)


@dataclass(frozen=True, slots=True)
class DirectorSettings:
    """Angka-angka yang mengatur perilaku direktur.

    Terpisah dari ``config.yaml`` supaya direktur dapat diuji tanpa memuat
    konfigurasi — dan supaya nilai bawaannya terlihat di tipe, bukan tersembunyi
    di dalam berkas YAML.
    """

    #: Berapa kali sebuah draf boleh direvisi sebelum bab itu menyerah (§31).
    #: Total percobaan menulis = ``max_revisions + 1``.
    max_revisions: int = 2

    #: Target panjang bab, dalam kata. Diteruskan apa adanya ke penulis dan
    #: peninjau supaya keduanya menilai terhadap angka yang sama.
    min_words: int = DEFAULT_MIN_WORDS

    #: Panduan gaya dari ``config.yaml``.
    style_guide: str = ""


@dataclass(frozen=True, slots=True)
class _GateOutcome:
    """Hasil menjalankan seluruh gate atas satu draf.

    Bentuk internal, bukan API. Ia ada supaya :meth:`BookDirector._run_gates`
    dapat mengembalikan **tiga** hal sekaligus — record terbaru, apakah lulus,
    dan gate mana yang menolak — tanpa memaksa pemanggilnya membongkar
    ``record.reviews`` untuk menebak yang ketiga.
    """

    record: ChapterRecord
    approved: bool
    rejected_by: str | None = None


class BookDirector:
    """Menjalankan pipeline §31 dari ``BookRequest`` sampai Markdown bab.

    Menerima **port**, bukan implementasi: ``StateStore``, ``ChapterArtifacts``,
    ``Researcher``, dan ``Reporter`` semuanya berupa Protocol. Karena itu
    seluruh alur di berkas ini dapat dijalankan tanpa jaringan maupun tanpa
    filesystem — lihat ``tests/integration/test_orchestrator_offline.py``.

    ``self._gates`` bertipe ``tuple[ReviewGate, ...]``, jadi :class:`PassThroughGate`
    dan :class:`ChapterReviewer` tidak dapat dibedakan dari sini. Itulah LSP
    dalam bentuk yang berguna: orchestrator yang sama menjalankan rantai 1 gate
    hari ini dan rantai 10 tahap nanti, tanpa satu pun percabangan.
    """

    def __init__(
        self,
        *,
        planner: BookPlanner,
        chapter_planner: ChapterPlannerAgent,
        writer: ChapterWriter,
        researcher: Researcher,
        gates: tuple[ReviewGate, ...],
        state: StateStore,
        artifacts: ChapterArtifacts,
        reporter: Reporter,
        settings: DirectorSettings | None = None,
    ) -> None:
        self._planner = planner
        self._chapter_planner = chapter_planner
        self._writer = writer
        self._researcher = researcher
        self._gates = gates
        self._state = state
        self._artifacts = artifacts
        self._reporter = reporter
        self._settings = settings or DirectorSettings()

    # -----------------------------------------------------------------
    # Tingkat buku
    # -----------------------------------------------------------------
    def plan(self, request: BookRequest) -> tuple[BookSpec, tuple[str, ...]]:
        """Susun ``BookSpec`` dari ``request`` dan simpan ke checkpoint (§15).

        Aman dijalankan ulang: spesifikasi lama diganti, sedangkan record bab
        yang sudah dikerjakan **tidak tersentuh** — mereka hidup di berkasnya
        sendiri. Merencanakan ulang karena itu tidak menghapus pekerjaan yang
        sudah ada, hanya mengganti rencananya.
        """
        spec, notes = self._planner.plan(request, style_guide=self._settings.style_guide)

        # Record yang sudah ada dibaca ulang dan ditempelkan, supaya
        # ``BookState.chapters`` tidak menyimpan potret basi dari perencanaan
        # sebelumnya. State yang berbohong tentang bab yang sudah selesai lebih
        # buruk daripada state yang tidak menyebutkannya sama sekali.
        records = tuple(
            record
            for record in (self._state.load_chapter(n) for n in spec.chapter_numbers())
            if record is not None
        )
        base = self._state.load_book() or BookState(request=request)
        self._state.save_book(
            base.model_copy(update={"request": request, "spec": spec, "chapters": records})
        )
        return spec, notes

    def run(
        self,
        *,
        numbers: tuple[int, ...] | None = None,
        force: bool = False,
    ) -> RunReport:
        """Kerjakan seluruh bab yang belum selesai, satu per satu (§31, §35).

        :param numbers: batasi ke nomor-nomor ini. ``None`` berarti seluruh bab
            di ``BookSpec``.
        :param force: kerjakan ulang bab yang sudah ``APPROVED``. Dipakai
            ``--force`` untuk mengulang satu bab tanpa menyentuh sisanya.

        Bab yang gagal **tidak** membatalkan sisanya; ia disimpan sebagai
        ``FAILED`` dan dilaporkan di akhir. Kegagalan sistemik membatalkan
        seluruh proses — lihat :data:`SYSTEMIC_ERRORS`.

        :raises ModelAuthError: bila kredensial model ditolak.
        :raises ModelUnavailableError: bila sebuah model tidak ada di server.
        """
        book = self._require_book()
        spec = self._require_spec(book)
        available = spec.chapter_numbers()

        targets = tuple(sorted(numbers)) if numbers is not None else available
        # Nomor yang tidak ada di BookSpec ditolak **sekarang**, bukan saat gilirannya
        # tiba: ``run --from 1 --to 99`` tidak boleh mengerjakan delapan bab lebih
        # dulu, membayarnya, lalu baru memberi tahu bahwa bab 99 tidak ada.
        unknown = tuple(number for number in targets if number not in set(available))
        if unknown:
            raise ChapterNotPlannedError(unknown[0], available)

        records = self._load_records(available)
        pending = tuple(n for n in pending_numbers(spec, records, force=force) if n in set(targets))
        already_done = len(targets) - len(pending)

        if pending:
            self._reporter.info(
                f"Mengerjakan {len(pending)} bab: {', '.join(str(n) for n in pending)}"
            )
        else:
            self._reporter.info("Tidak ada bab yang perlu dikerjakan.")

        if already_done:
            self._reporter.info(f"{already_done} bab dilewati karena sudah selesai.")

        for number in pending:
            try:
                self.run_chapter(number, force=force)
            except SYSTEMIC_ERRORS as exc:
                # Dibatalkan, bukan dikandung: setiap bab sisa akan gagal dengan
                # cara yang identik, dan anggarannya lebih baik disimpan. Pesannya
                # diambil dari exception itu sendiri — record bab belum sempat
                # ditandai, jadi tidak ada yang bisa dibaca dari sana.
                reason = self._describe(exc)
                self._reporter.warn(f"Proses dibatalkan pada bab {number} — {reason}")
                return self._build_report(targets, aborted=True, abort_reason=reason)
            except CONTAINABLE_ERRORS as exc:
                self._mark_failed(number, exc)

        # Bab yang sudah selesai tidak dikerjakan — tetapi tetap **dikunjungi**,
        # untuk memastikan deliverable-nya masih ada (§28).
        settled = tuple(n for n in targets if n not in set(pending))
        for number in settled:
            self._restore_if_finished(number)

        return self._build_report(targets, aborted=False)

    def run_chapter(self, number: int, *, force: bool = False) -> ChapterRecord:
        """Kerjakan satu bab sampai tuntas, atau sampai ia menyerah.

        Urutannya persis §31: riset → rencana bab → tulis → gate → (revisi → gate)*
        → setujui → render.

        **Kegagalan tidak dikandung di sini.** Bila bab ini tidak dapat
        diselesaikan, exception naik ke pemanggil — karena bagi
        ``write-chapter N``, kegagalan itulah hasilnya. :meth:`run` yang
        mengandungnya, karena di sana ada bab lain yang masih layak dikerjakan.

        :param force: mulai ulang dari ``PLANNED``. Spesifikasi bab dipertahankan
            — ia milik ``BookSpec``, bukan hasil kerja bab ini.

        :returns: record akhir bab. Statusnya ``APPROVED`` bila berhasil; bila
            peninjau menolak sampai batas revisi habis, ``FAILED_REVIEW`` —
            **tanpa exception**, karena penolakan adalah kondisi bisnis yang
            diharapkan, dan ``record.last_review().feedback`` sudah memuat
            alasannya.

        :raises BookNotPlannedError: bila ``plan`` belum pernah dijalankan.
        :raises ChapterNotPlannedError: bila ``number`` tidak ada di ``BookSpec``.
        """
        book = self._require_book()
        spec = self._require_spec(book)
        base = find_chapter(spec, number)

        # ``spec`` sengaja **tidak** diisi dari ``base`` di sini (§16).
        #
        # ``record.spec`` berarti "spesifikasi bab yang sudah dirincikan", dan
        # keluarganya adalah keluaran Chapter Planner — bukan entri bab milik
        # Book Planner. Mengisinya di sini membuat :meth:`_ensure_plan` melihat
        # spec yang sudah terisi lalu berhenti, sehingga perincian per bab tidak
        # pernah terjadi, penulis selalu menerima rencana tingkat buku, dan
        # ``prompts/planner.chapter.md`` beserta seluruh agentnya menjadi kode
        # mati. Yang menentukan sudah-dirincikan-atau-belum adalah ``load_chapter``
        # di atas: record yang tersimpan membawa spec hasil perincian, dan itulah
        # yang membuat resume melanjutkan alih-alih merincikan ulang.
        record = self._state.load_chapter(number) or ChapterRecord(number=number)
        if force:
            record = reset_for_rerun(record)
        elif record.status is ChapterStatus.FAILED:
            # Bab yang gagal pada jalannya yang lalu dikerjakan ulang (§35).
            # ``pending_numbers`` sudah memasukkan bab ini ke daftar kerja sejak
            # awal — tanpa langkah ini, ``run`` kedua akan mati di sini dengan
            # ``IllegalTransitionError``, dan satu-satunya jalan keluar yang
            # tersisa adalah ``--force``, yang membuang riset dan draf yang masih
            # baik. Lihat :func:`~domain.rules.reopen_failed`.
            self._reporter.warn(
                f"Bab {number}: percobaan sebelumnya gagal — dikerjakan ulang "
                "dari pekerjaan yang sudah ada."
            )
            record = reopen_failed(record)
            # Disimpan sebelum dikerjakan: bila prosesnya terputus, state di
            # disk tetap konsisten dengan apa yang baru saja diputuskan.
            self._state.save_chapter(record)

        if is_approved(record.status) and not force:
            return self._restore_markdown_if_missing(record)

        self._reporter.chapter_started(number, base.title)

        record = self._ensure_research(record, base, book)
        record = self._ensure_plan(record, base, book)

        return self._write_and_review(record, book)

    # -----------------------------------------------------------------
    # Tahap-tahap
    # -----------------------------------------------------------------
    def _ensure_research(
        self,
        record: ChapterRecord,
        spec: ChapterSpec,
        book: BookState,
    ) -> ChapterRecord:
        """Kumpulkan paket riset, kecuali sudah ada (§17).

        Paket yang sudah ada dilewati meski isinya terdegradasi. Perilaku itu
        disengaja: begitu RAG tersedia, bab yang **sudah** dikerjakan dengan
        paket kosong tidak boleh diam-diam berubah isinya hanya karena ``run``
        dijalankan lagi — perubahan itu harus lewat ``--force``, dan itu
        keputusan pengguna.
        """
        if record.research is not None:
            return record

        package = self._researcher.collect(spec, book)
        if package.degraded:
            self._reporter.warn(
                f"Bab {record.number}: riset terdegradasi — "
                "klaim tanpa bukti akan ditandai, bukan dihilangkan."
            )

        updated = with_research(record, package)
        self._state.save_chapter(updated)
        return updated

    def _ensure_plan(
        self,
        record: ChapterRecord,
        base: ChapterSpec,
        book: BookState,
    ) -> ChapterRecord:
        """Rincikan rencana bab, kecuali sudah ada (§16)."""
        if record.spec is not None:
            return record

        planned, notes = self._chapter_planner.detail(
            base, book=book, style_guide=self._settings.style_guide
        )
        for note in notes:
            self._reporter.warn(f"Bab {record.number}: {note}")

        updated = record.model_copy(update={"spec": planned})
        self._state.save_chapter(updated)
        return updated

    def _write_and_review(self, record: ChapterRecord, book: BookState) -> ChapterRecord:
        """Tulis, nilai, dan revisi sampai diterima atau anggaran revisi habis (§31).

        Penghitung revisi hidup di ``record``, bukan di variabel lokal. Itu yang
        membuat resume melanjutkan penomoran alih-alih mengulanginya: bab yang
        terputus pada revisi ke-2 tidak kembali menjadi revisi ke-1.
        """
        budget = max(self._settings.max_revisions, 0) + 1

        for attempt in range(budget):
            record = self._ensure_draft(record, book)
            outcome = self._run_gates(record, book)
            record = outcome.record

            if outcome.approved:
                return self._finalize(record)

            if attempt + 1 < budget:
                self._reporter.warn(
                    f"Bab {record.number} ditolak gate {outcome.rejected_by!r} "
                    f"(revisi {record.revision}); menulis ulang."
                )
                record = self._enter_revision(record)
            else:
                self._reporter.warn(
                    f"Bab {record.number} menyerah setelah {budget} percobaan."
                )

        return record

    def _ensure_draft(self, record: ChapterRecord, book: BookState) -> ChapterRecord:
        """Pastikan ada draf yang siap dinilai, **tanpa menulis ulang yang sudah ada**.

        Resume bekerja pada butir terkecil yang sudah selesai, dan draf adalah
        butir yang mahal. Bab yang terputus tepat setelah drafnya ditulis —
        misalnya karena peninjau gagal menghubungi model — tidak boleh menulis
        draf itu lagi: hasilnya akan berbeda, dan satu panggilan model terbuang
        untuk mengganti pekerjaan yang sebenarnya sudah beres.

        ``FAILED_REVIEW`` diperlakukan sebagai ``REVISION`` di sini, karena
        memang itulah artinya: "dikembalikan, menunggu revisi". Tabel §27 dengan
        benar menolak ``(FAILED_REVIEW, DRAFT)`` — bab yang dikembalikan harus
        **direvisi**, bukan ditulis dari nol. Tanpa penerjemahan ini, bab yang
        gagal tidak akan pernah bisa dikerjakan ulang, dan ``run`` kedua akan
        melempar ``IllegalTransitionError`` alih-alih menyelesaikan buku.
        """
        if record.draft is not None and record.status in DRAFT_READY_STATUSES:
            return record

        if record.status is ChapterStatus.FAILED_REVIEW:
            record = self._enter_revision(record)

        return self._produce_draft(record, book)

    def _produce_draft(self, record: ChapterRecord, book: BookState) -> ChapterRecord:
        """Hasilkan draf — pertama kali dengan menulis, sesudahnya dengan merevisi."""
        spec = record.spec
        if spec is None:  # dijaga _ensure_plan; dijaga juga oleh tipe
            raise GatePreconditionError("writer", record.number, "spesifikasi bab")

        research = record.research or ResearchPackage.empty()
        current = record.draft

        if current is None:
            draft, notes = self._writer.write(
                spec,
                book=book,
                research=research,
                style_guide=self._settings.style_guide,
                min_words=self._settings.min_words,
            )
        else:
            # Umpan balik dikirim seluruhnya, apa adanya. Catatan yang dipangkas
            # menghasilkan revisi yang mengabaikan sebagian kekurangan, lalu
            # ditolak lagi dengan catatan yang sama — dan satu putaran terbuang.
            draft, notes = self._writer.revise(
                current,
                spec=spec,
                book=book,
                feedback=self._feedback_of(record),
                revision=record.revision,
                research=research,
                style_guide=self._settings.style_guide,
            )

        for note in notes:
            self._reporter.warn(f"Bab {record.number}: {note}")

        updated = with_draft(record, draft)
        self._state.save_chapter(updated)
        return updated

    def _run_gates(self, record: ChapterRecord, book: BookState) -> _GateOutcome:
        """Jalankan gate berurutan sampai ada yang menolak atau semuanya lulus.

        Setiap vonis disimpan sebelum gate berikutnya dipanggil. Bab yang
        terputus di tengah rantai gate karena itu tetap memuat vonis yang sudah
        keluar — dan ``status``, yang menjadi berkas state, sebenarnya sudah
        menunjukkannya.

        **Gate yang tahapnya sudah dilalui dilewati.** Itu jatuh langsung dari
        :func:`~domain.transitions.can_advance`: gate yang ``produces``-nya tidak
        lagi di depan status saat ini sudah pernah lulus pada proses sebelumnya.
        Tanpa ini, resume di tengah rantai akan menilai ulang bab yang sama —
        membayar peninjau untuk vonis yang sudah tersimpan di ``reviews``, dan
        pada rantai panjang membayarnya berkali-kali.
        """
        current = record
        for gate in self._gates:
            if not can_advance(current.status, gate.produces):
                continue

            result = gate.evaluate(current, book)
            self._reporter.gate_result(current.number, result)

            current = with_gate_result(current, result, produces=gate.produces)
            self._state.save_chapter(current)

            if not result.approved:
                return _GateOutcome(record=current, approved=False, rejected_by=gate.name)

        return _GateOutcome(record=current, approved=True)

    def _enter_revision(self, record: ChapterRecord) -> ChapterRecord:
        """Pindahkan bab dari ``FAILED_REVIEW`` ke antrean revisi (§27)."""
        updated = record.model_copy(
            update={"status": transition(record.status, ChapterEvent.REVISE)}
        )
        self._reporter.stage(record.number, updated.status)
        self._state.save_chapter(updated)
        return updated

    def _finalize(self, record: ChapterRecord) -> ChapterRecord:
        """Setujui bab dan tulis Markdown-nya (§27, §39).

        Dua langkah, dan urutannya bermakna: status dinaikkan ke ``APPROVED``
        lebih dulu, baru Markdown ditulis. Urutan sebaliknya akan meninggalkan
        berkas Markdown di ``output/`` — yang **di-track git** — untuk bab yang
        sebenarnya belum disetujui, dan itu berarti commit yang mengklaim lebih
        banyak daripada yang benar.

        Persetujuan di sini memakai :func:`~domain.transitions.status_after_gate`,
        sehingga rantai tetap dijaga: bab yang belum melewati gate mana pun tidak
        dapat tiba-tiba menjadi ``APPROVED``.
        """
        draft = record.draft
        if draft is None:  # hanya mungkin bila daftar gate kosong
            raise GatePreconditionError("approval", record.number, "draf")

        approved = record.model_copy(
            update={
                "status": status_after_gate(
                    record.status, ChapterStatus.APPROVED, gate="approval"
                ),
                "error": None,
            }
        )

        spec = record.spec
        path = self._artifacts.save_chapter(
            approved.number,
            render_chapter_markdown(draft, number=approved.number, spec=spec),
        )

        final = approved.model_copy(update={"markdown_path": path})
        self._state.save_chapter(final)
        self._record_summary(final)
        self._reporter.chapter_finished(final)
        return final

    def _restore_if_finished(self, number: int) -> None:
        """Pulihkan Markdown bab yang sudah selesai, bila berkasnya hilang (§28).

        Dipanggil untuk bab-bab yang **tidak** dikerjakan. Justru bab seperti
        itulah yang berkasnya paling mungkin hilang: tidak ada lagi yang
        menyentuhnya, sehingga tidak ada yang menyadarinya sampai seseorang
        membuka ``output/chapters/`` dan mendapati bolong di tengah.

        Bab yang belum selesai sengaja dilewati. Ia akan segera ditulis ulang
        oleh :meth:`run_chapter`, dan merender Markdown-nya sekarang berarti
        menulis deliverable untuk bab yang belum tentu lolos.
        """
        record = self._state.load_chapter(number)
        if record is None or not is_approved(record.status):
            return
        self._restore_markdown_if_missing(record)

    def _restore_markdown_if_missing(self, record: ChapterRecord) -> ChapterRecord:
        """Render ulang Markdown bab yang sudah ``APPROVED`` tetapi berkasnya hilang (§28).

        Inilah gunanya memisahkan state dari deliverable. ``state/chapterNN.json``
        memuat ``ChapterDraft`` utuh, dan :func:`~domain.rendering.render_chapter_markdown`
        murni — jadi menghapus seluruh ``output/`` tidak menghilangkan satu huruf
        pun pekerjaan. Yang tidak dapat direkonstruksi adalah state, dan itulah
        satu-satunya yang harus dijaga.

        Tidak ada panggilan model di sini, dan itu memang maksudnya: bab yang
        sudah disetujui tidak boleh berubah isinya hanya karena berkasnya
        terhapus.
        """
        if self._artifacts.exists(record.number):
            return record

        draft = record.draft
        if draft is None:
            # Disetujui tanpa draf tidak mungkin lewat jalur normal; bila state
            # datang dari luar, membiarkannya lebih baik daripada mengarang isi.
            return record

        path = self._artifacts.save_chapter(
            record.number,
            render_chapter_markdown(draft, number=record.number, spec=record.spec),
        )
        restored = record.model_copy(update={"markdown_path": path})
        self._state.save_chapter(restored)
        self._reporter.warn(f"Bab {record.number}: Markdown hilang, dirender ulang dari state.")
        return restored

    def _record_summary(self, record: ChapterRecord) -> None:
        """Simpan ringkasan bab ke ``BookState.summaries`` (§29).

        Ringkasan inilah satu-satunya hal yang dibaca bab berikutnya tentang bab
        sebelumnya, dan ia harus sudah tersimpan **sebelum** bab berikutnya
        dimulai. Menyimpannya di akhir ``run`` akan membuat bab-bab yang ditulis
        dalam proses yang sama tidak pernah melihatnya — dan itu justru
        kondisi normal, bukan kasus tepi.

        ``terminology`` sengaja tidak diisi pada MVP: mengekstrak istilah butuh
        Consistency Checker (§26) yang belum ada, dan menebaknya dari prosa akan
        mengisi memori bersama dengan entri yang salah — lebih buruk daripada
        kosong, karena agent berikutnya akan mempercayainya.
        """
        draft = record.draft
        if draft is None or not draft.summary.strip():
            return

        book = self._state.load_book()
        if book is None:
            return

        summaries = dict(book.summaries)
        summaries[record.number] = draft.summary.strip()
        self._state.save_book(book.model_copy(update={"summaries": summaries}))

    # -----------------------------------------------------------------
    # Kegagalan
    # -----------------------------------------------------------------
    def _mark_failed(self, number: int, exc: BaseException) -> ChapterRecord:
        """Tandai satu bab gagal, simpan, dan jangan hilangkan pekerjaannya.

        Pekerjaan yang sudah ada dipertahankan: ``reset_for_rerun`` **tidak**
        dipanggil di sini. Draf yang sudah ditulis dan vonis yang sudah keluar
        tetap tersimpan, sehingga orang dapat membuka ``chapterNN.json`` dan
        melihat seberapa jauh bab itu sampai sebelum menyerah.

        Bukti keluaran mentah ikut disimpan di sini — bukan lebih awal — karena
        **hanya di sini nomor babnya diketahui**. Agent sengaja tidak tahu bab
        mana yang sedang dikerjakannya; yang dibawanya hanyalah teks mentah di
        dalam :class:`~domain.errors.AgentOutputError`. Menuliskannya di tempat
        lain menuntut agent menyimpan nomor bab, dan itu persis kekotoran yang
        membuat satu agent tidak lagi dapat dipakai ulang antar-bab.
        """
        record = self._state.load_chapter(number) or ChapterRecord(number=number)
        message = self._describe(exc)
        updated = with_failure(record, message)

        self._state.save_chapter(updated)
        self._reporter.warn(f"Bab {number} gagal — {message}")
        self._persist_raw_failure(number, exc)
        return updated

    def _persist_raw_failure(self, number: int, exc: BaseException) -> None:
        """Simpan teks mentah yang gagal di-parse ke ``state/parse_fail/`` (§37).

        Ini satu-satunya bukti yang tersisa setelah perbaikan gagal: keluaran yang
        tidak dapat di-parse tidak pernah menjadi objek apa pun, jadi ia tidak
        muncul di ``chapterNN.json`` maupun di log. Tanpanya, memperbaiki prompt
        berarti menebak-nebak apa yang sebenarnya dikembalikan model.

        Selain :class:`~domain.errors.AgentOutputError` tidak ada yang disimpan:
        kegagalan lain (mis. transisi ilegal, model tak terjangkau) tidak
        meninggalkan teks mentah untuk disimpan.

        Kegagalan menyimpan bukti **tidak** boleh menenggelamkan kegagalan bab yang
        sedang dilaporkan — bab itu sudah ditandai dan tersimpan, dan melempar dari
        sini akan mengganti pesan aslinya dengan pesan tentang disk. Karena itu
        kegagalannya dilaporkan sebagai peringatan dan alur berlanjut.

        Yang ditangkap adalah :class:`~domain.errors.StateWriteError`, bukan
        ``OSError``: penulisan atomik di ``memory/checkpoints.py`` sudah
        menerjemahkan galat OS menjadi galat domain di batasnya, dan menangkap
        ``OSError`` di sini berarti tidak menangkap apa pun.
        """
        if not isinstance(exc, AgentOutputError):
            return
        try:
            path = self._state.save_raw_failure(number, exc.attempts, exc.raw)
        except StateWriteError as write_exc:
            self._reporter.warn(
                f"Bab {number}: teks mentah yang gagal tidak dapat disimpan — {write_exc}"
            )
            return
        self._reporter.warn(f"Bab {number}: teks mentah yang gagal disimpan di {path}")

    def _describe(self, exc: BaseException) -> str:
        """Ringkas sebuah exception menjadi satu baris untuk laporan (MURNI).

        Nama tipenya ikut ditulis karena ia yang menentukan tindakannya:
        ``ModelUnavailableError`` berarti "``ollama pull``", sedangkan
        ``ModelAuthError`` berarti "``ollama signin``". Pesannya sendiri sudah
        menyebutkan keduanya; yang ini ringkasannya, bukan penggantinya.
        """
        return f"{type(exc).__name__}: {exc}"

    def _build_report(
        self,
        targets: tuple[int, ...],
        *,
        aborted: bool,
        abort_reason: str | None = None,
    ) -> RunReport:
        """Susun :class:`~domain.state.RunReport` dari apa yang benar-benar ada di disk.

        Angka-angkanya dihitung dari checkpoint, bukan dari penghitung di dalam
        loop. Perbedaannya terasa pada resume: bab yang disetujui pada proses
        **sebelumnya** tetap terhitung, sehingga "6 dari 8" berarti enam bab
        selesai — bukan enam bab yang selesai pada proses ini.

        Ketiga angka itu menjumlah persis ke ``total``, dan pembagiannya
        ditentukan oleh **keberadaan record**, bukan oleh statusnya:

        * ``approved`` — record ada dan disetujui.
        * ``failed`` — record ada tetapi tidak disetujui. Setelah proses yang
          selesai, setiap bab yang pernah disentuh pasti berakhir di sini atau
          di ``approved``; tidak ada keadaan ketiga.
        * ``skipped`` — belum pernah disentuh sama sekali.

        Sengaja **tidak** memakai ``record.error is not None`` sebagai penanda
        gagal. Bab yang ditolak peninjau sampai batas revisi habis berstatus
        ``FAILED_REVIEW`` dengan ``error`` kosong — dan justru bab-bab itulah
        yang paling perlu dilaporkan. Menghitungnya sebagai ``skipped`` akan
        membuat laporan berkata "8 bab dilewati" untuk buku yang seluruh babnya
        ditolak.
        """
        records = self._load_records(targets)
        approved = sum(1 for r in records if is_approved(r.status))

        return RunReport(
            total=len(targets),
            approved=approved,
            failed=len(records) - approved,
            skipped=len(targets) - len(records),
            records=records,
            aborted=aborted,
            abort_reason=abort_reason,
        )

    def _load_records(self, numbers: tuple[int, ...]) -> tuple[ChapterRecord, ...]:
        """Baca beberapa record sekaligus, lewati bab yang belum pernah dikerjakan.

        Ditulis di sini alih-alih ditambahkan ke :class:`~domain.ports.StateStore`
        karena ia bukan kemampuan baru, hanya penghematan penulisan: port sudah
        punya :meth:`load_chapter`, dan satu pemanggil yang butuh membacanya
        berkali-kali tidak cukup menjadi alasan memperlebar kontrak yang harus
        dipenuhi setiap penyimpanan state — termasuk penyimpanan dalam memori
        milik tes.
        """
        found = (self._state.load_chapter(number) for number in numbers)
        return tuple(record for record in found if record is not None)

    # -----------------------------------------------------------------
    # Prasyarat
    # -----------------------------------------------------------------
    def _require_book(self) -> BookState:
        """State buku, atau gagal dengan menunjuk perintah yang harus dijalankan dulu."""
        book = self._state.load_book()
        if book is None:
            raise BookNotPlannedError(BOOK_STATE_HINT)
        return book

    def _require_spec(self, book: BookState) -> BookSpec:
        """Spesifikasi buku dari state, atau gagal dengan pesan yang dapat ditindaklanjuti."""
        if book.spec is None:
            raise BookNotPlannedError(BOOK_STATE_HINT)
        return book.spec

    @staticmethod
    def _feedback_of(record: ChapterRecord) -> tuple[str, ...]:
        """Catatan gate terakhir yang menolak bab ini (MURNI).

        Bila tidak ada — mungkin karena record dimuat dari state yang lebih tua
        daripada vonisnya — dikembalikan satu catatan yang menyatakan hal itu.
        Mengembalikan himpunan kosong akan membuat penulis merevisi tanpa tahu
        apa yang salah, dan itu putaran yang pasti terbuang.
        """
        review = record.last_review()
        if review is None or not review.feedback:
            return ("Tidak ada catatan peninjau yang tersimpan untuk revisi ini.",)
        return review.feedback


__all__ = [
    "BOOK_STATE_HINT",
    "CONTAINABLE_ERRORS",
    "DRAFT_READY_STATUSES",
    "SYSTEMIC_ERRORS",
    "BookDirector",
    "DirectorSettings",
]
