"""Penyimpanan dan pembangunan graf konsep (§14).

Graf ini **turunan**, dan seluruh berkasnya disusun mengikuti satu konsekuensi
dari kenyataan itu: apa pun yang ada di sini dapat dibuang dan dibangun ulang.
Karena itu tidak ada operasi yang menyunting satu simpul — yang ada hanyalah
"baca seluruhnya" dan "tulis seluruhnya", persis seperti
:class:`~domain.ports.GraphStore` memintanya. Graf yang dapat disunting
sepotong-sepotong adalah graf yang harus dijaga konsistensinya, dan tidak ada
satu pun pertanyaan di blueprint ini yang membutuhkan itu.

**Sumbernya, dan mengapa hanya ini.** Graf dibangun dari berkas ``.tex``, dan
dari dua direktori: ``input/source_latex/`` (§11, bahan rujukan yang ditulis
manusia) dan ``output/latex/chapters/`` (§25, buku ini sendiri). Yang **tidak**
dibaca adalah prosa: ``BookState.summaries`` memuat ringkasan bab, dan
mengubahnya menjadi konsep menuntut penilaian yang hanya model dapat
memberikannya — pekerjaan yang bukan pekerjaan perintah ``ingest``. Rencana
tahap ini sempat menyebut ringkasan bab sebagai sumber; yang benar-benar
dikerjakan di sini adalah sumber yang **dapat dipastikan**, sebab graf yang
memuat tebakan lebih buruk daripada graf yang kosong: ia terlihat dapat
dipercaya.

**Mengapa penulis atomiknya ditulis ulang di sini.** Sama persis dengan alasan
yang sudah tertulis di ``latex/artifacts.py``: perilakunya identik dengan
:func:`memory.checkpoints.atomic_write_text` (``.tmp`` di direktori yang sama,
``os.replace`` dengan percobaan ulang untuk handle Windows yang tertahan),
tetapi ``memory/`` adalah lapisan luar, dan sebuah adapter tidak boleh
mengenalnya (``test_adapter_layers_do_not_import_outer_layers``). Ini salinan
ketiga dari fungsi yang sama, dan salinan itu adalah harga yang dibayar untuk
aturan lapisan yang justru membuat ketiga paket ini dapat diuji terpisah.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Sequence

from domain.errors import ArtifactWriteError
from domain.graph import ConceptEdge, ConceptGraph, ConceptNode, build_graph
from domain.latex import chapter_number_from_filename

from graph.entities import concepts_from_latex
from graph.relations import references_from_latex

#: Nama berkas graf di dalam ``knowledge/graph/``.
GRAPH_FILENAME = "concepts.json"

#: Akhiran berkas yang dibaca sebagai sumber (§11).
SUFFIX = ".tex"

#: Berapa kali ``os.replace`` diulang sebelum menyerah. Angka dan alasannya sama
#: dengan penulis state dan artefak LaTeX: antivirus dan indexer di Windows dapat
#: memegang handle berkas beberapa puluh milidetik setelah penulis menutupnya.
_REPLACE_RETRIES = 8
_REPLACE_BACKOFF_S = 0.025


# ---------------------------------------------------------------------------
# Pembacaan berkas
# ---------------------------------------------------------------------------
def tex_files(directory: Path) -> tuple[Path, ...]:
    """Seluruh berkas ``.tex`` di bawah ``directory``, terurut (rekursif).

    Terurut karena graf yang dibangun ulang dari bahan yang sama harus **sama**:
    konsep yang namanya kembar disatukan oleh :func:`~domain.graph.build_graph`
    dengan "kemunculan pertama yang menang", dan urutan yang bergantung pada
    filesystem berarti definisi yang bertahan pun bergantung padanya.
    """
    if not directory.is_dir():
        return ()
    return tuple(sorted(path for path in directory.rglob(f"*{SUFFIX}") if path.is_file()))


def graph_from_directory(directory: Path) -> ConceptGraph:
    """Graf konsep dari setiap berkas ``.tex`` di bawah ``directory`` (§11, §14).

    Berkas yang tidak dapat dibaca **dilewati**, bukan digagalkan. Tidak ada satu
    pun jalan bagi pemanggil untuk menindaklanjutinya: yang dihasilkan fungsi ini
    adalah bahan mentah bagi :func:`~domain.graph.build_graph`, dan graf yang
    kehilangan satu berkas tetap graf yang benar tentang berkas-berkas lainnya.
    Yang justru salah adalah membuat ``ingest`` berhenti membangun apa pun karena
    satu berkas rujukan yang rusak — dan itu juga sebabnya ``directory`` yang
    tidak ada mengembalikan graf kosong alih-alih melempar.

    Nomor bab diambil dari nama berkasnya
    (:func:`~domain.latex.chapter_number_from_filename`). Berkas bahan yang tidak
    mengikuti pola itu menghasilkan konsep tanpa nomor bab — dan itu memang
    keadaannya: bahan rujukan tidak selalu milik satu bab.
    """
    nodes: list[ConceptNode] = []
    edges: list[ConceptEdge] = []
    for path in tex_files(directory):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        chapter = chapter_number_from_filename(path.name)
        nodes.extend(concepts_from_latex(text, chapter=chapter))
        edges.extend(references_from_latex(text))
    return build_graph(nodes, edges)


def graph_from_directories(directories: Sequence[Path]) -> ConceptGraph:
    """Gabungan graf beberapa direktori, terurut sesuai ``directories`` (§11, §14).

    Digabungkan setelah masing-masing dibangun, bukan dengan menumpuk seluruh
    berkasnya lebih dulu: pemisahan itu yang membuat relasi antar-berkas hanya
    lahir **di dalam** satu direktori. Dua bagian dari dua buku berbeda yang
    kebetulan berlabel sama tidak boleh saling merujuk hanya karena keduanya
    ditemukan dalam satu jalankan.
    """
    nodes: list[ConceptNode] = []
    edges: list[ConceptEdge] = []
    for directory in directories:
        part = graph_from_directory(directory)
        nodes.extend(part.nodes)
        edges.extend(part.edges)
    return build_graph(nodes, edges)


# ---------------------------------------------------------------------------
# Penyimpanan
# ---------------------------------------------------------------------------
def _replace_with_retry(tmp: Path, target: Path) -> None:
    """Pindahkan ``tmp`` ke ``target``, tahan terhadap handle Windows yang tertahan.

    :raises ArtifactWriteError: bila seluruh percobaan gagal.
    """
    last: PermissionError | None = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, target)
            return
        except PermissionError as exc:  # pragma: no cover - bergantung timing OS
            last = exc
            time.sleep(_REPLACE_BACKOFF_S * (attempt + 1))

    raise ArtifactWriteError(target, "berkas terkunci proses lain") from last


class JsonGraphStore:
    """Implementasi :class:`~domain.ports.GraphStore` di atas satu berkas JSON.

    Satu berkas, bukan satu berkas per simpul: graf ini berisi puluhan konsep,
    dibaca dan ditulis seluruhnya, dan di-track git bersama ``knowledge/``-nya.
    Satu berkas berarti satu diff yang terbaca sebagai "grafnya berubah", bukan
    lima puluh berkas yang harus dijumlahkan pembacanya.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        """Direktori graf (``knowledge/graph``)."""
        return self._directory

    @property
    def path(self) -> Path:
        """Berkas graf (``knowledge/graph/concepts.json``)."""
        return self._directory / GRAPH_FILENAME

    def load(self) -> ConceptGraph:
        """Baca graf tersimpan; graf kosong bila belum ada atau tidak terbaca.

        Graf yang belum dibangun bukan kerusakan, dan graf yang berkasnya rusak
        juga bukan kerusakan yang layak menghentikan pembangunan ulang: keduanya
        berakhir sebagai ``ConceptGraph()``, dan pemanggilnya — perintah
        ``ingest`` — akan segera menimpanya dengan graf yang benar. Melempar di
        sini berarti satu berkas JSON yang setengah tertulis membuat perintah yang
        justru memperbaikinya tidak dapat dijalankan.
        """
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ConceptGraph()
        try:
            return ConceptGraph.model_validate_json(text)
        except ValueError:
            return ConceptGraph()

    def save(self, graph: ConceptGraph) -> None:
        """Tulis seluruh graf, menimpa yang lama.

        :raises ArtifactWriteError: bila penulisan gagal.
        """
        path = self.path
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(graph.model_dump_json(indent=2))
            _replace_with_retry(tmp, path)
        except OSError as exc:
            raise ArtifactWriteError(path, str(exc)) from exc


__all__ = [
    "GRAPH_FILENAME",
    "SUFFIX",
    "JsonGraphStore",
    "graph_from_directories",
    "graph_from_directory",
    "tex_files",
]
