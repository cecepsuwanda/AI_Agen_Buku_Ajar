"""ModelRegistry — seluruh kebijakan resolusi peran → model (§6, §7).

Registry adalah **data murni**: tidak punya method yang menyentuh jaringan dan
tidak menyimpan klien. Itulah yang mencegahnya menjadi god object, dan itulah
juga yang membuat seluruh berkas ini berjalan tanpa Ollama sama sekali.

Preseden yang diuji dengan tiga kasus:

    flag CLI  >  env ``BUKUAJAR_*``  >  entri profil  >  default bawaan

Dua tingkat pertama sudah diuji di ``test_config.py`` (mereka hidup di
``app/config.py``). Dua tingkat terakhir diuji di sini, karena di sinilah entri
profil benar-benar berubah menjadi ``ModelSpec``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from domain.errors import ConfigError, UnknownRoleError
from models.model_registry import ModelRegistry

PROFILES: dict[str, dict[str, Any]] = {
    "default": {
        "planner": {"model": "gpt-oss:120b-cloud", "temperature": 0.3, "max_tokens": 8192},
        "writer": {"model": "gemma4:31b-cloud", "temperature": 0.7},
        "reviewer": {"model": "gpt-oss:120b-cloud", "temperature": 0.1},
    },
    "local": {
        "planner": {"model": "gemma3:4b", "num_ctx": 32768},
        "writer": {"model": "gemma3:4b", "num_ctx": 32768, "max_tokens": 6144},
        "reviewer": {"model": "gemma3:4b", "num_ctx": 32768},
    },
}

CAPABILITIES: dict[str, dict[str, Any]] = {
    "gpt-oss:120b-cloud": {"thinking": True, "tools": True, "context_length": 131072},
    "gemma4:31b-cloud": {"thinking": True, "vision": True, "context_length": 262144},
    "gemma3:4b": {"thinking": False, "vision": True, "context_length": 131072},
}


def _registry(
    *,
    profile: str = "default",
    overrides: dict[str, str] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
) -> ModelRegistry:
    return ModelRegistry.from_sources(
        profiles=PROFILES if profiles is None else profiles,
        capabilities=CAPABILITIES,
        base_url="http://localhost:11434",
        profile=profile,
        overrides=overrides,
    )


# ---------------------------------------------------------------------------
# Preseden: entri profil menang atas default bawaan
# ---------------------------------------------------------------------------
def test_the_profile_entry_is_used_when_nothing_overrides_it() -> None:
    """Tingkat keempat: nilai dari blok ``profiles:`` yang menang."""
    spec = _registry().spec_for("writer")

    assert spec.model == "gemma4:31b-cloud"
    assert spec.temperature == 0.7


def test_an_unset_option_stays_none_rather_than_becoming_zero() -> None:
    """``None`` berarti "pakai default bawaan" — bukan nol.

    Untuk panggilan LLM, membedakan keduanya itu penting: ``temperature=0`` adalah
    permintaan determinisme yang sah, dan menuliskannya sebagai nol secara diam-diam
    akan mengubah perilaku setiap peran yang tidak menyebut temperature.
    """
    spec = _registry().spec_for("planner")

    assert spec.num_ctx is None
    assert spec.think is None


def test_choosing_a_profile_changes_the_whole_map() -> None:
    """Profil ``local`` adalah jalur yang dijamin bekerja tanpa jaringan."""
    registry = _registry(profile="local")

    assert registry.spec_for("writer").model == "gemma3:4b"
    assert registry.profile == "local"


# ---------------------------------------------------------------------------
# Preseden: override menang atas entri profil
# ---------------------------------------------------------------------------
def test_an_override_replaces_the_model_for_one_role_only() -> None:
    """``--set-model`` mengganti **satu** peran; sisanya tidak tersentuh."""
    registry = _registry(overrides={"writer": "gemma3:4b"})

    assert registry.spec_for("writer").model == "gemma3:4b"
    assert registry.spec_for("planner").model == "gpt-oss:120b-cloud"


def test_an_override_keeps_the_roles_other_options() -> None:
    """Yang diganti adalah namanya saja — temperature dan anggaran tetap milik peran itu.

    Kalau tidak, mengganti model akan diam-diam mengembalikan setiap opsi ke
    default, dan ``--set-model`` menjadi jauh lebih merusak daripada namanya.
    """
    registry = _registry(overrides={"writer": "gemma3:4b"})

    assert registry.spec_for("writer").temperature == 0.7


def test_an_override_for_an_unknown_role_is_refused_and_lists_the_real_ones() -> None:
    """Salah ketik nama peran harus terlihat sebagai daftar peran yang benar."""
    with pytest.raises(UnknownRoleError) as excinfo:
        _registry(overrides={"writter": "gemma3:4b"})

    assert excinfo.value.role == "writter"
    assert "writer" in str(excinfo.value)


def test_spec_for_an_unknown_role_is_refused() -> None:
    with pytest.raises(UnknownRoleError):
        _registry().spec_for("pedagogy")


# ---------------------------------------------------------------------------
# Kapabilitas: gerbang parameter ``think``
# ---------------------------------------------------------------------------
def test_thinking_follows_the_capability_table_when_the_role_is_silent() -> None:
    """``qwen3-coder:480b-cloud`` tidak mendukung ``think``; mengirimnya akan ditolak server.

    Karena itu keputusan ini diambil dari tabel ``models:``, bukan dari niat
    pemanggil.
    """
    assert _registry().supports_thinking("planner") is True

    local = _registry(profile="local")
    assert local.supports_thinking("writer") is False


def test_an_explicit_role_setting_beats_the_capability_table() -> None:
    """Entri peran boleh menimpanya — dan dalam dua arah."""
    profiles = {
        "default": {
            "a": {"model": "gemma3:4b", "think": True},
            "b": {"model": "gpt-oss:120b-cloud", "think": False},
        }
    }

    registry = _registry(profiles=profiles)

    assert registry.supports_thinking("a") is True
    assert registry.supports_thinking("b") is False


def test_an_unknown_model_gets_the_conservative_capability_default() -> None:
    """Model di luar tabel dianggap tidak mendukung apa pun.

    Lebih baik kehilangan sedikit kualitas karena tidak mengirim ``think``,
    daripada mengirim parameter yang tidak dikenal ke model dan gagal.
    """
    registry = _registry(overrides={"writer": "model-yang-tak-dikenal"})

    capability = registry.capability_for("model-yang-tak-dikenal")

    assert capability.thinking is False
    assert capability.vision is False
    assert capability.context_length is None
    assert registry.supports_thinking("writer") is False


def test_distinct_models_deduplicates_and_sorts() -> None:
    """``doctor`` memprobe satu kali per **model**, bukan per peran."""
    assert _registry(profile="local").distinct_models() == ("gemma3:4b",)

    assert _registry().distinct_models() == ("gemma4:31b-cloud", "gpt-oss:120b-cloud")


# ---------------------------------------------------------------------------
# Penolakan yang jelas
# ---------------------------------------------------------------------------
def test_an_unknown_profile_names_the_available_ones() -> None:
    """``--profile`` yang salah harus langsung menampilkan pilihan yang benar."""
    with pytest.raises(ConfigError, match="local") as excinfo:
        _registry(profile="produksi")

    assert "produksi" in str(excinfo.value)


def test_a_profile_without_any_role_is_refused() -> None:
    """""Profil ada tetapi kosong" adalah konfigurasi rusak, bukan pipeline tanpa peran."""
    with pytest.raises(ConfigError, match="peran"):
        _registry(profiles={"default": {}})


def test_an_out_of_range_temperature_is_refused() -> None:
    """Batasnya milik tipe, sehingga tidak ada satu pun jalur yang dapat melewatinya."""
    with pytest.raises(ValidationError):
        _registry(profiles={"default": {"writer": {"model": "gemma3:4b", "temperature": 9.0}}})


def test_an_empty_model_name_is_refused() -> None:
    """``min_length=1``: model tanpa nama akan gagal jauh di dalam adapter, tanpa konteks."""
    with pytest.raises(ValidationError):
        _registry(profiles={"default": {"writer": {"model": ""}}})


# ---------------------------------------------------------------------------
# Kemurnian & kebekuan
# ---------------------------------------------------------------------------
def test_the_registry_is_frozen() -> None:
    """Data beku: tidak ada peran yang dapat diganti setelah registry dirakit."""
    registry = _registry()

    with pytest.raises(ValidationError):
        registry.profile = "local"  # type: ignore[misc]


def test_building_a_registry_does_not_mutate_the_configuration() -> None:
    """``from_sources`` tidak boleh menyentuh bahan mentahnya.

    Override diterapkan dengan ``model_copy``, bukan dengan menulis balik ke peta
    yang diberikan pemanggil — kalau tidak, ``--set-model`` akan menetap di
    konfigurasi dan ikut terpakai oleh ``doctor`` yang berjalan setelahnya.
    """
    import copy

    profiles = copy.deepcopy(PROFILES)
    snapshot = copy.deepcopy(profiles)

    ModelRegistry.from_sources(
        profiles=profiles,
        capabilities=CAPABILITIES,
        base_url="http://localhost:11434",
        profile="default",
        overrides={"writer": "gemma3:4b"},
    )

    assert profiles == snapshot


def test_the_base_url_is_carried_through_untouched() -> None:
    """Registry hanya membawa alamatnya; yang menyambung adalah factory."""
    assert _registry().base_url == "http://localhost:11434"
