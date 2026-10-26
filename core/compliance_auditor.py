"""Built-in compliance audit mode.

Runs live probes against a sandbox basic orchestrator (temp dirs, offline LLM)
and produces a timestamped markdown report. Every claim is labeled
[VERIFIED] / [UNVERIFIED] / [NOT RUN] per the ASI spec.
"""

import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

from .audit_logger import AuditLogger
from .knowledge_base import KnowledgeBaseEngine
from .llm_client import LLMClient
from .memory_manager import MemoryManager
from .orchestrator import AgentOrchestrator
from .safety_governor import SafetyGovernor
from .tool_agent import ToolAgent


class ComplianceAuditor:
    def __init__(self, project_dir: str) -> None:
        self.project_dir = project_dir
        self.report_dir = os.path.join(project_dir, "logs", "compliance_reports")
        os.makedirs(self.report_dir, exist_ok=True)

    def run(self) -> Dict[str, Any]:
        started = time.time()
        findings: List[Tuple[str, str, str, str]] = []  # (component, claim, status, detail)
        sandbox = tempfile.mkdtemp(prefix="amsyong_audit_")
        try:
            findings.extend(self._probe_audit_logger(sandbox))
            findings.extend(self._probe_memory_manager(sandbox))
            findings.extend(self._probe_safety_governor(sandbox))
            findings.extend(self._probe_tool_agent(sandbox))
            findings.extend(self._probe_orchestrator(sandbox))
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

        report_path = self._write_report(findings, started)
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration_sec": round(time.time() - started, 3),
            "findings": findings,
            "report_path": report_path,
            "summary": self._summarize(findings),
        }

    # -- probes ----------------------------------------------------------

    def _probe_audit_logger(self, sandbox: str) -> List[Tuple[str, str, str, str]]:
        out: List[Tuple[str, str, str, str]] = []
        log_dir = os.path.join(sandbox, "logs")
        logger = AuditLogger(log_dir)
        logger.log_event("WF-TEST", "Probe", "Ping", "SUCCESS", details="probe")
        jsonl = os.path.join(log_dir, "audit_trail.jsonl")
        txt = os.path.join(log_dir, "audit.log")
        out.append(("AuditLogger", "Writes JSONL trail",
                    "VERIFIED" if os.path.isfile(jsonl) else "UNVERIFIED",
                    f"jsonl={os.path.isfile(jsonl)}"))
        out.append(("AuditLogger", "Writes human-readable audit.log",
                    "VERIFIED" if os.path.isfile(txt) else "UNVERIFIED",
                    f"txt={os.path.isfile(txt)}"))
        summary = logger.get_log_summary()
        out.append(("AuditLogger", "Session summary counters",
                    "VERIFIED" if summary.get("SUCCESS", 0) >= 1 else "UNVERIFIED",
                    json.dumps(summary)))
        return out

    def _probe_memory_manager(self, sandbox: str) -> List[Tuple[str, str, str, str]]:
        out: List[Tuple[str, str, str, str]] = []
        mem_dir = os.path.join(sandbox, "memory")
        mm = MemoryManager(mem_dir)
        mm.store_long_term("probe_key", {"hello": "world"}, category="probe")
        mm.set_short_term("WF-PROBE", "k", "v")
        out.append(("MemoryManager", "Long-term persistence round-trip",
                    "VERIFIED" if mm.retrieve_long_term("probe_key") == {"hello": "world"}
                    else "UNVERIFIED",
                    f"retrieved={mm.retrieve_long_term('probe_key')}"))
        out.append(("MemoryManager", "Short-term isolation per workflow",
                    "VERIFIED" if mm.get_short_term("WF-PROBE", "k") == "v"
                    and mm.get_short_term("WF-OTHER", "k") is None else "UNVERIFIED",
                    ""))
        mm.clear_short_term("WF-PROBE")
        out.append(("MemoryManager", "Short-term cleanup",
                    "VERIFIED" if mm.get_short_term("WF-PROBE", "k") is None
                    else "UNVERIFIED",
                    ""))
        return out

    def _probe_safety_governor(self, sandbox: str) -> List[Tuple[str, str, str, str]]:
        out: List[Tuple[str, str, str, str]] = []
        log_dir = os.path.join(sandbox, "logs")
        mem_dir = os.path.join(sandbox, "memory")
        logger = AuditLogger(log_dir)
        gov = SafetyGovernor(logger=logger, memory_dir=mem_dir)
        blocked = gov.check_action_allowed("delete_file")
        allowed = gov.check_action_allowed("generate_docx")
        out.append(("SafetyGovernor", "Restricts delete_file",
                    "VERIFIED" if not blocked["allowed"] else "UNVERIFIED",
                    json.dumps(blocked)))
        out.append(("SafetyGovernor", "Allows generate_docx",
                    "VERIFIED" if allowed["allowed"] else "UNVERIFIED",
                    json.dumps(allowed)))
        sensitive = gov.scan_for_sensitive_data("my api_key is sk-ant-AAAAAAAAAAAAAAAAAAAA")
        out.append(("SafetyGovernor", "Detects sensitive data",
                    "VERIFIED" if sensitive["found"] else "UNVERIFIED",
                    json.dumps(sensitive)))
        escalation = gov.should_escalate(verification_score=30)
        out.append(("SafetyGovernor", "Escalates on low verification score",
                    "VERIFIED" if escalation["escalate"] else "UNVERIFIED",
                    json.dumps(escalation)))
        return out

    def _probe_tool_agent(self, sandbox: str) -> List[Tuple[str, str, str, str]]:
        out: List[Tuple[str, str, str, str]] = []
        log_dir = os.path.join(sandbox, "logs")
        mem_dir = os.path.join(sandbox, "memory")
        logger = AuditLogger(log_dir)
        gov = SafetyGovernor(logger=logger, memory_dir=mem_dir)
        mem = MemoryManager(mem_dir)
        ta = ToolAgent(logger=logger, governor=gov, memory=mem)
        tools = [t["name"] for t in ta.list_tools()]
        out.append(("ToolAgent", "Registers built-in tools",
                    "VERIFIED" if {"generate_docx", "generate_pptx",
                                   "search_knowledge_base"} <= set(tools) else "UNVERIFIED",
                    json.dumps(tools)))
        unauthorized = ta.run_tool("nonexistent_tool")
        out.append(("ToolAgent", "Rejects unregistered tools",
                    "VERIFIED" if not unauthorized["success"] else "UNVERIFIED",
                    unauthorized.get("error", "")))
        return out

    def _probe_orchestrator(self, sandbox: str) -> List[Tuple[str, str, str, str]]:
        out: List[Tuple[str, str, str, str]] = []
        kb_dir = os.path.join(self.project_dir, "01_Knowledge_Base")
        kb = KnowledgeBaseEngine(kb_dir) if os.path.isdir(kb_dir) else None
        llm = LLMClient(provider="offline")
        if kb is None:
            out.append(("Orchestrator", "Knowledge base present for end-to-end probe",
                        "NOT RUN", "01_Knowledge_Base missing in sandbox."))
            return out
        orch = AgentOrchestrator(kb, llm, sandbox)
        result = orch.execute_workflow("assistant", "Siapa PT Amsyong?")
        out.append(("Orchestrator", "End-to-end workflow returns a result",
                    "VERIFIED" if isinstance(result, dict) and "workflow_id" in result
                    else "UNVERIFIED",
                    f"status={result.get('status')}"))
        out.append(("Orchestrator", "Audit trail recorded",
                    "VERIFIED" if result.get("audit_trail") else "UNVERIFIED",
                    f"entries={len(result.get('audit_trail', []))}"))
        return out

    # -- reporting -------------------------------------------------------

    @staticmethod
    def _summarize(findings: List[Tuple[str, str, str, str]]) -> Dict[str, int]:
        counts = {"VERIFIED": 0, "UNVERIFIED": 0, "NOT RUN": 0}
        for _, _, status, _ in findings:
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _write_report(self, findings: List[Tuple[str, str, str, str]],
                      started: float) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.report_dir, f"compliance_{ts}.md")
        summary = self._summarize(findings)
        lines = [
            f"# Compliance Audit Report — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"Duration: {round(time.time() - started, 3)}s",
            "",
            "## Summary",
            f"- VERIFIED: {summary['VERIFIED']}",
            f"- UNVERIFIED: {summary['UNVERIFIED']}",
            f"- NOT RUN: {summary['NOT RUN']}",
            "",
            "## Findings",
            "",
            "| Component | Claim | Status | Detail |",
            "|-----------|-------|--------|--------|",
        ]
        for component, claim, status, detail in findings:
            lines.append(f"| {component} | {claim} | {status} | {detail} |")
        lines.append("")
        lines.append("## Raw JSON")
        lines.append("```json")
        lines.append(json.dumps([
            {"component": c, "claim": cl, "status": s, "detail": d}
            for c, cl, s, d in findings
        ], ensure_ascii=False, indent=2))
        lines.append("```")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return path
