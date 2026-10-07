"""Pemetaan peran → model (§6, §7, §32).

``ModelRegistry`` adalah **data murni**: hasil parse konfigurasi, sudah
menerapkan profil dan override. Seluruh *kebijakan* resolusi ada di sini, dan
:meth:`ModelRegistry.from_sources` adalah pemetaan data-masuk/data-keluar yang
dapat diuji penuh tanpa jaringan.

Sengaja **tidak** meng-import ``app.config``: registry menerima bahan mentahnya
sebagai argumen, sehingga tidak ada siklus antara konfigurasi dan model.
"""

from __future__ import annotations

import warnings
from typing import Any, Mapping

from pydantic import Field, model_validator

from domain.base import FrozenModel
from domain.errors import ConfigError, UnknownRoleError


class ModelCapability(FrozenModel):
    """Metadata kapabilitas sebuah model.

    Dicocokkan :command:`doctor` terhadap ``/api/tags``. Yang paling penting:
    ``thinking`` menentukan apakah parameter ``think`` boleh dikirim sama sekali
    — ``qwen3-coder:480b-cloud`` tidak mendukungnya.
    """

    thinking: bool = False
    vision: bool = False
    tools: bool = False
    context_length: int | None = None
    is_remote: bool = False


class ModelSpec(FrozenModel):
    """Satu entri peran → model beserta opsinya.

    ``None`` berarti "pakai default bawaan" — bukan nol. Untuk panggilan LLM,
    membedakan "tidak diset" dari "diset ke 0" itu penting: ``temperature=0``
    adalah permintaan determinisme yang sah.
    """

    model: str = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    num_ctx: int | None = Field(default=None, ge=128)
    think: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_name(cls, data: Any) -> Any:
        """Terima ``name:`` sebagai alias ``model:`` (§6 menulis ``model``, §32 ``name``)."""
        if isinstance(data, Mapping) and "model" not in data and "name" in data:
            warnings.warn(
                "Kunci 'name' pada entri model sudah usang; gunakan 'model'.",
                DeprecationWarning,
                stacklevel=2,
            )
            data = {**data, "model": data["name"]}
            data.pop("name", None)
        return data


class ModelRegistry(FrozenModel):
    """Peta peran → model yang sudah diselesaikan penuh.

    Tidak punya method yang menyentuh jaringan dan tidak menyimpan client —
    itulah yang mencegahnya menjadi god object.
    """

    profile: str
    base_url: str
    roles: Mapping[str, ModelSpec]
    capabilities: Mapping[str, ModelCapability] = Field(default_factory=dict)

    # -- Konstruksi --------------------------------------------------------
    @classmethod
    def from_sources(
        cls,
        *,
        profiles: Mapping[str, Mapping[str, Any]],
        capabilities: Mapping[str, Mapping[str, Any]],
        base_url: str,
        profile: str,
        overrides: Mapping[str, str] | None = None,
    ) -> "ModelRegistry":
        """Bangun registry dari bahan mentah konfigurasi (MURNI).

        :raises ConfigError: bila profil tidak ada atau peran tidak lengkap.
        :raises UnknownRoleError: bila override menyebut peran yang tidak dikenal.
        """
        if profile not in profiles:
            raise ConfigError(
                f"Profil {profile!r} tidak ada di config.yaml. "
                f"Profil tersedia: {', '.join(sorted(profiles)) or '(tidak ada)'}"
            )

        raw_roles = profiles[profile]
        roles = {name: ModelSpec.model_validate(entry) for name, entry in raw_roles.items()}

        caps = {
            name: ModelCapability.model_validate(entry)
            for name, entry in capabilities.items()
        }

        for role, model_name in (overrides or {}).items():
            if role not in roles:
                raise UnknownRoleError(role, tuple(sorted(roles)))
            roles[role] = roles[role].model_copy(update={"model": model_name})

        if not roles:
            raise ConfigError(f"Profil {profile!r} tidak mendefinisikan peran apa pun")

        return cls(
            profile=profile,
            base_url=base_url,
            roles=dict(roles),
            capabilities=dict(caps),
        )

    # -- Query -------------------------------------------------------------
    def spec_for(self, role: str) -> ModelSpec:
        """Spesifikasi model untuk ``role``.

        :raises UnknownRoleError: bila peran tidak terdaftar.
        """
        try:
            return self.roles[role]
        except KeyError:
            raise UnknownRoleError(role, tuple(sorted(self.roles))) from None

    def capability_for(self, model_name: str) -> ModelCapability:
        """Kapabilitas ``model_name``; default konservatif bila tidak terdaftar.

        Default-nya sengaja "tidak mendukung apa pun" — lebih baik kehilangan
        sedikit kualitas karena tidak mengirim ``think``, daripada mengirim
        parameter yang tidak dikenal ke model dan gagal.
        """
        return self.capabilities.get(model_name, ModelCapability())

    def supports_thinking(self, role: str) -> bool:
        """True bila ``think`` boleh dikirim untuk peran ini."""
        spec = self.spec_for(role)
        if spec.think is not None:
            return spec.think
        return self.capability_for(spec.model).thinking

    def distinct_models(self) -> tuple[str, ...]:
        """Nama model unik yang dipakai profil ini (untuk probe ``doctor``)."""
        return tuple(sorted({spec.model for spec in self.roles.values()}))
