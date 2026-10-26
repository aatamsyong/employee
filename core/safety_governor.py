"""Safety governor: policy checks, tool authorization, escalation, human override.

Config is persisted in ``memory/safety_policy.json`` so UI changes survive
Streamlit reruns. Every decision is routed through the AuditLogger with
secrets redacted.
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .redaction import find_secrets, redact_text

_FILE_LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "max_tool_calls_per_workflow": 20,
    "max_retries_per_tool": 3,
    "max_workflow_duration_seconds": 300,
    "allowed_output_formats": ["docx", "pptx", "txt", "json"],
    "restricted_actions": ["delete_file", "send_email", "api_external_call"],
    "require_human_confirmation_for": ["delete_file", "send_email"],
    "sensitive_data_patterns": [
        "password", "api_key", "secret", "token", "credential",
    ],
    "escalation_threshold_score": 50,
    "allowed_tools": None,
    "block_sensitive_data_in_prompts": True,
}


class SafetyGovernor:
    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 logger=None, memory_dir: Optional[str] = None) -> None:
        self._config = dict(DEFAULT_CONFIG)
        self._logger = logger
        self._override_log: List[Dict[str, Any]] = []
        self._policy_path = os.path.join(memory_dir, "safety_policy.json") if memory_dir else None
        self._load_persisted_config()
        if config:
            self._config.update(config)
            self._persist_config()

    def _load_persisted_config(self) -> None:
        if not self._policy_path or not os.path.isfile(self._policy_path):
            return
        try:
            with _FILE_LOCK, open(self._policy_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for k, v in saved.items():
                    if k in DEFAULT_CONFIG:
                        self._config[k] = v
        except Exception:
            pass

    def _persist_config(self) -> None:
        if not self._policy_path:
            return
        os.makedirs(os.path.dirname(self._policy_path), exist_ok=True)
        with _FILE_LOCK:
            with open(self._policy_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)

    def _log(self, event_type: str, data: Dict[str, Any],
             workflow_id: str = "-", status: str = "SUCCESS") -> None:
        if self._logger is None:
            return
        try:
            self._logger.log_event(
                workflow_id=workflow_id,
                agent_name="SafetyGovernor",
                action=f"safety_governor.{event_type}",
                status=status,
                details=json.dumps(data, ensure_ascii=False, default=str)[:500],
            )
        except Exception:
            pass

    def get_config(self) -> Dict[str, Any]:
        return dict(self._config)

    def update_config(self, key: str, value: Any) -> None:
        self._config[key] = value
        self._persist_config()
        self._log("config_update", {"key": key, "value": value})

    def check_action_allowed(self, action_name: str) -> Dict[str, Any]:
        restricted = self._config.get("restricted_actions", [])
        if action_name in restricted:
            result = {"allowed": False, "reason": f"Action '{action_name}' is restricted."}
            self._log("check_action_allowed", {"action": action_name, **result}, status="BLOCKED")
            return result
        result = {"allowed": True, "reason": f"Action '{action_name}' allowed."}
        self._log("check_action_allowed", {"action": action_name, **result})
        return result

    def check_output_format(self, format_name: str) -> Dict[str, Any]:
        allowed = self._config.get("allowed_output_formats", [])
        fmt = format_name.lower().lstrip(".")
        if fmt in allowed:
            return {"allowed": True, "reason": f"Format '{fmt}' allowed."}
        return {"allowed": False, "reason": f"Format '{fmt}' not in {allowed}."}

    def check_tool_call_budget(self, current_count: int) -> Dict[str, Any]:
        max_calls = self._config.get("max_tool_calls_per_workflow", 20)
        remaining = max(0, max_calls - current_count)
        allowed = current_count < max_calls
        result = {"allowed": allowed, "remaining": remaining,
                  "reason": f"Tool calls: {current_count}/{max_calls}."}
        self._log("check_tool_call_budget", result,
                  status="SUCCESS" if allowed else "BLOCKED")
        return result

    def check_workflow_duration(self, elapsed_seconds: float) -> Dict[str, Any]:
        max_dur = self._config.get("max_workflow_duration_seconds", 300)
        remaining = max(0, int(max_dur - elapsed_seconds))
        allowed = elapsed_seconds < max_dur
        result = {"allowed": allowed, "remaining": remaining,
                  "reason": f"Duration: {int(elapsed_seconds)}/{max_dur}s."}
        self._log("check_workflow_duration", result,
                  status="SUCCESS" if allowed else "WARNING")
        return result

    def scan_for_sensitive_data(self, text: str) -> Dict[str, Any]:
        patterns = self._config.get("sensitive_data_patterns", [])
        text_lower = text.lower()
        matched = [p for p in patterns if p.lower() in text_lower]
        regex_hits = find_secrets(text)
        all_hits = matched + regex_hits
        if all_hits:
            return {"found": True, "patterns_matched": all_hits,
                    "recommendation": "Sensitive data detected; redact before continuing."}
        return {"found": False, "patterns_matched": [],
                "recommendation": "No sensitive data detected."}

    def should_escalate(self, verification_score: float, error_count: int = 0) -> Dict[str, Any]:
        threshold = self._config.get("escalation_threshold_score", 50)
        max_retries = self._config.get("max_retries_per_tool", 3)
        reasons = []
        if verification_score < threshold:
            reasons.append(f"Verification score {verification_score} < {threshold}.")
        if error_count > max_retries:
            reasons.append(f"Errors {error_count} > retries {max_retries}.")
        escalate = len(reasons) > 0
        return {"escalate": escalate,
                "reason": " ".join(reasons) if reasons else "No escalation needed.",
                "recommended_action": "Escalate to user." if escalate else "Continue automatically."}

    def request_human_override(self, action_name: str, context: str) -> Dict[str, Any]:
        confirmation_list = self._config.get("require_human_confirmation_for", [])
        requires = action_name in confirmation_list
        return {"requires_override": requires, "action": action_name, "context": context}

    def approve_override(self, action_name: str, approved_by: str = "user") -> None:
        entry = {"action": action_name, "approved_by": approved_by,
                 "timestamp": datetime.now(timezone.utc).isoformat()}
        self._override_log.append(entry)
        self._log("approve_override", entry)

    def get_override_log(self) -> List[Dict[str, Any]]:
        return list(self._override_log)
