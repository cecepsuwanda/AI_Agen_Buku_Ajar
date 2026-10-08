"""Adapter graf konsep — batas sistem tempat bahan bertemu bentuk yang dapat ditelusuri (§14).

Paket ini mengikuti aturan yang sama dengan ``ingestion/``, ``rag/``, dan
``latex/``: ia boleh menyentuh dunia luar (berkas), ia hanya boleh bergantung
pada ``domain/``, dan ia **tidak boleh** mengenal ``app/``, ``agents/``,
``models/``, maupun ``memory/``. Ia juga tidak boleh memanggil adapter lain —
dan itu yang paling terasa di sini: ``graph/`` membaca berkas ``.tex`` sendiri
alih-alih meminjam ``ingestion/latex_loader.py``, sebab keduanya menanyakan hal
yang berbeda atas teks yang sama. Pemuat di ``ingestion/`` meratakan LaTeX
menjadi potongan untuk pencarian semantik; graf justru membutuhkan batas antar
bagian dan label yang dirujuk, dan meratakannya lebih dulu berarti membangunnya
kembali dari teks yang sudah kehilangan keduanya.

Tiga bagian:

* :mod:`graph.entities` — konsep dari berkas LaTeX: bagian, definisi.
* :mod:`graph.relations` — relasi yang benar-benar dinyatakan LaTeX, yaitu
  rujukan silang ``\\label``/``\\ref``.
* :mod:`graph.knowledge_graph` — perakitan dan penyimpanannya ke
  ``knowledge/graph/concepts.json``.

Seluruh operasi **atas** grafnya — prasyarat, tetangga, penjelasan kembar,
penyimpangan istilah — ada di :mod:`domain.graph`, sebagai fungsi murni. Paket
ini tidak menambah satu pun aturan penelusuran; ia hanya menyediakan bahan dan
tempat menyimpannya.
"""

from graph.entities import concepts_from_latex, plain_definition, sections_of
from graph.knowledge_graph import (
    GRAPH_FILENAME,
    JsonGraphStore,
    graph_from_directories,
    graph_from_directory,
    tex_files,
)
from graph.relations import references_from_latex

__all__ = [
    "GRAPH_FILENAME",
    "JsonGraphStore",
    "concepts_from_latex",
    "graph_from_directories",
    "graph_from_directory",
    "plain_definition",
    "references_from_latex",
    "sections_of",
    "tex_files",
]
