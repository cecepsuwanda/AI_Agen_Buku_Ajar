# AI Agent Buku Ajar

Agen CLI yang menyusun **buku ajar** dari RPS (Rencana Pembelajaran Semester)
secara bab-per-bab, memakai beberapa model LLM melalui [Ollama](https://ollama.com)
— satu model per peran, bukan satu model untuk segalanya.

Dokumen acuan arsitekturnya adalah `AI_Agen_Buku_Ajar_Python_Ollama_MultiLLM.md`
(disebut **blueprint** di seluruh kode dan dokumen ini; rujukan seperti §27, §31,
§36 menunjuk pasal di sana). Kode ini **mengikuti penomoran pasal blueprint**
dengan sengaja, sehingga setiap keputusan desain dapat ditelusuri kembali ke
sumbernya.

---

## Status: MVP inti, berjalan ujung-ke-ujung

**Yang sudah ada:** RPS masuk → `output/chapters/chapter01.md` keluar, lewat
rantai planner → perincian bab → penulis → penilai, dengan checkpoint, resume,
dan isolasi kegagalan per bab. Seluruhnya dapat diuji tanpa jaringan.

**Yang belum ada** (dan direncanakan): ingestion PDF, RAG, parser RPS, keluaran
LaTeX + QA, Fact/Citation/Pedagogy/Consistency Checker, Knowledge Graph, dan Web UI.

Blueprint §27 mendefinisikan rantai **10 tahap** per bab; MVP ini baru memiliki
**4 agent**. Jembatannya adalah `PassThroughGate`: tahap yang belum berpenghuni
tetap dilewati dan dicatat `skipped=True`, sehingga rantai state-nya lengkap sejak
hari pertama dan tidak akan perlu migrasi saat agent berikutnya menyusul.

---

## Kebutuhan

| Kebutuhan | Versi | Catatan |
|---|---|---|
| Python | **3.11** (3.11.9 terverifikasi) | 3.12 belum diuji; sintaks generic PEP 695 sengaja tidak dipakai |
| Ollama | 0.35+ | `localhost:11434`; akun cloud diperlukan hanya untuk profil `default` |
| LaTeX | MiKTeX 24.1+ | **belum dipakai** pada iterasi ini |

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

# 2. Susun rencana buku dari RPS.
python -m app plan --rps input/rps/rps.tex --chapters 8

# 3. Kerjakan seluruh bab (mengulang secara otomatis bila terputus).
python -m app run

# 4. Lihat statusnya, lalu gabungkan menjadi satu berkas.
python -m app status
python -m app export
```

`run` juga dapat dijalankan **tanpa `plan` lebih dulu** — ia akan menyusun
rencananya sendiri. Menjalankannya tanpa keduanya akan menolak dengan pesan yang
menyebut urutan yang benar, bukan traceback.

### Melihat prompt tanpa membakar token

```powershell
python -m app run --dry-run --rps input/rps/rps.tex
```

`--dry-run` menjalankan **pipeline yang sungguhan** — planner, perincian bab,
penulis, gate, checkpoint, penulisan Markdown — dengan satu perubahan: adapter
chat-nya tidak mengirim HTTP, melainkan menuliskan setiap prompt ke
`state/dryrun/NN-peran.txt` (lengkap dengan nama model dan angka sampling yang
benar-benar akan dipakai peran itu) lalu mengembalikan instance yang disintesis
dari skema. Jadi yang dibuktikan adalah **jalur produksinya**, bukan jalur uji
coba yang terpisah.

Seluruh hasilnya dikurung di `state/dryrun/`; `state/` dan `output/` yang
sungguhan tidak tersentuh sama sekali.

---

## Perintah

| Perintah | Gunanya |
|---|---|
| `doctor` | Periksa konfigurasi, server Ollama, dan keterjangkauan setiap peran. `--no-probe` melewati uji kirim-1-token. |
| `plan` | Susun `BookSpec` dari RPS, simpan ke `state/book.json`. |
| `run` | Kerjakan seluruh bab yang belum selesai. **Resume adalah bawaan.** |
| `write-chapter N` | Kerjakan satu bab sampai tuntas, atau sampai ia menyerah. |
| `status` | Tabel tiap bab: status, revisi, vonis gate, dan model per peran. |
| `export` | Gabungkan bab yang sudah disetujui menjadi `output/book.md`. |

Opsi yang berlaku umum:

| Opsi | Artinya |
|---|---|
| `--rps/-r` | Berkas RPS yang menjadi dasar buku. |
| `--output/-o` | Direktori keluaran (bawaan `output/`). |
| `--profile/-p` | Profil model: `default` atau `local`. |
| `--set-model PERAN=MODEL` | Ganti model satu peran; boleh diulang. |
| `--chapter/-c`, `--from`, `--to` | Batasi bab yang dikerjakan. |
| `--force` | Kerjakan ulang bab yang sudah disetujui. |
| `--max-revisions` | Batas revisi per bab (bawaan dari `config.yaml`). |
| `--dry-run` | Tulis prompt ke `state/dryrun/`, tanpa memanggil model. |
| `--config/-C`, `--verbose/-v`, `--log-json` | Konfigurasi, kerincian, log JSONL (§38). |
| `--references`, `--latex-template` | **Diterima, lalu diperingatkan** bahwa ingestion belum ada. Antarmuka blueprint tetap stabil, dan celahnya terlihat — bukan sunyi. |

**Override model tanpa satu flag per peran** (flag per peran akan melanggar Open/Closed —
setiap peran baru menuntut flag baru):

```powershell
python -m app run --set-model writer=gemma3:4b --set-model reviewer=gemma3:4b
```

**Pemetaan §42 dipertahankan:** tanpa subcommand, baris invokasi blueprint jatuh
ke `run`. Jadi keduanya setara, dan `tests/integration/test_cli_offline.py`
membuktikan keduanya menghasilkan artefak yang **identik**:

```powershell
python -m app.main --rps input/rps/rps.tex --output output/book
python -m app.main run --rps input/rps/rps.tex --output output/book
```

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
| `BUKUAJAR_STATE_DIR`, `BUKUAJAR_OUTPUT_DIR` | Direktori runtime |
| `BUKUAJAR_OLLAMA_BASE_URL` | Alamat server Ollama |

**Agent tidak pernah menyebut nama model.** Ia selalu memanggil
`router.chat("writer")`, dan peran mana memakai model mana ditentukan sepenuhnya
oleh blok `profiles:` (§6, §7). Mengganti model = mengedit `config.yaml`, tanpa
menyentuh satu baris pun kode agent.

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
app/      composition root, CLI, konfigurasi, pelaporan    ← satu-satunya pembaca config
agents/   orkestrator + agent (stateless, terima port)      ← hanya boleh impor domain/
domain/   tipe beku, aturan bisnis, port, state machine     ← MURNI: tanpa IO sama sekali
models/   adapter LLM (nama §5 dipertahankan)                ← satu-satunya pengimpor SDK
memory/   state store, checkpoint, artefak Markdown
prompts/  kontrak prompt ber-front-matter (§36)
```

| Kategori | Bentuk | Alasan |
|---|---|---|
| Data domain | Pydantic `frozen=True, extra="forbid"` | Imutabel; field halusinasi gagal keras, bukan hilang diam-diam |
| Aturan bisnis | **Fungsi murni** di `domain/` | Komposabel, dapat diuji tanpa IO |
| Port | `typing.Protocol` | Abstraksi struktural → DIP & ISP |
| Adapter (IO) | Kelas di `models/`, `app/prompting.py` | Satu-satunya tempat efek samping |
| Agent | Kelas stateless, kolaborator lewat `__init__` | Punya peran; tidak menyimpan state bisnis |

**"Murni" adalah gerbang build, bukan suasana hati.**
`tests/unit/test_architecture.py` menelusuri AST dan menegakkan tiga invarian:
`domain/` hanya boleh mengimpor pustaka yang tidak menyentuh dunia luar (daftar
**putih**, bukan daftar hitam); `import ollama` muncul di **tepat satu berkas**
(`models/ollama_client.py`); dan `agents/` tidak menyentuh SDK maupun UI.
Kegagalannya menyebut berkas dan barisnya.

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
5. **`NullResearcher` mengembalikan paket riset kosong dengan `degraded=True`**
   (§41 menaruh Researcher sebelum Writer, tetapi RAG di luar cakupan). Prompt
   penulis dirancang bekerja tanpa bahan dan menandai setiap klaim tak didukung di
   `unresolved_claims` — jadi perilakunya **berbeda** dari maksud §34, degradasinya
   tercatat di state, dan menjalankan RAG nanti dapat memperbaiki bab yang sama.
   Ini konflik cakupan paling signifikan dan diterima secara sadar.
6. **Kebijakan atas hasil model tinggal di `domain/completion.py`, bukan di dalam
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

## Berkas runtime (§28, §37, §38, §39)

```
state/                          output/                        ← DI-TRACK git (§39)
├── book.json                   ├── book.md                    ← hasil `export`
├── chapter01.json              └── chapters/chapter01.md
├── run.jsonl                          ↑ DITURUNKAN dari ChapterRecord.draft
├── parse_fail/
│   └── chapter02.attempt1.txt
└── dryrun/                     ← sandbox --dry-run, sekali pakai
```

**`state/chapterNN.json` otoritatif; Markdown turunan.** Karena itu bab yang
sudah `APPROVED` tetapi berkas `.md`-nya hilang akan **dirender ulang** dari
record saat resume — murah, karena perendernya murni.

**`state/` tidak di-commit; `output/` di-commit.** Yang pertama adalah turunan
yang dapat dibangun ulang dari `run`; yang kedua adalah sumber buku, dan revisi
AI per bab justru ingin dapat dilacak lewat Git (§39).

| Berkas | Isinya | Pasal |
|---|---|---|
| `state/run.jsonl` | Satu baris per panggilan model: peran, model, versi prompt (`versi+hash`), token, latensi | §38 |
| `state/parse_fail/*.txt` | Keluaran mentah yang gagal di-parse, untuk memperbaiki prompt | §37 |
| `state/book.json` | `schema_version`, permintaan, spesifikasi, ringkasan bab, istilah | §29 |

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
  pekerjaan yang sudah ada tetap utuh.
- **Kegagalan sistemik membatalkan segera**: bila model writer tidak terjangkau,
  setiap bab sisa akan gagal dengan cara yang identik, jadi menghentikan sekarang
  adalah benar — bukan melanjutkan sambil membakar anggaran untuk N bab.

---

## Pengujian

```powershell
pytest                          # seluruh suite, TANPA jaringan (bawaan)
pytest tests/unit/test_architecture.py -v   # gerbang DIP + kemurnian
pytest -m live -q               # opt-in: 1 smoke test terhadap Ollama sungguhan
python -m mypy app domain agents memory models && python -m flake8
```

`pytest.ini` menyetel `-m "not live"`, sehingga suite bawaan **selalu hijau secara
offline**. Tes yang menyentuh Ollama sungguhan ditandai `@pytest.mark.live` dan
di-skip secara bawaan; tes itu juga **skip** (bukan gagal) pada mesin tanpa
Ollama.

Aset uji yang paling berdaya guna adalah `SchemaEchoChatModel`: ia **mensintesis
instance valid dari `request.format_schema`**. Akibatnya, pipeline penuh dapat
dijalankan ujung-ke-ujung tanpa JSON tulisan tangan dan tanpa jaringan — dan
setiap kali model domain mendapat field baru, model echo itu mengikutinya
otomatis, sehingga tes integrasinya **tidak membusuk**.

Prompt dibaca dari direktori `prompts/` **yang asli**, bukan salinan palsu:
membacanya murni, dan berkas prompt itu sendiri yang sedang diuji. Yang dipalsukan
hanya model dan reporter — justru itu pembayaran DIP: seluruh pipeline berjalan
sungguhan tanpa jaringan, tanpa satu pun `if testing` di kode produksi.

---

## Pemecahan masalah

| Gejala | Sebab dan tindakan |
|---|---|
| `ModelAuthError: butuh akun Ollama` | Profil `default` memakai model cloud. Jalankan `ollama signin`, atau pakai `--profile local`. |
| `ModelUnavailableError` (HTTP 404) | Model belum di-*pull*. `ollama pull <model>`, atau ganti lewat `--set-model`. |
| `TruncatedOutputError` | Anggaran token habis (model `thinking` paling rawan). Naikkan `max_tokens` peran itu di `config.yaml`. |
| Bab gagal berulang kali | Buka `state/parse_fail/chapterNN.attempt*.txt` — di situ keluaran mentah yang gagal di-parse, satu-satunya bukti untuk memperbaiki promptnya. |
| Keluaran tidak sesuai harapan | `python -m app run --dry-run` untuk membaca prompt yang benar-benar dikirim, lengkap dengan model dan angka samplingnya. |

---

## Lisensi

MIT — lihat `LICENSE`.
