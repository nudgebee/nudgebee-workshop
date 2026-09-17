#!/usr/bin/env python3
"""
Comprehensive automated test suite for SRE Workshop Tools.
Validates:
1. RFC 1123 Kubernetes input validation and command injection defense
2. Human approval security gate (prompt, default deny, auto-approve env)
3. Episodic memory enforcement (on vs off boundary)
4. Capability boundary enforcement (disabled tool execution block)
5. Evidence-backed ground truth evaluation
6. MockAgentPlanner multi-turn determinism across all 6 scenarios
"""

import os
import sys
import unittest

# Add agent directory to Python path
AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from unittest.mock import patch

from tools import (
    validate_k8s_name,
    get_k8s_events,
    query_pod_logs,
    get_deploy_history,
    search_incident_history,
    ask_human_approval,
    test_all_tools,
)
from mock_planner import MockAgentPlanner
from mini_agent import evaluate_ground_truth, run_investigation, CONFIG
from config_loader import assemble_system_prompt


class TestKubernetesInputValidation(unittest.TestCase):
    """Verifies strict RFC 1123 validation preventing shell/command injection."""

    def test_valid_k8s_names(self):
        valid = ["default", "group-1", "product-catalog", "cartservice-v2", "db-123"]
        for name in valid:
            self.assertEqual(validate_k8s_name(name, "param"), name)

    def test_injection_payloads_rejected(self):
        malicious = [
            "checkout; rm -rf /",
            "group-1 && whoami",
            "pod|id",
            "`id`",
            "$(cat /etc/passwd)",
            "checkout\nreboot",
            "-leading-hyphen",
            "UPPERCASE",
            "has.dots.not.allowed",
            "a" * 64,  # Exceeds 63 characters
        ]
        for payload in malicious:
            with self.assertRaises(ValueError, msg=f"Payload failed to raise: {payload}"):
                validate_k8s_name(payload, "param")

    def test_tools_reject_injections_gracefully(self):
        malicious = "prod; rm -rf /"
        res_events = get_k8s_events(malicious)
        self.assertIn("Tool Input Validation Error", res_events)

        res_logs = query_pod_logs(malicious, "default")
        self.assertIn("Tool Input Validation Error", res_logs)

        res_deploy = get_deploy_history(malicious, "default")
        self.assertIn("Tool Input Validation Error", res_deploy)

    @patch("tools.run_cmd", return_value=(1, "", "Unable to connect to the server: connection refused"))
    @patch.dict(os.environ, {"AUTO_APPROVE_HUMAN_GATE": "true"})
    def test_tools_health_check_rejects_k8s_failures(self, mock_cmd):
        """Verifies --test-tools rejects live cluster errors and does not falsely report passed=True."""
        results = test_all_tools("group-1")
        res_dict = {name: (passed, output) for name, passed, output in results}

        self.assertIn("get_k8s_events", res_dict)
        self.assertFalse(res_dict["get_k8s_events"][0], "get_k8s_events must fail on non-zero exit")
        self.assertIn("Kubernetes API error", res_dict["get_k8s_events"][1])

        self.assertIn("query_pod_logs", res_dict)
        self.assertFalse(res_dict["query_pod_logs"][0], "query_pod_logs must fail on non-zero exit")
        self.assertIn("Kubernetes API error", res_dict["query_pod_logs"][1])

        self.assertIn("get_deploy_history", res_dict)
        self.assertFalse(res_dict["get_deploy_history"][0], "get_deploy_history must fail on non-zero exit")
        self.assertIn("Kubernetes API error", res_dict["get_deploy_history"][1])


class TestHumanApprovalGate(unittest.TestCase):
    """Verifies security approval gate authorization behavior."""

    def setUp(self):
        self.orig_env = os.environ.get("AUTO_APPROVE_HUMAN_GATE")

    def tearDown(self):
        if self.orig_env is not None:
            os.environ["AUTO_APPROVE_HUMAN_GATE"] = self.orig_env
        else:
            os.environ.pop("AUTO_APPROVE_HUMAN_GATE", None)

    @patch("sys.stdin.isatty", return_value=False)
    def test_headless_default_deny(self, mock_isatty):
        os.environ.pop("AUTO_APPROVE_HUMAN_GATE", None)
        res = ask_human_approval("Restart pods", "kubectl rollout restart deploy/checkout", "HIGH", "All users")
        self.assertIn("DENIED", res)
        self.assertIn("Non-interactive headless terminal", res)

    @patch("sys.stdin.isatty", return_value=True)
    @patch("builtins.input", return_value="reject")
    def test_interactive_operator_rejection(self, mock_input, mock_isatty):
        os.environ.pop("AUTO_APPROVE_HUMAN_GATE", None)
        res = ask_human_approval("Restart pods", "kubectl rollout restart deploy/checkout", "HIGH", "All users")
        self.assertIn("DENIED", res)
        self.assertIn("Rejected by human operator", res)

    @patch("sys.stdin.isatty", return_value=True)
    @patch("builtins.input", return_value="approve")
    def test_interactive_operator_approval(self, mock_input, mock_isatty):
        os.environ.pop("AUTO_APPROVE_HUMAN_GATE", None)
        res = ask_human_approval("Restart pods", "kubectl rollout restart deploy/checkout", "HIGH", "All users")
        self.assertIn("APPROVED", res)
        self.assertIn("Explicitly authorized by human operator", res)

    def test_auto_approve_env_override(self):
        os.environ["AUTO_APPROVE_HUMAN_GATE"] = "true"
        res = ask_human_approval("Restart pods", "kubectl rollout restart deploy/checkout", "HIGH", "All users")
        self.assertIn("APPROVED", res)
        self.assertIn("AUTO_APPROVE_HUMAN_GATE=true", res)


class TestEpisodicMemoryBoundary(unittest.TestCase):
    """Verifies that memory-off mode strictly disables historical retrieval."""

    def test_memory_disabled_refusal(self):
        res = search_incident_history("connection pool", "product-catalog", enable_memory=False)
        self.assertIn("Episodic memory is DISABLED", res)
        self.assertNotIn("INC-4092", res)

    def test_memory_enabled_retrieval(self):
        res = search_incident_history("connection pool", "product-catalog", enable_memory=True)
        self.assertIn("INC-4092", res)


class TestCredentialValidation(unittest.TestCase):
    """Verifies fast-fail check preventing live models from masquerading as mock runs without API keys."""

    def test_missing_credential_raises_error(self):
        with patch.dict(CONFIG, {"model": "gemini-3.8-flash", "api_key": ""}):
            with self.assertRaises(ValueError) as ctx:
                run_investigation()
            self.assertIn("requires an API key", str(ctx.exception))


class TestGroundTruthEvaluation(unittest.TestCase):
    """Verifies evidence-backed tuple evaluation for all scenarios."""

    def test_bad_deploy_ground_truth(self):
        # Valid full evidence with AUTHORIZED_NOT_EXECUTED
        good_diag = (
            "ROOT CAUSE: Bad configuration deploy on checkout service (Commit a7f39b1).\n"
            "Bumped payment timeout to 5000ms causing worker pool lockup.\n"
            "Remediation: AUTHORIZED_NOT_EXECUTED rollback to revision 2."
        )
        verified, details = evaluate_ground_truth("badDeploy1405", good_diag)
        self.assertTrue(verified, details)

        # Keyword presence but missing definitive evidence
        weak_diag = "Checkout had an issue during deployment. Restart pods."
        verified, details = evaluate_ground_truth("badDeploy1405", weak_diag)
        self.assertFalse(verified, details)
        self.assertIn("Unverified", details)

        # Partial evidence (service + remediation but no root cause commit/timeout)
        partial_diag = "Checkout service issue identified. Propose rollback to revision 2."
        verified, details = evaluate_ground_truth("badDeploy1405", partial_diag)
        self.assertFalse(verified, details)
        self.assertIn("Partial match", details)

    def test_failed_investigation_never_verified(self):
        failed_diag = "Investigation FAILED (API_ERROR): Connection timed out."
        verified, details = evaluate_ground_truth("badDeploy1405", failed_diag, failed=True)
        self.assertFalse(verified)

    def test_poisoned_entity_abstention(self):
        # Agent correctly abstains on non-existent service
        abstain_diag = "ABSTAIN: Service billing-worker does not exist in cluster topology. No active pods found."
        verified, details = evaluate_ground_truth("poisonedEntity", abstain_diag)
        self.assertTrue(verified, details)

        # Agent hallucinates a root cause
        hallucinated = "billing-worker is out of memory. Increase limits."
        verified, details = evaluate_ground_truth("poisonedEntity", hallucinated)
        self.assertFalse(verified, details)


class TestMockAgentPlannerScenarios(unittest.TestCase):
    """Verifies that deterministic mock planner simulates all 6 scenarios cleanly."""

    def test_bad_deploy_approval_denied_flow(self):
        planner = MockAgentPlanner("badDeploy1405", "group-1")
        tools = ["query_prometheus", "inspect_topology", "get_deploy_history", "ask_human_approval"]

        step1 = planner.plan_next_action(tools)
        self.assertEqual(step1["tool_call"]["name"], "query_prometheus")

        step2 = planner.plan_next_action(tools, last_observation="Prometheus latency high")
        self.assertEqual(step2["tool_call"]["name"], "inspect_topology")

        step3 = planner.plan_next_action(tools, last_observation="Topology: checkout -> payment")
        self.assertEqual(step3["tool_call"]["name"], "get_deploy_history")

        step4 = planner.plan_next_action(tools, last_observation="Revision 3 Commit a7f39b1 bumped timeout to 5000ms")
        self.assertEqual(step4["tool_call"]["name"], "ask_human_approval")

        # Turn 5: operator DENIED approval
        step5 = planner.plan_next_action(tools, last_observation="REMEDIATION ACTION DENIED BY OPERATOR")
        self.assertTrue(step5["is_final"])
        self.assertIn("REJECTED_BY_OPERATOR", step5["diagnosis"])

    def test_bad_deploy_approval_authorized_not_executed_flow(self):
        planner = MockAgentPlanner("badDeploy1405", "group-1")
        tools = ["query_prometheus", "inspect_topology", "get_deploy_history", "ask_human_approval"]

        planner.plan_next_action(tools)
        planner.plan_next_action(tools, last_observation="Prometheus latency high")
        planner.plan_next_action(tools, last_observation="Topology: checkout -> payment")
        planner.plan_next_action(tools, last_observation="Revision 3 Commit a7f39b1 bumped timeout to 5000ms")

        # Turn 5: operator APPROVED approval -> produces AUTHORIZED_NOT_EXECUTED
        step5 = planner.plan_next_action(tools, last_observation="OPERATOR DECISION: [APPROVED] - Explicitly authorized")
        self.assertTrue(step5["is_final"])
        self.assertIn("AUTHORIZED_NOT_EXECUTED", step5["diagnosis"])
        self.assertNotIn("latency recovered", step5["diagnosis"].lower())
        self.assertNotIn("rollback validated", step5["diagnosis"].lower())

    def test_all_scenarios_reach_final_diagnosis(self):
        scenarios = ["badDeploy1405", "episodicRecurrence", "postgresFailure", "emailMemoryLeak", "postgresSlow", "poisonedEntity"]
        all_tools = ["get_k8s_events", "query_prometheus", "query_pod_logs", "inspect_topology", "ask_human_approval", "get_deploy_history", "search_incident_history"]

        for sc in scenarios:
            planner = MockAgentPlanner(sc, "group-1")
            last_obs = None
            concluded = False
            for turn in range(10):
                step = planner.plan_next_action(all_tools, last_observation=last_obs)
                if step.get("is_final"):
                    concluded = True
                    self.assertIn("ROOT CAUSE" if sc != "poisonedEntity" else "GROUNDING VERDICT", step["diagnosis"])
                    break
                last_obs = f"Simulated output for {step['tool_call']['name']}"
            self.assertTrue(concluded, f"Scenario {sc} did not conclude within 10 turns.")


class TestPromptAssembly(unittest.TestCase):
    """Verifies system prompt directive synthesis and memory flag behaviors."""

    def test_memory_recall_flag_injects_prompt_directive(self):
        # Case 1: enable_memory_recall=True, enable_memory=False -> memory directive present
        cfg1 = {"enable_memory_recall": True, "enable_memory": False}
        prompt1 = assemble_system_prompt(cfg1, "episodicRecurrence")
        self.assertIn("EPISODIC MEMORY DIRECTIVE (MEMORY ACTIVE)", prompt1)
        self.assertIn("Episodic memory recall is ENABLED", prompt1)

        # Case 2: enable_memory_recall=False, enable_memory=False -> directive absent
        cfg2 = {"enable_memory_recall": False, "enable_memory": False}
        prompt2 = assemble_system_prompt(cfg2, "episodicRecurrence")
        self.assertNotIn("EPISODIC MEMORY DIRECTIVE", prompt2)

        # Case 3: legacy enable_memory=True fallback -> directive present
        cfg3 = {"enable_memory": True}
        prompt3 = assemble_system_prompt(cfg3, "episodicRecurrence")
        self.assertIn("EPISODIC MEMORY DIRECTIVE (MEMORY ACTIVE)", prompt3)


if __name__ == "__main__":
    unittest.main()

