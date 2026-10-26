import os
import sys
import glob
import json
import streamlit as st
from datetime import datetime

st.set_page_config(
    page_title="PT Amsyong AI - Karyawan Virtual",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.knowledge_base import KnowledgeBaseEngine
from core.llm_client import LLMClient
from core.orchestrator import AgentOrchestrator
from core.compliance_auditor import ComplianceAuditor

KB_DIR = os.path.join(BASE_DIR, "01_Knowledge_Base")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LOG_DIR = os.path.join(BASE_DIR, "logs")
MEMORY_DIR = os.path.join(BASE_DIR, "memory")
for d in (OUTPUT_DIR, LOG_DIR, MEMORY_DIR):
    os.makedirs(d, exist_ok=True)


# -- Cached singletons (persist across reruns) -------------------------

@st.cache_resource
def get_kb_engine():
    return KnowledgeBaseEngine(KB_DIR)


@st.cache_resource
def get_orchestrator(provider: str, api_key: str):
    llm = LLMClient(provider=provider, api_key=api_key or None)
    return AgentOrchestrator(get_kb_engine(), llm, BASE_DIR)


# -- Custom CSS ---------------------------------------------------------

st.markdown("""
<style>
    .main-header { font-size: 26px; font-weight: 700; color: #102C57; margin-bottom: 5px; }
    .sub-header { font-size: 14px; color: #555; margin-bottom: 20px; }
    .metric-card { background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px;
                   padding: 16px; text-align: center; }
    .status-SUCCESS { color: #16a34a; font-weight: 600; }
    .status-PASSED  { color: #16a34a; font-weight: 600; }
    .status-WARNING { color: #d97706; font-weight: 600; }
    .status-ESCALATED { color: #dc2626; font-weight: 600; }
    .status-FAILED  { color: #dc2626; font-weight: 600; }
    .status-REJECTED { color: #dc2626; font-weight: 600; }
    .status-PASSED_NO_ARTIFACT { color: #d97706; font-weight: 600; }
    .watermark {
        position: fixed;
        left: 12px;
        bottom: 12px;
        z-index: 9999;
        opacity: 1.0;
        pointer-events: none;
    }
    .watermark img {
        max-width: 160px;
        height: auto;
        border-radius: 6px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    }
</style>
<div class="watermark"><img src="static/watermark.jpg" alt="watermark"></div>
""", unsafe_allow_html=True)


# -- Helpers -------------------------------------------------------------

def _ts_to_str(ts) -> str:
    """Format timestamp (epoch float or ISO string) to readable local time."""
    if ts is None:
        return "-"
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        return str(ts)[:19].replace("T", " ")
    except Exception:
        return str(ts)


def status_badge(status: str) -> str:
    return f'<span class="status-{status}">{status}</span>'


def render_workflow_summary(result: dict) -> None:
    status = result.get("status", "UNKNOWN")
    wf_id = result.get("workflow_id", "-")
    score = result.get("verification", {}).get("score", 0)
    duration = result.get("total_duration_sec", 0)
    errors = result.get("errors", 0)
    st.markdown(
        f"**Workflow:** `{wf_id}` &nbsp; | &nbsp; "
        f"**Status:** {status_badge(status)} &nbsp; | &nbsp; "
        f"**Score:** {score}/100 &nbsp; | &nbsp; "
        f"**Errors:** {errors} &nbsp; | &nbsp; "
        f"**Duration:** {duration}s"
    )
    esc = result.get("safety_checks", {}).get("escalation", {})
    if esc.get("escalate"):
        st.warning(f"Escalation triggered: {esc.get('reason', '')}")
    if errors > 0:
        st.warning(f"{errors} step(s) fell back or failed. See Audit Trail for details.")


def render_orchestration_trace(result: dict) -> None:
    with st.expander("Detail Eksekusi Pipeline (Audit Trail)", expanded=False):
        render_workflow_summary(result)
        st.markdown("#### 1. Rencana Eksekusi (Planner Agent)")
        for idx, step in enumerate(result.get("plan_steps", []), 1):
            st.markdown(f"{idx}. {step}")
        st.markdown("#### 2. Bukti & Fakta Rujukan (Retrieval Agent)")
        if result.get("evidence"):
            for ev in result["evidence"]:
                st.markdown(f"- **[{ev['category']}] `{ev['source']}`**: {ev['snippet']}")
        else:
            st.markdown("*(Tidak ada dokumen spesifik terdeteksi, menggunakan aturan default)*")
        st.markdown("#### 3. Hasil Audit Verifikasi (Critic / Verifier Agent)")
        score = result.get("verification", {}).get("score", 0)
        st.progress(min(score, 100) / 100)
        st.markdown(f"**Skor Kualitas Dokumen:** {score}/100")
        for finding in result.get("verification", {}).get("findings", []):
            st.markdown(f"- {finding}")
        st.markdown("#### 4. Audit Trail")
        for entry in result.get("audit_trail", []):
            agent = entry.get("agent", "-")
            status = entry.get("status", "-")
            dur = entry.get("duration_ms")
            st.markdown(f"- **{agent}** — {status_badge(status)} ({dur}ms)")


def render_pending_review(orch: AgentOrchestrator, result: dict) -> None:
    pending = result.get("memory_state", {}).get("pending_review")
    if not pending or pending.get("resolved"):
        return
    wf_id = result.get("workflow_id")
    st.error(f"⚠️ Workflow `{wf_id}` requires human review: {pending.get('trigger', '')}")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Approve result", key=f"approve_{wf_id}"):
            orch.memory.resolve_pending_review(wf_id, "APPROVED", "user")
            orch.audit_logger.log_human_override(wf_id, "approve_result",
                                                 "APPROVED", "user",
                                                 note=f"Score {pending.get('score')}")
            st.success("Approved. Workflow result accepted.")
            st.rerun()
    with col2:
        if st.button("❌ Reject / rerun", key=f"reject_{wf_id}"):
            orch.memory.resolve_pending_review(wf_id, "REJECTED", "user")
            orch.audit_logger.log_human_override(wf_id, "reject_result",
                                                 "REJECTED", "user",
                                                 note=f"Score {pending.get('score')}")
            st.warning("Rejected. You may rerun the task with adjusted parameters.")
            st.rerun()


# -- Sidebar -------------------------------------------------------------

with st.sidebar:
    st.markdown("### 🏢 PT Amsyong AI")
    st.caption("Karyawan Virtual (Aimployee) Ecosystem")
    st.markdown("---")

    menu = st.radio(
        "Pilih Modul:",
        [
            "🏠 Dashboard",
            "📑 Proposal Generator",
            "📊 Presentasi Generator",
            "📃 SOP Generator",
            "📈 Marketing & Tender",
            "🤖 AI Assistant",
            "📁 Knowledge Base",
            "🛡️ Safety & Governance",
            "📜 Audit Log Viewer",
            "🧠 Memory Viewer",
            "🧪 Compliance Audit",
        ],
    )

    st.markdown("---")
    st.markdown("#### Konfigurasi Otak AI")
    llm_provider = st.selectbox(
        "Provider LLM:",
        ["Offline Enterprise Engine", "Google Gemini", "OpenAI ChatGPT", "Anthropic Claude"],
        index=0,
    )
    api_key_input = st.text_input(
        "API Key (Opsional):", type="password",
        help="Masukkan API Key untuk menggunakan model cloud langsung.",
    )
    provider_map = {
        "Offline Enterprise Engine": "offline",
        "Google Gemini": "gemini",
        "OpenAI ChatGPT": "openai",
        "Anthropic Claude": "claude",
    }
    selected_provider = provider_map[llm_provider]

    orch = get_orchestrator(selected_provider, api_key_input)
    kb_engine = orch.kb_engine
    kb_stats = kb_engine.get_stats()
    st.caption(f"Knowledge Base: {kb_stats['total_documents']} Dokumen | {kb_stats['categories']} Kategori")

    # -- Safety settings panel ------------------------------------------
    st.markdown("---")
    with st.expander("🛡️ Safety Settings", expanded=False):
        cfg = orch.safety.get_config()
        new_threshold = st.slider(
            "Escalation threshold score", 0, 100,
            int(cfg.get("escalation_threshold_score", 50)),
            help="Workflows scoring below this value trigger human review.",
        )
        new_budget = st.number_input(
            "Max tool calls / workflow", 1, 200,
            int(cfg.get("max_tool_calls_per_workflow", 20)),
        )
        new_duration = st.number_input(
            "Max workflow duration (seconds)", 10, 3600,
            int(cfg.get("max_workflow_duration_seconds", 300)),
        )
        block_sensitive = st.checkbox(
            "Block prompts containing sensitive data",
            value=bool(cfg.get("block_sensitive_data_in_prompts", True)),
        )
        if st.button("💾 Save safety settings"):
            orch.safety.update_config("escalation_threshold_score", new_threshold)
            orch.safety.update_config("max_tool_calls_per_workflow", new_budget)
            orch.safety.update_config("max_workflow_duration_seconds", new_duration)
            orch.safety.update_config("block_sensitive_data_in_prompts", block_sensitive)
            st.success("Safety settings saved.")
        st.caption("Settings persist in `memory/safety_policy.json`.")


# ========================================================================
# PAGE: Dashboard
# ========================================================================
if menu == "🏠 Dashboard":
    st.markdown('<div class="main-header">PT Amsyong AI - Ruang Kerja Karyawan Virtual</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Platform otomasi dokumen, presentasi, SOP, dan intelijen bisnis berbasis Knowledge Base internal.</div>', unsafe_allow_html=True)

    summary = orch.audit_logger.get_log_summary(persisted=True)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="metric-card"><h3>📄 5+</h3><p>Modul Aktif</p></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="metric-card"><h3>📚 {kb_stats["total_documents"]}</h3><p>Dokumen Knowledge</p></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="metric-card"><h3>🧠 {selected_provider.upper()}</h3><p>Engine AI</p></div>', unsafe_allow_html=True)
    with col4:
        ok = summary.get("SUCCESS", 0)
        warn = summary.get("WARNING", 0) + summary.get("ESCALATED", 0)
        fail = summary.get("FAILED", 0) + summary.get("BLOCKED", 0)
        if fail > 0:
            badge = f'<span class="status-FAILED">{fail} failed</span>'
        elif warn > 0:
            badge = f'<span class="status-WARNING">{warn} warnings</span>'
        else:
            badge = f'<span class="status-SUCCESS">{ok} OK</span>'
        st.markdown(f'<div class="metric-card"><h3>🛡️ {badge}</h3><p>Audit Trail (live)</p></div>', unsafe_allow_html=True)

    st.markdown("### Aksi Cepat (Quick Launch)")
    qcol1, qcol2, qcol3 = st.columns(3)
    with qcol1:
        if st.button("📑 Buat Proposal Sistem Dokumen Digital", use_container_width=True):
            st.session_state["quick_task"] = "proposal_dokumen"
            st.info("Buka modul '📑 Proposal Generator' untuk melihat draft yang disiapkan.")
    with qcol2:
        if st.button("📊 Buat Presentasi Sistem Manajemen Dokumen", use_container_width=True):
            st.session_state["quick_task"] = "presentation_doc"
            st.info("Buka modul '📊 Presentasi Generator' untuk melihat slide yang disiapkan.")
    with qcol3:
        if st.button("📃 Buat SOP Pengadaan Barang dan Jasa", use_container_width=True):
            st.session_state["quick_task"] = "sop_proc"
            st.info("Buka modul '📃 SOP Generator' untuk melihat SOP yang disiapkan.")

    st.markdown("---")
    st.markdown("### Ringkasan Profil dan Layanan PT Amsyong (Data Simulasi)")
    docs = kb_engine.search("Profil PT Amsyong 2026", top_k=2)
    for d in docs:
        with st.expander(f"📌 {d['file_name']} ({d['category']})"):
            st.markdown(d["content"][:600] + "...")


# ========================================================================
# PAGE: Proposal Generator
# ========================================================================
elif menu == "📑 Proposal Generator":
    st.markdown('<div class="main-header">📑 Generator Proposal Otomatis</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Menyusun proposal komersial dan teknis lengkap berdasar portofolio perusahaan dan menghasilkan file Word (.docx).</div>', unsafe_allow_html=True)

    default_client = "PT Contoh Mitra Nusantara" if st.session_state.get("quick_task") == "proposal_dokumen" else ""
    default_title = "Pengadaan dan Implementasi Sistem Manajemen Dokumen Digital" if st.session_state.get("quick_task") == "proposal_dokumen" else ""

    with st.form("proposal_form"):
        col1, col2 = st.columns(2)
        with col1:
            client_name = st.text_input("Nama Klien / Calon Pengguna:", value=default_client)
            project_title = st.text_input("Judul Proyek / Pengadaan:", value=default_title)
        with col2:
            scope_select = st.multiselect(
                "Lingkup Layanan Terkait:",
                ["Sistem Manajemen Dokumen", "Pengadaan Komputer Desktop",
                 "Infrastruktur Jaringan LAN", "Digitalisasi Arsip", "Pelatihan Pengguna"],
                default=["Sistem Manajemen Dokumen", "Pelatihan Pengguna"],
            )
            timeline_weeks = st.number_input("Estimasi Durasi (Minggu):", min_value=1, max_value=52, value=5)
        user_reqs = st.text_area("Deskripsi Kebutuhan Khusus / Syarat KAK:")
        submit_proposal = st.form_submit_button("🚀 Jalankan Agen Pembuat Proposal", use_container_width=True)

    if submit_proposal:
        if not project_title:
            st.error("Silakan isi Judul Proyek terlebih dahulu.")
        else:
            with st.spinner("Basic Pipeline Orchestrator sedang bekerja..."):
                extra_params = {
                    "client_name": client_name or "Klien Mitra PT Amsyong",
                    "project_title": project_title,
                    "scope": scope_select,
                    "timeline_weeks": timeline_weeks,
                }
                combined_prompt = (f"Buat proposal teknis dan komersial untuk {client_name}. "
                                   f"Judul Proyek: {project_title}. Lingkup: {', '.join(scope_select)}. "
                                   f"Durasi: {timeline_weeks} minggu. Catatan khusus: {user_reqs}")
                result = orch.execute_workflow("proposal", combined_prompt, extra_params)
                render_workflow_summary(result)
                render_pending_review(orch, result)
                if result.get("artifact") and os.path.exists(result["artifact"]["file_path"]):
                    with open(result["artifact"]["file_path"], "rb") as f:
                        file_bytes = f.read()
                    st.download_button(
                        label=f"📥 {result['artifact']['download_label']}",
                        data=file_bytes, file_name=result["artifact"]["file_name"],
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                    )
                st.markdown("### Pratinjau Isi Proposal")
                st.markdown(result["final_content"])
                render_orchestration_trace(result)


# ========================================================================
# PAGE: Presentation Generator
# ========================================================================
elif menu == "📊 Presentasi Generator":
    st.markdown('<div class="main-header">📊 Generator Presentasi Slide (PowerPoint)</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Menyusun slide deck presentasi bisnis terstruktur dan menghasilkan file PowerPoint (.pptx).</div>', unsafe_allow_html=True)

    default_topic = "Solusi AI CRM dan Otomasi Internal PT Amsyong" if st.session_state.get("quick_task") == "presentation_crm" else ""
    with st.form("pres_form"):
        pres_title = st.text_input("Judul Presentasi:", value=default_topic)
        pres_audience = st.text_input("Target Audiens:")
        slide_count = st.slider("Jumlah Slide yang Diinginkan:", min_value=3, max_value=12, value=5)
        pres_points = st.text_area("Poin-Poin Utama yang Harus Dibahas:")
        submit_pres = st.form_submit_button("🚀 Jalankan Agen Pembuat Presentasi", use_container_width=True)

    if submit_pres:
        if not pres_title:
            st.error("Silakan isi Judul Presentasi.")
        else:
            with st.spinner("Basic Pipeline Orchestrator sedang merancang alur slide..."):
                extra_params = {
                    "project_title": pres_title,
                    "client_name": pres_audience or "Audiens Eksekutif",
                    "slide_count": slide_count,
                }
                combined_prompt = (f"Buatkan presentasi sebanyak {slide_count} slide tentang: {pres_title}. "
                                   f"Target audiens: {pres_audience}. Poin utama: {pres_points}")
                result = orch.execute_workflow("presentation", combined_prompt, extra_params)
                render_workflow_summary(result)
                render_pending_review(orch, result)
                if result.get("artifact") and os.path.exists(result["artifact"]["file_path"]):
                    with open(result["artifact"]["file_path"], "rb") as f:
                        file_bytes = f.read()
                    st.download_button(
                        label=f"📥 {result['artifact']['download_label']}",
                        data=file_bytes, file_name=result["artifact"]["file_name"],
                        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        use_container_width=True,
                    )
                st.markdown("### Rincian Materi Slide")
                st.markdown(result["final_content"])
                render_orchestration_trace(result)


# ========================================================================
# PAGE: SOP Generator
# ========================================================================
elif menu == "📃 SOP Generator":
    st.markdown('<div class="main-header">📃 Generator Standar Operasional Prosedur (SOP)</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Membuat dokumen SOP formal berstandar ISO dan administrasi rapi lengkap dengan file Word (.docx).</div>', unsafe_allow_html=True)

    default_sop = "SOP Pengadaan Barang dan Jasa" if st.session_state.get("quick_task") == "sop_proc" else ""
    with st.form("sop_form"):
        sop_title = st.text_input("Judul SOP:", value=default_sop)
        department = st.selectbox("Departemen / Divisi Pelaksana:",
                                  ["Pengadaan & Logistik", "Keuangan & Administrasi",
                                   "Teknologi Informasi", "Operasional"])
        safety_rules = st.text_area("Instruksi / Batasan Khusus:")
        submit_sop = st.form_submit_button("🚀 Jalankan Agen Pembuat SOP", use_container_width=True)

    if submit_sop:
        if not sop_title:
            st.error("Silakan isi Judul SOP.")
        else:
            with st.spinner("Basic Pipeline Orchestrator sedang memverifikasi standar administrasi..."):
                extra_params = {"project_title": sop_title, "department": department}
                combined_prompt = (f"Buatkan SOP resmi PT Amsyong untuk: {sop_title}. "
                                   f"Departemen: {department}. Ketentuan khusus: {safety_rules}")
                result = orch.execute_workflow("sop", combined_prompt, extra_params)
                render_workflow_summary(result)
                render_pending_review(orch, result)
                if result.get("artifact") and os.path.exists(result["artifact"]["file_path"]):
                    with open(result["artifact"]["file_path"], "rb") as f:
                        file_bytes = f.read()
                    st.download_button(
                        label=f"📥 {result['artifact']['download_label']}",
                        data=file_bytes, file_name=result["artifact"]["file_name"],
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                    )
                st.markdown("### Isi Standar Operasional Prosedur")
                st.markdown(result["final_content"])
                render_orchestration_trace(result)


# ========================================================================
# PAGE: Marketing & Tender
# ========================================================================
elif menu == "📈 Marketing & Tender":
    st.markdown('<div class="main-header">📈 Asisten Intelijen Pemasaran & Tender</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Menganalisis dokumen tender, menyusun Unique Selling Proposition (USP), dan strategi memenangkan penawaran.</div>', unsafe_allow_html=True)

    tender_prompt = st.text_area("Masukkan Info / Persyaratan Tender:")
    comp_context = st.text_input("Kompetitor yang Diketahui (Opsional):")
    if st.button("🚀 Analisis Persyaratan & Susun Strategi Menang", use_container_width=True):
        if not tender_prompt:
            st.warning("Silakan masukkan ringkasan syarat tender.")
        else:
            with st.spinner("Agen Marketing & Analis Tender sedang mengevaluasi..."):
                combined = (f"Analisis tender berikut: {tender_prompt}. "
                            f"Konteks kompetitor: {comp_context}. "
                            f"Susun keunggulan PT Amsyong dan strategi pemenangan.")
                result = orch.execute_workflow("marketing", combined, {"category_filter": "Tender"})
                render_workflow_summary(result)
                render_pending_review(orch, result)
                st.markdown("### Analisis Strategi Penawaran Tender")
                st.markdown(result["final_content"])
                render_orchestration_trace(result)


# ========================================================================
# PAGE: AI Assistant
# ========================================================================
elif menu == "🤖 AI Assistant":
    st.markdown('<div class="main-header">🤖 Aimployee - Asisten Karyawan Virtual PT Amsyong</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Tanya jawab langsung seputar dokumen perusahaan, proyek, spesifikasi teknis, atau minta bantuan analisis.</div>', unsafe_allow_html=True)

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = [
            {"role": "assistant",
             "content": "Halo! Saya adalah Karyawan Virtual PT Amsyong (Aimployee). "
                        "Saya siap membantu Anda membuat proposal, presentasi, SOP, "
                        "atau mencari informasi dari Knowledge Base internal perusahaan."},
        ]
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    user_query = st.chat_input("Tanyakan apa saja seputar PT Amsyong...")
    if user_query:
        st.session_state.chat_history.append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.markdown(user_query)
        with st.chat_message("assistant"):
            with st.spinner("Aimployee sedang mencari data di Knowledge Base..."):
                result = orch.execute_workflow("assistant", user_query)
                answer = result["final_content"]
                st.markdown(answer)
                st.session_state.chat_history.append({"role": "assistant", "content": answer})
                render_workflow_summary(result)
                render_pending_review(orch, result)
                render_orchestration_trace(result)


# ========================================================================
# PAGE: Knowledge Base
# ========================================================================
elif menu == "📁 Knowledge Base":
    st.markdown('<div class="main-header">📁 Eksplorasi & Manajemen Knowledge Base</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Kelola perpustakaan dokumen digital PT Amsyong yang menjadi sumber fakta Karyawan AI.</div>', unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["🔍 Cari & Telusuri Dokumen", "📤 Tambah Dokumen Baru", "📊 Status Indeks"])
    with tab1:
        cat_filter = st.selectbox("Filter Kategori:", kb_engine.get_categories())
        search_kw = st.text_input("Kata Kunci Pencarian:")
        results = kb_engine.search(search_kw, category_filter=cat_filter, top_k=10)
        st.write(f"Menampilkan {len(results)} dokumen:")
        for r in results:
            with st.expander(f"📄 {r['file_name']} | Kategori: {r['category']}"):
                st.caption(f"Lokasi: {r['rel_path']} | Ukuran: {r['size_bytes']} bytes")
                st.text_area("Konten:", value=r["content"], height=250, disabled=True,
                             key=f"kb_view_{r['rel_path']}")
    with tab2:
        st.markdown("#### Tambah Dokumen ke Knowledge Base")
        new_cat = st.selectbox(
            "Pilih Kategori Tujuan:",
            ["Company_Profile", "Services", "Projects", "Clients/Enterprise_Simulasi",
             "Clients/Government", "Clients/BUMN", "Clients/Private", "SOP", "Proposal",
             "Competitors", "Templates"],
        )
        uploaded_file = st.file_uploader(
            "Pilih Berkas (Markdown .md, Teks .txt, Word .docx, atau PDF .pdf):",
            type=["md", "txt", "docx", "pdf"],
        )
        if uploaded_file is not None:
            target_sub = os.path.join(KB_DIR, new_cat.replace("/", os.sep))
            os.makedirs(target_sub, exist_ok=True)
            target_path = os.path.join(target_sub, uploaded_file.name)
            if st.button("💾 Simpan Berkas ke Knowledge Base"):
                with open(target_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                kb_engine.refresh_index()
                st.success(f"Berkas '{uploaded_file.name}' berhasil disimpan ke '{new_cat}'.")
    with tab3:
        st.markdown("#### Ringkasan Indeks Knowledge Base")
        stats = kb_engine.get_stats()
        st.json(stats)
        if st.button("🔄 Segarkan / Re-index Seluruh Dokumen"):
            kb_engine.refresh_index()
            st.success("Indeks Knowledge Base berhasil diperbarui!")


# ========================================================================
# PAGE: Safety & Governance
# ========================================================================
elif menu == "🛡️ Safety & Governance":
    st.markdown('<div class="main-header">🛡️ Safety & Governance Dashboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Live view of the safety governor configuration, override log, and policy checks.</div>', unsafe_allow_html=True)

    cfg = orch.safety.get_config()
    st.markdown("### Current Safety Configuration")
    cfg_rows = [
        {"Setting": "Max tool calls / workflow", "Value": cfg.get("max_tool_calls_per_workflow"),
         "Keterangan": "Batas maksimal pemanggilan tool dalam 1 workflow"},
        {"Setting": "Max retries / tool", "Value": cfg.get("max_retries_per_tool"),
         "Keterangan": "Batas percobaan ulang jika tool gagal"},
        {"Setting": "Max workflow duration (detik)", "Value": cfg.get("max_workflow_duration_seconds"),
         "Keterangan": "Batas durasi total 1 workflow"},
        {"Setting": "Escalation threshold score", "Value": cfg.get("escalation_threshold_score"),
         "Keterangan": "Skor di bawah nilai ini memicu tinjauan manual"},
        {"Setting": "Allowed output formats", "Value": ", ".join(cfg.get("allowed_output_formats", [])),
         "Keterangan": "Format file yang boleh dihasilkan"},
        {"Setting": "Restricted actions", "Value": ", ".join(cfg.get("restricted_actions", [])),
         "Keterangan": "Aksi yang TIDAK boleh dilakukan otomatis"},
        {"Setting": "Require human confirmation", "Value": ", ".join(cfg.get("require_human_confirmation_for", [])),
         "Keterangan": "Aksi yang butuh persetujuan Anda"},
        {"Setting": "Block sensitive prompts", "Value": "YA" if cfg.get("block_sensitive_data_in_prompts") else "TIDAK",
         "Keterangan": "Blokir prompt yang mengandung data sensitif"},
    ]
    st.dataframe(cfg_rows, use_container_width=True, hide_index=True)

    st.markdown("### Policy Memory File")
    policy_path = os.path.join(MEMORY_DIR, "safety_policy.json")
    if os.path.isfile(policy_path):
        with open(policy_path, "r", encoding="utf-8") as f:
            st.code(f.read(), language="json")
    else:
        st.info("No persisted policy yet. Change a setting in the sidebar to create it.")

    st.markdown("### Human Override Log")
    overrides = orch.safety.get_override_log()
    if overrides:
        override_rows = [
            {"Waktu": o.get("timestamp", "-"),
             "Aksi": o.get("action", "-"),
             "Disetujui oleh": o.get("approved_by", "-")}
            for o in overrides
        ]
        st.dataframe(override_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No overrides recorded yet.")

    st.markdown("### Pending Reviews")
    pending = orch.memory._pending_reviews
    if pending:
        pending_rows = []
        for wf_id, v in pending.items():
            pending_rows.append({
                "Workflow ID": wf_id,
                "Status": "Selesai" if v.get("resolved") else "Menunggu",
                "Pemicu": v.get("trigger", "-"),
                "Skor": v.get("score", "-"),
                "Jumlah Error": v.get("error_count", 0),
                "Keputusan": v.get("decision", "-"),
            })
        st.dataframe(pending_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No pending reviews.")


# ========================================================================
# PAGE: Audit Log Viewer
# ========================================================================
elif menu == "📜 Audit Log Viewer":
    st.markdown('<div class="main-header">📜 Audit Log Viewer</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Browse the persisted audit trail. Secrets are redacted automatically.</div>', unsafe_allow_html=True)

    summary = orch.audit_logger.get_log_summary(persisted=True)
    st.markdown("#### Summary")
    summary_rows = [{"Status": k, "Jumlah": v} for k, v in summary.items()]
    st.dataframe(summary_rows, use_container_width=True, hide_index=True)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kw = st.text_input("Keyword")
    with col2:
        agent_filter = st.text_input("Agent")
    with col3:
        status_filter = st.selectbox("Status", ["(any)", "SUCCESS", "WARNING", "FAILED",
                                                "ESCALATED", "BLOCKED"])
    with col4:
        type_filter = st.selectbox("Type", ["(any)", "EVENT", "POLICY_CHECK",
                                            "ESCALATION", "FAILURE", "HUMAN_OVERRIDE"])
    limit = st.slider("Limit", 10, 200, 50)
    entries = orch.audit_logger.search_logs(
        keyword=kw or None,
        agent_filter=agent_filter or None,
        status_filter=status_filter if status_filter != "(any)" else None,
        type_filter=type_filter if type_filter != "(any)" else None,
        limit=limit,
    )
    st.write(f"Menampilkan {len(entries)} entri (terbaru di atas).")
    if entries:
        table_rows = []
        for e in entries:
            table_rows.append({
                "Waktu": (e.get("timestamp") or "-")[:19].replace("T", " "),
                "Tipe": e.get("type", "-"),
                "Workflow": e.get("workflow_id", "-"),
                "Agen": e.get("agent") or e.get("policy_name") or e.get("from_agent", "-"),
                "Aksi": e.get("action", "-"),
                "Status": e.get("status", "-"),
                "Durasi (ms)": e.get("duration_ms", "-"),
                "Keterangan": (e.get("details") or e.get("reason")
                               or e.get("error_message") or "-")[:120],
            })
        st.dataframe(table_rows, use_container_width=True, hide_index=True)
    else:
        st.info("Tidak ada entri yang cocok dengan filter.")


# ========================================================================
# PAGE: Memory Viewer
# ========================================================================
elif menu == "🧠 Memory Viewer":
    st.markdown('<div class="main-header">🧠 Memory Viewer</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Inspect long-term memory, tool reliability stats, and workflow history.</div>', unsafe_allow_html=True)

    lt = orch.memory.get_all_long_term()
    workflow_history = {k: v for k, v in lt.items() if v.get("category") == "workflow_history"}
    tool_memory = {k: v for k, v in lt.items() if v.get("category") == "tool_memory"}
    other = {k: v for k, v in lt.items() if v.get("category") not in ("workflow_history", "tool_memory")}

    st.markdown(f"### Workflow History ({len(workflow_history)})")
    if workflow_history:
        wf_rows = []
        for k, v in workflow_history.items():
            val = v.get("value") if isinstance(v, dict) else v
            if isinstance(val, dict):
                wf_rows.append({
                    "Workflow ID": k.replace("workflow_", ""),
                    "Tipe Tugas": val.get("task_type", "-"),
                    "Status": val.get("status", "-"),
                    "Skor": val.get("score", "-"),
                    "Durasi (detik)": val.get("duration", "-"),
                    "Jumlah Error": val.get("errors", 0),
                    "Waktu": _ts_to_str(v.get("timestamp")),
                })
        if wf_rows:
            st.dataframe(wf_rows, use_container_width=True, hide_index=True)
        else:
            st.json(workflow_history)
    else:
        st.info("No workflow history yet.")

    st.markdown(f"### Tool Memory ({len(tool_memory)})")
    if tool_memory:
        rows = []
        for k, v in tool_memory.items():
            val = v.get("value") if isinstance(v, dict) else v
            if isinstance(val, dict):
                calls = val.get("calls", 0)
                failures = val.get("failures", 0)
                total_ms = val.get("total_ms", 0)
                reliability = (calls - failures) / calls * 100 if calls else 0
                rows.append({"tool": k.replace("tool_", ""), "calls": calls,
                             "failures": failures, "total_ms": round(total_ms, 1),
                             "reliability_pct": round(reliability, 1)})
        st.dataframe(rows)
    else:
        st.info("No tool memory yet. Run a workflow to populate it.")

    if other:
        st.markdown(f"### Other long-term entries ({len(other)})")
        other_rows = []
        for k, v in other.items():
            other_rows.append({
                "Kunci": k,
                "Kategori": v.get("category", "-") if isinstance(v, dict) else "-",
                "Nilai": str(v.get("value", "-"))[:120] if isinstance(v, dict) else str(v)[:120],
                "Waktu": _ts_to_str(v.get("timestamp") if isinstance(v, dict) else None),
            })
        st.dataframe(other_rows, use_container_width=True, hide_index=True)


# ========================================================================
# PAGE: Compliance Audit
# ========================================================================
elif menu == "🧪 Compliance Audit":
    st.markdown('<div class="main-header">🧪 Compliance Audit Mode</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Run live probes against the basic orchestration stack. Every claim is VERIFIED / UNVERIFIED / NOT RUN.</div>', unsafe_allow_html=True)

    if st.button("🚀 Run Compliance Audit", use_container_width=True):
        with st.spinner("Probing basic orchestrator components in a sandbox..."):
            auditor = ComplianceAuditor(BASE_DIR)
            report = auditor.run()
        st.markdown(f"**Duration:** {report['duration_sec']}s &nbsp; | &nbsp; "
                    f"**Report:** `{report['report_path']}`")
        st.markdown("#### Summary")
        st.json(report["summary"])
        st.markdown("#### Findings")
        st.dataframe([
            {"component": c, "claim": cl, "status": s, "detail": d}
            for c, cl, s, d in report["findings"]
        ])
        # Offer the markdown report as download
        if os.path.isfile(report["report_path"]):
            with open(report["report_path"], "r", encoding="utf-8") as f:
                st.download_button("📥 Download full report (.md)", f.read(),
                                   file_name=os.path.basename(report["report_path"]),
                                   mime="text/markdown", use_container_width=True)

    st.markdown("### Previous Reports")
    reports = sorted(glob.glob(os.path.join(BASE_DIR, "logs", "compliance_reports", "*.md")),
                     key=os.path.getmtime, reverse=True)
    if reports:
        for rp in reports[:5]:
            with open(rp, "r", encoding="utf-8") as f:
                content = f.read()
            with st.expander(os.path.basename(rp)):
                st.markdown(content)
    else:
        st.info("No reports yet. Click the button above to run one.")
