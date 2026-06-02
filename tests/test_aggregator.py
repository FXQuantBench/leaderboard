from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aggregator import aggregate_repository


class AggregatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.now = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_models_file(self, payload: dict[str, dict[str, str]]) -> None:
        (self.repo_root / "models.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def write_eval(self, model_id: str, date: str, payload: dict[str, object]) -> None:
        eval_path = self.repo_root / "model_results" / model_id / "results" / "eval" / f"{date}.json"
        eval_path.parent.mkdir(parents=True, exist_ok=True)
        defaults = {
            "run_id": date,
            "mode": "eval",
            "model_id": model_id,
            "strategy_sha": "sha-a",
            "start_date": date,
            "end_date": date,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "calmar_ratio": 0.0,
            "annualized_return": 0.0,
            "volatility": 0.0,
            "total_trades": 0,
            "avg_spread_cost_pips": 0.0,
            "runtime_seconds": 0.1,
            "completed_at": "2026-06-02T00:00:00Z",
            "timed_out": False,
        }
        defaults.update(payload)
        eval_path.write_text(json.dumps(defaults, indent=2) + "\n", encoding="utf-8")

    def read_json(self, relative_path: str) -> object:
        return json.loads((self.repo_root / relative_path).read_text(encoding="utf-8"))

    def test_aggregates_three_day_history_with_hand_computed_sharpe(self) -> None:
        self.write_models_file(
            {
                "alpha": {
                    "provider": "openai",
                    "repo_url": "https://github.com/fxquantbench/alpha",
                }
            }
        )
        self.write_eval("alpha", "2026-06-01", {"annualized_return": 0.252, "win_rate": 0.25, "total_trades": 2})
        self.write_eval("alpha", "2026-06-02", {"annualized_return": 0.504, "win_rate": 0.50, "total_trades": 3})
        self.write_eval("alpha", "2026-06-03", {"annualized_return": 0.0, "win_rate": 1.0, "total_trades": 5})

        aggregate_repository(self.repo_root, now=self.now)

        leaderboard = self.read_json("data/leaderboard.json")
        model = leaderboard["models"][0]
        daily_returns = [0.001, 0.002, 0.0]
        expected_mean = sum(daily_returns) / 3
        expected_variance = ((0.001 - expected_mean) ** 2 + (0.002 - expected_mean) ** 2 + (0.0 - expected_mean) ** 2) / 2
        expected_sharpe = (expected_mean / math.sqrt(expected_variance)) * math.sqrt(252)

        self.assertEqual(leaderboard["updated_at"], "2026-06-02T12:00:00Z")
        self.assertEqual(model["model_id"], "alpha")
        self.assertAlmostEqual(model["sharpe"], expected_sharpe)
        self.assertAlmostEqual(model["win_rate"], (0.25 + 0.50 + 1.0) / 3)
        self.assertEqual(model["total_trades"], 10)
        self.assertEqual(model["days_active"], 3)
        self.assertEqual(model["provider"], "openai")
        self.assertEqual(model["repo_url"], "https://github.com/fxquantbench/alpha")

    def test_equity_curve_stays_continuous_across_strategy_changes(self) -> None:
        self.write_models_file(
            {
                "alpha": {
                    "provider": "openai",
                    "repo_url": "https://github.com/fxquantbench/alpha",
                }
            }
        )
        self.write_eval("alpha", "2026-06-01", {"annualized_return": 0.252, "strategy_sha": "sha-a"})
        self.write_eval("alpha", "2026-06-02", {"annualized_return": 0.252, "strategy_sha": "sha-b"})

        aggregate_repository(self.repo_root, now=self.now)

        equity = self.read_json("data/alpha_equity.json")
        leaderboard = self.read_json("data/leaderboard.json")

        self.assertAlmostEqual(equity[0]["equity"], 100.1)
        self.assertAlmostEqual(equity[1]["equity"], 100.2001)
        self.assertEqual(leaderboard["models"][0]["current_strategy_version"], "sha-b")

    def test_timed_out_results_are_skipped(self) -> None:
        self.write_models_file(
            {
                "alpha": {
                    "provider": "openai",
                    "repo_url": "https://github.com/fxquantbench/alpha",
                }
            }
        )
        self.write_eval("alpha", "2026-06-01", {"annualized_return": 0.252, "total_trades": 3})
        self.write_eval("alpha", "2026-06-02", {"annualized_return": 0.504, "total_trades": 99, "timed_out": True})

        aggregate_repository(self.repo_root, now=self.now)

        leaderboard = self.read_json("data/leaderboard.json")
        equity = self.read_json("data/alpha_equity.json")

        self.assertEqual(leaderboard["models"][0]["days_active"], 1)
        self.assertEqual(leaderboard["models"][0]["total_trades"], 3)
        self.assertEqual(len(equity), 1)

    def test_models_are_sorted_by_sharpe_descending(self) -> None:
        self.write_models_file(
            {
                "alpha": {
                    "provider": "openai",
                    "repo_url": "https://github.com/fxquantbench/alpha",
                },
                "beta": {
                    "provider": "anthropic",
                    "repo_url": "https://github.com/fxquantbench/beta",
                },
            }
        )
        self.write_eval("alpha", "2026-06-01", {"annualized_return": 0.252})
        self.write_eval("alpha", "2026-06-02", {"annualized_return": 0.504})
        self.write_eval("alpha", "2026-06-03", {"annualized_return": 0.0})
        self.write_eval("beta", "2026-06-01", {"annualized_return": 0.0})
        self.write_eval("beta", "2026-06-02", {"annualized_return": 0.0})
        self.write_eval("beta", "2026-06-03", {"annualized_return": 0.0})

        aggregate_repository(self.repo_root, now=self.now)

        leaderboard = self.read_json("data/leaderboard.json")
        self.assertEqual([model["model_id"] for model in leaderboard["models"]], ["alpha", "beta"])

    def test_previous_snapshot_rolls_forward_before_overwrite(self) -> None:
        self.write_models_file(
            {
                "alpha": {
                    "provider": "openai",
                    "repo_url": "https://github.com/fxquantbench/alpha",
                }
            }
        )
        self.write_eval("alpha", "2026-06-01", {"annualized_return": 0.252})

        aggregate_repository(self.repo_root, now=self.now)
        first_leaderboard = self.read_json("data/leaderboard.json")
        first_previous = self.read_json("data/leaderboard_prev.json")

        self.assertEqual(first_previous["models"], [])

        self.write_eval("alpha", "2026-06-02", {"annualized_return": 0.504})
        aggregate_repository(self.repo_root, now=datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc))

        second_previous = self.read_json("data/leaderboard_prev.json")
        self.assertEqual(second_previous, first_leaderboard)


if __name__ == "__main__":
    unittest.main()