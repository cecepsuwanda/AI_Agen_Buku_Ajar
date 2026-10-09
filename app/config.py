"""Pemuatan konfigurasi dan preseden override.

Preseden yang diimplementasikan (blueprint §6/§32, dengan tiga kasus uji):

    flag CLI  >  env ``BUKUAJAR_*``  >  entri profil  >  default bawaan

Penerapannya dipisah menjadi **fungsi murni** (:func:`apply_env`,
:func:`parse_overrides`) yang bekerja atas ``Mapping`` biasa. Fungsi-fungsi itu
dapat diuji penuh tanpa filesystem dan tanpa ``os.environ``; hanya
:func:`load_config` yang menyentuh berkas dan lingkungan nyata.

Modul ini **tidak** tahu apa pun soal Ollama, agent, atau pipeline. Ia hanya
mengubah YAML menjadi :class:`AppConfig` yang tervalidasi.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

import yaml
from pydantic import Field, ValidationError

from domain.base import FrozenModel
from domain.errors import ConfigError

#: Versi skema konfigurasi yang dipahami kode ini.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})

#: Nama berkas konfigurasi bawaan.
DEFAULT_CONFIG_FILENAME = "config.yaml"


# ---------------------------------------------------------------------------
# Bentuk konfigurasi
# ---------------------------------------------------------------------------
class OllamaConfig(FrozenModel):
    """Koneksi ke server Ollama."""

    base_url: str = "http://localhost:11434"
    request_timeout_s: float = Field(default=600.0, gt=0)
    warmup: bool = True
    #: ``keep_alive`` Ollama (mis. ``"10m"`` atau ``-1`` untuk menahan model di
    #: memori). Berguna untuk model lokal yang butuh ~41 s saat cold load.
    keep_alive: str | None = None


class RetryConfig(FrozenModel):
    """Kebijakan retry **transien** dan perbaikan JSON terstruktur.

    Keduanya sengaja terpisah: ``attempts`` adalah pengulangan *permintaan yang
    sama* setelah timeout, sedangkan ``repair_attempts`` adalah percobaan ulang
    dengan **prompt yang diubah** (itu perbaikan, bukan retry).
    """

    attempts: int = Field(default=3, ge=1, le=10)
    initial_backoff_s: float = Field(default=1.0, ge=0.0)
    max_backoff_s: float = Field(default=8.0, ge=0.0)
    repair_attempts: int = Field(default=2, ge=0, le=5)


class BookDefaults(FrozenModel):
    """Default tingkat buku (§15, §18, §31)."""

    language: str = "id"
    default_chapters: int = Field(default=8, ge=1, le=60)
    max_revisions: int = Field(default=2, ge=0, le=10)
    min_words_per_chapter: int = Field(default=1200, ge=0)
    #: Ambang skor kelulusan review. Ditegakkan `domain.rules.decide_review`,
    #: bukan hanya dinyatakan di prompt peninjau.
    review_threshold: int = Field(default=7, ge=0, le=10)
    style_guide: str = ""


class PipelineConfig(FrozenModel):
    """Susunan gate pipeline.

    Daftar ini adalah **satu-satunya** tempat rangkaian tahap ditentukan.
    Menambahkan pedagogy reviewer nanti = daftarkan gate-nya di
    ``agents/gates.py`` dan tambahkan namanya di sini. Nol suntingan pada
    ``book_director.py``.
    """

    gates: tuple[str, ...] = ("reviewer",)


class RagConfig(FrozenModel):
    """Retrieval-augmented generation (§10, §13).

    ``enabled: false`` adalah keadaan yang sah dan **bukan** keadaan yang rusak:
    ``NullResearcher`` tetap menjadi researcher yang jujur. Yang tidak boleh
    terjadi adalah diam-diam menyalakan RAG pada indeks yang belum dibangun —
    karena itu ``ingest`` adalah perintah tersendiri, bukan efek samping ``run``.
    """

    enabled: bool = True
    #: Ukuran potongan dalam karakter, bukan token: model embedding yang dipakai
    #: (``nomic-embed-text``) memotong pada 2048 token, dan karakter adalah satuan
    #: yang dapat dihitung tanpa pustaka tokenizer tambahan.
    chunk_chars: int = Field(default=1200, ge=200, le=8000)
    #: Tumpang tindih antar-potongan. Tanpa ini, kalimat yang jatuh tepat di batas
    #: potongan hilang dari kedua sisi, dan itulah kalimat yang paling sering
    #: memuat definisi.
    chunk_overlap: int = Field(default=200, ge=0, le=2000)
    top_k: int = Field(default=8, ge=1, le=50)
    #: Di bawah ambang ini, sebuah PDF dianggap hasil scan dan dialihkan ke jalur
    #: OCR (§10). Rata-rata karakter per halaman pada PDF teks biasa jauh di atas
    #: angka ini; pada PDF hasil scan nilainya mendekati nol.
    ocr_min_chars_per_page: int = Field(default=80, ge=0)


class LatexConfig(FrozenModel):
    """Pembangkit & pemeriksa LaTeX (§25, §26)."""

    enabled: bool = True
    #: Mesin yang dijalankan. Berapa kali ia dipanggil — dan apakah ``bibtex``
    #: dipanggil di antaranya — bukan urusan nilai ini melainkan
    #: :func:`~domain.latex.latex_passes`.
    #:
    #: Bawaannya ``pdflatex`` karena ia ada di setiap pemasangan LaTeX. Alternatif
    #: yang lebih pintar adalah ``latexmk``, yang tahu sendiri berapa kali harus
    #: diulang, tetapi ia skrip Perl: tanpa ``perl`` di ``PATH`` — keadaan bawaan
    #: PowerShell di Windows, dan Git Bash menyediakannya sendiri — ia tidak
    #: berjalan meski berkasnya ada.
    engine: str = "pdflatex"
    #: Direktori template. ``None`` berarti memakai template bawaan di
    #: ``latex/templates/``; argumen ``--latex-template`` (§42) mengisinya dari
    #: ``input/source_latex/``.
    template_dir: str | None = None
    timeout_s: float = Field(default=180.0, gt=0)
    #: Berkas bantu (``.aux``, ``.log``, …) adalah turunan yang dapat dibangun
    #: ulang, dan menyimpannya membuat setiap diff berisik. Disimpan hanya saat
    #: menelusuri masalah kompilasi.
    keep_aux: bool = False


class GraphConfig(FrozenModel):
    """Knowledge graph konsep (§14)."""

    enabled: bool = True
    #: Batas panjang lintasan saat menelusuri prasyarat. Graf yang dibangun dari
    #: ringkasan bab dapat memuat siklus ("A memakai B", "B dijelaskan lewat A");
    #: batas ini membuat penelusurannya berhenti dengan sendirinya.
    max_depth: int = Field(default=8, ge=1, le=50)


class LoggingConfig(FrozenModel):
    """Catatan per panggilan model, dipisah per peran (§38)."""

    enabled: bool = True
    #: Berapa karakter keluaran model yang disimpan apa adanya di setiap baris.
    #:
    #: ``0`` berarti tidak menyimpan keluaran sama sekali; itu tetap sah untuk
    #: orang yang hanya ingin angka lama dan tokennya. Bawaannya menyimpan
    #: sebagian, bukan seluruhnya: satu draf bab adalah puluhan ribu karakter,
    #: dan delapan bab dengan tiga revisi akan menulis berkas yang lebih besar
    #: daripada bukunya sendiri.
    output_chars: int = Field(default=4000, ge=0)


class PathsConfig(FrozenModel):
    """Jalur relatif terhadap root project (masih ``str`` — konversi di container)."""

    prompts: str = "prompts"
    input: str = "input"
    state: str = "state"
    output: str = "output"
    #: Basis pengetahuan turunan: indeks vektor (§13) dan graf konsep (§14).
    #: Dapat dihapus dan dibangun ulang kapan saja dari ``input/`` — itulah
    #: sebabnya ia bukan ``state/``.
    knowledge: str = "knowledge"


class AppConfig(FrozenModel):
    """Konfigurasi aplikasi yang sudah tervalidasi dan beku."""

    schema_version: int = 1
    profile: str = "default"
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    book: BookDefaults = Field(default_factory=BookDefaults)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    rag: RagConfig = Field(default_factory=RagConfig)
    latex: LatexConfig = Field(default_factory=LatexConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    #: Bahan mentah ``profiles`` — diteruskan apa adanya ke
    #: :meth:`models.model_registry.ModelRegistry.from_sources`, yang memegang
    #: seluruh kebijakan resolusi. ``app/config.py`` sengaja tidak menduplikasi
    #: kebijakan itu.
    profiles: Mapping[str, Mapping[str, Any]] = Field(default_factory=dict)

    #: Metadata kapabilitas model (§6), dicocokkan ``doctor``.
    models: Mapping[str, Mapping[str, Any]] = Field(default_factory=dict)

    def with_profile(self, profile: str) -> "AppConfig":
        """Salinan dengan profil aktif diganti (beku — tidak ada mutasi)."""
        return self.model_copy(update={"profile": profile})


# ---------------------------------------------------------------------------
# Fungsi murni: preseden & override
# ---------------------------------------------------------------------------
#: env var → jalur di dalam dokumen konfigurasi.
#:
#: Sengaja berupa tabel, bukan rantai ``if``: menambah satu variabel lingkungan
#: adalah satu baris di sini, tanpa menyentuh logika apa pun.
ENV_PATHS: Mapping[str, tuple[str, ...]] = {
    "BUKUAJAR_PROFILE": ("profile",),
    "BUKUAJAR_OLLAMA_BASE_URL": ("ollama", "base_url"),
    "BUKUAJAR_REQUEST_TIMEOUT_S": ("ollama", "request_timeout_s"),
    "BUKUAJAR_WARMUP": ("ollama", "warmup"),
    "BUKUAJAR_KEEP_ALIVE": ("ollama", "keep_alive"),
    "BUKUAJAR_RETRY_ATTEMPTS": ("retry", "attempts"),
    "BUKUAJAR_REPAIR_ATTEMPTS": ("retry", "repair_attempts"),
    "BUKUAJAR_MAX_REVISIONS": ("book", "max_revisions"),
    "BUKUAJAR_DEFAULT_CHAPTERS": ("book", "default_chapters"),
    "BUKUAJAR_MIN_WORDS": ("book", "min_words_per_chapter"),
    "BUKUAJAR_REVIEW_THRESHOLD": ("book", "review_threshold"),
    "BUKUAJAR_STATE_DIR": ("paths", "state"),
    "BUKUAJAR_OUTPUT_DIR": ("paths", "output"),
    "BUKUAJAR_KNOWLEDGE_DIR": ("paths", "knowledge"),
    "BUKUAJAR_RAG_ENABLED": ("rag", "enabled"),
    "BUKUAJAR_RAG_TOP_K": ("rag", "top_k"),
    "BUKUAJAR_LATEX_ENABLED": ("latex", "enabled"),
}


def apply_env(
    raw: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    """Terapkan variabel ``BUKUAJAR_*`` di atas dokumen konfigurasi (MURNI).

    Nilai sengaja dibiarkan bertipe ``str`` — Pydantic mode *lax* yang
    mengonversinya ke ``int``/``float``/``bool`` sesuai anotasi field. Dengan
    begitu, tabel di atas cukup menyebut **di mana** nilai diletakkan, tanpa
    menduplikasi informasi **tipe apa** nilai itu. Koersi tetap satu sumber:
    skema :class:`AppConfig`.

    Variabel yang tidak dikenal **diabaikan**, bukan ditolak: ``BUKUAJAR_*``
    dapat dipakai proses lain di lingkungan yang sama, dan gagal keras karena
    variabel asing akan menyulitkan tanpa manfaat nyata.
    """
    result: dict[str, Any] = {key: value for key, value in raw.items()}

    for var, path in ENV_PATHS.items():
        value = env.get(var)
        if value is None or value == "":
            continue
        _set_path(result, path, value)

    return result


def _set_path(target: MutableMapping[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Set ``value`` pada ``path`` bersarang, menyalin setiap tingkat yang dilalui.

    Penyalinan per tingkat menjaga dokumen asli (hasil ``yaml.safe_load``) tidak
    termutasi, sehingga :func:`apply_env` tetap dapat diperlakukan sebagai
    fungsi murni oleh pemanggilnya.
    """
    *parents, leaf = path
    cursor: MutableMapping[str, Any] = target
    for key in parents:
        existing = cursor.get(key)
        branch: dict[str, Any] = dict(existing) if isinstance(existing, Mapping) else {}
        cursor[key] = branch
        cursor = branch
    cursor[leaf] = value


def parse_overrides(specs: Sequence[str]) -> dict[str, str]:
    """Parse ``--set-model writer=gemma3:4b`` menjadi ``{peran: model}`` (MURNI).

    Satu flag generik mengalahkan satu flag per peran: menambahkan peran baru
    tidak menuntut flag CLI baru (OCP).

    :raises ConfigError: pada entri tanpa ``=``, peran/nama kosong, atau
        peran yang disebut dua kali.
    """
    overrides: dict[str, str] = {}
    for spec in specs:
        if "=" not in spec:
            raise ConfigError(
                f"Override model {spec!r} tidak berbentuk 'peran=model'. "
                f"Contoh: --set-model writer=gemma3:4b"
            )
        role, _, model = spec.partition("=")
        role, model = role.strip(), model.strip()
        if not role or not model:
            raise ConfigError(
                f"Override model {spec!r} tidak lengkap: peran dan nama model wajib diisi"
            )
        if role in overrides:
            raise ConfigError(
                f"Peran {role!r} di-override lebih dari sekali "
                f"({overrides[role]!r} lalu {model!r}). Sisakan satu."
            )
        overrides[role] = model
    return overrides


# ---------------------------------------------------------------------------
# IO: pemuatan dari berkas
# ---------------------------------------------------------------------------
def resolve_config_path(path: str | Path | None = None, *, root: Path | None = None) -> Path:
    """Tentukan berkas konfigurasi yang akan dibaca.

    ``BUKUAJAR_CONFIG`` diperiksa lebih dulu agar seluruh perintah dapat
    diarahkan ke berkas lain tanpa flag berulang.
    """
    if path is not None:
        return Path(path)
    from_env = os.environ.get("BUKUAJAR_CONFIG")
    if from_env:
        return Path(from_env)
    base = root if root is not None else Path.cwd()
    return base / DEFAULT_CONFIG_FILENAME


def load_config(
    path: str | Path | None = None,
    *,
    root: Path | None = None,
    env: Mapping[str, str] | None = None,
    profile: str | None = None,
) -> AppConfig:
    """Baca, terapkan preseden, lalu validasi konfigurasi.

    :param profile: nilai dari ``--profile``. Ini preseden **tertinggi**, jadi
        ia diterapkan setelah ``env`` — itulah sebabnya ia parameter tersendiri
        dan bukan sekadar kunci di dalam ``env``.

    :raises ConfigError: berkas tidak ada, YAML tidak valid, versi skema tidak
        dikenal, atau ada field yang gagal validasi.
    """
    resolved = resolve_config_path(path, root=root)
    environment = os.environ if env is None else env

    if not resolved.is_file():
        raise ConfigError("Berkas konfigurasi tidak ditemukan", path=resolved)

    try:
        document = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML tidak valid: {exc}", path=resolved) from exc
    except OSError as exc:
        raise ConfigError(f"Gagal membaca: {exc}", path=resolved) from exc

    if document is None:
        raise ConfigError("Berkas konfigurasi kosong", path=resolved)
    if not isinstance(document, Mapping):
        raise ConfigError(
            f"Akar YAML harus berupa mapping, bukan {type(document).__name__}", path=resolved
        )

    merged = apply_env(document, environment)
    if profile:
        merged["profile"] = profile

    try:
        config = AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(
            f"Konfigurasi tidak valid:\n{_format_validation_error(exc)}", path=resolved
        ) from exc

    if config.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ConfigError(
            f"schema_version {config.schema_version} tidak dikenal. "
            f"Didukung: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
            path=resolved,
        )

    return config


def _format_validation_error(exc: ValidationError) -> str:
    """Rangkum ValidationError menjadi beberapa baris yang menunjuk lokasi."""
    lines = []
    for error in exc.errors()[:10]:
        location = ".".join(str(part) for part in error.get("loc", ())) or "(akar)"
        lines.append(f"  - {location}: {error.get('msg', 'tidak valid')}")
    if exc.error_count() > 10:
        lines.append(f"  ... dan {exc.error_count() - 10} masalah lain")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_CONFIG_FILENAME",
    "ENV_PATHS",
    "SUPPORTED_SCHEMA_VERSIONS",
    "AppConfig",
    "BookDefaults",
    "OllamaConfig",
    "PathsConfig",
    "PipelineConfig",
    "RetryConfig",
    "apply_env",
    "load_config",
    "parse_overrides",
    "resolve_config_path",
]
