"""Test suite for the Amsyong-AI-LAB orchestration framework.

Run from the project root:
    python -m pytest tests/ -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.redaction import find_secrets, redact, redact_text
from core.audit_logger import AuditLogger
from core.safety_governor import SafetyGovernor
from core.memory_manager import MemoryManager
from core.tool_agent import ToolAgent
from core.orchestrator import AgentOrchestrator, _SUPPORTED_TASK_TYPES
from core.llm_client import LLMClient
from core.knowledge_base import KnowledgeBaseEngine
from core.compliance_auditor import ComplianceAuditor


class RedactionTests(unittest.TestCase):
    def test_finds_openai_key(self):
        self.assertIn("openai_key", find_secrets("my key is sk-AAAAAAAAAAAAAAAAAAAAAAAA"))

    def test_finds_anthropic_key(self):
        self.assertIn("anthropic_key", find_secrets("sk-ant-AAAAAAAAAAAAAAAAAAAAAAAA"))

    def test_finds_google_key(self):
        self.assertIn("google_api_key", find_secrets("AIza1234567890abcdefghijklmnopqrst"))

    def test_redacts_assigned_secret(self):
        out = redact_text("password: hunter2")
        self.assertNotIn("hunter2", out)
        self.assertIn("[REDACTED]", out)

    def test_redacts_url_credential(self):
        out = redact_text("https://api.example.com/v1?key=sk-1234567890abcdef&foo=bar")
        self.assertIn("key=[REDACTED]", out)
        self.assertNotIn("sk-1234567890abcdef", out)

    def test_redact_dict(self):
        result = redact({"api_key": "sk-AAAAAAAAAAAAAAAAAAAA", "user": "alice"})
        self.assertEqual(result["user"], "alice")
        self.assertNotIn("sk-AAAAAAAAAAAAAAAAAAAA", json.dumps(result))


class AuditLoggerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.logger = AuditLogger(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_jsonl_and_txt(self):
        self.logger.log_event("WF-1", "Agent", "action", "SUCCESS")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "audit_trail.jsonl")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "audit.log")))

    def test_session_summary(self):
        self.logger.log_event("WF-1", "A", "x", "SUCCESS")
        self.logger.log_event("WF-1", "A", "y", "WARNING")
        self.logger.log_event("WF-1", "A", "z", "FAILED")
        s = self.logger.get_log_summary()
        self.assertEqual(s["SUCCESS"], 1)
        self.assertEqual(s["WARNING"], 1)
        self.assertEqual(s["FAILED"], 1)

    def test_search_filters(self):
        self.logger.log_event("WF-1", "Planner", "plan", "SUCCESS")
        self.logger.log_event("WF-2", "Critic", "verify", "WARNING")
        results = self.logger.search_logs(agent_filter="Planner")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["workflow_id"], "WF-1")

    def test_secrets_redacted_in_file(self):
        self.logger.log_event("WF-1", "A", "x", "SUCCESS",
                              details="api_key=sk-AAAAAAAAAAAAAAAAAAAA")
        with open(os.path.join(self.tmp, "audit_trail.jsonl"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("sk-AAAAAAAAAAAAAAAAAAAA", content)
        self.assertIn("[REDACTED]", content)


class SafetyGovernorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mem_dir = os.path.join(self.tmp, "memory")
        self.log_dir = os.path.join(self.tmp, "logs")
        self.logger = AuditLogger(self.log_dir)
        self.gov = SafetyGovernor(logger=self.logger, memory_dir=self.mem_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_restricted_action_blocked(self):
        r = self.gov.check_action_allowed("delete_file")
        self.assertFalse(r["allowed"])

    def test_allowed_action_passes(self):
        r = self.gov.check_action_allowed("generate_docx")
        self.assertTrue(r["allowed"])

    def test_sensitive_scan(self):
        r = self.gov.scan_for_sensitive_data("my api_key is sk-ant-AAAAAAAAAAAAAAAAAAAA")
        self.assertTrue(r["found"])

    def test_escalation_threshold(self):
        r = self.gov.should_escalate(verification_score=30)
        self.assertTrue(r["escalate"])
        r = self.gov.should_escalate(verification_score=90)
        self.assertFalse(r["escalate"])

    def test_config_persisted(self):
        self.gov.update_config("escalation_threshold_score", 77)
        gov2 = SafetyGovernor(logger=self.logger, memory_dir=self.mem_dir)
        self.assertEqual(gov2.get_config()["escalation_threshold_score"], 77)


class MemoryManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mm = MemoryManager(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_short_term_isolation(self):
        self.mm.set_short_term("WF-1", "k", "v1")
        self.mm.set_short_term("WF-2", "k", "v2")
        self.assertEqual(self.mm.get_short_term("WF-1", "k"), "v1")
        self.assertEqual(self.mm.get_short_term("WF-2", "k"), "v2")
        self.mm.clear_short_term("WF-1")
        self.assertIsNone(self.mm.get_short_term("WF-1", "k"))

    def test_long_term_persistence(self):
        self.mm.store_long_term("probe", {"hello": "world"}, category="test")
        mm2 = MemoryManager(self.tmp)
        self.assertEqual(mm2.retrieve_long_term("probe"), {"hello": "world"})

    def test_pending_review(self):
        self.mm.set_pending_review("WF-1", {"reason": "low score"})
        r = self.mm.resolve_pending_review("WF-1", "APPROVED", "user")
        self.assertEqual(r["decision"], "APPROVED")
        self.assertTrue(r["resolved"])

    def test_contamination_detection(self):
        for i in range(150):
            self.mm.set_short_term("WF-1", f"k{i}", i)
        self.assertIsNotNone(self.mm.detect_contamination("WF-1"))


class ToolAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mem_dir = os.path.join(self.tmp, "memory")
        self.log_dir = os.path.join(self.tmp, "logs")
        self.logger = AuditLogger(self.log_dir)
        self.gov = SafetyGovernor(logger=self.logger, memory_dir=self.mem_dir)
        self.mem = MemoryManager(self.mem_dir)
        self.ta = ToolAgent(logger=self.logger, governor=self.gov, memory=self.mem)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lists_builtin_tools(self):
        names = {t["name"] for t in self.ta.list_tools()}
        self.assertIn("generate_docx", names)
        self.assertIn("generate_pptx", names)
        self.assertIn("search_knowledge_base", names)

    def test_unregistered_tool_rejected(self):
        r = self.ta.run_tool("does_not_exist")
        self.assertFalse(r["success"])
        self.assertIn("not registered", r["error"])

    def test_tool_memory_recorded(self):
        self.ta.run_tool("generate_docx", args={
            "title": "T", "subtitle": "S", "sections": [],
            "output_path": os.path.join(self.tmp, "x.docx"),
        })
        rec = self.mem.retrieve_long_term("tool_generate_docx")
        self.assertIsNotNone(rec)
        self.assertGreaterEqual(rec["calls"], 1)


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kb_dir = os.path.join(self.tmp, "01_Knowledge_Base", "Company_Profile")
        os.makedirs(self.kb_dir)
        with open(os.path.join(self.kb_dir, "sample.txt"), "w", encoding="utf-8") as f:
            f.write("---\nverified: true\n---\nPT Amsyong is an IT company.\n")
        self.kb = KnowledgeBaseEngine(os.path.join(self.tmp, "01_Knowledge_Base"))
        self.llm = LLMClient(provider="offline")
        self.orch = AgentOrchestrator(self.kb, self.llm, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_validate_task_type(self):
        self.assertEqual(AgentOrchestrator._validate_task_type("proposal"), "proposal")
        self.assertEqual(AgentOrchestrator._validate_task_type("unknown"), "assistant")
        self.assertEqual(AgentOrchestrator._validate_task_type(""), "assistant")

    def test_empty_prompt_rejected(self):
        r = self.orch.execute_workflow("proposal", "")
        self.assertEqual(r["status"], "REJECTED")

    def test_assistant_workflow_runs(self):
        r = self.orch.execute_workflow("assistant", "Siapa PT Amsyong?")
        self.assertIn(r["status"], ("SUCCESS", "PASSED_NO_ARTIFACT", "WARNING"))
        self.assertTrue(r["workflow_id"].startswith("WF-"))
        self.assertGreaterEqual(r["verification"]["score"], 0)

    def test_proposal_produces_docx(self):
        r = self.orch.execute_workflow("proposal", "Buat proposal untuk PT X",
                                       {"project_title": "Demo", "client_name": "PT X"})
        self.assertIsNotNone(r["artifact"])
        self.assertEqual(r["artifact"]["type"], "DOCX")
        self.assertTrue(os.path.isfile(r["artifact"]["file_path"]))

    def test_presentation_produces_pptx(self):
        r = self.orch.execute_workflow("presentation", "Buat presentasi",
                                       {"project_title": "Demo", "client_name": "PT X"})
        self.assertIsNotNone(r["artifact"])
        self.assertEqual(r["artifact"]["type"], "PPTX")

    def test_sensitive_prompt_blocked(self):
        r = self.orch.execute_workflow("assistant", "my api_key is sk-AAAAAAAAAAAAAAAAAAAA")
        self.assertEqual(r["status"], "REJECTED")

    def test_workflow_history_recorded(self):
        self.orch.execute_workflow("assistant", "hello")
        lt = self.orch.memory.get_all_long_term()
        wf_keys = [k for k in lt if k.startswith("workflow_WF-")]
        self.assertGreaterEqual(len(wf_keys), 1)


class ComplianceAuditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kb_dir = os.path.join(self.tmp, "01_Knowledge_Base", "Company_Profile")
        os.makedirs(self.kb_dir)
        with open(os.path.join(self.kb_dir, "sample.txt"), "w", encoding="utf-8") as f:
            f.write("PT Amsyong is an IT company.\n")
        self.project = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_audit_runs_and_writes_report(self):
        auditor = ComplianceAuditor(self.project)
        report = auditor.run()
        self.assertTrue(os.path.isfile(report["report_path"]))
        self.assertIn("VERIFIED", report["summary"])
        # Every finding must carry a status label
        for _, _, status, _ in report["findings"]:
            self.assertIn(status, ("VERIFIED", "UNVERIFIED", "NOT RUN"))


if __name__ == "__main__":
    unittest.main()
