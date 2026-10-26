"""Structured, persistent audit trail for the basic orchestration framework.

Every entry is written twice:
- ``logs/audit_trail.jsonl``: machine-readable, used for replay and search.
- ``logs/audit.log``: human-readable via Python ``logging`` (rotating).
Secrets are redacted before anything touches disk.
"""

import json
import logging
import os
import threading
from collections import Counter
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional

from .redaction import redact

_FILE_LOCK = threading.Lock()


class AuditLogger:
    def __init__(self, log_dir: str) -> None:
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self._session_entries: List[Dict[str, Any]] = []
        self.jsonl_path = os.path.join(self.log_dir, "audit_trail.jsonl")
        self.text_log_path = os.path.join(self.log_dir, "audit.log")
        self._py_logger = self._build_python_logger()

    def _build_python_logger(self) -> logging.Logger:
        # One logger per log file; Streamlit reruns must not stack duplicate handlers.
        name = "amsyong.audit." + os.path.abspath(self.text_log_path).replace(".", "_")
        py_logger = logging.getLogger(name)
        py_logger.setLevel(logging.INFO)
        py_logger.propagate = False
        if not py_logger.handlers:
            handler = RotatingFileHandler(
                self.text_log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s"))
            py_logger.addHandler(handler)
        return py_logger

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _record(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        entry = redact(entry)
        self._session_entries.append(entry)
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _FILE_LOCK:
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        self._write_text_line(entry)
        return entry

    def _write_text_line(self, entry: Dict[str, Any]) -> None:
        status = str(entry.get("status", ""))
        level = logging.INFO
        if status in ("WARNING", "NEEDS_REVISION", "ESCALATED", "BLOCKED", "PENDING_REVIEW"):
            level = logging.WARNING
        elif status in ("FAILED", "ABORTED"):
            level = logging.ERROR
        actor = entry.get("agent") or entry.get("policy_name") or entry.get("from_agent") or "-"
        what = entry.get("action") or entry.get("trigger") or entry.get("error_type") or entry.get("type")
        detail = entry.get("details") or entry.get("reason") or entry.get("error_message") or ""
        self._py_logger.log(
            level, f"{entry.get('workflow_id', '-')} | {entry.get('type')} | {actor} | {what} | {status} | {detail}"
        )

    def log_event(
        self,
        workflow_id: str,
        agent_name: str,
        action: str,
        status: str,
        details: Optional[str] = None,
        duration_ms: Optional[float] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        entry = {
            "timestamp": self._timestamp(),
            "type": "EVENT",
            "workflow_id": workflow_id,
            "agent": agent_name,
            "action": action,
            "status": status,
            "details": details,
            "duration_ms": duration_ms,
        }
        if data:
            entry["data"] = data
        return self._record(entry)

    def log_policy_check(
        self,
        workflow_id: str,
        policy_name: str,
        passed: bool,
        reason: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        entry = {
            "timestamp": self._timestamp(),
            "type": "POLICY_CHECK",
            "workflow_id": workflow_id,
            "policy_name": policy_name,
            "passed": passed,
            "status": "SUCCESS" if passed else "BLOCKED",
            "reason": reason,
        }
        if data:
            entry["data"] = data
        return self._record(entry)

    def log_escalation(
        self, workflow_id: str, trigger: str, from_agent: str, to_agent: str, reason: str
    ) -> Dict[str, Any]:
        return self._record({
            "timestamp": self._timestamp(),
            "type": "ESCALATION",
            "workflow_id": workflow_id,
            "trigger": trigger,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "reason": reason,
            "status": "ESCALATED",
        })

    def log_failure(
        self,
        workflow_id: str,
        agent_name: str,
        error_type: str,
        error_message: str,
        recovery_action: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._record({
            "timestamp": self._timestamp(),
            "type": "FAILURE",
            "workflow_id": workflow_id,
            "agent": agent_name,
            "error_type": error_type,
            "error_message": error_message,
            "recovery_action": recovery_action,
            "status": "FAILED",
        })

    def log_human_override(
        self, workflow_id: str, action: str, decision: str, actor: str, note: Optional[str] = None
    ) -> Dict[str, Any]:
        return self._record({
            "timestamp": self._timestamp(),
            "type": "HUMAN_OVERRIDE",
            "workflow_id": workflow_id,
            "agent": f"Human ({actor})",
            "action": action,
            "decision": decision,
            "details": note,
            "status": "SUCCESS" if decision == "APPROVED" else "WARNING",
        })

    def get_session_log(self) -> List[Dict[str, Any]]:
        return list(self._session_entries)

    def _read_all(self) -> List[Dict[str, Any]]:
        if not os.path.isfile(self.jsonl_path):
            return []
        entries = []
        with _FILE_LOCK:
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def get_log_summary(self, persisted: bool = False) -> Dict[str, int]:
        """Count entries by status for this session, or for the whole persisted trail."""
        entries = self._read_all() if persisted else self._session_entries
        counts = Counter(str(e.get("status", "UNKNOWN")) for e in entries)
        summary = {k: 0 for k in ("SUCCESS", "WARNING", "FAILED", "ESCALATED", "BLOCKED")}
        summary.update(counts)
        return summary

    def get_workflow_trail(self, workflow_id: str) -> List[Dict[str, Any]]:
        """Replay every persisted entry of one workflow in chronological order."""
        return [e for e in self._read_all() if e.get("workflow_id") == workflow_id]

    def search_logs(
        self,
        keyword: Optional[str] = None,
        agent_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        type_filter: Optional[str] = None,
        limit: int = 50,
        newest_first: bool = True,
    ) -> List[Dict[str, Any]]:
        entries = self._read_all()
        if newest_first:
            entries.reverse()
        results = []
        for entry in entries:
            if agent_filter:
                actor = entry.get("agent") or entry.get("from_agent") or entry.get("policy_name") or ""
                if agent_filter.lower() not in str(actor).lower():
                    continue
            if status_filter and str(entry.get("status", "")).upper() != status_filter.upper():
                continue
            if type_filter and str(entry.get("type", "")).upper() != type_filter.upper():
                continue
            if keyword and keyword.lower() not in json.dumps(entry, ensure_ascii=False).lower():
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results
