# Changelog

All notable changes to this project will be documented in this file.

#### Fixed
- `SafetyGovernor._log` was silently broken (wrong keyword args); now routes correctly through `AuditLogger.log_event`.
- Gemini API key no longer leaked into logs via exception messages (moved to URL params, redacted).
- Streamlit reruns no longer stack duplicate Python logging handlers (logger keyed by absolute log path).
