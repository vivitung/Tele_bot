import unittest
from pathlib import Path

from codex_runner import candidate_codex_paths, resolve_codex_bin, sync_codex_home_credentials
from security import PendingTaskStore, RiskAssessment, is_authorized, parse_allowed_user_ids


class SecurityTests(unittest.TestCase):
    def test_parse_allowed_user_ids(self) -> None:
        self.assertEqual(parse_allowed_user_ids("123, 456;789"), {123, 456, 789})

    def test_authorization(self) -> None:
        self.assertTrue(is_authorized(123, {123}))
        self.assertFalse(is_authorized(456, {123}))
        self.assertFalse(is_authorized(None, {123}))

    def test_safe_prompt_does_not_require_confirmation(self) -> None:
        assessment = RiskAssessment.from_prompt("解释这个目录结构")
        self.assertFalse(assessment.requires_confirmation)

    def test_risky_prompt_requires_confirmation(self) -> None:
        assessment = RiskAssessment.from_prompt("删除所有文件")
        self.assertTrue(assessment.requires_confirmation)
        self.assertTrue(assessment.reasons)

    def test_pending_task_belongs_to_user(self) -> None:
        store = PendingTaskStore()
        task = store.add(123, "删除所有文件", ("中文高风险操作描述",))

        self.assertIsNone(store.pop(task.task_id, 456))
        self.assertEqual(store.count_for_user(123), 1)
        self.assertEqual(store.pop(task.task_id, 123), task)
        self.assertEqual(store.count_for_user(123), 0)

    def test_resolve_codex_bin_from_config(self) -> None:
        python_exe = Path(__import__("sys").executable)
        self.assertEqual(resolve_codex_bin(str(python_exe)), python_exe.resolve())

    def test_resolve_codex_bin_falls_back_when_config_is_stale(self) -> None:
        resolved = resolve_codex_bin(r"C:\this\path\does\not\exist\codex.exe")
        self.assertTrue(resolved.exists())

    def test_candidate_codex_paths_are_paths(self) -> None:
        self.assertTrue(all(isinstance(path, Path) for path in candidate_codex_paths()))

    def test_sync_codex_home_credentials_noops_when_source_equals_target(self) -> None:
        target = Path(".codex-home")
        self.assertEqual(sync_codex_home_credentials(target, target), [])


if __name__ == "__main__":
    unittest.main()
