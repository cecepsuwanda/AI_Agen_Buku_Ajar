"""Tes konfigurasi — preseden, override, dan penolakan yang jelas.

Preseden yang diuji secara eksplisit dengan tiga kasus, karena inilah yang
paling mudah rusak tanpa disadari saat ada yang menambah sumber konfigurasi baru:

    flag CLI  >  env ``BUKUAJAR_*``  >  entri profil  >  default bawaan
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import apply_env, load_config, parse_overrides
from domain.errors import ConfigError

MINIMAL_YAML = """
schema_version: 1
profile: default
ollama:
  base_url: http://localhost:11434
book:
  max_revisions: 2
profiles:
  default:
    writer: {model: "gemma4:31b-cloud", temperature: 0.7}
  local:
    writer: {model: gemma3:4b, num_ctx: 32768}
models:
  "gemma3:4b": {thinking: false, vision: true, context_length: 131072}
"""


def _write(tmp_path: Path, text: str = MINIMAL_YAML, name: str = "config.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Preseden
# ---------------------------------------------------------------------------
def test_defaults_apply_when_nothing_overrides(tmp_path: Path) -> None:
    """Tanpa override apa pun, nilai dari berkas yang menang."""
    config = load_config(_write(tmp_path), env={})

    assert config.profile == "default"
    assert config.ollama.base_url == "http://localhost:11434"
    assert config.book.max_revisions == 2


def test_env_overrides_file(tmp_path: Path) -> None:
    """Env ``BUKUAJAR_*`` mengalahkan isi berkas."""
    config = load_config(
        _write(tmp_path),
        env={"BUKUAJAR_PROFILE": "local", "BUKUAJAR_MAX_REVISIONS": "5"},
    )

    assert config.profile == "local"
    assert config.book.max_revisions == 5


def test_flag_profile_beats_env(tmp_path: Path) -> None:
    """``--profile`` mengalahkan env.

    Inilah kasus ketiga yang membuat preseden ini menjadi *tiga* tingkat, bukan
    dua: tanpa tes ini, ``load_config(profile=...)`` mudah tertimpa oleh
    ``apply_env`` yang berjalan lebih dulu, dan tidak ada yang menyadarinya.
    """
    config = load_config(
        _write(tmp_path),
        env={"BUKUAJAR_PROFILE": "local"},
        profile="default",
    )

    assert config.profile == "default"


def test_env_nested_value_is_coerced_by_schema(tmp_path: Path) -> None:
    """Nilai string dari env dikonversi sesuai anotasi field."""
    config = load_config(
        _write(tmp_path),
        env={"BUKUAJAR_WARMUP": "false", "BUKUAJAR_REQUEST_TIMEOUT_S": "42.5"},
    )

    assert config.ollama.warmup is False
    assert config.ollama.request_timeout_s == 42.5


def test_unknown_env_variables_are_ignored(tmp_path: Path) -> None:
    """Variabel ``BUKUAJAR_*`` yang tidak dikenal diabaikan, bukan ditolak."""
    config = load_config(_write(tmp_path), env={"BUKUAJAR_TIDAK_DIKENAL": "x"})

    assert config.profile == "default"


# ---------------------------------------------------------------------------
# Kemurnian apply_env
# ---------------------------------------------------------------------------
def test_apply_env_does_not_mutate_its_input() -> None:
    """``apply_env`` tidak boleh menyentuh dokumen aslinya."""
    original = {"profile": "default", "ollama": {"base_url": "http://lama"}}
    snapshot = {"profile": "default", "ollama": {"base_url": "http://lama"}}

    result = apply_env(original, {"BUKUAJAR_PROFILE": "local"})

    assert original == snapshot, "dokumen masukan ikut berubah"
    assert result["profile"] == "local"


def test_apply_env_creates_missing_nested_sections() -> None:
    """Blok yang belum ada di berkas tetap dapat diisi lewat env."""
    result = apply_env({}, {"BUKUAJAR_OLLAMA_BASE_URL": "http://remote:11434"})

    assert result == {"ollama": {"base_url": "http://remote:11434"}}


# ---------------------------------------------------------------------------
# parse_overrides
# ---------------------------------------------------------------------------
def test_parse_overrides_returns_role_to_model_map() -> None:
    """Beberapa ``--set-model`` digabung menjadi satu peta."""
    assert parse_overrides(["writer=gemma3:4b", "reviewer=gpt-oss:120b-cloud"]) == {
        "writer": "gemma3:4b",
        "reviewer": "gpt-oss:120b-cloud",
    }


def test_parse_overrides_tolerates_whitespace() -> None:
    """Spasi di sekitar ``=`` tidak menjadi bagian nama."""
    assert parse_overrides(["  writer = gemma3:4b  "]) == {"writer": "gemma3:4b"}


def test_parse_overrides_keeps_model_names_containing_equals() -> None:
    """Hanya ``=`` **pertama** yang memisah, sehingga nilai bertanda ``=`` utuh."""
    assert parse_overrides(["writer=a=b"]) == {"writer": "a=b"}


@pytest.mark.parametrize(
    "spec",
    ["writer", "=gemma3:4b", "writer=", "", "   "],
)
def test_parse_overrides_rejects_malformed_entries(spec: str) -> None:
    """Entri yang tidak berbentuk ``peran=model`` ditolak dengan pesan jelas."""
    with pytest.raises(ConfigError):
        parse_overrides([spec])


def test_parse_overrides_rejects_duplicate_role() -> None:
    """Peran yang di-override dua kali ditolak — daripada diam-diam memilih satu."""
    with pytest.raises(ConfigError, match="lebih dari sekali"):
        parse_overrides(["writer=a", "writer=b"])


# ---------------------------------------------------------------------------
# Penolakan yang jelas
# ---------------------------------------------------------------------------
def test_missing_file_names_the_path(tmp_path: Path) -> None:
    """Berkas tidak ada → pesan menyebut jalurnya."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path / "tidak-ada.yaml", env={})

    assert "tidak-ada.yaml" in str(excinfo.value)


def test_invalid_yaml_is_reported(tmp_path: Path) -> None:
    """YAML rusak dilaporkan sebagai ConfigError, bukan traceback PyYAML."""
    with pytest.raises(ConfigError, match="YAML tidak valid"):
        load_config(_write(tmp_path, "profile: [belum ditutup\n"), env={})


def test_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    """Versi skema yang tidak dikenal ditolak sebelum hal lain dijalankan."""
    with pytest.raises(ConfigError, match="schema_version"):
        load_config(_write(tmp_path, "schema_version: 99\n"), env={})


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    """Field asing di akar ditolak — salah ketik tidak boleh hilang diam-diam."""
    with pytest.raises(ConfigError, match="Konfigurasi tidak valid"):
        load_config(_write(tmp_path, MINIMAL_YAML + "\nollama_typo: 1\n"), env={})


def test_invalid_field_value_names_its_location(tmp_path: Path) -> None:
    """Pesan validasi menunjuk lokasi field yang bermasalah."""
    broken = MINIMAL_YAML.replace("max_revisions: 2", "max_revisions: -1")
    with pytest.raises(ConfigError) as excinfo:
        load_config(_write(tmp_path, broken), env={})

    assert "book.max_revisions" in str(excinfo.value)


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    """Berkas kosong ditolak, bukan diperlakukan sebagai konfigurasi bawaan."""
    with pytest.raises(ConfigError, match="kosong"):
        load_config(_write(tmp_path, ""), env={})


def test_root_must_be_a_mapping(tmp_path: Path) -> None:
    """Akar YAML berupa daftar ditolak dengan pesan yang menyebut tipenya."""
    with pytest.raises(ConfigError, match="mapping"):
        load_config(_write(tmp_path, "- satu\n- dua\n"), env={})


# ---------------------------------------------------------------------------
# ModelSpec: alias usang
# ---------------------------------------------------------------------------
def test_legacy_name_key_is_accepted_with_deprecation(tmp_path: Path) -> None:
    """``name:`` (§32) masih diterima sebagai alias ``model:`` (§6), dengan peringatan.

    Blueprint menyebut kunci ini dengan dua nama berbeda; menerima keduanya
    membuat konfigurasi lama tidak langsung rusak.
    """
    from models.model_registry import ModelRegistry

    with pytest.warns(DeprecationWarning, match="name"):
        registry = ModelRegistry.from_sources(
            profiles={"default": {"writer": {"name": "gemma3:4b"}}},
            capabilities={},
            base_url="http://localhost:11434",
            profile="default",
        )

    assert registry.spec_for("writer").model == "gemma3:4b"
