"""Perlengkapan bersama seluruh tes integrasi — asli, bukan tiruan.

Barang yang dibagi di sini sengaja berupa **implementasi sungguhan**:
``JsonStateStore`` yang benar-benar menulis berkas atomik, ``MarkdownArtifacts``
yang benar-benar menulis Markdown, dan ``RecordingReporter`` yang merekam alih-alih
mencetak. Yang di-*fake* di tes integrasi hanya ``ChatModel``.

Berkas ini ada karena lebih dari satu berkas uji merangkai pipeline yang sama
(``test_orchestrator_offline`` dan ``test_human_approval_offline``), dan dua
salinan perlengkapan akan berbeda diam-diam pada suatu hari — tepat pada bagian
yang seharusnya paling tidak boleh berbeda.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.container import FixedClock
from memory.artifacts import MarkdownArtifacts
from memory.project_state import JsonStateStore
from tests.fakes.reporting import RecordingReporter


@pytest.fixture
def state(tmp_path: Path) -> JsonStateStore:
    """Penyimpanan state asli di ``tmp_path`` — bukan palsu.

    Sengaja: atomik, ``schema_version``, dan penomoran berkas ikut diuji di sini.
    Penyimpanan palsu akan membuat seluruh tes integrasi tetap hijau meskipun
    ``memory/`` rusak — dan itu justru satu-satunya bagian yang paling mahal
    bila salah. Jamnya dibekukan supaya ``updated_at`` dapat diperiksa.
    """
    return JsonStateStore(tmp_path / "state", clock=FixedClock("2026-10-07T12:00:00+00:00"))


@pytest.fixture
def artifacts(tmp_path: Path) -> MarkdownArtifacts:
    """Penulis Markdown asli di ``output/`` milik tes."""
    return MarkdownArtifacts(tmp_path / "output")


@pytest.fixture
def reporter() -> RecordingReporter:
    """Reporter yang merekam setiap panggilan pelaporan."""
    return RecordingReporter()
