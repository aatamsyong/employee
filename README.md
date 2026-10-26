# Sistem Karyawan Virtual (Aimployee)

Platform operasional AI internal PT Amsyong yang mengintegrasikan Knowledge Base perusahaan dengan teknik Basic Multi-Stage Pipeline Orchestration untuk menghasilkan output kerja nyata (Proposal Word, Presentasi PowerPoint, SOP, Analisis Tender, dan Asisten Interaktif).

---

## 1. Arsitektur Basic Multi-Stage Pipeline Orchestration
Sistem dibangun berdasarkan prinsip pipeline modular dengan separation of concerns:
1. **Planner Agent** — mengurai tugas menjadi sub-langkah terstruktur dan menentukan batasan kerja.
2. **Retrieval Stage** — mengambil bukti faktual dari repositori `01_Knowledge_Base` melalui keyword search.
3. **Specialist Generator Agent** — memproduksi konten domain spesifik (Proposal, Presentasi, SOP, Marketing).
4. **Critic / Verifier Agent** — melakukan audit akurasi fakta, validasi format, mitigasi halusinasi, dan loop revisi.
5. **Tool Agent** — merakit output terverifikasi menjadi dokumen fisik (`.docx` dan `.pptx`) dengan otorisasi, budget, dan validator output.
6. **Safety Governor** — memeriksa kebijakan keamanan, eskalasi, dan override manual pengguna.
7. **Audit & Observability** — mencatat seluruh jejak eksekusi ke JSONL dan `logs/audit.log` (rotating), dengan redaksi rahasia otomatis.
8. **Memory Manager** — memori jangka pendek (per-workflow), jangka panjang (persisten), task-state, artefak, dan antrian tinjauan manusia.
9. **Compliance Auditor** — mode audit bawaan yang menjalankan probe di sandbox dan menghasilkan laporan VERIFIED/UNVERIFIED.

---

## 2. Struktur Direktori Proyek
```text
project/
├── 01_Knowledge_Base/       # Repositori data & dokumen acuan AI
├── core/                    # Engine inti sistem
│   ├── knowledge_base.py    # Mesin indexing dan pencarian keyword
│   ├── llm_client.py        # Gateway multi-provider (Gemini, OpenAI, Claude, Offline)
│   ├── orchestrator.py      # Basic orchestrator & quality gate
│   ├── tool_agent.py        # Pemanggil tool dengan otorisasi & validator
│   ├── audit_logger.py      # Audit trail JSONL + rotating audit.log
│   ├── memory_manager.py    # Short/long-term memory, task-state, artifacts
│   ├── safety_governor.py   # Kebijakan keamanan, eskalasi, override
│   ├── compliance_auditor.py # Built-in audit mode
│   ├── redaction.py         # Deteksi & redaksi rahasia
│   └── tools.py             # Generator dokumen .docx & .pptx
├── tests/                   # Test suite (30 kasus, unittest)
├── logs/                    # Audit trail, log per workflow, laporan kepatuhan
├── memory/                  # Data persisten (long-term memory, safety policy)
├── output/                  # Berkas hasil rakitan dokumen (.docx / .pptx)
├── app.py                   # Antarmuka web utama (Streamlit)
├── run_app.bat              # Skrip peluncur cepat (sekali klik)
├── Progress_log.md          # Status penutupan gap audit
├── CHANGELOG.md             # Catatan perubahan
└── TASK_TRACKER.md          # Pelacakan tugas
```

---

## 3. Fitur Utama Aplikasi
- **Dashboard** — statistik repositori, indikator kesehatan sistem **live** (baca audit trail), aksi cepat.
- **Proposal Generator** — pembuatan dokumen proposal teknis dan komersial `.docx`.
- **Presentasi Generator** — pembuatan slide deck `.pptx`.
- **SOP Generator** — penyusunan dokumen prosedur kerja berstandar ISO `.docx`.
- **Marketing & Tender Assistant** — analisis tender & strategi penawaran.
- **AI Assistant (Aimployee)** — chatbot berbasis fakta internal.
- **Knowledge Base Explorer** — pencarian, upload, indexing.
- **🛡️ Safety & Governance** — konfigurasi kebijakan, override log, pending reviews.
- **📜 Audit Log Viewer** — pencarian trail dengan filter agen/status/tipe.
- **🧠 Memory Viewer** — riwayat workflow, statistik keandalan tool, entri long-term.
- **🧪 Compliance Audit** — jalankan probe sandbox, unduh laporan markdown.
- **🔧 Safety Settings (sidebar)** — threshold eskalasi, budget tool, durasi, blokir data sensitif.
- **👤 Human Override** — dialog approve/reject saat workflow ter-eskalasi.

---

## 4. Cara Menjalankan Aplikasi
1. Buka folder project ini.
2. Klik ganda file `run_app.bat`, atau jalankan melalui terminal:
   ```powershell
   streamlit run app.py
   ```
3. Buka browser pada alamat `http://localhost:8509`.

---

## 5. Menjalankan Test Suite
```powershell
python -m pytest tests/ -v
```
Target: **30/30 PASS**.

---

## 6. Menjalankan Compliance Audit
Dari halaman **🧪 Compliance Audit** di UI, atau:
```python
from core.compliance_auditor import ComplianceAuditor
auditor = ComplianceAuditor(".")
report = auditor.run()
print(report["summary"])  # {'VERIFIED': 14, 'UNVERIFIED': 0, 'NOT RUN': 0}
```


## 7. MIT License
 
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
 
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
 
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
( W.Wijaya-2026  )
