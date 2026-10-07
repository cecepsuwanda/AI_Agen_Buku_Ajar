"""Composition root — **satu-satunya tempat yang membaca konfigurasi**.

Semua modul lain menerima dependensinya lewat ``__init__``. Berkas inilah yang
merakitnya, dan karena itu ia satu-satunya tempat yang boleh tahu daftar
lengkap bagian-bagiannya. Itulah definisi composition root, dan justru itu
yang membuat sisa aplikasi bebas dari ``global`` dan dari import melingkar.

Berkas ini **tumbuh satu field setiap langkah build**: langkah 6 (model),
langkah 7 (prompt), langkah 12 (state store), langkah 13 (orchestrator). Itu
memang tugasnya — bukan tanda desain yang bocor.

``Container`` beku (frozen dataclass): setelah dirakit, tidak ada satu pun
bagian yang bisa ditukar diam-diam di tengah jalan.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from tenacity import Retrying

from agents import (
    BookDirector,
    BookPlanner,
    ChapterPlannerAgent,
    ChapterWriter,
    DirectorSettings,
    NullResearcher,
    build_gates,
)
from agents.gates import GateContext
from app.config import AppConfig, PathsConfig, load_config
from app.prompting import FilePromptLibrary
from app.reporting import NullReporter, RichReporter
from domain.errors import ConfigError
from domain.ports import (
    ChapterArtifacts,
    ChatModel,
    Clock,
    ModelProvider,
    PromptLibrary,
    Reporter,
    StateStore,
)
from memory.artifacts import MarkdownArtifacts
from memory.project_state import BOOK_FILENAME, JsonStateStore
from models.dry_run import DryRunChatModel
from models.model_registry import ModelRegistry
from models.model_router import ModelRouter
from models.ollama_client import ChatModelFactory, build_retry

#: Nama direktori sandbox ``--dry-run`` di dalam ``state/`` (§28).
SANDBOX_DIRNAME = "dryrun"


# ---------------------------------------------------------------------------
# Jam
# ---------------------------------------------------------------------------
class SystemClock:
    """ :class:`domain.ports.Clock` yang membaca waktu nyata (UTC, ISO-8601).

    Ada sebagai objek — bukan ``datetime.now()`` yang tersebar — semata agar
    tes dapat menyuntikkan jam tetap dan membandingkan ``state/*.json``
    dengan hasil yang deterministik.
    """

    def now_iso(self) -> str:
        """Waktu sekarang dalam ISO-8601 UTC, resolusi detik."""
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class FixedClock:
    """Jam tetap untuk pengujian."""

    def __init__(self, value: str = "2026-01-01T00:00:00+00:00") -> None:
        self._value = value

    def now_iso(self) -> str:
        """Kembalikan waktu yang dibekukan saat konstruksi."""
        return self._value


# ---------------------------------------------------------------------------
# Jalur
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Seluruh jalur absolut yang dipakai aplikasi, sudah diselesaikan.

    Konfigurasi menyimpan jalur sebagai ``str`` relatif; penyelesaiannya
    terhadap root project terjadi **tepat di sini**, sekali, sehingga tidak ada
    modul lain yang menebak-nebak direktori kerja saat ini.
    """

    root: Path
    prompts: Path
    input: Path
    state: Path
    output: Path
    #: Akar sandbox ``--dry-run``: prompt, state, dan Markdown-nya semua di bawah
    #: sini. Bidang tersendiri — bukan properti turunan ``state`` — karena
    #: :meth:`for_dry_run`lah yang memindahkan ``state`` ke dalamnya, dan properti
    #: turunan akan ikut berpindah lalu menunjuk ke dalam dirinya sendiri.
    sandbox: Path

    @property
    def chapters_dir(self) -> Path:
        """Direktori Markdown per bab (``output/chapters``)."""
        return self.output / "chapters"

    @property
    def book_markdown(self) -> Path:
        """Berkas buku gabungan (``output/book.md``)."""
        return self.output / "book.md"

    @property
    def parse_fail_dir(self) -> Path:
        """Tempat teks mentah LLM yang gagal di-parse disimpan (§37)."""
        return self.state / "parse_fail"

    @property
    def dry_run_dir(self) -> Path:
        """Tempat ``--dry-run`` menulis prompt yang akan dikirim."""
        return self.sandbox

    @property
    def run_log(self) -> Path:
        """Log JSONL perjalanan (§38)."""
        return self.state / "run.jsonl"

    @classmethod
    def from_config(cls, root: Path, paths: PathsConfig) -> "ProjectPaths":
        """Bangun dari blok ``paths:`` konfigurasi (MURNI — tanpa menyentuh disk)."""
        return cls(
            root=root,
            prompts=root / paths.prompts,
            input=root / paths.input,
            state=root / paths.state,
            output=root / paths.output,
            sandbox=root / paths.state / SANDBOX_DIRNAME,
        )

    def for_dry_run(self) -> "ProjectPaths":
        """Salinan yang seluruh jalur runtime-nya berada di dalam sandbox (§28).

        ``--dry-run`` menjalankan pipeline **sungguhan** — itulah yang membuatnya
        berguna — dan pipeline sungguhan menulis state serta Markdown sungguhan.
        Bedanya hanya satu: isinya disintesis dari skema, bukan ditulis model.
        Membiarkannya menulis ke ``state/`` dan ``output/`` menghasilkan bab palsu
        yang tercatat ``APPROVED``, dan karena bab ``APPROVED`` **dilewati** saat
        resume (§28), jalankan sungguhan berikutnya akan melompatinya dan
        menyerahkan teks sintetis sebagai deliverable — tanpa satu pun tanda bahwa
        itu yang terjadi.

        Karena itu seluruh hasil dry-run dipindahkan ke satu direktori sekali pakai
        yang tidak di-track git. Yang tidak ikut berpindah adalah ``state`` dan
        ``output`` **sungguhan**: keduanya tidak disentuh sama sekali, sehingga
        ``export`` dan ``status`` tetap menjawab keadaan buku yang sebenarnya.
        """
        return replace(self, state=self.sandbox / "state", output=self.sandbox / "output")

    def prepare_sandbox(self) -> "ProjectPaths":
        """Siapkan sandbox ``--dry-run`` dan kembalikan jalur yang sudah menunjuk ke dalamnya.

        Membersihkan lebih dulu, dan itu bukan soal kerapian: berkas prompt diberi
        nomor urut panggilan yang **dimulai dari 1 di setiap proses**, jadi sisa
        dry-run sebelumnya akan bercampur dengan yang baru — ``01-planner.txt``
        dari kemarin duduk di sebelah ``01-reviewer.txt`` hari ini, dan tidak ada
        satu pun tanda di dalam berkasnya bahwa ia bukan dari jalankan ini.

        ``state/book.json`` yang sungguhan **disalin** masuk, bukan dibaca
        langsung — dan dibaca dari ``self.state``, yang pada titik ini masih
        menunjuk ke state sungguhan. Itulah sebabnya method ini **mengembalikan**
        jalur yang sudah dialihkan alih-alih menyuruh pemanggil memanggil
        :meth:`for_dry_run` sendiri: bila urutannya terbalik, ``self.state`` sudah
        menjadi state di dalam sandbox, dan salinan itu membaca dari berkas yang
        baru saja dihapus — diam-diam, tanpa galat, menghasilkan dry-run yang
        mengeluh "belum ada rencana buku" pada buku yang sudah direncanakan.

        ``--dry-run`` harus tetap dapat dipakai pada buku yang sedang dikerjakan —
        untuk melihat prompt apa yang akan diterima tiap bab — tanpa menulis satu
        pun berkas ke state sungguhan. Record babnya sengaja tidak ikut disalin,
        sehingga setiap bab dikerjakan dari awal dan **seluruh** prompt yang
        mungkin dikirim benar-benar terlihat, bukan hanya bab yang kebetulan belum
        selesai.

        ``ignore_errors`` disengaja: pada Windows, antivirus atau pengindeks dapat
        memegang handle sesaat sehingga penghapusan gagal separuh. Kerugiannya
        adalah beberapa berkas prompt basi; harga kegagalan keras di sini adalah
        perintah diagnostik yang tidak dapat dijalankan sama sekali. Yang pertama
        jelas lebih ringan.
        """
        source = self.state / BOOK_FILENAME

        shutil.rmtree(self.sandbox, ignore_errors=True)
        sandboxed = self.for_dry_run()
        sandboxed.ensure_runtime_dirs()

        if source.is_file():
            shutil.copy2(source, sandboxed.state / BOOK_FILENAME)
        return sandboxed

    def ensure_runtime_dirs(self) -> None:
        """Buat direktori runtime yang dibutuhkan.

        Sengaja **tidak** membuat ``input/``: bila direktori masukan tidak ada,
        itu kesalahan pengguna yang harus dilaporkan, bukan kondisi yang
        ditutupi dengan membuat folder kosong.

        Dipanggil sekali saat perakitan, bukan diserahkan kepada setiap penulis
        berkas: direktori keluaran yang tidak dapat dibuat adalah kegagalan
        sistemik, dan lebih baik ia muncul sebelum satu token pun dibelanjakan.
        """
        for directory in (self.state, self.parse_fail_dir, self.output, self.chapters_dir):
            directory.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Container:
    """Graf objek aplikasi yang sudah dirakit dan beku."""

    config: AppConfig
    paths: ProjectPaths
    registry: ModelRegistry
    router: ModelRouter
    model_factory: ChatModelFactory
    prompts: PromptLibrary
    reporter: Reporter
    clock: Clock
    state_store: StateStore
    artifacts: ChapterArtifacts
    director: BookDirector

    def override(self, **changes: Any) -> "Container":
        """Salinan container dengan beberapa bagian ditukar.

        Dipakai tes integrasi untuk menyuntikkan store di memori atau reporter
        senyap tanpa merakit ulang seluruh graf.
        """
        unknown = set(changes) - {field.name for field in fields(self)}
        if unknown:
            raise ConfigError(f"Container tidak punya field: {', '.join(sorted(unknown))}")
        return replace(self, **changes)


# ---------------------------------------------------------------------------
# Perakitan
# ---------------------------------------------------------------------------
def build_registry(
    config: AppConfig,
    *,
    profile: str | None = None,
    overrides: Mapping[str, str] | None = None,
) -> ModelRegistry:
    """Rakit :class:`ModelRegistry` dari konfigurasi.

    Fungsi terpisah — bukan method — supaya ``doctor`` dapat melaporkan
    kesalahan profil (mis. ``--profile salah``) **sebelum** membangun klien
    HTTP mana pun.
    """
    return ModelRegistry.from_sources(
        profiles=config.profiles,
        capabilities=config.models,
        base_url=config.ollama.base_url,
        profile=profile or config.profile,
        overrides=overrides,
    )


def build_director(
    *,
    router: ModelProvider,
    prompts: PromptLibrary,
    reporter: Reporter,
    state: StateStore,
    artifacts: ChapterArtifacts,
    config: AppConfig,
    max_revisions: int | None = None,
) -> BookDirector:
    """Rakit orkestrator beserta seluruh agent dan gate-nya.

    Menerima **port**, bukan :class:`Container` utuh — itulah yang membuatnya
    dapat diuji tanpa menyentuh konfigurasi, dan yang mencegahnya tumbuh menjadi
    "fungsi yang butuh segalanya".

    Perhatikan apa yang **tidak** ada di sini: tidak satu pun nama model. Setiap
    agent menerima ``router.chat("<peran>")``, dan peran mana memakai model mana
    ditentukan sepenuhnya oleh ``config.yaml`` (§6, §7).
    """
    repair_attempts = config.retry.repair_attempts
    style_guide = config.book.style_guide

    gates = build_gates(
        config.pipeline.gates,
        GateContext(
            router=router,
            prompts=prompts,
            reporter=reporter,
            min_words=config.book.min_words_per_chapter,
            review_threshold=config.book.review_threshold,
            repair_attempts=repair_attempts,
        ),
    )

    return BookDirector(
        planner=BookPlanner(
            model=router.chat("planner"),
            prompts=prompts,
            max_repair_attempts=repair_attempts,
        ),
        chapter_planner=ChapterPlannerAgent(
            model=router.chat("chapter_planner"),
            prompts=prompts,
            max_repair_attempts=repair_attempts,
        ),
        writer=ChapterWriter(
            model=router.chat("writer"),
            prompts=prompts,
            max_repair_attempts=repair_attempts,
        ),
        researcher=NullResearcher(),
        gates=gates,
        state=state,
        artifacts=artifacts,
        reporter=reporter,
        settings=DirectorSettings(
            # ``--max-revisions`` menang atas konfigurasi; angka lain selalu dari
            # konfigurasi. Tidak ada satu pun yang punya default kedua di sini —
            # ``DirectorSettings`` sendiri punya default, tetapi membiarkannya
            # terpakai berarti nilai di config.yaml diam-diam tidak berpengaruh.
            max_revisions=config.book.max_revisions if max_revisions is None else max_revisions,
            min_words=config.book.min_words_per_chapter,
            style_guide=style_guide,
        ),
    )


def build_retry_policy(config: AppConfig) -> "Retrying":
    """Ubah blok ``retry:`` konfigurasi menjadi kebijakan retry (transien saja).

    Composition root sengaja tidak **memakai** ``tenacity``: ia hanya meneruskan
    tiga angka. Konstruksi kebijakannya tinggal di lapisan adapter, karena
    ``tenacity`` adalah detail implementasi — bukan konsep domain, dan bukan
    konsep orkestrasi. Anotasinya diimpor di dalam ``TYPE_CHECKING`` supaya
    ketergantungan itu tetap hanya sebatas tipe.
    """
    return build_retry(
        attempts=config.retry.attempts,
        initial_backoff_s=config.retry.initial_backoff_s,
        max_backoff_s=config.retry.max_backoff_s,
    )


def build_container(
    *,
    config_path: str | Path | None = None,
    root: Path | None = None,
    profile: str | None = None,
    overrides: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
    reporter: Reporter | None = None,
    clock: Clock | None = None,
    chat_override: ChatModel | None = None,
    prompt_library: PromptLibrary | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
    max_revisions: int | None = None,
    verbose: bool = False,
) -> Container:
    """Rakit seluruh graf objek aplikasi.

    :param chat_override: **seluruh cerita dependency injection.** Menyuntikkan
        satu objek di sini menghasilkan pipeline produksi yang utuh dengan nol
        jaringan — itulah yang membuat ``--dry-run`` dan suite offline mungkin,
        tanpa satu pun ``if testing`` di dalam kode produksi.
    :param prompt_library: biasanya dibiarkan ``None``. Tes yang memakai prompt
        asli tidak perlu menyuntikkan apa pun; hanya tes yang menguji prompt
        rusak yang menggantinya.
    :param output_dir: ``--output``. Menimpa ``paths.output`` dari konfigurasi
        **sebelum** artefak dibangun, karena :class:`~memory.artifacts.MarkdownArtifacts`
        menyimpan direktorinya saat konstruksi; menimpanya setelah itu tidak
        berpengaruh.
    :param dry_run: ``--dry-run``. Menukar adapter chat dengan
        :class:`~models.dry_run.DryRunChatModel`, yang menulis prompt ke
        ``state/dryrun/`` dan tidak membuka soket. Dibuat **di sini**, bukan oleh
        pemanggil, karena hanya di sini direktori ``state/`` sudah diketahui —
        dan karena ``--dry-run`` tidak boleh punya jalur perakitan tersendiri.
        Seluruh jalur runtime ikut dipindahkan ke sandbox lewat
        :meth:`ProjectPaths.prepare_sandbox`, sehingga ``--output`` diabaikan pada
        mode ini — dan itu disengaja.
    :param max_revisions: ``--max-revisions``. ``None`` berarti angkanya diambil
        dari ``config.yaml``.

    Merakit direktur di sini (bukan di perintah) berarti kesalahan konfigurasi —
    nama gate yang salah, prompt yang kehilangan ``output_model`` — gagal saat
    container dibangun, sebelum satu token pun dibelanjakan.
    """
    project_root = (root or Path.cwd()).resolve()
    config = load_config(config_path, root=project_root, env=env, profile=profile)
    paths = ProjectPaths.from_config(project_root, config.paths)
    if output_dir is not None:
        paths = replace(paths, output=output_dir.resolve())
    if dry_run:
        paths = paths.prepare_sandbox()
    else:
        paths.ensure_runtime_dirs()
    registry = build_registry(config, overrides=overrides)

    override = chat_override
    if override is None and dry_run:
        override = DryRunChatModel(paths.dry_run_dir, specs=registry.roles)

    factory = ChatModelFactory(
        registry,
        timeout_s=config.ollama.request_timeout_s,
        retry=build_retry_policy(config),
        keep_alive=config.ollama.keep_alive,
        override=override,
    )
    router = ModelRouter(registry, factory)
    library = prompt_library if prompt_library is not None else FilePromptLibrary(paths.prompts)
    sink = reporter if reporter is not None else NullReporter()
    active_clock = clock if clock is not None else SystemClock()

    state_store = JsonStateStore(
        paths.state,
        clock=active_clock,
        parse_fail_dir=paths.parse_fail_dir,
    )
    artifacts = MarkdownArtifacts(paths.output)

    return Container(
        config=config,
        paths=paths,
        registry=registry,
        router=router,
        model_factory=factory,
        prompts=library,
        reporter=sink,
        clock=active_clock,
        state_store=state_store,
        artifacts=artifacts,
        director=build_director(
            router=router,
            prompts=library,
            reporter=sink,
            state=state_store,
            artifacts=artifacts,
            config=config,
            max_revisions=max_revisions,
        ),
    )


def build_console_reporter(*, verbose: bool = False) -> RichReporter:
    """Reporter terminal standar untuk perintah CLI."""
    return RichReporter(verbose=verbose)


__all__ = [
    "SANDBOX_DIRNAME",
    "Container",
    "FixedClock",
    "ProjectPaths",
    "SystemClock",
    "build_console_reporter",
    "build_container",
    "build_director",
    "build_registry",
    "build_retry_policy",
]
