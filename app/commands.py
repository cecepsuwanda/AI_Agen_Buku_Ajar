"""Isi perintah CLI.

``app/cli.py`` **hanya mem-parse argumen**; seluruh pekerjaan terjadi di sini,
di atas objek parameter yang beku. Konsekuensinya penting: subcommand dan
*callback* root memanggil fungsi ``do_*`` yang sama, sehingga hanya ada **satu
jalur kode** — dan jalur itu dapat diuji tanpa menyentuh ``CliRunner``.

Tiga hal yang diurus berkas ini, dan tidak ada yang lain:

1. **Menerjemahkan flag menjadi nilai domain.** ``--rps`` menjadi ``rps_text``
   yang sudah dibaca, ``--from/--to`` menjadi daftar nomor bab. Terjemahan itu
   adalah fungsi murni di bagian bawah berkas, terpisah dari perintahnya.
2. **Batas kesalahan.** Seluruh :class:`~domain.errors.BukuAjarError` ditangkap
   di sini dan menjadi pesan + kode keluar. Tidak ada traceback untuk kesalahan
   yang sudah terduga — kecuali dengan ``--verbose``, yang memintanya.
3. **Keluaran.** Tabel, laporan akhir, dan ``state/run.jsonl``.

Isi babnya sendiri tidak diurus di sini: itu ``BookDirector``. Berkas ini tidak
tahu apa itu gate, prompt, atau model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from rich.table import Table

from app.config import AppConfig, parse_overrides
from app.container import Container, build_console_reporter, build_container
from app.logging_setup import append_run_log, run_record
from app.reporting import RichReporter
from domain.book import BookRequest, BookSpec, StyleGuide
from domain.chapter import ChapterRecord
from domain.document import Document
from domain.enums import ChapterStatus, is_approved, is_problem
from domain.errors import (
    BookNotPlannedError,
    BukuAjarError,
    ChapterNotPlannedError,
    ConfigError,
    InputError,
    ServiceUnavailableError,
    StateCorruptError,
)
from domain.graph import (
    ConceptGraph,
    find_duplicate_explanations,
    neighbours,
    prerequisites_of,
)
from domain.latex import bibliography_sources, build_advisories, build_problems
from domain.rendering import render_book_markdown, render_chapter_markdown
from domain.rps import CoursePlan, render_course_plan
from domain.state import RunReport
from domain.transitions import has_reached
from graph.knowledge_graph import JsonGraphStore, graph_from_directories
from ingestion.document_normalizer import normalize_documents
from ingestion.loader import IngestedFile, OcrRoute, load_reference_files
from ingestion.rps_loader import parse_rps
from latex.compiler import LatexmkCompiler
from memory.project_state import BOOK_FILENAME
from models.ollama_client import list_installed_models, probe_model
from rag.chunker import chunk_documents

#: Peran yang **tidak** boleh diprobe dengan ``chat`` karena modelnya bukan
#: model bahasa. Memanggil ``chat`` pada ``nomic-embed-text`` akan gagal dan
#: menghasilkan vonis "tidak terjangkau" yang menyesatkan; keberadaannya cukup
#: diverifikasi lewat daftar model.
NON_CHAT_ROLES: frozenset[str] = frozenset({"embedding"})

#: Batas waktu probe. Model lokal butuh ~41 detik saat cold load, dan itu
#: normal — bukan kegagalan. Probe yang terlalu ketat akan melaporkan model
#: lokal yang sehat sebagai rusak.
PROBE_TIMEOUT_S = 180.0


# ---------------------------------------------------------------------------
# Parameter
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GlobalOptions:
    """Opsi yang berlaku untuk seluruh subcommand (dari callback root)."""

    config_path: Path | None = None
    profile: str | None = None
    set_model: tuple[str, ...] = ()
    verbose: bool = False
    log_json: bool = False

    def overrides(self) -> dict[str, str]:
        """Override peran→model yang sudah di-parse.

        :raises ConfigError: bila ada entri ``--set-model`` yang rusak.
        """
        return parse_overrides(self.set_model)


@dataclass(frozen=True, slots=True)
class DoctorParams:
    """Parameter perintah ``doctor``."""

    globals: GlobalOptions = field(default_factory=GlobalOptions)
    probe: bool = True


@dataclass(frozen=True, slots=True)
class RunParams:
    """Parameter perintah ``plan``, ``run``, ``write-chapter``, ``status``, ``export``.

    Satu objek untuk kelima perintah, bukan satu per perintah. Alasannya bukan
    penghematan: ``run`` dan ``plan`` harus menghasilkan ``BookRequest`` yang
    **identik** dari flag yang sama, dan dua kelas parameter yang mirip adalah
    cara paling andal membuat keduanya perlahan menyimpang.

    ``None`` berarti "tidak disebut di baris perintah", dan itu berbeda dari
    nol/kosong: ``None`` jatuh ke nilai ``config.yaml``, sedangkan nilai eksplisit
    menimpanya. Perbedaan itu yang membuat preseden tiga tingkat dapat diuji.
    """

    globals: GlobalOptions = field(default_factory=GlobalOptions)

    # -- masukan -----------------------------------------------------------
    rps: Path | None = None
    references: Path | None = None
    latex_template: Path | None = None

    # -- bentuk buku -------------------------------------------------------
    title: str = ""
    audience: str = ""
    language: str | None = None
    target_chapters: int | None = None
    max_revisions: int | None = None

    # -- keluaran & cakupan ------------------------------------------------
    output: Path | None = None
    chapter: int | None = None
    chapters_from: int | None = None
    chapters_to: int | None = None

    # -- perilaku ----------------------------------------------------------
    force: bool = False
    dry_run: bool = False
    #: ``export --latex``: rakit dan kompilasi buku LaTeX (§42).
    #:
    #: Berlaku pada ``export`` saja. Pada perintah lain ia tidak berpengaruh —
    #: dan itu bukan kelalaian: satu objek parameter dipakai lima perintah, dan
    #: menambahkan field yang hanya dibaca salah satunya lebih murah daripada
    #: memecah objeknya menjadi lima yang kemudian menyimpang.
    latex: bool = False


# ---------------------------------------------------------------------------
# Penerjemah flag → nilai domain (murni kecuali pembacaan berkas)
# ---------------------------------------------------------------------------
def read_input_file(what: str, path: Path) -> str:
    """Baca berkas masukan sebagai teks UTF-8.

    :raises InputError: bila berkasnya tidak ada, bukan berkas, atau tidak dapat
        dibaca. Dibedakan dari ``ConfigError`` supaya pesannya menunjuk flag yang
        barusan diketik (``--rps``), bukan berkas konfigurasi.
    """
    if not path.is_file():
        raise InputError(what, path, "berkas tidak ditemukan atau bukan berkas biasa")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(what, path, f"bukan teks UTF-8 ({exc.reason})") from exc
    except OSError as exc:
        raise InputError(what, path, str(exc)) from exc


def build_request(params: RunParams, config: AppConfig, *, rps_text: str = "") -> BookRequest:
    """Susun :class:`~domain.book.BookRequest` dari flag dan konfigurasi (MURNI).

    ``params.title`` yang kosong diisi dari nama berkas RPS — bukan dari judul
    bawaan: judul yang berasal dari nama berkas hampir selalu lebih baik daripada
    "Buku Ajar" generik, dan judul itu akan disebut planner sebagai konteks.
    """
    return BookRequest(
        title=params.title or _default_title(params.rps),
        audience=params.audience or "mahasiswa S1",
        language=params.language or config.book.language,
        target_chapters=params.target_chapters or config.book.default_chapters,
        rps_path=str(params.rps) if params.rps is not None else None,
        references_dir=str(params.references) if params.references is not None else None,
        latex_template_dir=(
            str(params.latex_template) if params.latex_template is not None else None
        ),
        rps_text=rps_text,
        style=StyleGuide(language=params.language or config.book.language),
    )


def _default_title(rps: Path | None) -> str:
    """Judul cadangan dari nama berkas RPS (MURNI)."""
    if rps is None:
        return "Buku Ajar"
    stem = rps.stem.replace("_", " ").replace("-", " ").strip()
    return stem.title() or "Buku Ajar"


@dataclass(frozen=True, slots=True)
class RpsInput:
    """RPS yang sudah dibaca **dan** diuraikan (§12).

    Ketiganya dibawa bersama karena ketiganya harus konsisten: teks yang dibaca
    perencana, kalender yang dipakai memeriksa minggu, dan catatan tentang apa yang
    tidak terbaca. Memisahkannya menjadi tiga panggilan akan memungkinkan pemanggil
    membaca RPS sekali dan mengurainya dari teks yang lain.
    """

    #: Teks yang dikirim ke Book Planner — ringkasan terstruktur, bukan berkas mentah.
    text: str
    #: RPS terurai; ``None`` bila berkasnya tidak memuat bagian yang dikenali.
    course: CoursePlan | None
    #: Catatan penguraian — apa yang tidak terbaca, dan apa yang janggal.
    notes: tuple[str, ...]


def read_rps_file(path: Path) -> RpsInput:
    """Baca berkas RPS, uraikan, dan siapkan teksnya untuk perencana (§12).

    Bila berkasnya tidak memuat satu pun bagian yang dikenali, teks **mentahnya**
    yang diteruskan dan ``course`` bernilai ``None``. Dua akibatnya disebut
    terang-terangan: perencana tetap mendapat isi RPS apa pun bentuknya (perilaku
    yang sama seperti sebelum pengurai ini ada), dan pemeriksaan pemetaan minggu
    dimatikan karena tidak ada kalender yang dapat dipakai memeriksanya.
    """
    raw = read_input_file("RPS", path)
    course, notes = parse_rps(raw)
    if course.is_empty():
        return RpsInput(text=raw, course=None, notes=notes)
    return RpsInput(text=render_course_plan(course), course=course, notes=notes)


def select_chapters(
    params: RunParams,
    available: tuple[int, ...],
) -> tuple[int, ...] | None:
    """Tentukan nomor bab yang dituju flag ``--chapter`` / ``--from`` / ``--to`` (MURNI).

    ``None`` berarti "seluruh bab". Rentang yang tidak menyebut batas bawahnya
    mulai dari bab pertama yang **ada**, bukan dari 1 — supaya ``--to 3`` pada
    buku yang babnya bernomor 5..8 tidak menghasilkan rentang kosong tanpa
    penjelasan.

    :raises ChapterNotPlannedError: bila hasilnya kosong padahal flag diberikan.
        Rentang yang tidak menyasar apa pun hampir selalu salah ketik, dan
        melaporkannya sebagai "tidak ada yang dikerjakan" akan menyesatkan.
    """
    if params.chapter is not None:
        if params.chapter not in set(available):
            raise ChapterNotPlannedError(params.chapter, available)
        return (params.chapter,)

    if params.chapters_from is None and params.chapters_to is None:
        return None

    low = params.chapters_from if params.chapters_from is not None else min(available, default=1)
    high = params.chapters_to if params.chapters_to is not None else max(available, default=low)
    selected = tuple(number for number in available if low <= number <= high)
    if not selected:
        raise ChapterNotPlannedError(low, available)
    return selected


# ---------------------------------------------------------------------------
# Laporan doctor
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RoleCheck:
    """Hasil pemeriksaan satu peran."""

    role: str
    model: str
    installed: bool
    is_remote: bool
    is_chat: bool
    reachable: bool | None = None  # None = tidak diprobe
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True bila peran ini aman dipakai."""
        if not self.installed:
            return False
        if self.is_chat and self.reachable is False:
            return False
        return True


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Ringkasan seluruh pemeriksaan."""

    base_url: str
    profile: str
    installed: tuple[str, ...]
    checks: tuple[RoleCheck, ...]
    server_error: str = ""

    @property
    def failures(self) -> tuple[RoleCheck, ...]:
        """Peran yang tidak aman dipakai."""
        return tuple(check for check in self.checks if not check.ok)

    @property
    def ok(self) -> bool:
        """True bila tidak ada kegagalan sistemik."""
        return not self.server_error and not self.failures


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
def do_doctor(params: DoctorParams) -> int:
    """Periksa bahwa seluruh peran benar-benar dapat dipakai.

    Mengubah kegagalan di bab ke-7 menjadi pesan jelas di awal, sebelum satu
    token berguna pun dibelanjakan.

    :returns: kode keluar — 0 bila sehat, 1 bila ada kegagalan sistemik.
    """
    reporter = RichReporter(verbose=params.globals.verbose)
    console = reporter.console

    try:
        container = build_container(
            config_path=params.globals.config_path,
            profile=params.globals.profile,
            overrides=params.globals.overrides(),
            reporter=reporter,
        )
    except BukuAjarError as exc:
        console.print(f"[bold red]Konfigurasi bermasalah[/bold red]\n{exc}")
        return 1

    report = _inspect_container(container, probe=params.probe)
    _print_report(reporter, report)
    return 0 if report.ok else 1


def _inspect_container(container: Container, *, probe: bool) -> DoctorReport:
    """Kumpulkan fakta tentang setiap peran (murni kecuali IO probe)."""
    registry = container.registry
    client = container.model_factory.client
    timeout_s = min(container.config.ollama.request_timeout_s, PROBE_TIMEOUT_S)

    try:
        installed = list_installed_models(
            client, base_url=registry.base_url, timeout_s=timeout_s
        )
    except ServiceUnavailableError as exc:
        return DoctorReport(
            base_url=registry.base_url,
            profile=registry.profile,
            installed=(),
            checks=(),
            server_error=str(exc),
        )

    installed_set = set(installed)
    checks: list[RoleCheck] = []

    for role in sorted(registry.roles):
        spec = registry.spec_for(role)
        is_chat = role not in NON_CHAT_ROLES
        present = spec.model in installed_set

        reachable: bool | None = None
        detail = ""
        if probe and is_chat and present:
            ok, detail = probe_model(
                client,
                spec.model,
                base_url=registry.base_url,
                timeout_s=timeout_s,
            )
            reachable = ok
            if ok:
                detail = "ok"
        elif not present:
            detail = "belum di-pull"

        checks.append(
            RoleCheck(
                role=role,
                model=spec.model,
                installed=present,
                is_remote=registry.capability_for(spec.model).is_remote,
                is_chat=is_chat,
                reachable=reachable,
                detail=detail,
            )
        )

    return DoctorReport(
        base_url=registry.base_url,
        profile=registry.profile,
        installed=installed,
        checks=tuple(checks),
    )


def _print_report(reporter: RichReporter, report: DoctorReport) -> None:
    """Cetak laporan doctor sebagai tabel."""
    console = reporter.console
    console.print(
        f"Ollama [bold]{report.base_url}[/bold] | profil "
        f"[bold]{report.profile}[/bold] | {len(report.installed)} model ter-*pull*"
    )

    if report.server_error:
        console.print(f"\n[bold red]Gagal[/bold red] {report.server_error}")
        return

    table = Table(header_style="bold", box=None, pad_edge=False)
    table.add_column("peran")
    table.add_column("model")
    table.add_column("sumber")
    table.add_column("status")

    for check in report.checks:
        if check.ok:
            status = "[green]siap[/green]"
            if check.reachable is None and check.is_chat:
                status = "[green]terpasang[/green]"
        else:
            status = f"[red]{check.detail or 'gagal'}[/red]"

        table.add_row(
            check.role,
            check.model,
            "[magenta]cloud[/magenta]" if check.is_remote else "lokal",
            status,
        )

    console.print(table)

    if report.ok:
        n = len(report.checks)
        console.print(f"\n[bold green]Sehat.[/bold green] {n} peran siap dipakai.")
        return

    console.print(f"\n[bold red]{len(report.failures)} peran bermasalah:[/bold red]")
    for check in report.failures:
        console.print(f"  - {check.role} -> {check.model}: {check.detail or 'tidak siap'}")
    console.print(
        "\nTindakan: jalankan [bold]ollama pull <model>[/bold] untuk model yang "
        "belum ada, [bold]ollama signin[/bold] untuk model cloud, atau pakai "
        "[bold]--profile local[/bold] untuk jalur yang dijamin offline."
    )


# ---------------------------------------------------------------------------
# Batas kesalahan bersama
# ---------------------------------------------------------------------------
def guarded(reporter: RichReporter, action: Callable[[], int]) -> int:
    """Jalankan ``action`` dan ubah kegagalan yang terduga menjadi pesan + kode keluar.

    Inilah satu-satunya tempat aplikasi ini "menangkap semua", dan letaknya di
    batas terluar dengan sengaja: di dalam pipeline, menangkap kegagalan berarti
    menyembunyikan bab mana yang rusak. Di sini tidak ada lagi yang bisa
    dilakukan selain memberi tahu pengguna.

    ``KeyboardInterrupt`` diperlakukan sebagai **keberhasilan yang belum
    selesai**, bukan kegagalan: setiap tahap sudah tersimpan di checkpoint (§28),
    jadi Ctrl-C tidak menghilangkan apa pun — dan pesannya harus mengatakan itu,
    supaya tidak ada yang mengulang dari nol karena mengira pekerjaannya hilang.
    """
    try:
        return action()
    except KeyboardInterrupt:
        reporter.warn(
            "Dihentikan. Pekerjaan yang sudah selesai tersimpan di state/ - "
            "jalankan perintah yang sama lagi untuk melanjutkan."
        )
        return 130
    except BukuAjarError as exc:
        reporter.console.print(f"[bold red]Gagal:[/bold red] {exc}")
        if reporter.verbose:
            reporter.console.print_exception()
        return 1


def open_container(params: RunParams, reporter: RichReporter) -> Container:
    """Rakit container sesuai flag.

    Satu tempat untuk seluruh perintah, supaya ``plan`` dan ``run`` tidak mungkin
    memakai profil, override, atau direktori keluaran yang berbeda.
    """
    return build_container(
        config_path=params.globals.config_path,
        profile=params.globals.profile,
        overrides=params.globals.overrides(),
        reporter=reporter,
        output_dir=params.output,
        dry_run=params.dry_run,
        max_revisions=params.max_revisions,
        verbose=params.globals.verbose,
    )


def warn_dry_run(reporter: RichReporter, params: RunParams) -> None:
    """Jelaskan apa yang **tidak** dilakukan ``--dry-run``, sebelum ia dijalankan.

    Dua hal yang perlu diketahui pengguna, dan keduanya tidak terlihat dari
    keluarannya: seluruh hasilnya — prompt, state, dan Markdown bab — mendarat di
    ``state/dryrun/``, bukan di ``output/``; dan ``--output`` karena itu
    diabaikan. Isi babnya disintesis dari skema, jadi menjalankannya di direktori
    keluaran sungguhan akan menyisakan bab palsu yang tercatat ``APPROVED`` — dan
    bab seperti itu dilewati pada jalankan berikutnya (§28).

    Diperingatkan alih-alih ditolak: ``--dry-run`` bersama ``--output`` adalah
    bentuk yang wajar ditulis orang (baris invokasi §42 memang memuat keduanya),
    dan menolaknya akan memaksa mereka menghapus flag yang tidak salah.
    """
    if not params.dry_run:
        return

    reporter.info(
        "dry-run: pipeline berjalan sungguhan, tetapi hasilnya - prompt, state, dan "
        "Markdown bab - ditulis ke state/dryrun/ dan tidak satu pun dikirim ke model."
    )
    if params.output is not None:
        reporter.warn(
            f"--output {params.output} diabaikan pada dry-run: isi babnya sintetis, "
            "dan menaruhnya di direktori keluaran akan membuatnya terlihat seperti "
            "bab sungguhan yang sudah disetujui."
        )


def warn_flag_limits(reporter: RichReporter, params: RunParams, config: AppConfig) -> None:
    """Jelaskan batas dua flag yang **tidak** berpengaruh pada perintah ini.

    ``--references`` sekarang sungguh dipakai — oleh ``ingest``, yang membangun
    indeks dari direktori itu. Tetapi ``plan`` dan ``run`` tidak membacanya sama
    sekali: yang mereka baca adalah indeks yang sudah ada di ``knowledge/``.
    Diam-diam menerimanya akan membuat pengguna mengira bahan rujukan barusan
    sudah ikut dipakai pada jalankan ini, padahal indeksnya masih yang lama — atau
    bahkan belum ada. Karena itu perbedaannya disebut, bukan disembunyikan, dan
    arahkan ke perintah yang benar.

    ``--latex-template`` memang belum berfungsi; keluaran saat ini masih Markdown.
    Diterima dan dicatat di ``BookRequest`` karena baris invokasi §42 memuatnya,
    dan menolaknya akan membuat baris itu gagal.
    """
    if params.references is not None and not config.rag.enabled:
        reporter.warn(
            f"--references {params.references} diabaikan pada jalankan ini: blok 'rag' "
            "di konfigurasi sedang dimatikan, sehingga peneliti tidak mengambil bahan "
            "sama sekali. Paket riset akan terdegradasi dan setiap klaim faktual "
            "ditandai di 'unresolved_claims'."
        )
    elif params.references is not None:
        reporter.info(
            f"--references {params.references} hanya dipakai oleh perintah 'ingest'. "
            "Peneliti membaca indeks di knowledge/ — jalankan 'ingest' lebih dulu "
            "agar bahannya benar-benar terbaca."
        )

    if params.latex_template is not None:
        reporter.warn(
            f"--latex-template {params.latex_template} dicatat di BookRequest, tetapi "
            "penulisan LaTeX belum diimplementasikan. Keluaran saat ini Markdown."
        )


def load_spec(container: Container) -> BookSpec:
    """Baca ``BookSpec`` dari state, atau jelaskan mengapa ia tidak ada.

    :raises BookNotPlannedError: bila ``state/book.json`` belum ada.
    :raises StateCorruptError: bila berkasnya ada tetapi tidak memuat spesifikasi.
        Itu berarti berkasnya ditulis oleh versi lain atau disunting tangan;
        menebak-nebak isinya jauh lebih buruk daripada berhenti.
    """
    book = container.state_store.load_book()
    path = container.paths.state / BOOK_FILENAME
    if book is None:
        raise BookNotPlannedError(path)
    if book.spec is None:
        raise StateCorruptError(path, "BookSpec tidak ada")
    return book.spec


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def do_plan(params: RunParams) -> int:
    """Susun ``BookSpec`` dari RPS dan simpan ke ``state/book.json`` (§15)."""
    reporter = build_console_reporter(verbose=params.globals.verbose)
    return guarded(reporter, lambda: _plan(params, reporter))


def _plan(params: RunParams, reporter: RichReporter) -> int:
    if params.rps is None:
        raise InputError("RPS", "(belum diberikan)", "sebutkan jalurnya dengan --rps PATH")

    container = open_container(params, reporter)
    warn_flag_limits(reporter, params, container.config)
    rps = read_rps_file(params.rps)
    report_rps_notes(reporter, params.rps, rps.notes)
    request = build_request(params, container.config, rps_text=rps.text)

    spec, notes = container.director.plan(request, course=rps.course)
    _print_spec(reporter, spec, notes)
    return 0


def report_rps_notes(reporter: RichReporter, path: Path, notes: tuple[str, ...]) -> None:
    """Sebutkan apa yang tidak terbaca dari RPS — sebelum babnya ditulis.

    Diperingatkan lebih dulu, bukan dilaporkan di akhir: penguraian yang meleset
    berarti seluruh rencana buku berdiri di atas RPS yang salah baca, dan yang
    perlu mengetahui itu adalah orang yang baru saja mengetik ``--rps``.
    """
    if not notes:
        return
    reporter.warn(f"Catatan penguraian RPS {path}:")
    for note in notes:
        reporter.warn(f"  - {note}")


def _print_spec(reporter: RichReporter, spec: BookSpec, notes: tuple[str, ...]) -> None:
    """Cetak tabel bab hasil perencanaan."""
    table = Table(title=f"Rencana: {spec.title}", header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("bab")
    table.add_column("tujuan", justify="right")
    table.add_column("contoh", justify="right")
    table.add_column("latihan", justify="right")

    for chapter in spec.chapters:
        table.add_row(
            str(chapter.number),
            chapter.title,
            str(len(chapter.objectives)),
            str(chapter.required_examples),
            str(chapter.required_exercises),
        )

    reporter.console.print(table)
    reporter.console.print(f"[green]{len(spec.chapters)} bab direncanakan.[/green]")
    for note in notes:
        reporter.warn(note)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def do_run(params: RunParams) -> int:
    """Kerjakan seluruh bab yang belum selesai, lalu laporkan (§31, §35)."""
    reporter = build_console_reporter(verbose=params.globals.verbose)
    return guarded(reporter, lambda: _run(params, reporter))


def _run(params: RunParams, reporter: RichReporter) -> int:
    warn_dry_run(reporter, params)

    container = open_container(params, reporter)
    warn_flag_limits(reporter, params, container.config)
    started_at = container.clock.now_iso()
    spec = _ensure_planned(params, reporter, container)

    targets = select_chapters(params, spec.chapter_numbers())
    report = container.director.run(numbers=targets, force=params.force)
    _print_run_report(reporter, report, params)
    _write_run_log(container, reporter, report, started_at, log_json=params.globals.log_json)
    return report.exit_code()


def _ensure_planned(params: RunParams, reporter: RichReporter, container: Container) -> BookSpec:
    """Kembalikan ``BookSpec``, susun lebih dulu bila buku ini belum pernah direncanakan.

    Perencanaan otomatis inilah yang membuat invokasi §42 — satu baris tanpa
    ``plan`` — benar-benar bekerja. Sebaliknya, rencana yang **sudah** ada tidak
    pernah ditimpa: menyusun ulang spesifikasi di tengah jalan akan membuang
    bab-bab yang sudah dikerjakan, dan itu kehilangan pekerjaan yang tidak
    diminta siapa pun.
    """
    if container.state_store.load_book() is not None:
        if params.rps is not None:
            reporter.warn(
                "--rps diabaikan: state/book.json sudah ada. "
                "Jalankan 'plan' untuk menyusun ulang spesifikasinya."
            )
        return load_spec(container)

    # Belum ada rencana, dan tidak ada bahan untuk menyusunnya: kesalahan urutan
    # perintah. Pesannya menunjuk 'plan', bukan sekadar menyatakan berkas tak ada.
    if params.rps is None:
        raise BookNotPlannedError(container.paths.state / BOOK_FILENAME)

    rps = read_rps_file(params.rps)
    report_rps_notes(reporter, params.rps, rps.notes)
    request = build_request(params, container.config, rps_text=rps.text)
    spec, notes = container.director.plan(request, course=rps.course)
    _print_spec(reporter, spec, notes)
    return spec


def do_write_chapter(number: int, params: RunParams) -> int:
    """Kerjakan satu bab sampai tuntas, atau sampai ia menyerah (§31)."""
    reporter = build_console_reporter(verbose=params.globals.verbose)
    return guarded(reporter, lambda: _write_chapter(number, params, reporter))


def _write_chapter(number: int, params: RunParams, reporter: RichReporter) -> int:
    warn_dry_run(reporter, params)
    container = open_container(params, reporter)
    record = container.director.run_chapter(number, force=params.force)
    reporter.chapter_finished(record)
    return 1 if is_problem(record.status) else 0


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------
def do_ingest(params: RunParams) -> int:
    """Bangun ulang basis pengetahuan turunan: indeks vektor **dan** graf konsep (§10, §13, §14).

    Perintah tersendiri, bukan efek samping ``run``. Alasan yang sama yang
    tertulis di :class:`~app.config.RagConfig`: menyalakan RAG pada indeks yang
    belum dibangun harus **terlihat** — dan di sini ia terlihat, karena membangun
    indeks adalah tindakan yang disebut namanya.

    Kedua turunan itu dibangun di sini karena keduanya milik ``knowledge/``, dan
    ``knowledge/`` adalah satu hal: bahan yang dapat dihapus dan dibangun ulang
    dari ``input/``. Memisahkannya menjadi dua perintah berarti menambah satu
    keadaan yang mungkin: indeks yang baru dan graf yang basi. Yang tetap berbeda
    adalah **sumbernya** — indeks dari ``input/references/``, graf dari sumber
    LaTeX — dan karena itu keduanya dijalankan terpisah di dalam.
    """
    reporter = build_console_reporter(verbose=params.globals.verbose)
    return guarded(reporter, lambda: _ingest(params, reporter))


def _ingest(params: RunParams, reporter: RichReporter) -> int:
    container = open_container(params, reporter)
    store = container.indexer
    if store is None:
        raise ConfigError(
            "Indeks vektor tidak dirakit pada jalankan ini (--dry-run). "
            "Bangun indeksnya dengan 'ingest' tanpa --dry-run."
        )

    # Graf lebih dulu, dan sebelum penjagaan apa pun di bawah: sumbernya berbeda
    # dari sumber indeks. Direktori rujukan yang kosong tidak boleh membuat graf
    # yang bahannya lengkap tidak ikut dibangun.
    _rebuild_graph(container, reporter)

    directory = (
        params.references if params.references is not None else container.paths.input / "references"
    )
    rag = container.config.rag
    ocr = OcrRoute(model=container.router.chat("vision"), prompts=container.prompts)

    files = load_reference_files(directory, ocr=ocr, ocr_min_chars=rag.ocr_min_chars_per_page)
    if not files:
        reporter.warn(
            f"Tidak ada bahan rujukan di {directory}. Akhiran yang dikenali: "
            ".pdf, .md, .txt, .tex. Indeks lama tidak diubah."
        )
        return 1

    plan = _chunk_by_file(files, chunk_chars=rag.chunk_chars, overlap_chars=rag.chunk_overlap)
    chunks = tuple(chunk for _, _, item_chunks in plan for chunk in item_chunks)
    if not chunks:
        reporter.warn(
            f"{len(files)} berkas terbaca, tetapi tidak satu pun memuat teks yang dapat "
            "diindeks. Indeks lama tidak diubah - periksa apakah berkasnya benar-benar kosong."
        )
        return 1

    # ``reset`` **sesudah** kedua penjagaan di atas, bukan sebelumnya: bahan yang
    # salah direktori atau berkas kosong akan menghapus indeks yang baik tanpa
    # menggantinya dengan apa pun, dan seluruh bab sesudahnya akan menulis tanpa
    # rujukan — persis kegagalan yang paling mahal di jalur ini.
    store.reset()
    written = store.index(chunks)
    _print_ingest_report(
        reporter,
        plan,
        written=written,
        index_dir=container.paths.vector_store_dir,
    )
    return 0


# ---------------------------------------------------------------------------
# graf konsep (§14)
# ---------------------------------------------------------------------------
def _graph_sources(container: Container) -> tuple[Path, ...]:
    """Direktori sumber graf konsep, berurut (§11, §14) (MURNI).

    Dua direktori, dan urutannya tetap: bahan rujukan yang ditulis manusia lebih
    dulu, lalu bab-bab yang dihasilkan buku ini sendiri. Urutan itu menentukan
    definisi mana yang bertahan ketika satu konsep muncul di keduanya — dan bahan
    rujukan memang yang seharusnya menang.
    """
    return (
        container.paths.input / "source_latex",
        container.paths.latex_chapters_dir,
    )


def _rebuild_graph(container: Container, reporter: RichReporter) -> None:
    """Bangun ulang ``knowledge/graph/`` dari sumber LaTeX, lalu laporkan (§14).

    :raises ArtifactWriteError: bila grafiknya gagal ditulis. Ditahan
        :func:`guarded` seperti kegagalan lain perintah ini — graf yang tidak
        dapat ditulis adalah kerusakan yang harus terlihat, bukan sesuatu yang
        boleh dilanjutkan diam-diam.
    """
    if not container.config.graph.enabled:
        reporter.info(
            "Graf konsep dimatikan (graph.enabled: false); knowledge/graph/ tidak diubah."
        )
        return

    store = JsonGraphStore(container.paths.graph_dir)
    previous = store.load()
    graph = graph_from_directories(_graph_sources(container))

    # Ditulis hanya bila berubah, sama seperti ``BookDirector._remember_chapter``:
    # graf yang sama persis dan ditulis ulang hanya menambah satu titik gagal dan
    # satu diff kosong di git.
    if graph != previous:
        store.save(graph)

    _print_graph_report(
        reporter,
        before=previous,
        after=graph,
        graph_dir=container.paths.graph_dir,
        sources=_graph_sources(container),
        max_depth=container.config.graph.max_depth,
    )


def _chunk_by_file(
    files: tuple[IngestedFile, ...],
    *,
    chunk_chars: int,
    overlap_chars: int,
) -> tuple[tuple[IngestedFile, tuple[Document, ...], tuple[Document, ...]], ...]:
    """Rapikan lalu potong tiap berkas, dan kembalikan ketiganya berpasangan (MURNI).

    Dikerjakan per berkas, bukan sekali untuk seluruh korpus, semata supaya
    laporannya dapat menyebut jumlah potongan **per berkas**: berapa banyak yang
    masuk dari tiap bahan adalah pertanyaan pertama yang muncul ketika hasil
    pencarian terasa kurang, dan menghitungnya belakangan berarti memotong ulang
    seluruh korpus hanya untuk sebuah tabel.
    """
    plan: list[tuple[IngestedFile, tuple[Document, ...], tuple[Document, ...]]] = []
    for item in files:
        documents = normalize_documents(item.documents)
        plan.append(
            (
                item,
                documents,
                chunk_documents(
                    documents,
                    chunk_chars=chunk_chars,
                    overlap_chars=overlap_chars,
                ),
            )
        )
    return tuple(plan)


def _print_ingest_report(
    reporter: RichReporter,
    plan: tuple[tuple[IngestedFile, tuple[Document, ...], tuple[Document, ...]], ...],
    *,
    written: int,
    index_dir: Path,
) -> None:
    """Cetak apa yang masuk ke indeks, per berkas."""
    table = Table(title=f"Indeks vektor: {index_dir}", header_style="bold")
    table.add_column("berkas")
    table.add_column("dokumen", justify="right")
    table.add_column("potongan", justify="right")
    table.add_column("jalur")

    for item, documents, chunks in plan:
        table.add_row(
            item.filename,
            str(len(documents)),
            str(len(chunks)),
            "[yellow]OCR[/yellow]" if item.used_ocr else "teks",
        )
    reporter.console.print(table)

    total_documents = sum(len(documents) for _, documents, _ in plan)
    total_chunks = sum(len(chunks) for _, _, chunks in plan)
    reporter.console.print(
        f"[green]{len(plan)} berkas, {total_documents} dokumen, {total_chunks} potongan "
        f"diindeks.[/green]"
    )
    if written != total_chunks:
        reporter.warn(
            f"{total_chunks - written} potongan tidak masuk: id-nya sama dengan potongan "
            "berkas lain (dua berkas bernama sama di subdirektori berbeda). Indeks memuat "
            f"{written} potongan unik - ganti nama salah satunya bila keduanya memang "
            "bahan yang berbeda."
        )
    reporter.info(
        "Bahan yang diganti cukup di-*ingest* ulang: id potongan diturunkan dari "
        "nama berkas, halaman, dan nomor paragraf."
    )


def _print_graph_report(
    reporter: RichReporter,
    *,
    before: ConceptGraph,
    after: ConceptGraph,
    graph_dir: Path,
    sources: tuple[Path, ...],
    max_depth: int,
) -> None:
    """Cetak graf konsep: tiap konsep, prasyaratnya, rujukannya, dan yang kembar (§14).

    Tiga kolom tengah tabel itu adalah tiga dari lima guna yang §14 sebutkan —
    ``prasyarat`` menjawab *prerequisite*, ``menunjuk`` menjawab *cross-reference*,
    dan kelompok penjelasan kembar di bawahnya menjawab *duplicate explanation
    detection*. ``max_depth`` diteruskan ke :func:`~domain.graph.prerequisites_of`
    alih-alih dibiarkan pada nilai bawaannya: graf ini dapat bersiklus, dan berapa
    jauh penelusurannya boleh berjalan adalah keputusan konfigurasi, bukan
    keputusan yang ditanam di dalam fungsi murni.
    """
    table = Table(title=f"Graf konsep: {graph_dir}", header_style="bold")
    table.add_column("konsep")
    table.add_column("bab", justify="right")
    table.add_column("prasyarat", justify="right")
    table.add_column("menunjuk", justify="right")

    for node in after.nodes:
        prerequisites = prerequisites_of(after, node.name, max_depth=max_depth)
        outgoing = neighbours(after, node.name)
        table.add_row(
            node.name,
            str(node.chapter) if node.chapter is not None else "-",
            str(len(prerequisites)) if prerequisites else "-",
            str(len(outgoing)) if outgoing else "-",
        )
    reporter.console.print(table)

    reporter.console.print(
        f"[green]{len(after.nodes)} konsep dan {len(after.edges)} relasi[/green] "
        f"(sebelumnya {len(before.nodes)} dan {len(before.edges)})."
    )
    if not after.nodes:
        listed = ", ".join(str(source) for source in sources)
        reporter.info(
            f"Belum ada berkas .tex di {listed}. Graf terisi sendiri begitu salah "
            "satunya ada; perintah ini tidak perlu dijalankan ulang untuk itu."
        )

    for group in find_duplicate_explanations(after):
        listed = ", ".join(repr(name) for name in group)
        reporter.warn(
            f"Konsep yang definisinya sama persis: {listed}. Satu konsep yang "
            "dijelaskan dua kali biasanya cukup dijelaskan sekali, lalu dirujuk (§14)."
        )

    reporter.info(
        "Graf ini turunan: ia dibangun dari berkas .tex, bukan dari ringkasan bab. "
        "Ringkasan bab harus diubah menjadi konsep oleh model, dan itu pekerjaan "
        "yang tidak dilakukan perintah ini."
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def do_status(params: RunParams) -> int:
    """Cetak tabel keadaan seluruh bab, plus model yang dipakai tiap peran."""
    reporter = build_console_reporter(verbose=params.globals.verbose)
    return guarded(reporter, lambda: _status(params, reporter))


def _status(params: RunParams, reporter: RichReporter) -> int:
    container = open_container(params, reporter)
    book = container.state_store.load_book()
    if book is None or book.spec is None:
        reporter.console.print(
            "Belum ada rencana buku di state/book.json. "
            "Jalankan 'plan --rps <berkas>' untuk memulainya."
        )
        return 0

    table = Table(title=f"Status: {book.spec.title}", header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("bab")
    table.add_column("status")
    table.add_column("revisi", justify="right")
    table.add_column("gate terakhir")

    for number in book.spec.chapter_numbers():
        record = container.state_store.load_chapter(number)
        table.add_row(*_status_row(number, book.spec, record))

    reporter.console.print(table)
    _print_roles(reporter, container)
    return 0


def _status_row(
    number: int,
    spec: BookSpec,
    record: ChapterRecord | None,
) -> tuple[str, ...]:
    """Satu baris tabel status (MURNI)."""
    title = next((c.title for c in spec.chapters if c.number == number), "?")
    if record is None:
        return (str(number), title, "[dim]belum dikerjakan[/dim]", "-", "-")

    last = record.last_review()
    verdict = "-" if last is None else f"{last.gate}: {'lulus' if last.approved else 'tolak'}"
    status = f"[red]{record.status}[/red]" if is_problem(record.status) else str(record.status)
    return (str(number), title, status, str(record.revision), verdict)


def _print_roles(reporter: RichReporter, container: Container) -> None:
    """Cetak peta peran -> model yang akan dipakai."""
    table = Table(title="Model per peran", header_style="bold", box=None)
    table.add_column("peran")
    table.add_column("model")
    table.add_column("sumber")

    for role in sorted(container.registry.roles):
        spec = container.registry.spec_for(role)
        capability = container.registry.capability_for(spec.model)
        table.add_row(role, spec.model, "cloud" if capability.is_remote else "lokal")

    reporter.console.print(table)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def do_export(params: RunParams) -> int:
    """Gabungkan bab-bab yang sudah disetujui menjadi ``output/book.md`` (§39).

    Dengan ``--latex``, buku LaTeX-nya juga dirakit dan dikompilasi (§42).
    """
    reporter = build_console_reporter(verbose=params.globals.verbose)
    return guarded(reporter, lambda: _export(params, reporter))


def _export(params: RunParams, reporter: RichReporter) -> int:
    container = open_container(params, reporter)
    spec = load_spec(container)

    parts, pending = _export_parts(container, spec)
    if not parts:
        reporter.console.print("Belum ada bab yang disetujui untuk digabung.")
        return 1

    destination = container.artifacts.save_book(render_book_markdown(parts, title=spec.title))
    reporter.console.print(f"[green]{len(parts)} bab digabung[/green] -> {destination}")
    if pending:
        numbers = ", ".join(str(number) for number in pending)
        reporter.warn(f"Bab {numbers} belum disetujui dan tidak ikut digabung.")

    if params.latex and _export_latex(container, reporter, spec) != 0:
        return 1
    return 0


def _export_latex(container: Container, reporter: RichReporter, spec: BookSpec) -> int:
    """Rakit dan kompilasi buku LaTeX menjadi ``output/latex/book.pdf`` (§42).

    Bab yang ikut adalah bab yang **sudah dikompilasi** gate §26, bukan bab yang
    sekadar disetujui. Bedanya penting: bab yang belum melewati §26 belum pernah
    dibuktikan dapat dikompilasi, dan merakitnya bersama bab lain akan
    memindahkan galatnya ke kompilasi buku — tempat satu galat menahan seluruh
    PDF, bukan satu bab.

    :returns: kode keluar perintah.
    """
    compiler = container.latex_compiler
    if not isinstance(compiler, LatexmkCompiler):
        reporter.console.print(
            "Perkakas LaTeX tidak tersedia, jadi buku LaTeX tidak dapat dirakit. "
            "Periksa `latex.enabled` dan `latex.engine` di konfigurasi, atau "
            "jalankan `ai-book doctor`."
        )
        return 1

    numbers, skipped = _latex_parts(container, spec)
    if not numbers:
        reporter.console.print("Belum ada bab yang dikompilasi LaTeX untuk dirakit.")
        return 1

    result = compiler.compile_book(
        title=spec.title,
        chapter_numbers=numbers,
        sources=_book_sources(container, numbers),
        destination=container.paths.latex_dir / "book.pdf",
    )
    if skipped:
        listing = ", ".join(str(number) for number in skipped)
        reporter.warn(f"Bab {listing} belum sampai tahap kompilasi LaTeX dan tidak ikut dirakit.")

    if not result.ok:
        reporter.console.print("[red]Buku LaTeX gagal dikompilasi.[/red]")
        for problem in build_problems(result):
            reporter.console.print(f"  - {problem}")
        if result.log_excerpt:
            reporter.console.print(result.log_excerpt)
        return 1

    reporter.console.print(
        f"[green]{len(numbers)} bab dirakit[/green] -> {result.pdf_path or 'book.pdf'}"
    )
    for advisory in build_advisories(result):
        reporter.warn(advisory)
    return 0


def _latex_parts(container: Container, spec: BookSpec) -> tuple[list[int], list[int]]:
    """Nomor bab yang sudah dikompilasi (§27), dan yang belum.

    Membaca status dari record, bukan dari keberadaan berkas ``.tex``: berkas
    dapat tertinggal dari jalankan sebelumnya, sedangkan status adalah kebenaran
    yang tercatat §28.
    """
    ready: list[int] = []
    skipped: list[int] = []
    for number in spec.chapter_numbers():
        record = container.state_store.load_chapter(number)
        if record is not None and has_reached(record.status, ChapterStatus.LATEX_COMPILED):
            ready.append(number)
        else:
            skipped.append(number)
    return ready, skipped


def _book_sources(container: Container, numbers: Sequence[int]) -> tuple[str, ...]:
    """Seluruh sumber yang harus ada di ``references.bib`` buku (MURNI).

    Gabungan sitasi buku dengan sitasi setiap draf yang ikut dirakit — bentuk
    yang sama dengan yang dipakai gate §25 dan §26 per bab, dan sengaja memakai
    fungsi yang sama: ``references.bib`` tingkat buku yang berbeda dari yang
    dipakai memeriksa bab berarti bab yang lulus pemeriksaan dapat kehilangan
    entrinya saat dirakit.
    """
    book = container.state_store.load_book()
    book_sources = tuple(book.citations.values()) if book is not None else ()
    draft_sources: list[str] = []
    for number in numbers:
        record = container.state_store.load_chapter(number)
        if record is not None and record.draft is not None:
            draft_sources.extend(record.draft.citations)
    return bibliography_sources(book_sources, draft_sources)


def _export_parts(
    container: Container,
    spec: BookSpec,
) -> tuple[list[tuple[int, str]], list[int]]:
    """Render Markdown setiap bab yang **disetujui**, dalam urutan nomor.

    Dirender dari ``ChapterRecord``, bukan dibaca dari ``output/chapters/*.md``.
    Dua alasan, dan keduanya praktis: record adalah sumber otoritatif §28, jadi
    bab yang berkas Markdown-nya terhapus tetap ikut tergabung; dan bab yang
    belum disetujui tidak akan pernah ikut, karena tidak ada record yang layak
    dirender untuknya.

    :returns: ``(bagian, nomor yang belum disetujui)``.
    """
    parts: list[tuple[int, str]] = []
    pending: list[int] = []

    for number in spec.chapter_numbers():
        record = container.state_store.load_chapter(number)
        if record is None or record.draft is None or not is_approved(record.status):
            pending.append(number)
            continue
        parts.append(
            (number, render_chapter_markdown(record.draft, number=number, spec=record.spec))
        )

    return parts, pending


# ---------------------------------------------------------------------------
# Pelaporan hasil
# ---------------------------------------------------------------------------
def _print_run_report(reporter: RichReporter, report: RunReport, params: RunParams) -> None:
    """Cetak ringkasan akhir satu kali ``run`` (§35)."""
    reporter.console.print(
        f"\n[bold]{report.approved} dari {report.total} bab disetujui.[/bold] "
        f"Gagal: {report.failed}. Dilewati: {report.skipped}."
    )

    if report.aborted:
        reporter.console.print(f"[bold red]Proses dibatalkan:[/bold red] {report.abort_reason}")
        reporter.console.print(
            "Tidak ada bab sisa yang dikerjakan - setiap bab berikutnya akan gagal dengan "
            "cara yang sama. Perbaiki penyebabnya, lalu jalankan lagi."
        )

    for record in report.records:
        if is_problem(record.status):
            reporter.console.print(
                f"  [red]bab {record.number}[/red]: {record.error or _last_feedback(record)}"
            )

    if params.dry_run:
        reporter.console.print(
            "[dim]dry-run: seluruh hasil tersimpan di state/dryrun/ dan tidak satu pun "
            "dikirim ke model. Isi babnya sintetis - jalankan tanpa --dry-run untuk "
            "yang sungguhan.[/dim]"
        )


def _last_feedback(record: ChapterRecord) -> str:
    """Catatan peninjau terakhir, sebagai alasan kegagalan bila tak ada pesan lain."""
    last = record.last_review()
    if last is None or not last.feedback:
        return "tidak ada keterangan"
    return " | ".join(last.feedback[:3])


def _write_run_log(
    container: Container,
    reporter: RichReporter,
    report: RunReport,
    started_at: str,
    *,
    log_json: bool,
) -> None:
    """Tulis satu baris JSONL ke ``state/run.jsonl`` bila ``--log-json`` diberikan.

    Kegagalan menulis log **tidak** menggagalkan run — lihat catatan di
    :mod:`app.logging_setup`. Yang dilakukan hanyalah memberitahukannya.
    """
    if not log_json:
        return

    record = run_record(
        report,
        profile=container.registry.profile,
        models={
            role: container.registry.spec_for(role).model
            for role in sorted(container.registry.roles)
        },
        started_at=started_at,
        finished_at=container.clock.now_iso(),
    )
    if append_run_log(container.paths.run_log, record) is None:
        reporter.warn(f"Gagal menulis log JSONL ke {container.paths.run_log}.")


__all__ = [
    "NON_CHAT_ROLES",
    "PROBE_TIMEOUT_S",
    "DoctorParams",
    "DoctorReport",
    "GlobalOptions",
    "RoleCheck",
    "RpsInput",
    "RunParams",
    "build_request",
    "do_doctor",
    "do_export",
    "do_ingest",
    "do_plan",
    "do_run",
    "do_status",
    "do_write_chapter",
    "guarded",
    "load_spec",
    "open_container",
    "read_input_file",
    "read_rps_file",
    "report_rps_notes",
    "select_chapters",
    "warn_dry_run",
    "warn_flag_limits",
]
