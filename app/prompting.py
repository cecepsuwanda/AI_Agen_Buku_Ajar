"""Kontrak prompt (§36) — adapter yang memuat dan merender berkas prompt.

**Jinja2, bukan ``str.format``.** Alasannya menentukan dan bukan selera: prompt
ini penuh dengan **kurung kurawal JSON literal**. ``str.format`` akan menuntut
``{{``/``}}`` di setiap contoh objek, dan gagal dengan cara yang membingungkan
pada kurung pertama yang lolos. Jinja2 juga memberi loop, kondisional, dan
kontrol whitespace — semuanya berguna di sini.

``StrictUndefined`` **wajib**. Prompt yang diam-diam merender ``spec.objectives``
yang hilang sebagai string kosong adalah bug kualitas yang baru terlihat dua
puluh menit kemudian sebagai bab yang buruk — jauh dari penyebabnya.

Modul ini juga tempat dua lapis penegakan OUTPUT §36 dimulai:

1. **Cegah penyimpangan dengan membangkitkan kontrak dari tipe.** Bagian OUTPUT
   prompt **tidak ditulis tangan** — ia dihasilkan ``render_output_contract``
   dari model Pydantic yang sama yang dikirim sebagai ``format=``. Bagian OUTPUT
   yang ditulis tangan menyimpang dari skema dalam hitungan minggu; yang
   dibangkitkan tidak mungkin menyimpang, sebab keduanya satu sumber.
2. **Penegakan sesungguhnya** ada di ``format=strict_schema(Model)`` dan
   ``model_validate_json``. Bagian OUTPUT di prompt hanyalah dokumentasi untuk
   model — bukan gerbangnya.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

import yaml
from jinja2 import Environment, StrictUndefined, TemplateError
from pydantic import BaseModel

from domain.book import BookSpec, ChapterSpec
from domain.chapter import ChapterDraft, ResearchFindings, ReviewVerdict
from domain.checking import CheckVerdict
from domain.document import OcrPage
from domain.errors import ConfigError
from domain.examples import ExampleSet, ExerciseSet
from domain.ports import RenderedPrompt
from domain.structured import render_output_contract

#: Peta eksplisit kunci bertitik di front-matter → model Pydantic.
#:
#: **Bukan** ``importlib`` dinamis dari berkas data. Import dinamis dari berkas
#: yang isinya ditentukan data adalah permukaan injeksi, dan ia mematikan
#: pemeriksaan tipe atas berkas ini. Peta eksplisit hanya beberapa baris dan
#: tetap type-checked — imbalannya jauh lebih besar daripada kerepotannya.
OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "planner.BookSpec": BookSpec,
    "planner.ChapterSpec": ChapterSpec,
    "writer.ChapterDraft": ChapterDraft,
    "reviewer.ReviewVerdict": ReviewVerdict,
    "example.ExampleSet": ExampleSet,
    "exercise.ExerciseSet": ExerciseSet,
    "researcher.ResearchFindings": ResearchFindings,
    "ocr.OcrPage": OcrPage,
    # Satu model untuk keempat pemeriksa §21–§24: vonisnya berbentuk sama, dan
    # bentuk yang sama itu tinggal di satu tempat (lihat ``domain.checking``).
    "checker.CheckVerdict": CheckVerdict,
}

#: Kunci front-matter yang wajib ada di setiap berkas prompt.
REQUIRED_FRONT_MATTER: tuple[str, ...] = ("version", "role", "output_model")

#: Pemisah front-matter YAML.
_FRONT_MATTER_FENCE = "---"


# ---------------------------------------------------------------------------
# Fungsi murni
# ---------------------------------------------------------------------------
def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Pisahkan front-matter YAML dari badan prompt (MURNI).

    Front-matter hanya dikenali bila berkas **dimulai** dengan ``---``. Dengan
    begitu, garis ``---`` di tengah badan prompt tetap menjadi garis horizontal
    Markdown biasa, bukan awal metadata.

    :raises ConfigError: bila front-matter ada tetapi YAML-nya tidak valid atau
        bukan mapping.
    """
    stripped = text.lstrip("﻿")
    if not stripped.startswith(_FRONT_MATTER_FENCE):
        return {}, stripped

    lines = stripped.splitlines()
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONT_MATTER_FENCE:
            closing = index
            break

    if closing is None:
        raise ConfigError("Front-matter prompt dibuka dengan '---' tetapi tidak ditutup")

    raw_meta = "\n".join(lines[1:closing])
    body = "\n".join(lines[closing + 1 :])

    try:
        parsed = yaml.safe_load(raw_meta) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Front-matter prompt bukan YAML yang valid: {exc}") from exc

    if not isinstance(parsed, Mapping):
        raise ConfigError(
            f"Front-matter prompt harus berupa mapping, bukan {type(parsed).__name__}"
        )

    return dict(parsed), body


def prompt_digest(text: str) -> str:
    """Sidik jari isi berkas prompt (MURNI).

    Disimpan di :class:`~domain.ports.RenderedPrompt` agar perubahan prompt yang
    belum di-*bump* versinya tetap dapat dilacak saat membandingkan mutu keluaran
    antar model (§38). Versi yang ditulis manusia bisa lupa dinaikkan; hash tidak.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def validate_metadata(name: str, meta: Mapping[str, Any]) -> type[BaseModel]:
    """Periksa front-matter dan kembalikan model keluarannya (MURNI).

    :raises ConfigError: bila kunci wajib hilang atau ``output_model`` menunjuk
        kunci yang tidak ada di :data:`OUTPUT_MODELS`.
    """
    missing = [key for key in REQUIRED_FRONT_MATTER if not meta.get(key)]
    if missing:
        raise ConfigError(
            f"Prompt {name!r} kehilangan kunci front-matter: {', '.join(missing)}"
        )

    key = str(meta["output_model"])
    try:
        return OUTPUT_MODELS[key]
    except KeyError:
        raise ConfigError(
            f"Prompt {name!r} menunjuk output_model {key!r} yang tidak dikenal. "
            f"Tersedia: {', '.join(sorted(OUTPUT_MODELS))}"
        ) from None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
class _Prompt:
    """Satu prompt yang sudah dimuat dan divalidasi (beku setelah dibuat)."""

    __slots__ = ("name", "system", "body", "version", "hash", "output_model_key", "model")

    def __init__(self, *, name: str, raw: str) -> None:
        meta, body = split_front_matter(raw)
        self.name = name
        self.model = validate_metadata(name, meta)
        self.system = str(meta.get("system", "") or "").strip()
        self.body = body
        self.version = str(meta["version"])
        self.output_model_key = str(meta["output_model"])
        self.hash = prompt_digest(raw)


class FilePromptLibrary:
    """ :class:`domain.ports.PromptLibrary` yang membaca berkas ``.md``.

    Seluruh prompt **dimuat dan divalidasi saat konstruksi**, bukan saat
    dipakai. Prompt dengan ``output_model`` yang salah ketik akan menggagalkan
    perakitan container — jauh sebelum satu token pun dibelanjakan. Menemukan
    kesalahan itu di tengah bab ke-5 adalah kegagalan yang mahal dan sepenuhnya
    dapat dihindari.

    Template Jinja2 di-*cache* per prompt (satu dari lima tempat mutasi yang
    diizinkan). Cache-nya diisi saat konstruksi, jadi tidak ada race dan tidak
    ada kejutan pada panggilan pertama.
    """

    def __init__(self, directory: Path, *, encoding: str = "utf-8") -> None:
        self._directory = Path(directory)
        self._encoding = encoding
        self._prompts: dict[str, _Prompt] = {}
        self._templates: dict[str, Any] = {}
        self._environment = Environment(
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            autoescape=False,  # keluaran adalah teks untuk LLM, bukan HTML
        )
        self._load_all()

    # -- konstruksi --------------------------------------------------------
    def _load_all(self) -> None:
        """Muat seluruh ``*.md`` di direktori prompt.

        :raises ConfigError: bila direktori tidak ada, kosong, atau ada prompt
            yang tidak valid.
        """
        if not self._directory.is_dir():
            raise ConfigError("Direktori prompt tidak ditemukan", path=self._directory)

        files = sorted(self._directory.glob("*.md"))
        if not files:
            raise ConfigError("Tidak ada berkas prompt (.md) di direktori", path=self._directory)

        for path in files:
            name = path.stem
            try:
                raw = path.read_text(encoding=self._encoding)
            except OSError as exc:
                raise ConfigError(f"Gagal membaca prompt: {exc}", path=path) from exc

            try:
                prompt = _Prompt(name=name, raw=raw)
            except ConfigError as exc:
                # Pesannya sudah menyebut nama prompt; tambahkan berkasnya agar
                # penelusuran berhenti di sini, bukan di pemanggil.
                raise ConfigError(f"{exc} (berkas prompt)", path=path) from None

            self._prompts[name] = prompt
            self._templates[name] = self._environment.from_string(prompt.body)

    # -- domain.ports.PromptLibrary ----------------------------------------
    def names(self) -> tuple[str, ...]:
        """Nama seluruh prompt yang tersedia, terurut."""
        return tuple(sorted(self._prompts))

    def render(self, name: str, context: Mapping[str, object]) -> RenderedPrompt:
        """Render prompt ``name`` dengan ``context``.

        ``output_contract`` disuntikkan otomatis: ia dibangkitkan dari model
        Pydantic yang sama yang akan dikirim sebagai ``format=``, sehingga
        bagian OUTPUT prompt tidak mungkin menyimpang dari skema yang menegakkan
        keluaran.

        :raises ConfigError: bila prompt tidak ada atau variabel wajib tidak
            diberikan (``StrictUndefined``).
        """
        try:
            prompt = self._prompts[name]
        except KeyError:
            raise ConfigError(
                f"Prompt {name!r} tidak ada. Tersedia: {', '.join(self.names())}",
                path=self._directory,
            ) from None

        full_context = dict(context)
        full_context.setdefault("output_contract", render_output_contract(prompt.model))

        try:
            user = self._templates[name].render(**full_context)
        except TemplateError as exc:
            raise ConfigError(
                f"Gagal merender prompt {name!r}: {exc}", path=self._directory / f"{name}.md"
            ) from exc

        return RenderedPrompt(
            name=name,
            system=prompt.system,
            user=user,
            version=prompt.version,
            hash=prompt.hash,
            output_model_key=prompt.output_model_key,
        )

    # -- Tambahan di luar port --------------------------------------------
    def output_model_for(self, name: str) -> type[BaseModel]:
        """Model Pydantic yang dideklarasikan prompt ``name``.

        Dipakai agent untuk mengambil skema ``format=`` dari **satu sumber yang
        sama** dengan yang membangkitkan kontrak OUTPUT.
        """
        try:
            return self._prompts[name].model
        except KeyError:
            raise ConfigError(f"Prompt {name!r} tidak ada", path=self._directory) from None

    @property
    def directory(self) -> Path:
        """Direktori tempat prompt dibaca."""
        return self._directory


__all__ = [
    "OUTPUT_MODELS",
    "REQUIRED_FRONT_MATTER",
    "FilePromptLibrary",
    "prompt_digest",
    "split_front_matter",
    "validate_metadata",
]
