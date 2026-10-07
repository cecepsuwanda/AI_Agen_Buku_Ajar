"""Adapter Ollama — **satu-satunya berkas yang meng-import `ollama`**.

Ditegakkan oleh ``tests/unit/test_architecture.py``. Seluruh sisa aplikasi
berbicara lewat :mod:`domain.ports`; jenis error SDK tidak pernah naik ke atas.

Dua hal di berkas ini lahir dari pengujian nyata di mesin target:

1. **Jejak penalaran tidak pernah disambungkan ke ``text``.** Pada model
   ``thinking``, ``message.thinking`` dapat menghabiskan seluruh anggaran token
   sehingga ``message.content`` keluar **kosong** dengan ``done_reason='length'``.
   Tercampurnya jejak itu ke draf bab akan menjadi bug kualitas yang sunyi.
2. Karena itu output terpotong punya error tersendiri
   (:class:`~domain.errors.TruncatedOutputError`), bukan diperlakukan sebagai
   "JSON rusak" biasa — penanganannya berbeda: naikkan anggaran, bukan perbaiki prompt.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

import ollama
from tenacity import Retrying, stop_after_attempt, wait_exponential

from domain.completion import interpret_completion
from domain.errors import (
    BukuAjarError,
    ModelAuthError,
    ModelTimeoutError,
    ModelUnavailableError,
    ServiceUnavailableError,
)
from domain.ports import ChatModel, ChatRequest, ChatResult, EmbeddingModel
from models.model_registry import ModelCapability, ModelRegistry, ModelSpec
from models.request_options import resolve_options

#: Pesan yang menandakan masalah autentikasi akun Ollama (model cloud).
_AUTH_MARKERS = (
    "sign in",
    "signin",
    "unauthorized",
    "forbidden",
    "api key",
    "not authenticated",
    "login",
)

_TIMEOUT_MARKERS = ("timed out", "timeout", "read timeout", "connect timeout")


def classify_error(
    exc: Exception,
    *,
    model: str,
    base_url: str,
    timeout_s: float,
) -> BukuAjarError:
    """Terjemahkan error SDK menjadi error domain (MURNI).

    Inilah batas tempat jenis SDK berhenti. Tidak ada agent yang melihat
    ``ollama.ResponseError``.
    """
    if isinstance(exc, ollama.ResponseError):
        status = int(getattr(exc, "status_code", -1) or -1)
        message = str(getattr(exc, "error", "") or exc)
        if status in (401, 403) or _looks_like(message, _AUTH_MARKERS):
            return ModelAuthError(model, f"HTTP {status}")
        if status == 404:
            return ModelUnavailableError(model, f"HTTP {status}")
        return ModelUnavailableError(model, f"HTTP {status}: {message}")

    if isinstance(exc, ollama.RequestError):
        if _looks_like(str(exc), _TIMEOUT_MARKERS):
            return ModelTimeoutError(model, timeout_s)
        return ServiceUnavailableError(base_url, str(exc))

    if isinstance(exc, TimeoutError):
        return ModelTimeoutError(model, timeout_s)

    if _looks_like(str(exc), _TIMEOUT_MARKERS):
        return ModelTimeoutError(model, timeout_s)

    return ServiceUnavailableError(base_url, f"{type(exc).__name__}: {exc}")


def _looks_like(message: str, markers: tuple[str, ...]) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in markers)


def build_retry(
    *,
    attempts: int = 3,
    initial_backoff_s: float = 1.0,
    max_backoff_s: float = 8.0,
) -> Retrying:
    """Bangun kebijakan retry untuk kegagalan **transien** saja.

    Objek ``Retrying``, bukan dekorator: kebijakan berasal dari konfigurasi,
    dapat di-*inject*, dan dapat diuji. Yang **tidak** di-retry adalah
    :class:`ModelAuthError` dan :class:`ModelUnavailableError` — kredensial
    tidak akan membaik dalam delapan detik, dan model yang hilang butuh
    ``ollama pull``, bukan percobaan ulang. Me-retry keduanya hanya mengubah
    kegagalan jelas 0,5 detik menjadi kegagalan 9 detik dengan pesan yang sama.

    Konstruksi kebijakan tinggal di sini (bukan di ``app/container.py``) karena
    ``tenacity`` adalah detail adapter: composition root cukup meneruskan tiga
    angka dari ``config.yaml`` tanpa perlu tahu ada backoff eksponensial.
    """
    return Retrying(
        stop=stop_after_attempt(max(attempts, 1)),
        wait=wait_exponential(
            multiplier=max(initial_backoff_s, 0.1), max=max(max_backoff_s, 0.1)
        ),
        retry=is_transient_failure,
        reraise=True,
    )


def default_retry() -> Retrying:
    """Kebijakan retry bawaan, dengan parameter standar."""
    return build_retry()


def is_transient_failure(retry_state: Any) -> bool:
    """True hanya untuk kegagalan yang masuk akal dicoba ulang (MURNI).

    Dipisah sebagai fungsi bernama — bukan lambda di dalam ``Retrying`` —
    supaya kebijakannya dapat diuji langsung, tanpa menjalankan panggilan apa pun.
    """
    outcome = retry_state.outcome
    if outcome is None:
        return False
    exc = outcome.exception()
    return isinstance(exc, (ModelTimeoutError, ServiceUnavailableError))


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
class OllamaChatModel:
    """Implementasi :class:`domain.ports.ChatModel` di atas SDK Ollama.

    Stateless kecuali handle klien dan spesifikasi perannya. Tidak tahu apa pun
    soal bab, review, atau perbaikan JSON — itu urusan agent.
    """

    def __init__(
        self,
        *,
        spec: ModelSpec,
        capability: ModelCapability,
        client: ollama.Client,
        base_url: str,
        timeout_s: float,
        supports_thinking: bool,
        retry: Retrying | None = None,
        keep_alive: str | float | None = None,
    ) -> None:
        self._spec = spec
        self._capability = capability
        self._client = client
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._supports_thinking = supports_thinking
        self._retry = retry or default_retry()
        self._keep_alive = keep_alive

    @property
    def model_name(self) -> str:
        """Nama model yang dipegang adapter ini."""
        return self._spec.model

    def complete(self, request: ChatRequest) -> ChatResult:
        """Jalankan satu panggilan chat, dengan retry untuk kegagalan transien."""
        payload = self._build_payload(request)

        for attempt in self._retry:
            with attempt:
                return self._call(payload, request)

        raise AssertionError("tidak tercapai")  # pragma: no cover - tenacity reraise

    # -- internal ----------------------------------------------------------
    def _build_payload(self, request: ChatRequest) -> dict[str, Any]:
        messages = []
        if request.system.strip():
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.user})

        payload: dict[str, Any] = {
            "model": request.model or self._spec.model,
            "messages": messages,
            "options": self._options(request),
            "stream": False,
        }
        if self._keep_alive is not None:
            payload["keep_alive"] = self._keep_alive

        if request.format_schema is not None:
            # Skema mentah sebagai dict — signature SDK 0.6.2 menyatakan
            # `format` bertipe `Literal['','json'] | dict | None`, jadi kita
            # tidak bersandar pada dukungan kelas Pydantic yang tidak dijanjikan.
            payload["format"] = dict(request.format_schema)

        # Hanya kirim `think` bila modelnya memang mendukung. Mengirimnya ke
        # qwen3-coder (tanpa kapabilitas thinking) akan ditolak server.
        if request.think and self._supports_thinking:
            payload["think"] = True

        return payload

    def _options(self, request: ChatRequest) -> dict[str, Any]:
        """Opsi efektif untuk ``request`` — aturannya tinggal di satu tempat.

        Delegasi ke :func:`~models.request_options.resolve_options`, bukan
        menyalin aturannya: ``--dry-run`` memakai fungsi yang sama, sehingga
        angka yang ditampilkannya tidak mungkin menyimpang dari yang dikirim.
        """
        return resolve_options(request, self._spec)

    def _call(self, payload: dict[str, Any], request: ChatRequest) -> ChatResult:
        started = time.perf_counter()
        try:
            response = self._client.chat(**payload)
        except Exception as exc:  # noqa: BLE001 - diterjemahkan ke error domain
            raise classify_error(
                exc,
                model=payload["model"],
                base_url=self._base_url,
                timeout_s=self._timeout_s,
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        message = response.message

        # Keputusan "terpotong atau tidak", dan janji bahwa jejak penalaran tidak
        # pernah bocor ke ``text``, tinggal di ``domain.completion`` — di sana ia
        # murni dan dapat diuji tanpa jaringan. Di sini hanya diterjemahkan dari
        # objek SDK menjadi nilai primitif, yang memang satu-satunya yang bisa
        # dikerjakan berkas ini dan tidak bisa dikerjakan berkas lain.
        return interpret_completion(
            model=response.model or payload["model"],
            content=message.content or "",
            thinking=getattr(message, "thinking", None),
            done_reason=response.done_reason or "",
            max_tokens=payload["options"].get("num_predict"),
            latency_ms=latency_ms,
            prompt_tokens=response.prompt_eval_count or 0,
            output_tokens=response.eval_count or 0,
        )


# ---------------------------------------------------------------------------
# Embedding (dipakai Tahap 3)
# ---------------------------------------------------------------------------
class OllamaEmbeddingModel:
    """Implementasi :class:`domain.ports.EmbeddingModel`."""

    def __init__(
        self,
        *,
        spec: ModelSpec,
        client: ollama.Client,
        base_url: str,
        timeout_s: float,
    ) -> None:
        self._spec = spec
        self._client = client
        self._base_url = base_url
        self._timeout_s = timeout_s

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Kembalikan satu vektor untuk setiap teks."""
        if not texts:
            return []
        try:
            response = self._client.embed(model=self._spec.model, input=list(texts))
        except Exception as exc:  # noqa: BLE001
            raise classify_error(
                exc,
                model=self._spec.model,
                base_url=self._base_url,
                timeout_s=self._timeout_s,
            ) from exc
        return [list(vector) for vector in response.embeddings]


# ---------------------------------------------------------------------------
# Factory — konstruksi adapter, memo, dan seam untuk tes
# ---------------------------------------------------------------------------
class ChatModelFactory:
    """Membangun dan meng-*memo* adapter untuk setiap peran.

    Ini implementasi dari :class:`models.model_router.ChatModelSource`.

    Parameter ``override`` adalah **seluruh cerita dependency injection**:
    menyuntikkan satu objek di sana menghasilkan pipeline produksi yang utuh
    dengan **nol jaringan**. Itulah yang membuat suite offline dan ``--dry-run``
    mungkin.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        timeout_s: float,
        retry: Retrying | None = None,
        keep_alive: str | float | None = None,
        override: ChatModel | None = None,
        client: ollama.Client | None = None,
    ) -> None:
        self._registry = registry
        self._timeout_s = timeout_s
        self._retry = retry
        self._keep_alive = keep_alive
        self._override = override
        self._client = client
        self._chat_cache: dict[str, ChatModel] = {}
        self._embed_cache: dict[str, EmbeddingModel] = {}

    @property
    def client(self) -> ollama.Client:
        """Klien HTTP bersama; dibuat malas agar ``--dry-run`` tak menyentuh jaringan."""
        if self._client is None:
            self._client = ollama.Client(
                host=self._registry.base_url, timeout=self._timeout_s
            )
        return self._client

    def chat_for(self, role: str) -> ChatModel:
        """Adapter chat untuk ``role`` (di-*memo* per peran)."""
        if self._override is not None:
            return self._override
        if role not in self._chat_cache:
            spec = self._registry.spec_for(role)
            self._chat_cache[role] = OllamaChatModel(
                spec=spec,
                capability=self._registry.capability_for(spec.model),
                client=self.client,
                base_url=self._registry.base_url,
                timeout_s=self._timeout_s,
                supports_thinking=self._registry.supports_thinking(role),
                retry=self._retry,
                keep_alive=self._keep_alive,
            )
        return self._chat_cache[role]

    def embedding_for(self, role: str) -> EmbeddingModel:
        """Adapter embedding untuk ``role``."""
        if role not in self._embed_cache:
            spec = self._registry.spec_for(role)
            self._embed_cache[role] = OllamaEmbeddingModel(
                spec=spec,
                client=self.client,
                base_url=self._registry.base_url,
                timeout_s=self._timeout_s,
            )
        return self._embed_cache[role]


# ---------------------------------------------------------------------------
# Probe untuk `doctor`
# ---------------------------------------------------------------------------
def list_installed_models(
    client: ollama.Client,
    *,
    base_url: str,
    timeout_s: float,
) -> tuple[str, ...]:
    """Nama seluruh model yang ter-*pull* di server.

    :raises ServiceUnavailableError: bila server tidak dapat dihubungi.
    """
    try:
        response = client.list()
    except Exception as exc:  # noqa: BLE001
        raise classify_error(
            exc, model="(list)", base_url=base_url, timeout_s=timeout_s
        ) from exc

    names: list[str] = []
    for item in response.models or []:
        name = getattr(item, "model", None) or getattr(item, "name", None)
        if name:
            names.append(str(name))
    return tuple(sorted(names))


def probe_model(
    client: ollama.Client,
    model: str,
    *,
    base_url: str = "",
    timeout_s: float,
) -> tuple[bool, str]:
    """Kirim satu token ke ``model`` untuk memastikan ia benar-benar menjawab.

    Memeriksa keberadaan tag tidak cukup: model ``:cloud`` ada di daftar lokal
    tetapi tetap bisa menolak karena autentikasi. Probe inilah yang mengubah
    kegagalan di bab ke-7 menjadi pesan jelas di awal.

    :returns: ``(berhasil, keterangan)`` — tidak melempar, agar ``doctor``
        dapat melaporkan semua peran sekaligus.
    """
    try:
        client.chat(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            options={"num_predict": 1, "temperature": 0.0},
            stream=False,
        )
    except Exception as exc:  # noqa: BLE001
        translated = classify_error(
            exc, model=model, base_url=base_url, timeout_s=timeout_s
        )
        return False, str(translated)
    return True, "ok"


__all__ = [
    "ChatModelFactory",
    "OllamaChatModel",
    "OllamaEmbeddingModel",
    "build_retry",
    "classify_error",
    "default_retry",
    "is_transient_failure",
    "list_installed_models",
    "probe_model",
]
