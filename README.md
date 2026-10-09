# AI Agent Buku Ajar

Agen CLI yang menyusun **buku ajar** dari RPS (Rencana Pembelajaran Semester)
secara bab-per-bab, memakai beberapa model LLM melalui [Ollama](https://ollama.com)
— satu model per peran, bukan satu model untuk segalanya.

Dokumen acuan arsitekturnya adalah `AI_Agen_Buku_Ajar_Python_Ollama_MultiLLM.md`
(disebut **blueprint** di seluruh kode dan dokumen ini; rujukan seperti §27, §31,
§44 menunjuk pasal di sana). Kode ini **mengikuti penomoran pasal blueprint**
dengan sengaja, sehingga setiap keputusan desain dapat ditelusuri kembali ke
sumbernya.

Prinsip yang menentukan seluruh bentuknya, dan yang paling mudah dilupakan:
**AI co-author, bukan generator buku otonom penuh** (§44). Bab yang sudah jadi
tetap menunggu persetujuan manusia sebelum dinyatakan final, dan perintah untuk
menyetujui maupun mengembalikannya tersedia sepanjang waktu — dengan atau tanpa
gate persetujuan dinyalakan.

---

## Status: rantai §27 lengkap, ujung ke ujung

**Yang sudah ada.** RPS masuk → `output/book.md` dan `output/latex/book.pdf`
keluar, lewat rantai yang utuh:

```
RPS → rencana buku → perincian bab → penulis draf
    → contoh (§19)      → latihan (§20)
    → fakta (§21)       → sitasi (§22)
    → pedagogi (§23)    → konsistensi (§24)
    → peninjau akhir (§27)
    → LaTeX (§25)       → QA & kompilasi (§26)
    → menunggu keputusan manusia (§44)
```

Sembilan gate terakhir di jalur itu dijalankan berurutan pada rantai
`example_writer, exercise_writer, fact_checker, citation_checker,
pedagogy_reviewer, consistency_checker, reviewer, latex_writer, latex_qa`.
Rantai state-nya sendiri punya 13 status §27, dan setiap sisipan baru dijaga
supaya **tidak mengubah arti satu pun pasangan status lama** — sehingga
`state/chapterNN.json` yang sudah ada tetap sah tanpa migrasi.

**Yang belum ada:** Web UI / dasbor (ditunda atas keputusan pengguna; lihat
*Yang sengaja ditunda*), dan `knowledge/metadata/` (§5) yang belum dibutuhkan.
Flag `--latex-template` diterima dan dicatat di `BookRequest`, tetapi belum
dipakai untuk merakit dokumen.

---

## Kebutuhan

| Kebutuhan | Versi | Catatan |
|---|---|---|
| Python | **3.11** (3.11.9 terverifikasi) | 3.12 belum diuji; sintaks generic PEP 695 sengaja tidak dipakai |
| Ollama | 0.35+ | `localhost:11434`; akun cloud diperlukan hanya untuk profil `default` |
| LaTeX | MiKTeX 24.1+ | `pdflatex` di `PATH`. `latexmk` lebih pintar, tetapi ia skrip Perl — dan `perl` tidak ada di PowerShell bawaan Windows. Karena itu bawaannya `pdflatex` |
| Model embedding | `nomic-embed-text` | Untuk RAG (§13). Tanpa ini, `ingest` gagal dengan pesan yang menyebut `ollama pull` |
| PDF referensi | opsional | `input/references/`. PDF hasil scan dialihkan ke jalur OCR (§10) memakai model `vision` |

Pustaka Python baru yang masuk bersama MVP 2–7: `chromadb` (vector store
tertanam), `pypdf` (teks per halaman), `PyMuPDF` (halaman → gambar untuk OCR,
dan ekstraksi gambar). Semuanya di-pin di `requirements.txt`.

---

## Pemasangan

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows PowerShell
pip install -r requirements-dev.txt  # sekaligus requirements.txt
```

Untuk memakai alias `ai-book` (opsional):

```powershell
pip install -e .
```

Tanpa itu, **bentuk kanoniknya adalah `python -m app`** — dan itulah yang dipakai
di seluruh dokumen ini.

---

## Mulai cepat

```powershell
# 1. Pastikan konfigurasi, server, dan setiap peran siap.
python -m app doctor

# 2. Bangun basis pengetahuan dari input/references/ (§10, §13, §14).
python -m app ingest

# 3. Susun rencana buku dari RPS.
python -m app plan --rps input/rps/rps.tex --chapters 8

# 4. Kerjakan seluruh bab (mengulang secara otomatis bila terputus).
python -m app run

# 5. Lihat statusnya, lalu gabungkan menjadi satu berkas.
python -m app status
python -m app export

# 6. Rakit dan kompilasi buku LaTeX-nya (§42).
python -m app export --latex
```

`run` juga dapat dijalankan **tanpa `plan` lebih dulu** — ia akan menyusun
rencananya sendiri. Menjalankannya tanpa keduanya akan menolak dengan pesan yang
menyebut urutan yang benar, bukan traceback.

`ingest` adalah perintah tersendiri, bukan efek samping `run`, dan itu disengaja:
menyalakan RAG pada indeks yang belum dibangun harus **terlihat**. Tanpa bahan
rujukan, `rag.enabled: false` mengembalikan `NullResearcher` — paket riset ditandai
`degraded`, penulis dilarang mengutip, dan setiap klaim faktual wajib muncul di
`unresolved_claims`. Itu keadaan yang jujur, bukan keadaan yang rusak.

### Melihat prompt tanpa membakar token

```powershell
python -m app run --dry-run --rps input/rps/rps.tex
```

`--dry-run` menjalankan **pipeline yang sungguhan** — planner, perincian bab,
penulis, seluruh gate, checkpoint, penulisan Markdown — dengan satu perubahan:
adapter chat-nya tidak mengirim HTTP, melainkan menuliskan setiap prompt ke
`state/dryrun/NN-peran.txt` (lengkap dengan nama model dan angka sampling yang
benar-benar akan dipakai peran itu) lalu mengembalikan instance yang disintesis
dari skema. Jadi yang dibuktikan adalah **jalur produksinya**, bukan jalur uji
coba yang terpisah.

Seluruh hasilnya dikurung di `state/dryrun/`; `state/` dan `output/` yang
sungguhan tidak tersentuh sama sekali. Indeks vektor juga tidak dibangun pada
mode ini, karena `--dry-run` menjanjikan **nol jaringan**.

---

## Perintah

| Perintah | Gunanya |
|---|---|
| `doctor` | Periksa konfigurasi, server Ollama, dan keterjangkauan setiap peran. `--no-probe` melewati uji kirim-1-token. |
| `ingest` | Bangun ulang `knowledge/`: indeks vektor (§13) dan graf konsep (§14). |
| `plan` | Susun `BookSpec` dari RPS, simpan ke `state/book.json`. |
| `run` | Kerjakan seluruh bab yang belum selesai. **Resume adalah bawaan.** |
| `write-chapter N` | Kerjakan satu bab sampai tuntas, atau sampai ia menyerah. |
| `status` | Tabel tiap bab: status, revisi, vonis gate, dan model per peran. |
| `export` | Gabungkan bab yang sudah disetujui menjadi `output/book.md`. |
| `export --latex` | Rakit `main.tex` + `references.bib`, kompilasi, hasilkan `book.pdf` (§42). |
| `compare` | Kerjakan satu bab dengan beberapa model, lalu bandingkan hasilnya (§33). |
| `approve N` | Setujui sebuah bab atas keputusan manusia (§44). |
| `reject N --reason "…"` | Kembalikan sebuah bab ke penulis, dengan alasannya (§44). |

Opsi yang berlaku umum:

| Opsi | Artinya |
|---|---|
| `--rps/-r` | Berkas RPS yang menjadi dasar buku. |
| `--references` | Direktori bahan rujukan. **Dibaca `ingest`**, bukan `plan`/`run` — dan perbedaannya disebutkan saat dijalankan, bukan didiamkan. |
| `--output/-o` | Direktori keluaran (bawaan `output/`). |
| `--profile/-p` | Profil model: `default` atau `local`. |
| `--set-model PERAN=MODEL` | Ganti model satu peran; boleh diulang. |
| `--gates NAMA,NAMA` | Persempit rantai gate untuk jalankan ini saja (§27). |
| `--chapter/-c`, `--from`, `--to` | Batasi bab yang dikerjakan. |
| `--force` | Kerjakan ulang bab yang sudah disetujui. |
| `--max-revisions` | Batas revisi per bab (bawaan dari `config.yaml`). |
| `--dry-run` | Tulis prompt ke `state/dryrun/`, tanpa memanggil model. |
| `--config/-C`, `--verbose/-v`, `--log-json` | Konfigurasi, kerincian, log JSONL (§38). |
| `--latex-template` | **Diterima dan dicatat di `BookRequest`, belum dipakai** untuk merakit dokumen. Antarmuka §42 tetap stabil, dan celahnya terlihat — bukan sunyi. |

**Override model tanpa satu flag per peran** (flag per peran akan melanggar Open/Closed —
setiap peran baru menuntut flag baru):

```powershell
python -m app run --set-model writer=gemma3:4b --set-model reviewer=gemma3:4b
```

**Memilih model secara terukur, bukan dari namanya** (§33). `compare` menjalankan
bab yang sama beberapa kali — masing-masing dengan `state/` dan `output/` sendiri
— lalu menyandingkan skor peninjau, jumlah revisi, banyaknya panggilan, token, dan
detiknya. Angka panggilan dan token dibaca dari catatan per peran (§38), bukan
ditebak:

```powershell
python -m app compare --role writer --models gemma3:4b,gemma4:31b-cloud
python -m app compare --dry-run --models a,b    # susunannya saja, tanpa satu token
```

**Pemetaan §42 dipertahankan:** tanpa subcommand, baris invokasi blueprint jatuh
ke `run`. Jadi keduanya setara, dan `tests/integration/test_cli_offline.py`
membuktikan keduanya menghasilkan artefak yang **identik**:

```powershell
python -m app.main --rps input/rps/rps.tex --output output/book
python -m app.main run --rps input/rps/rps.tex --output output/book
```

### Keputusan manusia sebelum bab dinyatakan final (§44)

Gate `human_approval` **mati secara bawaan** — ia sengaja tidak ada di
`pipeline.gates` bawaan. Menyalakannya adalah satu baris:

```yaml
pipeline:
  gates: [..., latex_qa, human_approval]
```

Ketika aktif, bab yang sudah selesai dikerjakan dan terkompilasi **berhenti**
alih-alih dinyatakan `APPROVED`. Berhentinya itu adalah jawaban ketiga — bukan
lulus, bukan tolak:

```
Bab 1 menunggu persetujuan manusia (gate 'human_approval').
Jalankan 'approve 1' atau 'reject 1 --reason "..."'.
```

Dua hal yang membedakannya dari bab yang gagal, dan keduanya disengaja: **anggaran
revisi tidak terpakai** (menunggu bukan kesalahan penulis), dan **kode keluarnya
nol** — skrip yang memeriksa exit code tidak boleh melihat kegagalan pada buku
yang sesungguhnya sudah selesai dikerjakan. Di `status`, kolom gate terakhirnya
berbunyi `menunggu`, bukan `tolak`.

```powershell
python -m app approve 1                          # lanjutkan rantai, tulis deliverable
python -m app reject 1 --reason "Bagian 3 terlalu cepat."
```

`reject` justru paling berguna **saat gate §44 mati**: bab yang sudah `APPROVED`
tidak lagi dilewati gate mana pun, jadi tidak ada jalur otomatis yang dapat
mengembalikannya ke penulis. Yang mahal — riset dan drafnya — tetap utuh, dan
alasannya mengalir ke `ChapterWriter.revise` pada `run` berikutnya. `approve`
pada bab yang sudah disetujui bukan kesalahan: ia memastikan deliverable-nya ada.

### Biaya token: apa yang sudah dikerjakan untuk menekannya

Rantai berisi sembilan gate, dan tiap revisi menjalankannya ulang. Dengan
`max_revisions: 2` dan delapan bab, kasus terburuknya adalah 3 × 9 × 8 = **216
panggilan model**. Tiga penangkalnya, dalam urutan yang dipakai sehari-hari:

1. **`--dry-run`** — nol token, dan menempuh rantai yang sama. Ini jalur
   verifikasi utama.
2. **Pemeriksaan deterministik sebelum memanggil model.** Citation Checker
   menolak kunci sitasi yang tidak ada di basis pengetahuan tanpa bertanya kepada
   LLM; Consistency Checker menjalankan `detect_terminology_drift` lebih dulu.
   Model hanya dipanggil untuk pertanyaan yang benar-benar butuh penilaian.
3. **`--gates`** — mempersempit rantai untuk satu jalankan, mis.
   `--gates reviewer` saat menelusuri satu bagian.

Untuk perbandingan antar-model, `--dry-run` membuktikan susunannya tanpa biaya;
angka sungguhannya baru masuk akal dari jalankan yang benar-benar memanggil model.

---

## Konfigurasi

Seluruhnya di `config.yaml`. Presedennya, dari yang menang:

```
flag CLI  >  env BUKUAJAR_*  >  entri profil  >  default bawaan
```

| Variabel lingkungan | Menimpa |
|---|---|
| `BUKUAJAR_CONFIG` | Jalur berkas konfigurasi |
| `BUKUAJAR_PROFILE` | Profil aktif |
| `BUKUAJAR_STATE_DIR`, `BUKUAJAR_OUTPUT_DIR`, `BUKUAJAR_KNOWLEDGE_DIR` | Direktori runtime |
| `BUKUAJAR_OLLAMA_BASE_URL` | Alamat server Ollama |
| `BUKUAJAR_RAG_ENABLED`, `BUKUAJAR_RAG_TOP_K` | Retrieval (§13) |
| `BUKUAJAR_LATEX_ENABLED` | Pembangkit & pemeriksa LaTeX (§25) |
| `BUKUAJAR_MAX_REVISIONS`, `BUKUAJAR_MIN_WORDS`, `BUKUAJAR_REVIEW_THRESHOLD` | Ambang mutu |

**Agent tidak pernah menyebut nama model.** Ia selalu memanggil
`router.chat("writer")`, dan peran mana memakai model mana ditentukan sepenuhnya
oleh blok `profiles:` (§6, §7). Mengganti model = mengedit `config.yaml`, tanpa
menyentuh satu baris pun kode agent.

### Blok di luar profil

| Blok | Isi | Pasal |
|---|---|---|
| `pipeline.gates` | Urutan gate. **Urutannya penting** dan tidak dapat ditukar — gate yang `produces`-nya sudah terlewati akan di-skip tanpa suara, sehingga `build_gates` memvalidasi majunya rantai saat start | §27 |
| `rag` | `enabled`, `chunk_chars`, `chunk_overlap`, `top_k`, `ocr_min_chars_per_page` | §10, §13 |
| `latex` | `enabled`, `engine` (`pdflatex`/`latexmk`), `template_dir`, `timeout_s`, `keep_aux` | §25, §26 |
| `graph` | `enabled`, `max_depth` (graf dari ringkasan bab dapat memuat siklus) | §14 |
| `logging` | `enabled`, `output_chars` (berapa karakter keluaran model disimpan apa adanya) | §38 |

`latex.enabled: false` bukan sekadar mematikan kompilasi: gate `latex_writer` dan
`latex_qa` tetap terdaftar tetapi menjadi pass-through, dan **tidak ada berkas
yang ditulis** — itu janji konfigurasinya sendiri, dan janji itu hanya dapat
ditepati di tempat yang tahu apakah konfigurasinya berbunyi begitu.

### Dua profil, dan satu jaminan

| Profil | Isi | Jaminan |
|---|---|---|
| `default` | Model cloud besar, dipilih per tugas (§33) | Butuh akun Ollama. **Kualitas terbaik.** |
| `local` | Seluruh peran → `gemma3:4b` | **Satu-satunya jalur yang dijamin bekerja tanpa jaringan dan tanpa kuota.** |

> **Baca ini sebelum mengandalkan profil `default`.** Model cloud adalah
> dependensi eksternal yang hidup: sign-out, kuota habis, atau laptop tanpa
> jaringan akan mematikannya sepenuhnya. `doctor` dan `--profile local` adalah
> penangkalnya. Perlu diketahui juga bahwa `local` **lambat saat cold load**
> (~41 detik terukur pada mesin ini) dan kualitas `gemma3:4b` memadai untuk
> membuktikan pipeline berjalan, **belum** untuk buku ajar yang sesungguhnya.
> Satu catatan lagi: pada profil `local`, penulis dan peninjau memakai model yang
> sama, sehingga skor peninjauannya tidak dapat dianggap penilaian independen.

Satu perangkap yang sudah dipanggang ke desain: model `thinking` dapat
menghabiskan seluruh anggaran token pada jejak penalaran sehingga `content`
keluar **kosong** (tereproduksi di mesin ini). Karena itu `thinking: true` di
blok `models:` wajib diiringi `max_tokens` yang longgar, `content` dibaca **hanya**
dari `message.content`, dan jejak `thinking` tidak pernah disambungkan ke draf bab.

---

## Arsitektur

Ports & adapters (hexagonal). **Aturan induknya: class di batas sistem, fungsi
murni di tengahnya.**

```
app/        composition root, CLI, konfigurasi, pelaporan   ← satu-satunya pembaca config
agents/     orkestrator + agent (stateless, terima port)     ← hanya boleh impor domain/
domain/     tipe beku, aturan bisnis, port, state machine    ← MURNI: tanpa IO sama sekali
models/     adapter LLM (nama §5 dipertahankan)              ← satu-satunya pengimpor SDK ollama
memory/     state store, checkpoint, artefak Markdown
ingestion/  PDF/LaTeX/Markdown → Document (§9–§12)           ← adapter: pypdf, PyMuPDF
rag/        chunker, embedding, vector store, retriever      ← adapter: chromadb
graph/      ekstraksi konsep & relasi → knowledge/graph      ← §14
latex/      penulis .tex, pemanggil mesin LaTeX, parser log  ← adapter: subprocess
prompts/    kontrak prompt ber-front-matter (§36)
```

| Kategori | Bentuk | Alasan |
|---|---|---|
| Data domain | Pydantic `frozen=True, extra="forbid"` | Imutabel; field halusinasi gagal keras, bukan hilang diam-diam |
| Aturan bisnis | **Fungsi murni** di `domain/` | Komposabel, dapat diuji tanpa IO |
| Port | `typing.Protocol` | Abstraksi struktural → DIP & ISP |
| Adapter (IO) | Kelas di `models/`, `ingestion/`, `rag/`, `graph/`, `latex/`, `app/prompting.py` | Satu-satunya tempat efek samping |
| Agent | Kelas stateless, kolaborator lewat `__init__` | Punya peran; tidak menyimpan state bisnis |

**"Murni" adalah gerbang build, bukan suasana hati.**
`tests/unit/test_architecture.py` menelusuri AST dan menegakkan empat invarian:
`domain/` hanya boleh mengimpor pustaka yang tidak menyentuh dunia luar (daftar
**putih**, bukan daftar hitam); masing-masing SDK (`ollama`, `chromadb`, `pypdf`,
`pymupdf`) muncul di **tepat satu berkas**; `agents/` tidak menyentuh SDK maupun
UI; dan paket adapter tidak mengimpor `{app, agents, models, memory}`.
Kegagalannya menyebut berkas dan barisnya.

**Graf konsep sengaja tanpa `networkx`.** Grafnya berisi puluhan konsep, bukan
juta node, dan seluruh operasi §14 (`prerequisites_of`, `neighbours`,
`find_duplicate_explanations`, `detect_terminology_drift`) adalah penelusuran
sederhana atas `tuple` — dapat diuji habis-habisan tanpa satu pun dependensi.
`networkx` akan menyumbang satu kotak hitam untuk sesuatu yang lebih jernih
ditulis sendiri.

### Deviasi sadar dari blueprint

1. **Ada paket `domain/`** yang tidak ada di §5. §5 menaruh `BookState` di
   `memory/` dan tidak menyediakan tempat bagi tipe murni; tanpa `domain/`, DIP
   tidak dapat ditegakkan. Ini bagian yang menopang seluruh persyaratan
   OOP/FP/SOLID.
2. **`models/` mengikuti nama §5, dan di sini berarti *adapter LLM*** — bukan
   tipe domain, sebagaimana kebiasaan Python yang lazim. Nama blueprint
   dipertahankan, dengan catatan ini sebagai penukarnya.
3. **`BookState` adalah model Pydantic**, bukan kelas biasa seperti §29 — ia
   harus round-trip ke `book.json` dan memeriksa `schema_version`.
4. **§6 menulis `model:`, §32 menulis `name:`** untuk entri yang sama; `name`
   diperlakukan sebagai alias `model`.
5. **Gate LaTeX adalah satu-satunya gate yang menulis berkas.** Ini menyimpang
   dari sifat gate lain, dan disengaja: menulis `.tex` adalah pekerjaan batas
   sistem, dan alternatifnya — menyunting `book_director.py` agar mengenal
   artefak LaTeX — akan merusak sifat Open/Closed yang justru menjadi alasan
   registry gate ada. Sisi baiknya: `book_director.py` tetap tidak tahu apa pun
   tentang LaTeX.
6. **Peninjau pedagogi dan konsistensi memakai peran model yang sama**
   (`reviewer`), karena §23/§24 tidak menyediakan peran tersendiri dan keduanya
   memang pekerjaan menilai prosa. Yang membedakan keduanya adalah promptnya,
   bukan modelnya.
7. **Kebijakan atas hasil model tinggal di `domain/completion.py`, bukan di dalam
   adapter.** Terlihat seperti pemindahan yang tidak perlu — tiga aturan (`length`
   berarti terpotong, `content` kosong dengan jejak penalaran berarti terpotong,
   jejak penalaran tidak pernah disambungkan ke teks) hanya dipanggil dari satu
   tempat. Alasannya bukan kerapian: `models/ollama_client.py` adalah
   **satu-satunya berkas yang boleh meng-import `ollama`**, dan karena itu
   satu-satunya berkas yang tidak dapat diimpor oleh tes mana pun — tes yang
   mengimpornya akan menggagalkan gerbang AST `test_architecture.py` itu sendiri.
   Aturan yang tinggal di sana hanya dapat diuji dengan jaringan sungguhan.
   **Jangan menyatukannya kembali ke adapter.**

---

## Berkas runtime (§28, §37, §38, §39, §42)

```
state/                          output/                        ← DI-TRACK git (§39)
├── book.json                   ├── book.md                    ← hasil `export`
├── chapter01.json              ├── chapters/chapter01.md
├── run.jsonl                   ├── logs/writer.jsonl          ← §38, per peran
├── parse_fail/                 ├── figures/                    ← §42, dari PDF rujukan
│   └── chapter02.attempt1.txt  └── latex/
└── dryrun/                          ├── main.tex
                                     ├── preamble.tex
knowledge/                           ├── references.bib
├── vector_db/                       ├── chapters/chapter01.tex
└── graph/                           ├── book.pdf               ← hasil `export --latex`
                                     └── *.aux|*.log|*.fls      ← TIDAK di-track
```

**`state/chapterNN.json` otoritatif; setiap deliverable turunan.** Karena itu bab
yang sudah `APPROVED` tetapi berkas `.md`-nya hilang akan **dirender ulang** dari
record saat resume — murah, karena perendernya murni — dan `export` membaca
`state/`, bukan direktori keluaran.

**`state/` dan `knowledge/` tidak di-commit; `output/` di-commit.** Yang pertama
adalah turunan yang dapat dibangun ulang (`run`, `ingest`); yang terakhir adalah
hasil kerja, dan revisi AI per bab justru ingin dapat dilacak lewat Git (§39).
Berkas bantu LaTeX dikecualikan secara spesifik di `output/latex/` — urutan
aturan di `.gitignore` itu penting, karena aturan yang terakhir cocok yang menang.

| Berkas | Isinya | Pasal |
|---|---|---|
| `state/run.jsonl` | Satu baris per jalankan: total, disetujui, gagal, menunggu, dilewati, dan model per peran | §38 |
| `output/logs/<peran>.jsonl` | Satu baris per panggilan model: peran, model, token, latensi, cuplikan keluaran | §38 |
| `state/parse_fail/*.txt` | Keluaran mentah yang gagal di-parse, untuk memperbaiki prompt | §37 |
| `state/book.json` | `schema_version`, permintaan, spesifikasi, ringkasan bab, istilah, kutipan | §29 |
| `output/latex/book.pdf` | PDF akhir, hanya bila `latex.enabled` dan mesin LaTeX-nya ada | §42 |

Bukti di `parse_fail/` ditulis oleh `BookDirector`, bukan oleh agent: agent tidak
tahu bab mana yang sedang dikerjakannya, dan memang tidak boleh tahu — nomor bab
hanya diketahui di tempat kegagalannya ditangkap.

### Resume dan isolasi kegagalan

- `run` melewati bab berstatus `APPROVED` secara bawaan; `--force` mengulanginya,
  `--chapter N` menargetkannya.
- **State rusak → berhenti dengan `StateCorruptError`.** State tidak pernah
  dihapus otomatis.
- **Kegagalan berskup bab tidak membunuh proses** (§35): babnya ditandai
  `FAILED`, state-nya disimpan, dan proses **lanjut** ke bab berikutnya — dengan
  pekerjaan yang sudah ada tetap utuh. Gate yang menolak tidak masuk jalur ini;
  ia mengirim babnya ke revisi.
- **Kegagalan sistemik membatalkan segera**: bila model writer tidak terjangkau,
  setiap bab sisa akan gagal dengan cara yang identik, jadi menghentikan sekarang
  adalah benar — bukan melanjutkan sambil membakar anggaran untuk N bab.
- **Menunggu keputusan manusia bukan kegagalan** (§44): anggaran revisi tidak
  terpakai, `exit_code` nol, dan babnya muncul di kolom `menunggu`.

---

## Pengujian

```powershell
pytest                                      # seluruh suite, TANPA jaringan (bawaan)
pytest tests/unit/test_architecture.py -v   # gerbang DIP + kemurnian
pytest -m live -q                           # opt-in: Ollama & latexmk sungguhan
python -m mypy --strict domain agents app models memory ingestion rag graph latex
python -m flake8 .
```

`pytest.ini` menyetel `-m "not live"`, sehingga suite bawaan **selalu hijau secara
offline**. Tes yang menyentuh Ollama atau mesin LaTeX sungguhan ditandai
`@pytest.mark.live` dan di-skip secara bawaan; tes itu juga **skip** (bukan gagal)
pada mesin tanpa perkakasnya.

Aset uji yang paling berdaya guna adalah `SchemaEchoChatModel`: ia **mensintesis
instance valid dari `request.format_schema`**. Akibatnya, pipeline penuh dapat
dijalankan ujung-ke-ujung tanpa JSON tulisan tangan dan tanpa jaringan — dan
setiap kali model domain mendapat field baru, model echo itu mengikutinya
otomatis, sehingga tes integrasinya **tidak membusuk**.

Prompt dibaca dari direktori `prompts/` **yang asli**, bukan salinan palsu:
membacanya murni, dan berkas prompt itu sendiri yang sedang diuji. Yang dipalsukan
hanya model dan reporter — justru itu pembayaran DIP: seluruh pipeline berjalan
sungguhan tanpa jaringan, tanpa satu pun `if testing` di kode produksi.

Log LaTeX untuk parser §26 disimpan sebagai **berkas uji** di
`tests/data/latex_logs/`, ditulis sebagai bentuk yang *diharapkan* dari LaTeX.
`tests/live/test_latex_compile.py` yang mengompilasi sungguhan dan membandingkannya
— itulah yang membuktikan harapan itu benar.

---

## Yang sengaja ditunda

- **Web UI / dasbor** (MVP 7). Ditunda atas keputusan pengguna. *Seam*-nya tetap
  disiapkan agar tidak menjadi jalan buntu: seluruh data yang dibutuhkan sebuah UI
  sudah tersedia sebagai `RunReport`, `ChapterRecord`, dan `state/run.jsonl` —
  sebuah UI cukup membaca ketiganya dan memanggil perintah CLI yang sudah ada,
  tanpa satu pun perubahan pada `agents/` atau `domain/`.
- **`knowledge/metadata/`** (§5): belum ada kebutuhan nyata; dibuat saat ada.
- **`--latex-template`**: diterima dan dicatat, belum dipakai untuk merakit.
- **Migrasi skema state**: tidak diperlukan — penyisipan status §19/§20 menjaga
  urutan relatif seluruh pasangan status lama, dan itu dikunci oleh tes.

---

## Pemecahan masalah

| Gejala | Sebab dan tindakan |
|---|---|
| `ModelAuthError: butuh akun Ollama` | Profil `default` memakai model cloud. Jalankan `ollama signin`, atau pakai `--profile local`. |
| `ModelUnavailableError` (HTTP 404) | Model belum di-*pull*. `ollama pull <model>`, atau ganti lewat `--set-model`. |
| `TruncatedOutputError` | Anggaran token habis (model `thinking` paling rawan). Naikkan `max_tokens` peran itu di `config.yaml`. |
| Bab gagal berulang kali | Buka `state/parse_fail/chapterNN.attempt*.txt` — di situ keluaran mentah yang gagal di-parse, satu-satunya bukti untuk memperbaiki promptnya. |
| `book.pdf` tidak ada | `latex.enabled: false`, `pdflatex` tidak di `PATH`, atau babnya belum `LATEX_COMPILED`. `python -m app doctor` menyebutkan yang pertama dan kedua; `status` menyebutkan yang ketiga. |
| Kompilasi LaTeX gagal | Gate `latex_qa` sudah mencoba memperbaikinya sendiri sampai `repair_attempts` kali. Bila tetap gagal, `latex.keep_aux: true` menyimpan `.log`-nya untuk diperiksa. |
| `ingest` melaporkan nol dokumen | `input/references/` kosong. Isi dengan PDF/LaTeX/Markdown; PDF hasil scan otomatis dialihkan ke jalur OCR. |
| Keluaran tidak sesuai harapan | `python -m app run --dry-run` untuk membaca prompt yang benar-benar dikirim, lengkap dengan model dan angka samplingnya. |
| Biaya token membengkak | `--gates` untuk mempersempit rantai, dan `--dry-run` untuk memeriksa tanpa biaya. Lihat *Biaya token* di atas. |

---

## Lisensi

MIT — lihat `LICENSE`.
