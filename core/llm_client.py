"""LLM client: dispatches prompts to cloud providers or the offline engine.

Tracks ``last_error`` and ``last_provider_used`` so the orchestrator can
detect fallbacks without parsing the content string.
"""

import os
from typing import Dict, Any, Optional

try:
    import requests
except ImportError:
    requests = None


class LLMClient:
    def __init__(self, provider: str = "offline", api_key: Optional[str] = None,
                 model: Optional[str] = None) -> None:
        self.provider = provider.lower()
        self.api_key = api_key or os.environ.get("AI_API_KEY", "")
        self.model = model
        self.last_error: Optional[str] = None
        self.last_provider_used: str = "offline"

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 2500, temperature: float = 0.3) -> str:
        self.last_error = None
        self.last_provider_used = "offline"
        if self.provider == "openai" and self.api_key:
            return self._call_openai(system_prompt, user_prompt, max_tokens, temperature)
        if self.provider == "gemini" and self.api_key:
            return self._call_gemini(system_prompt, user_prompt, max_tokens, temperature)
        if self.provider == "claude" and self.api_key:
            return self._call_claude(system_prompt, user_prompt, max_tokens, temperature)
        return self._offline_engine(system_prompt, user_prompt)

    def _call_openai(self, system_prompt: str, user_prompt: str,
                     max_tokens: int, temperature: float) -> str:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)
            model_name = self.model or "gpt-4o-mini"
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": user_prompt}],
                max_tokens=max_tokens, temperature=temperature,
            )
            self.last_provider_used = "openai"
            return response.choices[0].message.content
        except Exception as exc:
            self.last_error = f"OpenAI API error: {exc}"
            return self._offline_engine(system_prompt, user_prompt)

    def _call_gemini(self, system_prompt: str, user_prompt: str,
                     max_tokens: int, temperature: float) -> str:
        if requests is None:
            self.last_error = "requests library not installed."
            return self._offline_engine(system_prompt, user_prompt)
        try:
            model_name = self.model or "gemini-2.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"role": "user",
                              "parts": [{"text": f"System Directive:\n{system_prompt}\n\nUser Task:\n{user_prompt}"}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
            }
            resp = requests.post(url, headers=headers, json=payload,
                                 params={"key": self.api_key}, timeout=30)
            data = resp.json()
            if "candidates" in data and data["candidates"]:
                parts = data["candidates"][0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                if text.strip():
                    self.last_provider_used = "gemini"
                    return text
            self.last_error = f"Gemini returned no candidates: {data.get('error', {}).get('message', 'unknown')}"
        except Exception as exc:
            self.last_error = f"Gemini request failed: {exc}"
        return self._offline_engine(system_prompt, user_prompt)

    def _call_claude(self, system_prompt: str, user_prompt: str,
                     max_tokens: int, temperature: float) -> str:
        if requests is None:
            self.last_error = "requests library not installed."
            return self._offline_engine(system_prompt, user_prompt)
        try:
            url = "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                       "content-type": "application/json"}
            payload = {
                "model": self.model or "claude-3-5-sonnet-20241022",
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            data = resp.json()
            if data.get("content"):
                text = data["content"][0].get("text", "")
                if text.strip():
                    self.last_provider_used = "claude"
                    return text
            self.last_error = f"Claude returned empty content: {data}"
        except Exception as exc:
            self.last_error = f"Claude request failed: {exc}"
        return self._offline_engine(system_prompt, user_prompt)

    def _offline_engine(self, system_prompt: str, user_prompt: str) -> str:
        p_lower = user_prompt.lower()
        if "proposal" in p_lower:
            return self._format_proposal_response(user_prompt)
        if "presentasi" in p_lower or "slide" in p_lower or "presentation" in p_lower:
            return self._format_presentation_response(user_prompt)
        if "sop" in p_lower:
            return self._format_sop_response(user_prompt)
        if "marketing" in p_lower or "tender" in p_lower:
            return self._format_marketing_response(user_prompt)
        return self._format_assistant_response(user_prompt)

    def _format_proposal_response(self, prompt: str) -> str:
        return """# DOKUMEN PROPOSAL TEKNIS & BIAYA SIMULASI
PT Amsyong (Data Simulasi)
Nomor: AMS-PRO-2026-001

## 1. Pendahuluan dan Latar Belakang
PT Amsyong mengajukan proposal implementasi sistem manajemen dokumen digital dan pengadaan infrastruktur teknologi informasi. Solusi ini dirancang untuk memusatkan penyimpanan data, mempermudah pelacakan dokumen, dan meningkatkan efisiensi operasional organisasi.

## 2. Lingkup Pekerjaan (Scope of Work)
1. Analisis kebutuhan pengguna dan alur dokumen organisasi.
2. Perancangan struktur folder, hak akses pengguna, dan metadata arsip.
3. Pengembangan antarmuka berbasis web dengan modul pencarian cepat.
4. Pengujian sistem dan migrasi dokumen contoh (maksimal 5.000 dokumen).
5. Pelatihan administrator dan pengguna internal (maksimal 15 peserta).
6. Penyusunan dokumentasi teknis dan pemeliharaan selama 90 hari setelah serah terima.

## 3. Rencana Pelaksanaan
Total estimasi durasi pekerjaan adalah 37 hari kerja, mencakup analisis kebutuhan, desain, pengembangan, pengujian, pelatihan, dan serah terima akhir.

## 4. Rencana Anggaran Biaya (Simulasi)
Total nilai paket pekerjaan adalah Rp50.000.000 (Subtotal) ditambah PPN 11% sebesar Rp5.500.000, dengan total nilai penawaran sebesar Rp55.500.000.

## 5. Termin Pembayaran
- Termin I (30%): Setelah penandatanganan persetujuan pekerjaan.
- Termin II (40%): Setelah modul aplikasi selesai dikembangkan dan diuji.
- Termin III (30%): Setelah pelatihan dan berita acara serah terima selesai."""

    def _format_presentation_response(self, prompt: str) -> str:
        return """# MATERI PRESENTASI SIMULASI
PT Amsyong (Data Simulasi)

## Slide 1: Judul Presentasi
- Judul: Implementasi Sistem Manajemen Dokumen Digital
- Subjudul: Sentralisasi Arsip dan Peningkatan Efisiensi Alur Kerja Organisasi

## Slide 2: Permasalahan dan Kebutuhan
- Dokumen operasional tersimpan di berbagai tempat terpisah.
- Pencarian arsip dan pengendalian versi dokumen memakan waktu lama.
- Kebutuhan pemantauan status persetujuan dokumen secara terpusat.

## Slide 3: Solusi yang Diusulkan
- Aplikasi manajemen dokumen berbasis web yang mudah digunakan.
- Fitur pencarian terstruktur berbasis kata kunci dan metadata.
- Pengaturan hak akses bertingkat dan pencatatan aktivitas pengguna (audit log).

## Slide 4: Tahapan dan Jadwal Kerja
- Analisis kebutuhan dan desain sistem: 10 hari kerja.
- Pengembangan dan pengujian aplikasi: 20 hari kerja.
- Pelatihan pengguna dan serah terima: 7 hari kerja.

## Slide 5: Kesimpulan dan Langkah Selanjutnya
- Solusi siap diterapkan untuk mempercepat digitalisasi organisasi.
- Pembahasan teknis lanjutan dan penjadwalan demo sistem contoh."""

    def _format_sop_response(self, prompt: str) -> str:
        return """# STANDAR OPERASIONAL PROSEDUR (SOP)
PT Amsyong (Data Simulasi)
Nomor Dokumen: AMS-SOP-PROC-001
Judul: SOP Pengadaan Barang dan Jasa

## 1. Tujuan
Menetapkan prosedur pengadaan barang dan jasa agar proses berjalan terstruktur, terdokumentasi, transparan, dan dapat diaudit.

## 2. Ruang Lingkup
Mencakup alur permintaan pengadaan, seleksi vendor rekanan, evaluasi penawaran harga, persetujuan pejabat berwenang, penerimaan barang, dan pemeriksaan tagihan keuangan.

## 3. Penanggung Jawab
- Pemohon: Mengisi formulir kebutuhan barang atau jasa.
- Manajer Pengadaan: Memeriksa spesifikasi dan memimpin evaluasi perbandingan vendor.
- Manajer Keuangan: Memeriksa ketersediaan anggaran dan kelengkapan bukti faktur.
- Direktur: Memberikan otorisasi persetujuan pengadaan.

## 4. Tahapan Prosedur Kerja
1. Pemohon mengajukan formulir permintaan pengadaan lengkap dengan jadwal dan estimasi biaya.
2. Manajer Pengadaan mengumpulkan minimal penawaran pembanding dari vendor terdaftar.
3. Evaluasi penawaran dilakukan berdasarkan harga, spesifikasi teknis, waktu pengiriman, dan garansi.
4. Penerbitan Purchase Order (PO) setelah persetujuan otorisasi diperoleh.
5. Petugas penerimaan memeriksa kesesuaian fisik dan menyusun laporan ketidaksesuaian bila ditemukan cacat barang.
6. Bagian keuangan memproses pembayaran setelah faktur dan bukti serah terima cocok.

## 5. Arsip dan Dokumentasi
Seluruh rekaman formulir permintaan, penawaran vendor, bukti penerimaan, dan faktur disimpan dalam folder transaksi pengadaan resmi."""

    def _format_marketing_response(self, prompt: str) -> str:
        return """# ANALISIS STRATEGI PEMASARAN & EVALUASI TENDER
PT Amsyong (Simulasi)

## 1. Ringkasan Eksekutif
Peluang pengadaan teknologi informasi dan digitalisasi saat ini menuntut efisiensi, transparansi harga, dan kesiapan pemeliharaan jangka panjang.

## 2. Analisis Keunggulan Kompetitif (Unique Value Proposition)
- Keandalan Solusi: Didukung rekam jejak pada proyek simulasi (AMS-PRJ-001 hingga AMS-PRJ-005).
- Kepatuhan Spesifikasi: Menjamin pemenuhan 100% persyaratan Kerangka Acuan Kerja (KAK/TOR).
- Transparansi Biaya: Struktur penawaran harga yang jelas dan terperinci.

## 3. Strategi Penawaran Tender
- Menyesuaikan penawaran teknis dengan batas spesifikasi minimum pada KAK.
- Menyusun jadwal pelaksanaan yang realistis dengan alokasi hari kerja terukur.
- Melampirkan kelengkapan administrasi dan dokumen kualifikasi sesuai checklist."""

    def _format_assistant_response(self, prompt: str) -> str:
        return """Berdasarkan dokumen simulasi internal PT Amsyong, saya siap membantu tugas Anda.

Layanan simulasi PT Amsyong mencakup:
1. Pengadaan Perangkat Komputer dan Infrastruktur Jaringan.
2. Implementasi Sistem Manajemen Dokumen Digital.
3. Digitalisasi Arsip dan Manajemen Data Aset.
4. Pemeliharaan dan Pelatihan Aplikasi Bisnis.

Silakan pilih menu di panel sebelah kiri untuk membuat Proposal, Presentasi Slide, SOP, atau melakukan pencarian di Knowledge Base."""
