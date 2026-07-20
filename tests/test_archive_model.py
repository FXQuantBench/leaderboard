from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aggregator import aggregate_repository
from archive_model import ArchiveError, archive_model, remove_active_model_files, validate_preconditions


class ArchiveModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.write_models_file(
            {
                "alpha": {"provider": "openai", "repo_url": "https://example.com/alpha"},
                "beta": {"provider": "google", "repo_url": "https://example.com/beta"},
            }
        )
        self.write_eval("alpha", "2026-07-01")
        self.write_eval("beta", "2026-07-01")
        aggregate_repository(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_models_file(self, models: dict[str, dict[str, str]]) -> None:
        (self.repo_root / "models.json").write_text(json.dumps(models, indent=2) + "\n", encoding="utf-8")

    def write_eval(self, model_id: str, date: str) -> None:
        eval_path = self.repo_root / "model_results" / model_id / "results" / "eval" / f"{date}.json"
        eval_path.parent.mkdir(parents=True, exist_ok=True)
        eval_path.write_text(
            json.dumps(
                {
                    "annualized_return": 0.252,
                    "win_rate": 0.5,
                    "total_trades": 1,
                    "strategy_sha": "strategy",
                    "timed_out": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def test_removal_preserves_other_models_and_regenerates_active_leaderboard(self) -> None:
        alpha_backtest = self.repo_root / "model_results" / "alpha" / "results" / "backtest" / "saved.json"
        alpha_backtest.parent.mkdir(parents=True, exist_ok=True)
        alpha_backtest.write_text("{}\n", encoding="utf-8")

        remove_active_model_files(self.repo_root, "alpha")
        aggregate_repository(self.repo_root)

        models = json.loads((self.repo_root / "models.json").read_text(encoding="utf-8"))
        leaderboard = json.loads((self.repo_root / "data" / "leaderboard.json").read_text(encoding="utf-8"))

        self.assertNotIn("alpha", models)
        self.assertFalse((self.repo_root / "model_results" / "alpha").exists())
        self.assertFalse((self.repo_root / "data" / "alpha_equity.json").exists())
        self.assertTrue((self.repo_root / "model_results" / "beta").is_dir())
        self.assertTrue((self.repo_root / "data" / "beta_equity.json").is_file())
        self.assertEqual([model["model_id"] for model in leaderboard["models"]], ["beta"])

    def test_preconditions_reject_a_dirty_worktree(self) -> None:
        with patch("archive_model.run_git", return_value=" M models.json"):
            with self.assertRaisesRegex(ArchiveError, "Working tree must be clean"):
                validate_preconditions(self.repo_root, "alpha")

    def test_preconditions_require_current_main_and_no_existing_archive_branch(self) -> None:
        with patch(
            "archive_model.run_git",
            side_effect=["", "main", "", "same-sha", "same-sha", "archive/alpha"],
        ):
            with self.assertRaisesRegex(ArchiveError, "Local archive branch already exists"):
                validate_preconditions(self.repo_root, "alpha")

    def test_archive_pushes_snapshot_before_committing_main_cleanup(self) -> None:
        with (
            patch("archive_model.validate_preconditions"),
            patch("archive_model.remove_active_model_files"),
            patch("archive_model.aggregate_repository"),
            patch("archive_model.stage_active_cleanup"),
            patch("archive_model.run_git", return_value="") as run_git,
        ):
            archive_model(self.repo_root, "alpha")

        self.assertEqual(
            run_git.call_args_list,
            [
                call(self.repo_root, "branch", "archive/alpha", "HEAD"),
                call(self.repo_root, "push", "origin", "archive/alpha"),
                call(self.repo_root, "commit", "-m", "archive: retire alpha"),
                call(self.repo_root, "push", "origin", "HEAD:main"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
