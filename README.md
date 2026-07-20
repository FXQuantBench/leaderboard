# FXQuantBench Leaderboard

Public static leaderboard for the FXQuantBench forex quant dev benchmark.

The repository ingests daily eval JSON files from model repos, recomputes cumulative model metrics, writes generated artifacts into `data/`, and serves a GitHub Pages frontend from those generated files.

## Data contract

- Eval inputs must live at `model_results/{model_id}/results/eval/{YYYY-MM-DD}.json`.
- The aggregator skips any eval file where `timed_out` is `true`.
- Per-model equity always compounds forward from 100 and does not reset when `strategy_sha` changes.
- Generated outputs are:
	- `data/leaderboard.json`
	- `data/leaderboard_prev.json`
	- `data/{model_id}_equity.json`

## Model metadata

`models.json` is the checked-in public metadata manifest keyed by `model_id`. It provides the fields that are not present in upstream eval JSONs:

```json
{
	"gemini-3-flash-preview": {
		"provider": "google",
		"repo_url": "https://github.com/fxquantbench/gemini-3-flash-preview"
	}
}
```

Any model that should render correctly on the public leaderboard needs a matching `models.json` entry.

## Local usage

Generate leaderboard data:

```bash
python aggregator.py
```

Run the focused aggregation tests:

```bash
python -m unittest tests.test_aggregator
```

Preview the static site locally:

```bash
python -m http.server 8080
```

Then open `http://localhost:8080`.

`file://` is not a supported preview mode for the frontend because the page fetches JSON data from `./data/`.

## Archiving a model

### GitHub Actions

In GitHub, open **Actions**, select **Archive Model Performance**, click **Run workflow**, and enter the active model ID. The workflow always checks out the latest `main`, creates `archive/<model_id>`, and pushes the archive branch before removing the model from `main`.

The workflow uses `BENCHMARK_BOT_TOKEN`, which must be configured as a repository Actions secret with permission to create branches and push to `main`. Archive operations and normal aggregation share a write lock, so GitHub queues either workflow until the other has finished.

### Local command

Archive a model from a clean, current `main` checkout:

```bash
python archive_model.py <model_id>
```

The command fetches `origin/main` and refuses to proceed unless the local worktree is clean and local `main` exactly matches it. It also requires a configured, pushable `origin` remote and credentials that can create branches and push to `main`.

For `python archive_model.py gemini-3-flash-preview`, the command:

1. Creates and pushes `archive/gemini-3-flash-preview` from the current `main` commit. The branch is a full pre-removal snapshot, including the model's eval and backtest results, metadata, generated equity file, and leaderboard snapshots.
2. Removes `model_results/gemini-3-flash-preview/`, the matching `models.json` entry, and `data/gemini-3-flash-preview_equity.json` from `main`.
3. Regenerates the shared leaderboard artifacts, commits the cleanup as `archive: retire gemini-3-flash-preview`, and pushes `main`.

No Git history is rewritten. Archive branches are immutable by convention; protect the `archive/*` pattern in repository settings if that must be enforced. If a remote operation fails after the archive branch is pushed, the command does not roll back local changes or delete the archive branch. Inspect the checkout and retry or repair the failed push deliberately.

## GitHub Actions

- `.github/workflows/aggregate.yml` runs on pushes to eval JSON inputs, regenerates `data/`, and commits updated artifacts back to `main` using `BENCHMARK_BOT_TOKEN`.
- `.github/workflows/pages.yml` packages `index.html` and `data/` and deploys them with GitHub Pages via `actions/deploy-pages`.

After the first deployment workflow runs, the repo owner must set **Settings -> Pages -> Source** to **GitHub Actions**.

## Contributing

New model submissions are tracked through the org issue template:

- https://github.com/FXQuantBench/.github/issues/new?template=new-model-request.md