"""Offline unit tests for optional AI proposal and strict validation behavior."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from integrations.ai_planner import AIPlannerError, create_ai_plan


def _mock_response(payload: object):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return Response()


class AIPlannerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "O3AI_AI_API_KEY": "test-key",
                "O3AI_AI_MODEL": "test-model",
                "O3AI_AI_BASE_URL": "https://ai.example.test/v1",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    @patch("integrations.ai_planner.urllib.request.urlopen")
    def test_valid_plan_is_proposal_only_and_dag_checked(self, urlopen):
        content = json.dumps(
            {
                "summary": "Do a task",
                "tasks": [
                    {"task_id": "a", "name": "Plan", "description": "plan", "dependencies": []},
                    {"task_id": "b", "name": "Build", "description": "build", "dependencies": ["a"]},
                ],
                "evidence_questions": ["Did tests pass?"],
            }
        )
        urlopen.return_value = _mock_response({"choices": [{"message": {"content": content}}]})
        result = create_ai_plan("Add a feature")
        self.assertEqual(result["status"], "PROPOSAL_ONLY")
        self.assertFalse(result["is_evidence"])
        self.assertFalse(result["can_authorize_payment"])
        self.assertEqual(len(result["tasks"]), 2)

        req = urlopen.call_args[0][0]
        sent_body = json.loads(req.data.decode("utf-8"))
        user_msg = sent_body["messages"][1]["content"]
        self.assertIn("<<<UNTRUSTED_INPUT_START>>>", user_msg)
        self.assertIn("<<<UNTRUSTED_INPUT_END>>>", user_msg)

    @patch("integrations.ai_planner.urllib.request.urlopen")
    def test_cycle_is_rejected(self, urlopen):
        plan = {
            "summary": "bad",
            "tasks": [
                {"task_id": "a", "name": "A", "description": "", "dependencies": ["b"]},
                {"task_id": "b", "name": "B", "description": "", "dependencies": ["a"]},
            ],
            "evidence_questions": [],
        }
        urlopen.return_value = _mock_response({"choices": [{"message": {"content": json.dumps(plan)}}]})
        with self.assertRaises(AIPlannerError):
            create_ai_plan("A goal")

    @patch("integrations.ai_planner.urllib.request.urlopen")
    def test_unknown_dependency_and_invalid_json_rejected(self, urlopen):
        plan = {
            "summary": "bad dep",
            "tasks": [
                {"task_id": "a", "name": "A", "description": "", "dependencies": ["missing"]},
            ],
            "evidence_questions": [],
        }
        urlopen.return_value = _mock_response({"choices": [{"message": {"content": json.dumps(plan)}}]})
        with self.assertRaises(AIPlannerError):
            create_ai_plan("A goal")

        urlopen.return_value = _mock_response({"choices": [{"message": {"content": "{not-json"}}]})
        with self.assertRaises(AIPlannerError):
            create_ai_plan("A goal")

    @patch("integrations.ai_planner.urllib.request.urlopen")
    def test_rejects_status_or_approval_assertions_and_field_limits(self, urlopen):
        for bad_plan in [
            {
                "summary": "sneaky",
                "status": "PASS",
                "tasks": [{"task_id": "a", "name": "A", "description": "", "dependencies": []}],
                "evidence_questions": [],
            },
            {
                "summary": "sneaky task",
                "tasks": [
                    {
                        "task_id": "a",
                        "name": "A",
                        "description": "",
                        "dependencies": [],
                        "human_approved": True,
                    }
                ],
                "evidence_questions": [],
            },
            {
                "summary": "too many questions",
                "tasks": [{"task_id": "a", "name": "A", "description": "", "dependencies": []}],
                "evidence_questions": ["q1", "q2", "q3", "q4", "q5", "q6"],
            },
        ]:
            urlopen.return_value = _mock_response({"choices": [{"message": {"content": json.dumps(bad_plan)}}]})
            with self.subTest(bad_plan=bad_plan), self.assertRaises(AIPlannerError):
                create_ai_plan("A goal")

    def test_requires_credentials_https_and_protects_evidence_files(self):
        with patch.dict(os.environ, {"O3AI_AI_API_KEY": "", "O3AI_AI_MODEL": ""}):
            with self.assertRaises(AIPlannerError):
                create_ai_plan("A goal")

        with patch.dict(os.environ, {"O3AI_AI_BASE_URL": "http://insecure.example/v1"}):
            with self.assertRaises(AIPlannerError):
                create_ai_plan("A goal")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AIPlannerError):
                create_ai_plan("A goal", output_path=Path(tmp) / "planner.json")

    @patch("integrations.ai_planner.urllib.request.urlopen")
    def test_provider_http_failure_raises_cleanly(self, urlopen):
        err = urllib.error.HTTPError("https://ai.example.test/v1", 500, "Err", {}, None)
        self.addCleanup(err.close)
        urlopen.side_effect = err
        with self.assertRaises(AIPlannerError):
            create_ai_plan("A goal")


if __name__ == "__main__":
    unittest.main()
