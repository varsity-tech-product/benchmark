"""Extract 7D tutor scores from score reports and compute inter-dimension correlations."""

import re
from pathlib import Path

import numpy as np

RESULTS_ROOT = Path(__file__).parent.parent / "results" / "run-single" / "anthropic"
AGENTS = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
JUDGES = ["scores_sonnet.md", "scores_haiku.md"]
DIMS = [
    "D1_level_detection",
    "D2_language_adaptation",
    "D3_scaffolding_calibration",
    "D4_domain_accuracy",
    "D5_code_teaching",
    "D6_empathetic_response",
    "D7_safety_boundaries",
]

DIM_PATTERN = re.compile(r"\|\s*(D[1-7]_\w+)\s*\|\s*([\d.]+)\s*\|")


def extract_7d_scores(filepath: Path) -> dict[str, float] | None:
    """Parse a scores_*.md file and extract 7D tutor scores."""
    try:
        text = filepath.read_text()
    except FileNotFoundError:
        return None

    scores = {}
    for m in DIM_PATTERN.finditer(text):
        dim_name = m.group(1)
        score = float(m.group(2))
        if dim_name in DIMS:
            scores[dim_name] = score

    if len(scores) == 7:
        return scores
    return None


def collect_all_scores():
    """Walk the results tree and collect all 7D score vectors."""
    records = []

    for agent in AGENTS:
        agent_dir = RESULTS_ROOT / agent
        if not agent_dir.exists():
            continue

        for score_file in sorted(agent_dir.rglob("scores_*.md")):
            # Skip sonnet_original
            if "original" in str(score_file):
                continue

            judge = score_file.name  # scores_sonnet.md or scores_haiku.md
            judge_name = "sonnet" if "sonnet" in judge else "haiku"

            # Extract path info
            rel = score_file.relative_to(agent_dir)
            _parts = list(  # noqa: F841
                rel.parts
            )  # e.g. ['data_analysis', 'D01_...', 'intermediate_run1', 'scores_sonnet.md']

            scores = extract_7d_scores(score_file)
            if scores is None:
                continue

            records.append(
                {
                    "agent": agent.split("-")[1],  # sonnet or haiku
                    "judge": judge_name,
                    "path": str(score_file.relative_to(RESULTS_ROOT)),
                    **scores,
                }
            )

    return records


def print_correlation_matrix(records, label="All"):
    """Compute and print Pearson correlation matrix for 7D scores."""
    if not records:
        print(f"\n[{label}] No records found.")
        return

    n = len(records)
    # Build matrix: n_samples x 7
    matrix = np.array([[r[d] for d in DIMS] for r in records])

    print(f"\n{'='*70}")
    print(f"  {label}  (n={n} observations)")
    print(f"{'='*70}")

    # Pearson correlation
    corr = np.corrcoef(matrix, rowvar=False)

    # Print header
    short = ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]
    print(f"\n{'':>6}", end="")
    for s in short:
        print(f"{s:>8}", end="")
    print()

    for i, s in enumerate(short):
        print(f"{s:>6}", end="")
        for j in range(7):
            val = corr[i, j]
            if i == j:
                print(f"{'—':>8}", end="")
            else:
                print(f"{val:>8.3f}", end="")
        print()

    # Highlight D2-D3
    r23 = corr[1, 2]
    print(f"\n  >>> D2-D3 Pearson r = {r23:.4f}")

    # Also show mean of all off-diagonal
    mask = ~np.eye(7, dtype=bool)
    mean_r = corr[mask].mean()
    print(f"  >>> Mean off-diagonal r = {mean_r:.4f}")

    # Show D1-D2 and D1-D3 for context
    print(f"  >>> D1-D2 r = {corr[0,1]:.4f}")
    print(f"  >>> D1-D3 r = {corr[0,2]:.4f}")
    print(f"  >>> D2-D6 r = {corr[1,5]:.4f}  (language-empathy, expect moderate)")
    print(f"  >>> D4-D5 r = {corr[3,4]:.4f}  (accuracy-code, expect moderate)")

    # Descriptive stats
    print("\n  Descriptive stats (normalized 0-1 scores):")
    for i, d in enumerate(DIMS):
        col = matrix[:, i]
        print(
            f"    {d}: mean={col.mean():.3f}  std={col.std():.3f}  min={col.min():.3f}  max={col.max():.3f}"
        )

    return corr


def main():
    records = collect_all_scores()
    print(f"Total records collected: {len(records)}")

    # Overall
    print_correlation_matrix(records, "ALL (both agents × both judges)")

    # By judge
    for judge in ["sonnet", "haiku"]:
        subset = [r for r in records if r["judge"] == judge]
        print_correlation_matrix(subset, f"Judge={judge}")

    # By agent
    for agent in ["sonnet", "haiku"]:
        subset = [r for r in records if r["agent"] == agent]
        print_correlation_matrix(subset, f"Agent={agent}")

    # Agent+Judge combos
    for agent in ["sonnet", "haiku"]:
        for judge in ["sonnet", "haiku"]:
            subset = [r for r in records if r["agent"] == agent and r["judge"] == judge]
            print_correlation_matrix(subset, f"Agent={agent}, Judge={judge}")


if __name__ == "__main__":
    main()
