"""Offline unit tests for optional AI proposal and validation behavior."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from o3ai.ai_planner import AIPlannerError, create_ai_plan


class AIPlannerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"O3AI_AI_API_KEY": "test-key", "O3AI_AI_MODEL": "test-model",
                                           "O3AI_AI_BASE_URL": "https://ai.example.test/v1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    @patch("o3ai.ai_planner.urllib.request.urlopen")
    def test_valid_plan_is_proposal_only_and_dag_checked(self, urlopen):
        content = json.dumps({"summary": "Do a task", "tasks": [
            {"task_id": "a", "name": "Plan", "description": "plan", "dependencies": []},
            {"task_id": "b", "name": "Build", "description": "build", "dependencies": ["a"]}],
            "evidence_questions": ["Did tests pass?"]})
        response_data = {"choices": [{"message": {"content": content}}]}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self): return json.dumps(response_data).encode()
        urlopen.return_value = Response()
        result = create_ai_plan("Add a feature")
        self.assertEqual(result["status"], "PROPOSAL_ONLY")
        self.assertEqual(len(result["tasks"]), 2)

    @patch("o3ai.ai_planner.urllib.request.urlopen")
    def test_cycle_is_rejected(self, urlopen):
        plan = {"summary": "bad", "tasks": [
            {"task_id": "a", "name": "A", "description": "", "dependencies": ["b"]},
            {"task_id": "b", "name": "B", "description": "", "dependencies": ["a"]}], "evidence_questions": []}
        response_data = {"choices": [{"message": {"content": json.dumps(plan)}}]}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self): return json.dumps(response_data).encode()
        urlopen.return_value = Response()
        with self.assertRaises(AIPlannerError):
            create_ai_plan("A goal")

    def test_requires_credentials(self):
        with patch.dict(os.environ, {"O3AI_AI_API_KEY": "", "O3AI_AI_MODEL": ""}):
            with self.assertRaises(AIPlannerError):
                create_ai_plan("A goal")


if __name__ == "__main__":
    unittest.main()
