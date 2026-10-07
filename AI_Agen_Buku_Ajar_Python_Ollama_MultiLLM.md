# Panduan Membangun AI Agent Buku Ajar
## Python + Ollama + Multi-LLM + RAG + LaTeX

## 1. Tujuan

Membangun aplikasi **AI Agent Buku Ajar** yang:

- ditulis dengan Python;
- menggunakan Ollama sebagai runtime LLM;
- dapat menggunakan **lebih dari satu model LLM**;
- membaca kumpulan referensi dari folder;
- menerima PDF, Markdown, TXT, LaTeX, BibTeX, dan dokumen RPS;
- menggunakan RAG untuk mengambil konteks yang relevan;
- menyusun buku **bab-per-bab**, bukan meminta satu model menghasilkan seluruh buku;
- mempunyai agent khusus untuk perencanaan, riset, penulisan, review, fact-checking, citation checking, dan LaTeX;
- menghasilkan source `.tex`, `.bib`, gambar/asset, serta PDF;
- menyimpan checkpoint sehingga proses dapat dilanjutkan;
- melakukan pemeriksaan kualitas sebelum sebuah bab dianggap selesai.

---

# 2. Prinsip Arsitektur

Jangan membuat pipeline:

```text
Prompt
  ↓
Satu LLM
  ↓
Buku lengkap
```

Gunakan pipeline agentic:

```text
RPS + Referensi + Aturan Buku
              │
              ▼
       Knowledge Ingestion
              │
       ┌──────┴──────┐
       ▼             ▼
   Vector RAG     Knowledge Graph
       │             │
       └──────┬──────┘
              ▼
       Book Planner Agent
              │
              ▼
        Chapter Planner
              │
              ▼
        Research Agent
              │
              ▼
       Chapter Writer
              │
       ┌──────┴──────┐
       ▼             ▼
 Fact Checker    Citation Checker
       │             │
       └──────┬──────┘
              ▼
       Pedagogy Reviewer
              │
              ▼
       Consistency Reviewer
              │
              ▼
          LaTeX Agent
              │
              ▼
       LaTeX Build / QA
              │
              ▼
       Approved Chapter
              │
              ▼
          Next Chapter
```

---

# 3. Proyek Open Source yang Perlu Dijadikan Referensi

## 3.1 AI Book Composer — kandidat fondasi utama

GitHub:

https://github.com/rdar-lab/ai-book-composer

Project ini mendeskripsikan dirinya sebagai tool untuk menghasilkan buku dari direktori source files menggunakan pola **Deep Agent**.

Workflow utamanya:

```text
Plan
  ↓
Execute
  ↓
Decorate
  ↓
Iterate
  ↓
Verify
```

Repository tersebut juga mendukung beberapa provider LLM termasuk Ollama, mempunyai dukungan input berbagai format, ekstraksi gambar PDF, dan generasi chapter secara paralel.

**Rekomendasi:** pelajari struktur orchestration dan workflow-nya terlebih dahulu.

---

## 3.2 Multi-Agent Book Writer

GitHub:

https://github.com/EimanTahir027/Multi-Agent-Book-Writer

Project ini sangat dekat dengan konsep multi-agent book writing.

Agent yang digunakan:

```text
Planner
Researcher
Writer
Editor
```

Pipeline:

```text
Planning
   ↓
Research
   ↓
Writing
   ↓
Editing
   ↓
Book
```

Repository menggunakan Python dan Ollama, dan README-nya mencantumkan model seperti Mistral, Llama 3, dan DeepSeek.

Project ini berguna sebagai contoh paling sederhana untuk memahami **shared state antar-agent**.

---

## 3.3 Multi-Agent Book Generator

GitHub:

https://github.com/Syed-Arieb/multi-agent-book-generator

Project ini menggunakan Python dan menghasilkan buku chapter-by-chapter.

Agent:

```text
Planner
Critic
Writer
Reviewer
```

Output yang dibuat antara lain:

```text
PLAN.md
Chapter_01.md
Chapter_02.md
...
Book.md
```

Project ini sangat berguna sebagai referensi untuk desain **chapter-level checkpointing** dan review loop.

---

## 3.4 The Writer

GitHub:

https://github.com/ScriptKiddie913/The-Writer

Project ini adalah aplikasi multi-agent lokal berbasis Ollama.

README-nya menyebut fitur:

- multi-agent autonomous writing;
- project-scoped RAG;
- persistent memory;
- knowledge-base ingestion;
- PDF/DOCX/PPTX/XLSX/Markdown;
- chapter-by-chapter drafting;
- continuity checking;
- resume workflow.

Tech stack-nya menggunakan Python/FastAPI, Ollama, SQLite, PyMuPDF, pypdf, dan ReportLab.

**Catatan:** project ini menggunakan ReportLab untuk output PDF. Untuk buku ajar berbasis LaTeX, bagian output perlu diganti atau ditambah dengan LaTeX pipeline.

---

## 3.5 Geeky Ghost Writer

GitHub:

https://github.com/GeekyGhost/Geeky-Ghost-Writer

Project ini adalah aplikasi Python + Ollama + Gradio yang dapat:

- membuat outline;
- menghasilkan buku chapter-by-chapter;
- memilih model Ollama;
- menyimpan chapter secara terpisah;
- menggabungkan chapter menjadi buku.

Project ini lebih sederhana dan cocok untuk mempelajari dasar implementasi book-generation berbasis Ollama.

---

## 3.6 ResearchPaper_Agent

GitHub:

https://github.com/virubot/ResearchPaper_Agent

Project ini bukan book writer, tetapi sangat relevan untuk komponen akademik.

Repository menggunakan:

- Python;
- Ollama;
- FAISS;
- Sentence Transformers;
- Streamlit;
- LaTeX.

Project ini dapat dijadikan referensi untuk:

```text
PDF / Research
      ↓
RAG
      ↓
LLM
      ↓
Academic content
      ↓
LaTeX
```

---

## 3.7 Novel Forge

GitHub:

https://github.com/Milimo-Quantum/novel-forge

Walaupun fokusnya novel, arsitekturnya sangat relevan.

Project ini menggunakan multi-agent, graph-based orchestration, dan adaptive model selection.

Agent yang tersedia mencakup:

```text
Writer
Reviewer
Editor
Consistency Checker
Stylist
Flow Enhancer
```

README juga menyebut adaptive model selection menggunakan Ollama/OpenRouter.

Ini dapat dijadikan referensi untuk **routing model berbeda berdasarkan jenis pekerjaan**.

---

# 4. Arsitektur Target

Arsitektur yang direkomendasikan:

```text
                    ┌─────────────────────┐
                    │    BOOK DIRECTOR    │
                    └──────────┬──────────┘
                               │
              ┌────────────────▼────────────────┐
              │        BOOK SPECIFICATION       │
              │ RPS / OBE / Style / Structure  │
              └────────────────┬────────────────┘
                               │
                  ┌────────────▼────────────┐
                  │    KNOWLEDGE INGESTOR   │
                  └────────────┬────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
         PDF Parser        LaTeX Parser      RPS Parser
              │                │                │
              └────────────────┼────────────────┘
                               ▼
                    ┌─────────────────────┐
                    │    RAG DATABASE     │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  KNOWLEDGE GRAPH    │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   BOOK PLANNER      │
                    └──────────┬──────────┘
                               │
                     Chapter specification
                               │
              ┌────────────────▼────────────────┐
              │          CHAPTER LOOP           │
              │                                 │
              │ Research → Write → Review      │
              │      ↑          ↓               │
              │      └── Revise ┘               │
              └────────────────┬────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │    LATEX AGENT      │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   LATEX QA AGENT    │
                    └──────────┬──────────┘
                               │
                         Approved Book
```

---

# 5. Struktur Project yang Direkomendasikan

```text
ai-book-author/
│
├── app/
│   ├── main.py
│   └── cli.py
│
├── agents/
│   ├── book_director.py
│   ├── planner.py
│   ├── chapter_planner.py
│   ├── researcher.py
│   ├── writer.py
│   ├── example_writer.py
│   ├── exercise_writer.py
│   ├── fact_checker.py
│   ├── citation_checker.py
│   ├── pedagogy_reviewer.py
│   ├── consistency_checker.py
│   ├── latex_writer.py
│   └── latex_qa.py
│
├── models/
│   ├── ollama_client.py
│   ├── model_router.py
│   └── model_registry.py
│
├── ingestion/
│   ├── pdf_loader.py
│   ├── latex_loader.py
│   ├── markdown_loader.py
│   ├── rps_loader.py
│   └── document_normalizer.py
│
├── rag/
│   ├── chunker.py
│   ├── embeddings.py
│   ├── retriever.py
│   └── vector_store.py
│
├── graph/
│   ├── entities.py
│   ├── relations.py
│   └── knowledge_graph.py
│
├── latex/
│   ├── templates/
│   ├── compiler.py
│   ├── validator.py
│   └── crossref_checker.py
│
├── memory/
│   ├── project_state.py
│   ├── chapter_state.py
│   └── checkpoints.py
│
├── prompts/
│   ├── planner.md
│   ├── researcher.md
│   ├── writer.md
│   ├── reviewer.md
│   └── latex.md
│
├── input/
│   ├── references/
│   ├── rps/
│   └── source_latex/
│
├── knowledge/
│   ├── vector_db/
│   ├── graph/
│   └── metadata/
│
├── output/
│   ├── chapters/
│   ├── latex/
│   ├── figures/
│   └── book.pdf
│
├── tests/
│
├── config.yaml
├── requirements.txt
└── README.md
```

---

# 6. Multi-LLM: Jangan Menggunakan Satu Model untuk Semua Agent

Salah satu tujuan utama sistem adalah memungkinkan beberapa model Ollama bekerja bersama.

Contoh konfigurasi:

```yaml
models:

  planner:
    provider: ollama
    model: MODEL_REASONING

  researcher:
    provider: ollama
    model: MODEL_REASONING

  writer:
    provider: ollama
    model: MODEL_WRITING

  reviewer:
    provider: ollama
    model: MODEL_REASONING

  fact_checker:
    provider: ollama
    model: MODEL_REASONING

  latex:
    provider: ollama
    model: MODEL_CODING

  vision:
    provider: ollama
    model: MODEL_VISION

  embedding:
    provider: ollama
    model: MODEL_EMBEDDING
```

Nama model jangan di-hard-code ke agent. Agent harus meminta:

```python
model_router.get_model("writer")
```

bukan:

```python
model = "some-model"
```

Dengan demikian model dapat diganti melalui konfigurasi.

---

# 7. Model Router

Buat satu komponen:

```text
ModelRouter
```

Contoh:

```python
class ModelRouter:

    def __init__(self, registry):
        self.registry = registry

    def get_model(self, task):
        return self.registry[task]
```

Kemudian:

```python
planner_model = router.get_model("planner")
writer_model = router.get_model("writer")
reviewer_model = router.get_model("reviewer")
latex_model = router.get_model("latex")
```

Keuntungan:

1. setiap tugas dapat memakai model berbeda;
2. model dapat diganti tanpa mengubah agent;
3. eksperimen A/B lebih mudah;
4. model kecil dapat digunakan untuk pekerjaan sederhana;
5. model reasoning yang lebih kuat dapat digunakan untuk review;
6. model vision dapat digunakan untuk diagram/gambar;
7. embedding model dapat dipisahkan dari chat model.

---

# 8. Peran Model

Gunakan prinsip:

```text
                 BOOK AGENT
                     │
       ┌─────────────┼─────────────┐
       │             │             │
       ▼             ▼             ▼
   Reasoning       Writing       Coding
     Model          Model         Model
       │             │             │
       ▼             ▼             ▼
 Planner/QA      Chapter        LaTeX
 Research        Content        Generator
```

Contoh pembagian:

```text
Model A → planning/reasoning
Model B → chapter writing
Model C → code/example generation
Model D → LaTeX
Model E → vision
Model F → embeddings
```

Model aktual ditentukan setelah pengujian kualitas dan kecepatan pada mesin/cloud Ollama yang digunakan.

---

# 9. Input Knowledge Base

Folder input sebaiknya:

```text
input/
├── references/
│   ├── book-compiler.pdf
│   ├── compiler-paper.pdf
│   └── programming-book.pdf
│
├── rps/
│   └── rps-teknik-kompilasi.tex
│
└── source_latex/
    ├── old-book.tex
    ├── chapter01.tex
    └── chapter02.tex
```

Semua file harus diproses menjadi struktur internal yang seragam.

Contoh:

```python
Document(
    id="doc001",
    source="book-compiler.pdf",
    page=42,
    section="Finite Automata",
    text="..."
)
```

Metadata harus dipertahankan.

Minimal:

```text
document_id
filename
page
section
paragraph
source_type
```

Metadata ini penting untuk citation dan fact checking.

---

# 10. PDF Ingestion

Pipeline:

```text
PDF
 ↓
PyMuPDF / pypdf
 ↓
Text extraction
 ↓
Page metadata
 ↓
Chunking
 ↓
Embedding
 ↓
Vector database
```

Untuk PDF hasil scan:

```text
PDF
 ↓
Page image
 ↓
Vision/OCR model
 ↓
Structured text
 ↓
Chunk
 ↓
Embedding
```

Jangan membuang nomor halaman karena citation checker memerlukannya.

---

# 11. LaTeX Ingestion

LaTeX source jangan diperlakukan hanya sebagai plain text.

Parser harus mengenali:

```text
\chapter
\section
\subsection
\begin{definition}
\begin{example}
\begin{theorem}
\begin{figure}
\begin{table}
\begin{lstlisting}
\cite
\label
\ref
```

Informasi tersebut dapat menjadi metadata Knowledge Graph.

Contoh:

```text
Concept
  │
  ├── Definition
  ├── Example
  ├── Theorem
  ├── Figure
  ├── Exercise
  └── Reference
```

---

# 12. RPS sebagai Knowledge Source

RPS harus menjadi sumber aturan buku.

Contoh:

```text
RPS
 │
 ├── CPMK
 ├── Sub-CPMK
 ├── Week 1
 ├── Week 2
 ├── ...
 ├── Week 16
 ├── Topics
 ├── Assessment
 └── Learning outcomes
```

Book Planner kemudian menghasilkan:

```text
Chapter 1 → Week 1-2
Chapter 2 → Week 3
Chapter 3 → Week 4-5
...
```

Dengan demikian buku tidak hanya "ditulis berdasarkan topik", tetapi juga konsisten dengan struktur pembelajaran.

---

# 13. RAG

RAG harus menjawab pertanyaan:

```text
Apa yang harus ditulis?
        ↓
Sumber apa yang mendukungnya?
        ↓
Di halaman mana?
        ↓
Apakah sumber tersebut relevan?
```

Retrieval result sebaiknya memiliki:

```python
{
    "text": "...",
    "source": "compiler.pdf",
    "page": 73,
    "section": "Lexical Analysis",
    "score": 0.87
}
```

Jangan memberikan kepada writer hanya:

```text
text
```

karena informasi sumber akan hilang.

---

# 14. Knowledge Graph

Vector RAG berguna untuk semantic retrieval.

Knowledge Graph berguna untuk hubungan antar-konsep.

Contoh:

```text
Lexer
 ├── uses → Regular Expression
 ├── produces → Token
 └── implemented_by → DFA

Parser
 ├── consumes → Token
 ├── produces → Parse Tree
 └── uses → CFG
```

Knowledge Graph dapat membantu:

- chapter dependency;
- prerequisite;
- cross-reference;
- terminology consistency;
- duplicate explanation detection.

Knowledge Graph bersifat **opsional pada MVP**, tetapi sangat berguna pada versi lanjutan.

---

# 15. Book Planner Agent

Input:

```text
RPS
Reference metadata
Book requirements
Target audience
Number of chapters
```

Output:

```json
{
  "title": "Teknik Kompilasi",
  "chapters": [
    {
      "number": 1,
      "title": "Pengantar Kompilator",
      "learning_outcomes": [],
      "concepts": [],
      "references": []
    }
  ]
}
```

Planner tidak boleh langsung menghasilkan isi buku.

Planner hanya menghasilkan **Book Specification**.

---

# 16. Chapter Planner

Untuk setiap chapter:

```text
Book Specification
      ↓
Chapter Planner
      ↓
Chapter Specification
```

Contoh:

```json
{
  "chapter": 3,
  "title": "Analisis Leksikal",
  "objectives": [
    "menjelaskan token",
    "menjelaskan lexical analyzer",
    "mengimplementasikan lexer sederhana"
  ],
  "sections": [
    "Konsep Token",
    "Regular Expression",
    "Finite Automata",
    "Lexical Analyzer",
    "Implementasi"
  ],
  "required_examples": 3,
  "required_exercises": 5,
  "references": []
}
```

---

# 17. Research Agent

Research Agent menerima Chapter Specification.

Ia melakukan:

```text
Chapter Specification
        ↓
RAG retrieval
        ↓
Source ranking
        ↓
Evidence collection
        ↓
Research Package
```

Research Package:

```text
Concepts
Definitions
Examples
Evidence
References
Page numbers
Potential figures
Potential exercises
```

Writer tidak melakukan pencarian sendiri jika tidak diperlukan.

---

# 18. Chapter Writer

Writer menerima:

```text
Chapter Specification
+
Research Package
+
Book Style Guide
+
Previous Chapter Summary
```

Output:

```text
Chapter Draft
```

Writer harus menghindari:

- citation yang tidak ada;
- referensi yang tidak ditemukan;
- fakta yang tidak didukung;
- konsep yang belum diperkenalkan;
- perubahan istilah tanpa alasan.

---

# 19. Example Agent

Untuk buku Informatika, contoh kode sebaiknya dipisahkan dari writer.

```text
Chapter Writer
      │
      ▼
Example Agent
      │
      ▼
Code Example
      │
      ▼
Code Reviewer
```

Code Reviewer memeriksa:

- syntax;
- logic;
- expected output;
- kesesuaian dengan bahasa pemrograman;
- konsistensi dengan teori.

---

# 20. Exercise Agent

Setiap bab dapat menghasilkan:

```text
Latihan Pemahaman
Latihan Konseptual
Latihan Coding
Studi Kasus
Soal Evaluasi
```

Exercise Agent harus menerima:

```text
learning objectives
chapter concepts
difficulty
```

sehingga latihan tidak dibuat secara acak.

---

# 21. Fact Checker

Fact Checker harus menjawab:

```text
Claim
 ↓
Retrieved Evidence
 ↓
Supported?
```

Output:

```json
{
  "claim": "...",
  "supported": true,
  "source": "book.pdf",
  "page": 125
}
```

Jika:

```text
supported = false
```

chapter kembali ke Writer untuk revisi.

---

# 22. Citation Checker

Citation Checker memastikan:

```text
\cite{aho2006}
```

memiliki:

```bibtex
@book{aho2006,
   ...
}
```

dan reference tersebut benar-benar berasal dari knowledge base.

Jangan mengizinkan model membuat citation identifier fiktif.

---

# 23. Pedagogy Reviewer

Reviewer ini khusus untuk buku ajar.

Periksa:

```text
Learning Objective
       ↓
Explanation
       ↓
Example
       ↓
Practice
       ↓
Exercise
```

Reviewer harus mendeteksi:

- penjelasan terlalu cepat;
- konsep prerequisite belum dijelaskan;
- contoh tidak terkait materi;
- latihan tidak sesuai tujuan pembelajaran;
- tingkat kesulitan terlalu tinggi;
- pengulangan yang tidak diperlukan.

---

# 24. Consistency Checker

Checker membandingkan chapter baru dengan seluruh book state.

Periksa:

```text
Terminology
Notation
Acronyms
Definitions
Variables
Chapter references
Figure numbering
Table numbering
Equation numbering
```

Contoh masalah:

```text
Bab 2: "finite automaton"
Bab 6: "finite automata"
Bab 7: "finite-state machine"
```

Agent harus menentukan apakah ketiga istilah memang sinonim atau harus distandardisasi.

---

# 25. LaTeX Agent

LaTeX Agent mengubah approved chapter menjadi:

```text
chapter03.tex
```

Writer sebaiknya tidak sekaligus menjadi LaTeX generator.

Pisahkan:

```text
Content
  ↓
LaTeX Agent
  ↓
LaTeX source
```

Contoh struktur:

```text
book/
├── main.tex
├── preamble.tex
├── references.bib
└── chapters/
    ├── chapter01.tex
    ├── chapter02.tex
    └── chapter03.tex
```

---

# 26. LaTeX QA

Setelah chapter dibuat:

```text
pdflatex
   ↓
Compiler
   ↓
Errors?
   ├── yes → LaTeX Agent → retry
   └── no
        ↓
Warnings
        ↓
Cross-reference check
        ↓
Approved
```

Periksa:

- compilation error;
- undefined reference;
- undefined citation;
- missing figure;
- missing label;
- broken equation;
- overfull box;
- duplicate label.

---

# 27. Chapter State Machine

Setiap chapter sebaiknya mempunyai status:

```text
PLANNED
   ↓
RESEARCHED
   ↓
DRAFTED
   ↓
FACT_CHECKED
   ↓
CITATION_CHECKED
   ↓
PEDAGOGY_REVIEWED
   ↓
CONSISTENCY_CHECKED
   ↓
LATEX_GENERATED
   ↓
LATEX_COMPILED
   ↓
APPROVED
```

Jika gagal:

```text
FAILED_REVIEW
      ↓
REVISION
      ↓
REVIEW
```

Ini jauh lebih aman daripada membuat buku sekaligus.

---

# 28. Checkpoint

Simpan state setelah setiap tahap.

Contoh:

```text
state/
├── book.json
├── chapter01.json
├── chapter02.json
└── chapter03.json
```

Contoh:

```json
{
  "chapter": 3,
  "status": "FACT_CHECKED",
  "revision": 2,
  "research_completed": true,
  "citation_completed": true
}
```

Jika aplikasi berhenti, proses dapat dilanjutkan dari state terakhir.

---

# 29. Shared Memory

Jangan menggunakan hanya context string besar.

Gunakan struktur:

```python
BookState
```

yang berisi:

```text
book metadata
RPS
chapter specifications
chapter summaries
terminology
definitions
citations
figures
cross references
approved chapters
review results
```

Contoh:

```python
class BookState:
    title: str
    rps: dict
    chapters: list
    terminology: dict
    citations: dict
    summaries: dict
    status: dict
```

---

# 30. Urutan Implementasi

## Tahap 1 — MVP

Implementasikan terlebih dahulu:

```text
Python
+
Ollama
+
2 model
+
Planner
+
Writer
+
Reviewer
+
Chapter output
```

Target:

```text
RPS
 ↓
Planner
 ↓
Chapter 1
 ↓
Writer
 ↓
Reviewer
 ↓
Chapter 1.md
```

Jangan mulai dari Knowledge Graph.

---

## Tahap 2 — Multi-LLM

Tambahkan:

```text
ModelRouter
ModelRegistry
```

Minimal:

```text
Planner → Model A
Writer → Model B
Reviewer → Model A
```

Kemudian tambahkan model khusus coding:

```text
Code Agent → Model C
```

---

## Tahap 3 — RAG

Tambahkan:

```text
PDF
 ↓
Parser
 ↓
Chunker
 ↓
Embedding
 ↓
Vector DB
 ↓
Retriever
```

Research Agent menggunakan hasil retrieval.

---

## Tahap 4 — RPS

Tambahkan parser untuk:

```text
RPS
CPMK
Sub-CPMK
Learning Outcomes
Week
Topics
Assessment
```

Planner harus menggunakan informasi tersebut.

---

## Tahap 5 — LaTeX

Tambahkan:

```text
chapter.md
     ↓
LaTeX Agent
     ↓
chapter.tex
     ↓
pdflatex
     ↓
PDF
```

---

## Tahap 6 — Quality Control

Tambahkan:

```text
Fact Checker
Citation Checker
Pedagogy Reviewer
Consistency Checker
LaTeX QA
```

---

## Tahap 7 — Knowledge Graph

Setelah RAG stabil:

```text
Concept Graph
Dependency Graph
Citation Graph
Chapter Graph
```

---

# 31. Contoh Orchestrator

Pseudo-code:

```python
def generate_book(book_request):

    book = planner.create_book_spec(book_request)

    for chapter in book.chapters:

        if checkpoint.is_completed(chapter.number):
            continue

        specification = chapter_planner.plan(chapter)

        research = researcher.collect(
            specification
        )

        draft = writer.write(
            specification,
            research
        )

        fact_result = fact_checker.check(
            draft,
            research
        )

        if not fact_result.approved:
            draft = writer.revise(
                draft,
                fact_result.feedback
            )

        citation_result = citation_checker.check(
            draft,
            research
        )

        draft = pedagogy_reviewer.review(
            draft,
            specification
        )

        draft = consistency_checker.review(
            draft,
            book
        )

        latex = latex_writer.generate(draft)

        qa = latex_qa.compile_and_check(latex)

        if qa.approved:
            checkpoint.save(chapter, latex)
```

---

# 32. Contoh Model Configuration

```yaml
ollama:
  base_url: http://localhost:11434

models:

  planner:
    name: "MODEL_A"

  researcher:
    name: "MODEL_A"

  writer:
    name: "MODEL_B"

  reviewer:
    name: "MODEL_A"

  code:
    name: "MODEL_C"

  latex:
    name: "MODEL_C"

  vision:
    name: "MODEL_D"

  embedding:
    name: "MODEL_E"
```

Model aktual sebaiknya dipilih melalui benchmark lokal/cloud Ollama, bukan diasumsikan dari nama model.

---

# 33. Kriteria Pemilihan Model

Benchmark setiap model dengan tugas yang sama.

## Planner

Uji:

```text
Apakah model dapat membuat outline yang konsisten dengan RPS?
```

## Writer

Uji:

```text
Apakah model dapat menjelaskan konsep secara pedagogis?
```

## Reviewer

Uji:

```text
Apakah model menemukan kesalahan dalam draft?
```

## Coding

Uji:

```text
Apakah contoh program dapat dijalankan?
```

## LaTeX

Uji:

```text
Apakah output dapat dikompilasi?
```

Jangan menentukan model hanya berdasarkan benchmark umum.

---

# 34. Prinsip Penting: Source Grounding

Writer tidak boleh bebas membuat fakta akademik.

Gunakan aturan:

```text
Jika claim berasal dari source:
    simpan source + page

Jika claim tidak memiliki evidence:
    tandai NEEDS_VERIFICATION
```

Jangan melakukan:

```text
LLM → citation
```

tetapi:

```text
RAG → evidence
       ↓
LLM → explanation
       ↓
Citation Checker
```

---

# 35. Prinsip Penting: Jangan Menghasilkan Buku Sekaligus

Jangan:

```python
book = llm.generate("Write a 12 chapter book...")
```

Gunakan:

```python
for chapter in chapters:
    research()
    write()
    review()
    verify()
    compile()
    save_checkpoint()
```

Keuntungannya:

- context lebih kecil;
- mudah melakukan revisi;
- mudah melakukan checkpoint;
- kualitas dapat diperiksa per bab;
- model berbeda dapat digunakan untuk tugas berbeda;
- kegagalan satu bab tidak menghilangkan seluruh buku.

---

# 36. Prompt Contract

Setiap agent harus memiliki kontrak output.

Contoh Writer:

```text
INPUT:
- Chapter specification
- Research package
- Style guide
- Previous chapter summary

OUTPUT:
- title
- learning_objectives
- sections
- examples
- exercises
- citations
- unresolved_claims

RULES:
- Jangan membuat citation yang tidak ada.
- Jangan mengubah definisi sumber tanpa alasan.
- Tandai claim yang tidak mempunyai evidence.
- Ikuti terminology dictionary.
```

Output sebaiknya JSON/structured object sebelum dirender ke Markdown/LaTeX.

---

# 37. Testing

Testing harus mencakup:

## Unit Test

```text
PDF parser
LaTeX parser
RPS parser
Model router
Citation checker
```

## Agent Test

```text
Planner
Writer
Reviewer
Fact Checker
```

## Integration Test

```text
RPS
 ↓
Planner
 ↓
Research
 ↓
Writer
 ↓
Reviewer
 ↓
LaTeX
```

## End-to-End Test

Gunakan satu bab kecil sebagai benchmark.

---

# 38. Logging

Simpan:

```text
timestamp
agent
model
prompt version
input IDs
retrieved sources
output
review result
latency
token usage jika tersedia
```

Contoh:

```text
logs/
├── planner.jsonl
├── writer.jsonl
├── reviewer.jsonl
└── latex.jsonl
```

Hal ini penting untuk membandingkan model.

---

# 39. Git

Source buku harus berada di Git:

```text
git/
├── main.tex
├── chapters/
├── references.bib
├── prompts/
├── config.yaml
└── agents/
```

Setiap chapter yang disetujui dapat menjadi commit:

```text
chapter 01 approved
chapter 02 approved
chapter 03 approved
```

Dengan demikian revisi AI dapat dilacak.

---

# 40. Roadmap

## MVP 1

```text
Python
Ollama
2 LLM
Planner
Writer
Reviewer
Markdown
```

## MVP 2

```text
PDF ingestion
RAG
Citation metadata
```

## MVP 3

```text
RPS
OBE
Chapter mapping
```

## MVP 4

```text
LaTeX
BibTeX
pdflatex
QA
```

## MVP 5

```text
Fact Checker
Pedagogy Reviewer
Consistency Checker
```

## MVP 6

```text
Knowledge Graph
Graph-based chapter dependency
```

## MVP 7

```text
Web UI
Dashboard
Resume
Model comparison
Human approval
```

---

# 41. Rekomendasi Urutan Pengembangan

Jangan langsung mengimplementasikan semua agent.

Urutan yang disarankan:

```text
1. OllamaClient
       ↓
2. ModelRouter
       ↓
3. BookState
       ↓
4. Planner
       ↓
5. ChapterPlanner
       ↓
6. Researcher
       ↓
7. Writer
       ↓
8. Reviewer
       ↓
9. Checkpoint
       ↓
10. PDF RAG
       ↓
11. RPS integration
       ↓
12. Citation Checker
       ↓
13. Fact Checker
       ↓
14. LaTeX Agent
       ↓
15. LaTeX QA
       ↓
16. Consistency Graph
       ↓
17. Knowledge Graph
```

---

# 42. Target Akhir

Sistem yang dihasilkan harus dapat dijalankan seperti:

```bash
python -m app.main \
    --rps input/rps/rps.tex \
    --references input/references \
    --latex-template input/source_latex \
    --output output/book
```

Kemudian:

```text
AI Book Author
      │
      ├── membaca RPS
      ├── membaca referensi
      ├── membangun knowledge base
      ├── membuat book specification
      ├── membuat chapter specification
      │
      ├── Chapter 1
      │    ├── research
      │    ├── write
      │    ├── fact check
      │    ├── citation check
      │    ├── pedagogy review
      │    └── LaTeX QA
      │
      ├── Chapter 2
      │    └── ...
      │
      └── Chapter N
           └── ...
```

Output:

```text
output/book/
├── main.tex
├── preamble.tex
├── references.bib
├── chapters/
│   ├── chapter01.tex
│   ├── chapter02.tex
│   └── chapter03.tex
├── figures/
├── logs/
├── checkpoints/
└── book.pdf
```

---

# 43. Kesimpulan Teknis

Fondasi terbaik bukan membuat satu "AI writer", tetapi membuat **orchestrated multi-agent system**.

Target arsitektur:

```text
Python
 │
 ├── Ollama
 │    ├── Reasoning LLM
 │    ├── Writing LLM
 │    ├── Coding/LaTeX LLM
 │    ├── Vision LLM
 │    └── Embedding Model
 │
 ├── RAG
 │
 ├── Knowledge Graph
 │
 ├── Book State
 │
 ├── Agent Orchestrator
 │
 └── LaTeX Pipeline
```

Repository yang paling layak dipelajari sebagai titik awal:

1. **AI Book Composer**  
   https://github.com/rdar-lab/ai-book-composer

2. **Multi-Agent Book Writer**  
   https://github.com/EimanTahir027/Multi-Agent-Book-Writer

3. **Multi-Agent Book Generator**  
   https://github.com/Syed-Arieb/multi-agent-book-generator

4. **The Writer**  
   https://github.com/ScriptKiddie913/The-Writer

5. **Geeky Ghost Writer**  
   https://github.com/GeekyGhost/Geeky-Ghost-Writer

6. **ResearchPaper_Agent**  
   https://github.com/virubot/ResearchPaper_Agent

7. **Novel Forge**  
   https://github.com/Milimo-Quantum/novel-forge

Dari ketujuh proyek tersebut, **AI Book Composer** paling dekat dengan sisi *book-from-source-files + deep-agent workflow*, **Multi-Agent Book Writer/Book Generator** paling sederhana untuk mempelajari orchestration chapter-by-chapter, **The Writer** paling dekat dengan aplikasi lokal + Ollama + RAG, dan **ResearchPaper_Agent** paling relevan untuk komponen akademik + RAG + LaTeX. 

---

# 44. Prinsip Implementasi Akhir

Sistem ini sebaiknya diperlakukan sebagai **AI co-author**, bukan generator buku otonom penuh.

Human tetap menjadi:

```text
Author / Subject Matter Expert
          │
          ▼
Book Director
          │
          ▼
AI Agents
          │
          ▼
Draft
          │
          ▼
Human Approval
          │
          ▼
Published Chapter
```

AI bertugas mempercepat:

```text
research
organization
drafting
examples
exercises
review
citation checking
LaTeX generation
QA
```

sedangkan keputusan akademik akhir tetap berada pada penulis/dosen.

---

## Referensi Repository

- AI Book Composer: https://github.com/rdar-lab/ai-book-composer
- Multi-Agent Book Writer: https://github.com/EimanTahir027/Multi-Agent-Book-Writer
- Multi-Agent Book Generator: https://github.com/Syed-Arieb/multi-agent-book-generator
- The Writer: https://github.com/ScriptKiddie913/The-Writer
- Geeky Ghost Writer: https://github.com/GeekyGhost/Geeky-Ghost-Writer
- ResearchPaper_Agent: https://github.com/virubot/ResearchPaper_Agent
- Novel Forge: https://github.com/Milimo-Quantum/novel-forge
