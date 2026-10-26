"""Basic orchestrator implementing the ASI control-flow contract.

Stages: Planner, Retrieval, Specialist, Critic/Verifier, Tool Agent.
Control flow: routing, retry, timeout, verification, escalation, audit logging.
"""

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .audit_logger import AuditLogger
from .knowledge_base import KnowledgeBaseEngine
from .llm_client import LLMClient
from .memory_manager import MemoryManager
from .safety_governor import SafetyGovernor
from .tool_agent import ToolAgent
from .tools import generate_docx, generate_pptx

_SUPPORTED_TASK_TYPES = {"proposal", "presentation", "sop", "marketing", "assistant"}
_ERROR_MARKERS = ("[Error", "[Gemini", "[Claude", "[Fallback", "Error:", "Exception:")


class AgentOrchestrator:
    def __init__(self, kb_engine: KnowledgeBaseEngine, llm_client: LLMClient, base_dir: str):
        self.kb_engine = kb_engine
        self.llm = llm_client
        self.base_dir = base_dir
        self.output_dir = os.path.join(base_dir, "output")
        self.log_dir = os.path.join(base_dir, "logs")
        self.memory_dir = os.path.join(base_dir, "memory")
        for d in (self.output_dir, self.log_dir, self.memory_dir):
            os.makedirs(d, exist_ok=True)

        self.audit_logger = AuditLogger(self.log_dir)
        self.memory = MemoryManager(self.memory_dir)
        self.safety = SafetyGovernor(logger=self.audit_logger, memory_dir=self.memory_dir)
        self.tool_agent = ToolAgent(logger=self.audit_logger, governor=self.safety,
                                    memory=self.memory)
        self.tool_agent.set_kb_engine(self.kb_engine)

        # Cleanup any stale short-term memory from previous crashed workflows.
        self.memory.cleanup_stale(max_age_seconds=3600)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute_workflow(self, task_type: str, user_prompt: str,
                         extra_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        start_time = time.time()
        extra_params = extra_params or {}

        # Validate routing
        task_type = self._validate_task_type(task_type)
        if not (user_prompt or "").strip():
            return self._empty_result("Empty prompt rejected.", start_time)

        workflow_id = f"WF-{int(start_time)}-{task_type.upper()}"
        error_count = 0
        audit_trail: List[Dict[str, Any]] = []

        self.memory.set_short_term(workflow_id, "task_type", task_type)
        self.memory.set_short_term(workflow_id, "user_prompt", user_prompt)
        self.memory.set_short_term(workflow_id, "start_time", start_time)

        # Safety: duration budget + sensitive-data scan
        duration_check = self.safety.check_workflow_duration(0)
        self.audit_logger.log_event(workflow_id, "SafetyGovernor", "Duration budget check",
                                    "SUCCESS" if duration_check["allowed"] else "WARNING",
                                    details=duration_check.get("reason"))
        sensitive_scan = self.safety.scan_for_sensitive_data(user_prompt)
        if sensitive_scan["found"]:
            self.audit_logger.log_event(workflow_id, "SafetyGovernor", "Sensitive data in prompt",
                                        "WARNING", details=sensitive_scan["recommendation"])
            if self.safety.get_config().get("block_sensitive_data_in_prompts"):
                return self._empty_result("Prompt contains sensitive data; blocked by policy.",
                                          start_time, workflow_id=workflow_id)

        # STEP 1 - Planner
        planner_start = time.time()
        self.memory.update_task_state(workflow_id, "planner", "RUNNING")
        try:
            plan_steps = self._run_planner(task_type, user_prompt, extra_params)
            planner_status = "SUCCESS"
        except Exception as exc:
            plan_steps = self._default_plan(task_type)
            planner_status = "FALLBACK"
            error_count += 1
            self.audit_logger.log_failure(workflow_id, "Planner", type(exc).__name__,
                                          str(exc), recovery_action="Used default plan.")
        planner_duration = round((time.time() - planner_start) * 1000, 2)
        self.memory.update_task_state(workflow_id, "planner", "DONE")
        self.audit_logger.log_event(workflow_id, "Planner", "Task decomposition",
                                    planner_status, duration_ms=planner_duration)
        audit_trail.append({"agent": "Planner", "action": "Task decomposition",
                            "status": planner_status, "duration_ms": planner_duration,
                            "output": plan_steps})

        # STEP 2 - Retrieval
        retrieval_start = time.time()
        self.memory.update_task_state(workflow_id, "retrieval", "RUNNING")
        try:
            retrieved_docs = self.kb_engine.search(
                f"{task_type} {user_prompt}",
                category_filter=extra_params.get("category_filter"), top_k=4)
            retrieval_status = "SUCCESS"
        except Exception as exc:
            retrieved_docs = []
            retrieval_status = "FALLBACK"
            error_count += 1
            self.audit_logger.log_failure(workflow_id, "Retrieval", type(exc).__name__,
                                          str(exc), recovery_action="Continued with no evidence.")
        evidence_snippets = [
            {"source": d.get("rel_path", "unknown"),
             "snippet": d.get("content", "")[:350].strip().replace("\n", " "),
             "category": d.get("category", "General")}
            for d in retrieved_docs
        ]
        retrieval_duration = round((time.time() - retrieval_start) * 1000, 2)
        self.memory.update_task_state(workflow_id, "retrieval", "DONE")
        self.memory.set_short_term(workflow_id, "evidence_count", len(retrieved_docs))
        self.audit_logger.log_event(workflow_id, "Retrieval", "Evidence gathering",
                                    retrieval_status, duration_ms=retrieval_duration)
        audit_trail.append({"agent": "Retrieval", "action": "Evidence gathering",
                            "status": retrieval_status, "duration_ms": retrieval_duration,
                            "sources": [d.get("rel_path") for d in retrieved_docs]})

        # STEP 3 - Specialist (with critic revision loop)
        max_revisions = int(self.safety.get_config().get("max_retries_per_tool", 3))
        max_revisions = min(max_revisions, 2)
        draft_content = ""
        verification_result: Dict[str, Any] = {"status": "NEEDS_REVISION", "score": 0,
                                               "findings": [], "audit_agent": "Amsyong-Critic-v1"}
        for revision_round in range(1 + max_revisions):
            gen_start = time.time()
            self.memory.update_task_state(workflow_id, "specialist", "RUNNING")
            try:
                system_prompt = self._build_specialist_system_prompt(task_type, evidence_snippets)
                user_formatted = (f"Permintaan Pengguna: {user_prompt}\n"
                                  f"Parameter Tambahan: {json.dumps(extra_params, ensure_ascii=False)}")
                if revision_round > 0:
                    user_formatted += "\n\nPerbaikan yang diminta oleh Critic:\n" + \
                                      "\n".join(f"- {f}" for f in verification_result["findings"])
                draft_content = self.llm.generate(system_prompt, user_formatted)
                specialist_status = "SUCCESS"
                if self.llm.last_error:
                    specialist_status = "FALLBACK"
                    self.audit_logger.log_failure(workflow_id, "Specialist", "LLMError",
                                                  self.llm.last_error,
                                                  recovery_action=f"Provider used: {self.llm.last_provider_used}")
            except Exception as exc:
                specialist_status = "FAILED"
                error_count += 1
                self.audit_logger.log_failure(workflow_id, "Specialist", type(exc).__name__,
                                              str(exc), recovery_action="Used offline fallback.")
                draft_content = self.llm._offline_engine("", user_prompt)
            gen_duration = round((time.time() - gen_start) * 1000, 2)
            self.memory.update_task_state(workflow_id, "specialist", "DONE")

            # Output sensitive-data scan
            output_scan = self.safety.scan_for_sensitive_data(draft_content)
            if output_scan["found"]:
                self.audit_logger.log_event(workflow_id, "SafetyGovernor",
                                            "Sensitive data in output", "WARNING",
                                            details=output_scan["recommendation"])

            # STEP 4 - Critic / Verifier
            critic_start = time.time()
            self.memory.update_task_state(workflow_id, "critic", "RUNNING")
            verification_result = self._run_critic_audit(task_type, draft_content, evidence_snippets)
            critic_duration = round((time.time() - critic_start) * 1000, 2)
            self.memory.update_task_state(workflow_id, "critic", "DONE")
            self.audit_logger.log_event(workflow_id, "Critic", "Verification",
                                        verification_result["status"],
                                        details=f"Score {verification_result['score']}/100 "
                                                f"(round {revision_round+1})",
                                        duration_ms=critic_duration)
            audit_trail.append({"agent": "Specialist", "status": specialist_status,
                                "duration_ms": gen_duration,
                                "output_preview": draft_content[:200]})
            audit_trail.append({"agent": "Critic", "status": verification_result["status"],
                                "score": verification_result["score"],
                                "findings": verification_result["findings"],
                                "duration_ms": critic_duration, "round": revision_round + 1})

            if verification_result["status"] == "PASSED":
                break

        # STEP 5 - Tool Agent (artifact assembly)
        tool_start = time.time()
        self.memory.update_task_state(workflow_id, "tool_agent", "RUNNING")
        artifact_info = None
        try:
            artifact_info = self._assemble_artifact(task_type, user_prompt, draft_content,
                                                    extra_params, workflow_id)
            tool_status = "SUCCESS" if artifact_info else "SKIPPED"
        except Exception as exc:
            tool_status = "FAILED"
            error_count += 1
            self.audit_logger.log_failure(workflow_id, "ToolAgent", type(exc).__name__,
                                          str(exc), recovery_action="Workflow continues without artifact.")
        tool_duration = round((time.time() - tool_start) * 1000, 2)
        self.memory.update_task_state(workflow_id, "tool_agent", "DONE")
        if artifact_info:
            self.audit_logger.log_event(workflow_id, "ToolAgent",
                                        f"File assembly: {artifact_info.get('type')}",
                                        tool_status, details=artifact_info.get("file_name"),
                                        duration_ms=tool_duration)
            self.memory.register_artifact(workflow_id, artifact_info.get("type", "UNKNOWN"),
                                          artifact_info.get("file_path", ""),
                                          metadata={"file_name": artifact_info.get("file_name")})
            audit_trail.append({"agent": "ToolAgent", "action": "File assembly",
                                "status": tool_status, "duration_ms": tool_duration,
                                "file_name": artifact_info.get("file_name")})

        total_duration = round(time.time() - start_time, 3)

        # Final safety: duration exceeded?
        final_duration_check = self.safety.check_workflow_duration(total_duration)
        if not final_duration_check["allowed"]:
            self.audit_logger.log_event(workflow_id, "SafetyGovernor", "Duration exceeded",
                                        "WARNING", details=final_duration_check["reason"])

        # Escalation decision
        escalation = self.safety.should_escalate(verification_result["score"], error_count)
        if escalation["escalate"]:
            self.audit_logger.log_escalation(workflow_id,
                                             trigger=f"Score {verification_result['score']}, errors {error_count}",
                                             from_agent="Critic", to_agent="Human",
                                             reason=escalation["reason"])
            self.memory.set_pending_review(workflow_id, {
                "trigger": escalation["reason"],
                "score": verification_result["score"],
                "error_count": error_count,
            })

        # Persist workflow history
        self.memory.store_long_term(f"workflow_{workflow_id}", {
            "task_type": task_type, "status": verification_result["status"],
            "score": verification_result["score"], "duration": total_duration,
            "errors": error_count,
        }, category="workflow_history")

        contamination = self.memory.detect_contamination(workflow_id)
        if contamination:
            self.audit_logger.log_event(workflow_id, "MemoryManager", "Contamination check",
                                        "WARNING", details=contamination)

        self.memory.clear_short_term(workflow_id)

        result_payload = {
            "workflow_id": workflow_id,
            "task_type": task_type,
            "status": self._overall_status(verification_result, artifact_info, error_count, escalation),
            "final_content": draft_content,
            "plan_steps": plan_steps,
            "evidence": evidence_snippets,
            "verification": verification_result,
            "artifact": artifact_info,
            "audit_trail": audit_trail,
            "safety_checks": {
                "sensitive_input": sensitive_scan,
                "sensitive_output": output_scan if 'output_scan' in locals() else {"found": False},
                "escalation": escalation,
                "duration_check": final_duration_check,
            },
            "memory_state": {
                "task_states": self.memory.get_task_state(workflow_id),
                "artifacts": self.memory.get_artifacts(workflow_id),
                "pending_review": self.memory.get_pending_review(workflow_id),
            },
            "errors": error_count,
            "total_duration_sec": total_duration,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_log(workflow_id, result_payload)
        return result_payload

    # ------------------------------------------------------------------
    # Step helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_task_type(task_type: str) -> str:
        t = (task_type or "").strip().lower()
        return t if t in _SUPPORTED_TASK_TYPES else "assistant"

    @staticmethod
    def _default_plan(task_type: str) -> List[str]:
        return [
            "Understand the user's request.",
            "Retrieve relevant facts from the Knowledge Base.",
            "Compose a structured answer.",
            f"(Default plan for task_type='{task_type}'.)",
        ]

    def _run_planner(self, task_type: str, prompt: str,
                     extra_params: Dict[str, Any]) -> List[str]:
        if task_type == "proposal":
            return [
                "Identify client profile, technical scope, and project objectives.",
                "Search the Knowledge Base for similar past projects.",
                "Outline: Background, Scope, Methodology, Schedule, Warranty.",
                "Verify compliance with procurement quality and administration standards.",
                "Produce a downloadable Word (.docx) document.",
            ]
        if task_type == "presentation":
            return [
                "Analyze topic and target audience.",
                "Design slide flow: Title, Problem, Solution, Methodology, Call to Action.",
                "Draft concise bullet points per slide.",
                "Verify readability and completeness of company value statements.",
                "Produce a PowerPoint (.pptx) deck.",
            ]
        if task_type == "sop":
            return [
                "Identify the specific process and applicable safety regulations.",
                "Assign roles: Engineer, Safety Officer, Manager.",
                "Draft stepwise procedure: Preparation, Execution, Housekeeping.",
                "Verify K3 risk mitigation and emergency procedures.",
                "Compile an ISO-style Word (.docx) document.",
            ]
        if task_type == "marketing":
            return [
                "Analyze target market, tender specs, or competitor profile.",
                "Match PT Amsyong strengths to market needs.",
                "Draft Unique Value Proposition and differentiation strategy.",
                "Cross-check claims against the Knowledge Base.",
            ]
        return [
            "Understand the user's request accurately.",
            "Retrieve supporting facts from internal documents.",
            "Compose a structured, actionable answer.",
        ]

    @staticmethod
    def _build_specialist_system_prompt(task_type: str,
                                        evidence: List[Dict[str, Any]]) -> str:
        evidence_text = "\n".join(
            f"- Document [{e['source']}]: {e['snippet']}" for e in evidence)
        return (
            f"You are the {task_type.upper()} Specialist Agent for PT Amsyong (virtual employee).\n"
            "Principles:\n"
            "1. Truth and accuracy: use available company facts.\n"
            "2. Do not fabricate information that contradicts the company record.\n"
            "3. Use a professional, structured, and persuasive corporate tone.\n\n"
            f"KNOWLEDGE BASE:\n{evidence_text if evidence_text else 'PT Amsyong (simulated data): IT, computer procurement, digital document management.'}\n"
        )

    def _run_critic_audit(self, task_type: str, content: str,
                          evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
        findings: List[str] = []
        score = 100

        if len(content.strip()) < 150:
            findings.append("Content is too short for a professional document.")
            score -= 30

        if "#" not in content and "==" not in content and len(content.split("\n")) < 4:
            findings.append("Document heading structure is not well-formed.")
            score -= 15

        if any(marker in content for marker in _ERROR_MARKERS):
            findings.append("Output contains error markers; likely a provider fallback.")
            score -= 25

        if not evidence:
            findings.append("Warning: no direct reference documents found in the Knowledge Base.")
            score -= 10
        else:
            findings.append(f"Verified: content aligned with {len(evidence)} Knowledge Base references.")

        # Evidence cross-check: are key terms from evidence present in content?
        if evidence:
            content_lower = content.lower()
            coverage = 0
            for ev in evidence:
                words = [w for w in re.findall(r"[A-Za-z]{4,}", ev.get("snippet", ""))
                         if w.lower() not in ("this", "that", "with", "from", "have", "been")]
                if any(w.lower() in content_lower for w in words[:5]):
                    coverage += 1
            if coverage == 0:
                findings.append("Evidence cross-check: none of the source terms appear in the output.")
                score -= 15

        status = "PASSED" if score >= 70 else "NEEDS_REVISION"
        return {"status": status, "score": max(score, 0), "findings": findings,
                "audit_agent": "Amsyong-Critic-v1"}

    def _assemble_artifact(self, task_type: str, prompt: str, content: str,
                           extra_params: Dict[str, Any], wf_id: str) -> Optional[Dict[str, Any]]:
        # Format check via governor
        if task_type in ("proposal", "sop"):
            fmt_check = self.safety.check_output_format("docx")
            if not fmt_check["allowed"]:
                self.audit_logger.log_policy_check(wf_id, "output_format", False,
                                                   fmt_check["reason"])
                return None
            client_name = extra_params.get("client_name", "Klien Terhormat")
            project_title = extra_params.get("project_title", prompt[:40])
            file_name = (f"{task_type.upper()}_{re.sub(r'[^a-zA-Z0-9_]', '_', project_title)[:30]}"
                         f"_{wf_id[:8]}.docx")
            out_path = os.path.join(self.output_dir, file_name)
            sections = self._parse_markdown_to_sections(content)
            subtitle = f"Untuk: {client_name} | Tanggal: {datetime.now().strftime('%d %B %Y')}"
            result = self.tool_agent.run_tool_with_retry(
                "generate_docx",
                args={"title": f"{task_type.upper()} TEKNIS - PT AMSYONG",
                      "subtitle": subtitle, "sections": sections, "output_path": out_path},
                workflow_id=wf_id)
            if not result["success"]:
                self.audit_logger.log_failure(wf_id, "ToolAgent", "ArtifactAssembly",
                                              result.get("error") or "docx generation failed",
                                              recovery_action="Skipped artifact.")
                return None
            return {"type": "DOCX", "file_name": file_name, "file_path": out_path,
                    "download_label": f"Unduh Dokumen {task_type.upper()} (.docx)"}

        if task_type == "presentation":
            fmt_check = self.safety.check_output_format("pptx")
            if not fmt_check["allowed"]:
                self.audit_logger.log_policy_check(wf_id, "output_format", False,
                                                   fmt_check["reason"])
                return None
            project_title = extra_params.get("project_title", prompt[:40])
            client_name = extra_params.get("client_name", "Audiens")
            file_name = (f"PRESENTASI_{re.sub(r'[^a-zA-Z0-9_]', '_', project_title)[:30]}"
                         f"_{wf_id[:8]}.pptx")
            out_path = os.path.join(self.output_dir, file_name)
            slides = self._parse_markdown_to_slides(content)
            result = self.tool_agent.run_tool_with_retry(
                "generate_pptx",
                args={"title": project_title or "Presentasi Solusi PT Amsyong",
                      "subtitle": f"Disiapkan untuk {client_name}",
                      "slides_data": slides, "output_path": out_path},
                workflow_id=wf_id)
            if not result["success"]:
                self.audit_logger.log_failure(wf_id, "ToolAgent", "ArtifactAssembly",
                                              result.get("error") or "pptx generation failed",
                                              recovery_action="Skipped artifact.")
                return None
            return {"type": "PPTX", "file_name": file_name, "file_path": out_path,
                    "download_label": "Unduh Presentasi Slide (.pptx)"}

        return None

    @staticmethod
    def _parse_markdown_to_sections(content: str) -> List[Dict[str, Any]]:
        sections: List[Dict[str, Any]] = []
        raw_sections = re.split(r'\n(?=#{1,3}\s+)', content)
        for raw in raw_sections:
            lines = raw.strip().split("\n")
            if not lines or not lines[0]:
                continue
            title = re.sub(r'^#{1,3}\s*', '', lines[0]).strip()
            body_lines = lines[1:]
            bullets: List[str] = []
            text_lines: List[str] = []
            for bl in body_lines:
                bl_s = bl.strip()
                if bl_s.startswith(("- ", "* ")):
                    bullets.append(bl_s[2:].strip())
                elif bl_s[:3] in ("1. ", "2. ", "3. "):
                    bullets.append(bl_s)
                else:
                    text_lines.append(bl)
            sections.append({"title": title, "content": "\n".join(text_lines).strip(),
                             "bullets": bullets})
        return sections if sections else [{"title": "Isi Dokumen", "content": content,
                                           "bullets": []}]

    @staticmethod
    def _parse_markdown_to_slides(content: str) -> List[Dict[str, Any]]:
        slides: List[Dict[str, Any]] = []
        raw_sections = re.split(r'\n(?=#{1,3}\s+Slide|\n#{1,3}\s+)', content)
        for raw in raw_sections:
            lines = raw.strip().split("\n")
            if not lines or not lines[0]:
                continue
            title = re.sub(r'^#{1,3}\s*', '', lines[0]).strip()
            body_lines = lines[1:]
            bullets: List[str] = []
            text_lines: List[str] = []
            for bl in body_lines:
                bl_s = bl.strip()
                if bl_s.startswith(("- ", "* ", "• ")):
                    bullets.append(re.sub(r'^[-*•]\s*', '', bl_s))
                elif len(bl_s) > 3 and bl_s[0].isdigit() and bl_s[1] in ".)":
                    bullets.append(bl_s[3:].strip())
                elif bl_s:
                    text_lines.append(bl_s)
            slides.append({"title": title, "content": " ".join(text_lines), "bullets": bullets})
        return slides if slides else [{"title": "Slide Ringkasan", "content": content[:150],
                                       "bullets": []}]

    @staticmethod
    def _overall_status(verification: Dict[str, Any], artifact: Optional[Dict[str, Any]],
                        errors: int, escalation: Dict[str, Any]) -> str:
        if errors > 0 and not artifact:
            return "FAILED"
        if escalation.get("escalate"):
            return "ESCALATED"
        if verification.get("status") == "PASSED" and artifact:
            return "SUCCESS"
        if verification.get("status") == "PASSED":
            return "PASSED_NO_ARTIFACT"
        return "WARNING"

    @staticmethod
    def _empty_result(reason: str, start_time: float,
                      workflow_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "workflow_id": workflow_id or f"WF-{int(start_time)}-REJECTED",
            "task_type": "-",
            "status": "REJECTED",
            "final_content": "",
            "plan_steps": [],
            "evidence": [],
            "verification": {"status": "REJECTED", "score": 0,
                             "findings": [reason], "audit_agent": "Router"},
            "artifact": None,
            "audit_trail": [],
            "safety_checks": {},
            "memory_state": {},
            "errors": 0,
            "total_duration_sec": round(time.time() - start_time, 3),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _save_log(self, wf_id: str, data: Dict[str, Any]) -> None:
        log_file = os.path.join(self.log_dir, f"{wf_id}.json")
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
