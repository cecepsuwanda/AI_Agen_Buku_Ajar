"""Satu smoke test terhadap Ollama sungguhan — opt-in, di-skip secara bawaan.

Suite bawaan proyek ini hijau **tanpa jaringan** (``pytest.ini`` menyetel
``-m "not live"``), dan itu memang janji utamanya. Berkas ini adalah
pengecualian yang disengaja: satu tes yang membuktikan bahwa seluruh jaring
pengaman offline itu tidak menyembunyikan kenyataan bahwa adapter sungguhan
masih dapat berbicara dengan server sungguhan.

Yang diuji di sini adalah **satu-satunya asumsi yang menopang seluruh desain**:
``format=<JSON Schema>`` membuat model mengembalikan JSON yang lolos validasi
Pydantic. Bila asumsi itu runtuh — karena perubahan SDK, model baru, atau
perilaku grammar Ollama yang berubah — tidak ada tes offline mana pun yang akan
menangkapnya, sebab semua tes offline memakai model palsu yang *sudah* tahu
jawabannya.

Memakai profil ``local`` (``gemma3:4b``) dengan sengaja: ia satu-satunya jalur
yang dijamin tanpa akun dan tanpa kuota, sehingga tes ini tidak menghabiskan
apa pun milik siapa pun setiap kali dijalankan.

Bila Ollama tidak berjalan atau modelnya belum di-*pull*, tes ini **skip**, bukan
gagal: mesin tanpa Ollama bukan mesin dengan kode yang rusak. Karena itu ia juga
tidak boleh menjadi satu-satunya bukti bahwa pipeline bekerja — bukti itu ada di
``tests/integration/``.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from app.config import load_config
from app.container import build_registry, build_retry_policy
from domain.errors import ModelAuthError, ModelUnavailableError
from domain.ports import ChatModel, ChatRequest
from domain.structured import parse_output, strict_schema
from models.model_router import ModelRouter
from models.ollama_client import ChatModelFactory
from tests.conftest import PROJECT_ROOT

#: Seluruh berkas ini hanya berjalan dengan ``pytest -m live``.
pytestmark = pytest.mark.live

#: Profil yang dijamin offline — lihat catatan modul.
LOCAL_PROFILE = "local"

#: Peran yang dipakai. Penulis, karena ia peran yang kontraknya paling longgar
#: (temperatur tinggi, keluaran panjang) — jadi bila ia patuh pada skema, peran
#: lain yang lebih ketat juga patuh.
ROLE = "writer"


class Greeting(BaseModel):
    """Kontrak sekecil mungkin yang masih berbentuk JSON berskema.

    Sengaja bukan model domain: yang sedang diuji adalah **jalur** structured
    output (skema → grammar → JSON → Pydantic), bukan kosakata buku ajar. Model
    domain yang besar hanya akan menambah waktu dan memperbesar peluang gagal
    karena alasan yang tidak berhubungan dengan yang ingin dibuktikan.
    """

    language: str = Field(min_length=1)
    greeting: str = Field(min_length=1)


@pytest.fixture(scope="module")
def model() -> ChatModel:
    """Adapter chat sungguhan untuk profil ``local``.

    Di-*scope*-kan per modul supaya cold load (~41 detik terukur pada mesin ini)
    hanya dibayar sekali bila kelak ada tes live kedua.
    """
    config = load_config(PROJECT_ROOT / "config.yaml", root=PROJECT_ROOT, profile=LOCAL_PROFILE)
    registry = build_registry(config, profile=LOCAL_PROFILE)
    factory = ChatModelFactory(
        registry,
        timeout_s=config.ollama.request_timeout_s,
        retry=build_retry_policy(config),
        keep_alive=config.ollama.keep_alive,
    )
    return ModelRouter(registry, factory).chat(ROLE)


def test_a_real_model_returns_json_that_satisfies_the_schema(model: ChatModel) -> None:
    """Skema yang dikirim betul-betul membatasi decoding, pada model sungguhan."""
    request = ChatRequest(
        # Nama model sengaja kosong: yang menentukan adalah spesifikasi peran,
        # bukan pemanggil (§6). Adapter mengisinya dari registry.
        model="",
        system="Balas hanya dengan satu objek JSON. Tanpa penjelasan, tanpa pagar markdown.",
        user=(
            "Sapa pembaca dalam bahasa Indonesia, lalu sebutkan kode bahasanya. "
            'Balas dengan bentuk: {"language": "<kode>", "greeting": "<sapaan>"}'
        ),
        format_schema=strict_schema(Greeting),
        temperature=0.0,
        max_tokens=512,
        role=ROLE,
        prompt_version="live-smoke",
    )

    try:
        result = model.complete(request)
    except (ModelUnavailableError, ModelAuthError) as exc:
        pytest.skip(f"Ollama atau profil {LOCAL_PROFILE!r} tidak siap: {exc}")

    # Gerbang sesungguhnya bukan "JSON-nya terlihat benar", melainkan bahwa ia
    # lolos validasi model Pydantic — persis gerbang yang dipakai pipeline.
    greeting = parse_output(result.text, Greeting)

    assert greeting.greeting.strip(), "sapaan tidak boleh kosong"
    assert greeting.language.strip(), "kode bahasa tidak boleh kosong"
    assert result.model, "adapter seharusnya melaporkan model yang benar-benar dipakai"
