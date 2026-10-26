# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Gap-closure release (2026-10-26)
Source: `audit_report.txt` vs `ASI — AGENT ORCHESTRATION.txt`.

#### Added
- `core/redaction.py` — shared secret detection and redaction (OpenAI, Anthropic, Google, URL credentials, assigned secrets).
- `core/compliance_auditor.py` — built-in audit mode with sandbox probes and VERIFIED/UNVERIFIED/NOT RUN labels.
- `core/tool_agent.py` — rewritten with SafetyGovernor gating, output validators, tool-memory recording.
- `core/audit_logger.py` — rewritten with JSONL + rotating `audit.log`, secret redaction, workflow trail replay.
- `core/safety_governor.py` — rewritten with persisted policy file, governor-aware logging, human-override queue.
- `core/memory_manager.py` — rewritten with atomic long-term writes, size pruning, pending reviews, contamination detection.
- `core/llm_client.py` — tracks `last_error` and `last_provider_used`; cloud errors no longer pollute content.
- `core/orchestrator.py` — rewritten control flow: routing validation, retry, critic revision loop, escalation, human override, honest status.
- `app.py` — new pages: 🛡️ Safety & Governance, 📜 Audit Log Viewer, 🧠 Memory Viewer, 🧪 Compliance Audit. Sidebar Safety Settings panel. Dashboard metric now reads live audit log.
- `tests/test_orchestration.py` — 30 unit tests covering every module.

#### Changed
- Critic score floor of 40 removed; score now reflects actual quality.
- Orchestrator artifact assembly now routes through ToolAgent (was calling `generate_docx`/`generate_pptx` directly).
- Workflow status is now one of `SUCCESS`, `PASSED_NO_ARTIFACT`, `WARNING`, `ESCALATED`, `FAILED`, `REJECTED` — no more fabricated "PASSED" badges.
- Success messages in UI are now conditional on actual workflow status.

#### Fixed
- `SafetyGovernor._log` was silently broken (wrong keyword args); now routes correctly through `AuditLogger.log_event`.
- Gemini API key no longer leaked into logs via exception messages (moved to URL params, redacted).
- Streamlit reruns no longer stack duplicate Python logging handlers (logger keyed by absolute log path).
