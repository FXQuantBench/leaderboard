from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import stdev
from typing import Any

TRADING_DAYS_PER_YEAR = 252
INPUT_GLOB = "model_results/*/results/eval/*.json"
MODEL_METADATA_FILE = "models.json"


@dataclass(frozen=True)
class EvalRecord:
    model_id: str
    date: str
    annualized_return: float
    win_rate: float
    total_trades: int
    strategy_sha: str


def _timestamp_to_iso8601(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def load_model_metadata(repo_root: Path) -> dict[str, dict[str, str]]:
    metadata_path = repo_root / MODEL_METADATA_FILE
    if not metadata_path.exists():
        return {}

    raw_metadata = _read_json(metadata_path)
    if not isinstance(raw_metadata, dict):
        raise ValueError(f"{metadata_path} must contain a JSON object keyed by model_id")

    normalized: dict[str, dict[str, str]] = {}
    for model_id, entry in raw_metadata.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Metadata entry for {model_id} must be a JSON object")
        normalized[str(model_id)] = {
            "provider": str(entry.get("provider", "")),
            "repo_url": str(entry.get("repo_url", "")),
        }
    return normalized


def discover_eval_records(repo_root: Path) -> dict[str, list[EvalRecord]]:
    records_by_model: dict[str, list[EvalRecord]] = {}

    for path in sorted(repo_root.glob(INPUT_GLOB)):
        payload = _read_json(path)
        if payload.get("timed_out") is True:
            continue

        model_id = path.parent.parent.parent.name
        records_by_model.setdefault(model_id, []).append(
            EvalRecord(
                model_id=model_id,
                date=path.stem,
                annualized_return=float(payload.get("annualized_return", 0.0)),
                win_rate=float(payload.get("win_rate", 0.0)),
                total_trades=int(payload.get("total_trades", 0)),
                strategy_sha=str(payload.get("strategy_sha", "")),
            )
        )

    for records in records_by_model.values():
        records.sort(key=lambda record: record.date)

    return records_by_model


def compute_sharpe_ratio(daily_returns: list[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0

    volatility = stdev(daily_returns)
    if volatility == 0:
        return 0.0

    mean_return = sum(daily_returns) / len(daily_returns)
    return (mean_return / volatility) * math.sqrt(TRADING_DAYS_PER_YEAR)


def compute_max_drawdown(equity_values: list[float]) -> float:
    peak = 100.0
    max_drawdown = 0.0

    for equity in equity_values:
        peak = max(peak, equity)
        if peak == 0:
            continue
        drawdown = (peak - equity) / peak
        max_drawdown = max(max_drawdown, drawdown)

    return max_drawdown


def compute_calmar_ratio(ending_equity: float, days_active: int, max_drawdown: float) -> float:
    if days_active == 0 or max_drawdown == 0 or ending_equity <= 0:
        return 0.0

    cagr = (ending_equity / 100.0) ** (TRADING_DAYS_PER_YEAR / days_active) - 1.0
    return cagr / max_drawdown


def build_outputs(
    records_by_model: dict[str, list[EvalRecord]],
    metadata_by_model: dict[str, dict[str, str]],
    updated_at: str,
) -> tuple[dict[str, Any], dict[str, list[dict[str, float | str]]]]:
    models: list[dict[str, Any]] = []
    equity_by_model: dict[str, list[dict[str, float | str]]] = {}

    for model_id, records in records_by_model.items():
        if not records:
            continue

        daily_returns = [record.annualized_return / TRADING_DAYS_PER_YEAR for record in records]
        equity_points: list[dict[str, float | str]] = []
        equity = 100.0

        for record, daily_return in zip(records, daily_returns):
            equity *= 1.0 + daily_return
            equity_points.append({"date": record.date, "equity": equity})

        equity_values = [float(point["equity"]) for point in equity_points]
        model_metadata = metadata_by_model.get(model_id, {})
        models.append(
            {
                "model_id": model_id,
                "provider": model_metadata.get("provider", ""),
                "sharpe": compute_sharpe_ratio(daily_returns),
                "max_drawdown": compute_max_drawdown(equity_values),
                "win_rate": sum(record.win_rate for record in records) / len(records),
                "calmar_ratio": compute_calmar_ratio(equity_values[-1], len(records), compute_max_drawdown(equity_values)),
                "total_trades": sum(record.total_trades for record in records),
                "days_active": len(records),
                "current_strategy_version": records[-1].strategy_sha,
                "repo_url": model_metadata.get("repo_url", ""),
            }
        )
        equity_by_model[model_id] = equity_points

    models.sort(key=lambda item: (-float(item["sharpe"]), str(item["model_id"])))
    return {"updated_at": updated_at, "models": models}, equity_by_model


def write_outputs(
    repo_root: Path,
    leaderboard_payload: dict[str, Any],
    equity_by_model: dict[str, list[dict[str, float | str]]],
) -> None:
    data_dir = repo_root / "data"
    leaderboard_path = data_dir / "leaderboard.json"
    previous_path = data_dir / "leaderboard_prev.json"

    data_dir.mkdir(parents=True, exist_ok=True)

    if leaderboard_path.exists():
        shutil.copyfile(leaderboard_path, previous_path)
    elif not previous_path.exists():
        _write_json(previous_path, {"updated_at": leaderboard_payload["updated_at"], "models": []})

    _write_json(leaderboard_path, leaderboard_payload)

    for model_id, equity_points in equity_by_model.items():
        _write_json(data_dir / f"{model_id}_equity.json", equity_points)


def aggregate_repository(repo_root: Path, now: datetime | None = None) -> dict[str, Any]:
    metadata_by_model = load_model_metadata(repo_root)
    records_by_model = discover_eval_records(repo_root)
    updated_at = _timestamp_to_iso8601(now)
    leaderboard_payload, equity_by_model = build_outputs(records_by_model, metadata_by_model, updated_at)
    write_outputs(repo_root, leaderboard_payload, equity_by_model)
    return leaderboard_payload


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    aggregate_repository(repo_root)


if __name__ == "__main__":
    main()
