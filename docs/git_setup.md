# Git Setup Notes

This document explains how to apply the `.gitignore` change so that
`outputs/` is tracked while large data folders remain ignored.

## What changed

Previously the `.gitignore` listed `outputs/` as ignored. Now it is
tracked, with `data_raw/`, `data_clean/`, and `intermediate/` still
ignored. The reasoning is recorded inline in the new `.gitignore`.

## Required steps after pulling the new `.gitignore`

Git does not automatically start tracking files that were previously
ignored when an ignore rule is removed. Run these commands once:

```bash
# Step 1: Pull / merge the updated .gitignore
git pull

# Step 2: Tell git to forget anything currently in the cache that
# might shadow the new rules (safe; does not delete files on disk).
git rm -r --cached . 2>/dev/null
git add .

# Step 3: Confirm what will be committed.
git status

# At this point the staged file list should include:
#   outputs/scenarios/baseline_2025/...
#   intermediate/.gitkeep
# It should NOT include any:
#   data_raw/*.gpkg, data_raw/*.xlsx
#   data_clean/*.csv, data_clean/*.gpkg
#   docs/notes_zh.md
#   __pycache__/

# Step 4: Commit and push.
git add .
git commit -m "Track outputs/ on GitHub; keep raw and intermediate data ignored"
git push
```

## What to expect on push

The first push that includes `outputs/` will upload roughly **2 to 3 MB**
of new files. Two largish files:

- `outputs/scenarios/baseline_2025/geojson/lower_districts.geojson` (~1.4 MB)
- `outputs/scenarios/baseline_2025/geojson/upper_districts.geojson` (~700 KB)

Both are well under GitHub's 50 MB warning threshold. The rest are
small CSVs and JSON.

## Workflow going forward

The team convention is: **commit `outputs/` only when promoting a
release-quality version**, not on every local rerun. Day-to-day:

```bash
# Re-run the pipeline locally as needed; do NOT commit the diff.
python scripts/04_run_full_pipeline.py

# To temporarily mute git's awareness of the local outputs diff:
git update-index --skip-worktree outputs/scenarios/baseline_2025/geojson/lower_districts.geojson
git update-index --skip-worktree outputs/scenarios/baseline_2025/geojson/upper_districts.geojson

# When you are ready to publish a new version of outputs:
git update-index --no-skip-worktree outputs/scenarios/baseline_2025/geojson/lower_districts.geojson
git update-index --no-skip-worktree outputs/scenarios/baseline_2025/geojson/upper_districts.geojson
git add outputs/
git commit -m "Refresh outputs for v3.1"
```

## If you previously committed `data_raw/` or `data_clean/` files

Check the git log for any large files that slipped through earlier:

```bash
git log --all --pretty=format: --name-only --diff-filter=A | sort -u | grep -E "data_(raw|clean)/" | head
```

If anything shows up, those files are still in git history and bloating
the repo. The cleanest fix is `git filter-repo` (a separate tool):

```bash
pip install git-filter-repo
git filter-repo --path data_raw/ --invert-paths
git filter-repo --path data_clean/ --invert-paths
git push --force
```

But only do this if a `git log` actually reveals large committed files.
For a project that has only been pushed once or twice it's likely
unnecessary.
