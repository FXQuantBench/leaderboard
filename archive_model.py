from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from aggregator import aggregate_repository

MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class ArchiveError(RuntimeError):
    """Raised when an archive operation cannot be completed safely."""


def run_git(repo_root: Path, *arguments: str) -> str:
    command = ("git", *arguments)
    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as error:
        details = error.stderr.strip() or error.stdout.strip() or "no output"
        raise ArchiveError(f"Git command failed ({' '.join(command)}): {details}") from error
    return result.stdout.strip()


def validate_model_id(model_id: str) -> None:
    if not MODEL_ID_PATTERN.fullmatch(model_id):
        raise ArchiveError(
            "Model ID must contain only letters, numbers, dots, underscores, and hyphens, "
            "and must start with a letter or number."
        )


def archive_branch_name(model_id: str) -> str:
    return f"archive/{model_id}"


def load_models(repo_root: Path) -> dict[str, Any]:
    models_path = repo_root / "models.json"
    try:
        with models_path.open("r", encoding="utf-8") as handle:
            models = json.load(handle)
    except FileNotFoundError as error:
        raise ArchiveError(f"Missing model metadata file: {models_path}") from error
    except json.JSONDecodeError as error:
        raise ArchiveError(f"Invalid JSON in {models_path}: {error}") from error

    if not isinstance(models, dict):
        raise ArchiveError(f"{models_path} must contain a JSON object keyed by model ID.")
    return models


def validate_preconditions(repo_root: Path, model_id: str) -> None:
    validate_model_id(model_id)

    if run_git(repo_root, "status", "--porcelain"):
        raise ArchiveError("Working tree must be clean before archiving a model.")
    if run_git(repo_root, "branch", "--show-current") != "main":
        raise ArchiveError("Archive models only from the main branch.")

    run_git(repo_root, "fetch", "origin", "main")
    if run_git(repo_root, "rev-parse", "HEAD") != run_git(repo_root, "rev-parse", "origin/main"):
        raise ArchiveError("Local main must exactly match origin/main before archiving a model.")

    models = load_models(repo_root)
    if model_id not in models:
        raise ArchiveError(f"Model {model_id!r} is not an active entry in models.json.")
    if not (repo_root / "model_results" / model_id).is_dir():
        raise ArchiveError(f"Missing model result directory: model_results/{model_id}")

    branch_name = archive_branch_name(model_id)
    if run_git(repo_root, "branch", "--list", branch_name):
        raise ArchiveError(f"Local archive branch already exists: {branch_name}")
    if run_git(repo_root, "ls-remote", "--heads", "origin", f"refs/heads/{branch_name}"):
        raise ArchiveError(f"Remote archive branch already exists: {branch_name}")


def remove_active_model_files(repo_root: Path, model_id: str) -> None:
    models = load_models(repo_root)
    if model_id not in models:
        raise ArchiveError(f"Model {model_id!r} is not an active entry in models.json.")

    result_directory = repo_root / "model_results" / model_id
    if not result_directory.is_dir():
        raise ArchiveError(f"Missing model result directory: model_results/{model_id}")

    shutil.rmtree(result_directory)
    del models[model_id]
    (repo_root / "models.json").write_text(json.dumps(models, indent=2) + "\n", encoding="utf-8")

    equity_path = repo_root / "data" / f"{model_id}_equity.json"
    if equity_path.exists():
        equity_path.unlink()


def stage_active_cleanup(repo_root: Path, model_id: str) -> None:
    run_git(
        repo_root,
        "add",
        "-A",
        "--",
        "models.json",
        f"model_results/{model_id}",
        f"data/{model_id}_equity.json",
        "data/leaderboard.json",
        "data/leaderboard_prev.json",
    )


def archive_model(repo_root: Path, model_id: str) -> None:
    validate_preconditions(repo_root, model_id)
    branch_name = archive_branch_name(model_id)

    run_git(repo_root, "branch", branch_name, "HEAD")
    run_git(repo_root, "push", "origin", branch_name)

    try:
        remove_active_model_files(repo_root, model_id)
        aggregate_repository(repo_root)
        stage_active_cleanup(repo_root, model_id)
        run_git(repo_root, "commit", "-m", f"archive: retire {model_id}")
        run_git(repo_root, "push", "origin", "HEAD:main")
    except Exception as error:
        raise ArchiveError(
            f"Archive branch {branch_name} was pushed, but main was not fully updated. "
            "No rollback was attempted; inspect the local checkout before retrying."
        ) from error

    print(f"Archived {model_id} on {branch_name} and removed it from main.")


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive one model's results on a dedicated branch and remove it from main."
    )
    parser.add_argument("model_id", help="Active model ID to archive")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    parsed_arguments = parse_arguments(arguments)
    repo_root = Path(__file__).resolve().parent
    try:
        archive_model(repo_root, parsed_arguments.model_id)
    except ArchiveError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
