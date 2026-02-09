# Quant Tutor Benchmark Dataset Pipeline

A pipeline for constructing the **QuantTutorBench** evaluation dataset -- a benchmark for evaluating AI tutoring capabilities in quantitative financial education.

Target paper: **"QuantTutorBench: A Multi-Dimensional Benchmark for Evaluating Quantitative Finance Tutoring Agents"** (NeurIPS 2026 Datasets & Benchmarks Track).

---

## QuantTutorBench Specification

QuantTutorBench is the first **domain-specific tutoring benchmark for quantitative finance**. Unlike existing financial AI benchmarks (QuantBench, AI-Trader, StockBench) that evaluate autonomous trading agents, QuantTutorBench evaluates how well an AI agent **teaches** quantitative finance to human learners.

The benchmark fills a gap at the intersection of financial AI and educational AI. It measures not just whether an agent gets the right answer, but whether it explains concepts clearly, adapts to the learner's level, uses effective pedagogy, and promotes genuine understanding.

### Benchmark Structure: Two Layers

The benchmark is organized into two layers that test different capabilities.

**Layer 1 -- Core Capabilities (~2,000 items)**

Layer 1 tests the agent's ability to correctly answer and explain quantitative finance topics. Each item is a single-turn task with a question, reference answer, and evaluation metadata.

| Task Category | Count | Description | Evaluation Method |
|:---|:---:|:---|:---|
| Conceptual Q&A | 500 | Financial concept questions (e.g., "What is a Sharpe ratio?", "How does dollar-cost averaging work?") | LLM-as-Judge with rubric |
| Strategy Explanation | 300 | Quant strategy explanation tasks (e.g., "Explain pairs trading", "What is momentum factor investing?") | LLM-as-Judge with rubric |
| Code Generation | 500 | Python quant finance coding problems (e.g., "Write a function to calculate portfolio VaR") with unit test suites | Automated unit tests |
| Code Debugging | 300 | Buggy quant Python code samples with known fixes and test cases | Automated unit tests |
| Data Interpretation | 200 | Questions requiring reading financial tables, reports, and calculating values from data | LLM-as-Judge with rubric |
| Multi-step Reasoning | 200 | Multi-step financial calculations with conversational context (follow-up questions building on prior answers) | LLM-as-Judge with rubric |

**Layer 2 -- Tutoring Skills (~500 multi-turn scenarios)**

Layer 2 tests the agent's pedagogical abilities. Each item is a multi-turn conversation scenario where the agent must demonstrate specific tutoring skills. These cannot be evaluated from single-turn Q&A -- they require scripted conversation trees.

| Task Category | Count | Description | Evaluation Method |
|:---|:---:|:---|:---|
| Adaptive Explanation | 150 | Agent must detect learner confusion and re-explain at a different level or with different analogies | LLM-as-Judge (Adaptability, Patience) |
| Actionable Feedback | 100 | Agent must review learner's work (code, strategy, analysis) and give specific, constructive feedback | LLM-as-Judge (Specificity, Constructiveness) |
| Hint Generation (Socratic) | 100 | Agent must guide the learner toward the answer through questions rather than giving the answer directly | LLM-as-Judge (Effectiveness, Non-Directness) |
| Goal Clarification | 75 | Agent must ask clarifying questions when the learner's request is vague or ambiguous | LLM-as-Judge (Question Quality) |
| Error Correction | 75 | Agent must identify and correct misconceptions in the learner's reasoning | LLM-as-Judge + Automated tests |

### Five Evaluation Dimensions

The benchmark evaluates agents across five dimensions. These dimensions ensure the benchmark remains useful as foundation models improve (the "boat on the sea" principle -- as the sea level rises, the boat must demonstrably rise with it).

| Dimension | What It Measures | How It Works | Key Metric |
|:---|:---|:---|:---|
| **Sensitivity** | Does the agent scaffold add value beyond a bare LLM? | Run the same benchmark with bare LLM vs full agent, compare scores | Scaffold Value-Add (SVA) |
| **Granularity** | Which agent component contributes the most? | Ablation studies: disable one component at a time, measure impact | Component Value-Add (CVA) |
| **Durability** | Can we still differentiate agents when all scores are high? | IRT calibration with difficulty/discrimination parameters per item | Ability Score (theta) |
| **Robustness** | Is the agent resilient to adversarial/edge-case inputs? | Adversarial test items: misleading data, trick questions, jargon overload | Robustness Score |
| **Efficiency** | Can we evaluate cheaply without running all 2,500 items? | IRT-based adaptive item selection: pick the most informative items first | Evaluation Cost |

### TSR Synthesis Pipeline

Each benchmark item is enriched with three synthesized fields via LLM calls (the "TSR" pipeline). These fields are **not training data** -- they are evaluation metadata consumed by the eval harness to score agent responses.

| Synthesized Field | What It Contains | How the Eval Harness Uses It |
|:---|:---|:---|
| **Learner Profile** | Inferred knowledge level, financial background, learning goals, potential misconceptions, emotional context | Fed into all 4 LLM judge prompts (pedagogy, clarity, tone, engagement) so the judge can assess if the agent pitched its response at the right level |
| **Tutoring Strategy** | Pedagogical approach, step-by-step teaching plan, key concepts, analogies/examples, follow-up questions | `key_concepts` list becomes the checklist for the `concept_coverage` automated metric; full strategy informs the judge rubric |
| **Synthetic Response** | A model tutoring response generated by the teacher model | Used as a reference in the `correctness` metric (40% weight in automated scoring via semantic similarity) |

Analogy: grading an essay exam requires (1) the exam question, (2) a sample A+ answer, (3) a grading rubric, and (4) knowledge of who the student is. The TSR fields provide items 2-4.

### Data Source Priorities

| Priority | Source | Type | Use Case |
|:---|:---|:---|:---|
| **Tier 1** | SEC Investor.gov | Government educational content | Layer 1 golden answers (factual) |
| **Tier 1** | CFPB Educational Resources | Government financial literacy | Layer 1 golden answers (beginner) |
| **Tier 1** | FINRA Investor Education | Regulatory educational content | Layer 1 golden answers (regulatory) |
| **Tier 1** | ConvFinQA | Academic dataset (multi-turn) | Layer 2 conversational structure |
| **Tier 2** | FinQA / TAT-QA | Academic datasets (numerical) | Layer 1 numerical reasoning tasks |
| **Tier 2** | Money.StackExchange | Community Q&A | Learner profile modeling (real confusion patterns) |
| **Tier 2** | Reddit finance subreddits | Community Q&A | Diverse question styles, real learner language |
| **Tier 3** | FiQA | Research dataset (BeIR) | Layer 1 general financial Q&A |

### NeurIPS 2026 Submission Requirements

The NeurIPS Datasets & Benchmarks Track requires:

1. Code and data must be accessible at submission time (not just upon acceptance)
2. A Croissant metadata record for machine-readable dataset description
3. A datasheet documenting the dataset's creation, composition, and intended uses
4. Hosting plan for long-term availability (GitHub + HuggingFace)
5. License specification (Apache 2.0 for code, CC-BY-4.0 for data)
6. Baseline results from evaluating 8-10 models (GPT-4.1, Claude 4, Gemini 2, Llama 4, DeepSeek-V3, Qwen-3, Mistral Large, Maxwell)

---

## Current Implementation Status

This section tracks what the pipeline currently produces vs. what the full specification requires.

### Layer 1 -- Core Capabilities

| Task Category | Target | Status | Current Data Sources |
|:---|:---:|:---|:---|
| Conceptual Q&A | 500 | **Covered** | FiQA, Money.SE, Reddit, SEC/CFPB/FINRA |
| Data Interpretation | 200 | **Partially covered** -- FinQA/TAT-QA with numerical prompts | FinQA, TAT-QA |
| Multi-step Reasoning | 200 | **Partially covered** -- ConvFinQA with conversation history | ConvFinQA |
| Strategy Explanation | 300 | **Not started** | TBD |
| Code Generation | 500 | **Not started** | TBD |
| Code Debugging | 300 | **Not started** | TBD |

### Layer 2 -- Tutoring Skills

| Task Category | Target | Status |
|:---|:---:|:---|
| Adaptive Explanation | 150 | **Not started** |
| Actionable Feedback | 100 | **Not started** |
| Hint Generation (Socratic) | 100 | **Not started** |
| Goal Clarification | 75 | **Not started** |
| Error Correction | 75 | **Not started** |

The current pipeline only produces single-turn Q&A pairs. Layer 2 requires a dedicated multi-turn conversation synthesis pipeline.

### Evaluation Dimensions

| Dimension | Status | Notes |
|:---|:---|:---|
| Sensitivity | **Implemented** (eval repo) | Bare vs scaffold comparison |
| Granularity | **Implemented** (eval repo) | Component ablation studies |
| Durability | **Not implemented** | Needs IRT item parameter calibration |
| Robustness | **Not implemented** | Needs adversarial test items |
| Efficiency | **Not implemented** | Needs IRT-based adaptive item selection |

### Key Gaps to Close

1. **Code tasks (800 items, 40% of Layer 1)** -- Requires curated Python quant finance coding problems with unit test suites, plus buggy code samples with known fixes. Cannot be sourced from Stack Exchange or Reddit -- needs a dedicated code task pipeline.

2. **Multi-turn tutoring scenarios (Layer 2, 500 items)** -- Requires scripted multi-turn conversations where the agent must adapt, give hints, clarify goals, and correct errors. Needs a conversation tree synthesis pipeline.

3. **Strategy Explanation (300 items)** -- Quant strategy explanation tasks. Could be sourced from existing data with filtering/curation or synthesized from a knowledge base.

4. **IRT calibration** -- Item Response Theory parameters (difficulty, discrimination) on each item, enabling the Durability and Efficiency dimensions and the "Evergreen" pipeline for continuous difficulty adjustment.

---

## Pipeline Overview

The dataset is built in a 5-stage pipeline. Each stage reads from the previous stage's output directory:

```
data/00_raw/  -->  data/01_structured/  -->  data/02_synthesized/  -->  data/03_packaged/
  (Ingest)          (Structure)               (Synthesize TSR)          (Validate & Upload)
```

The TSR synthesis step enriches each Q&A record with a Learner Profile, Tutoring Strategy, and Synthetic Response via LLM calls. These synthesized fields serve as evaluation metadata for the downstream eval harness (see "TSR Synthesis Pipeline" in the specification above).

---

## Prerequisites

- Python 3.10+
- An [OpenRouter](https://openrouter.ai/) API key (for synthesis and LLM-assisted structuring)
- A [HuggingFace](https://huggingface.co/settings/tokens) token (for upload step)

---

## Step 1: Set Up the Environment

```bash
git clone https://github.com/varsity-tech-product/benchmark.git
cd benchmark
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pre-commit install
```

**Verify:**

```bash
python -c "import pydantic, openai, zstandard, huggingface_hub; print('OK')"
pre-commit run --all-files
```

---

## Step 2: Configure API Keys

```bash
cp .env.template .env
```

Edit `.env` and fill in your keys:

```
OPENROUTER_API_KEY=your_key_here
HF_TOKEN=your_huggingface_token_here
```

**Verify:**

```bash
python -c "from dotenv import load_dotenv; import os; load_dotenv(); assert os.getenv('OPENROUTER_API_KEY') != 'your_key_here', 'Set your API key'; print('OK')"
```

---

## Step 3: Download Raw Data

Each ingest script downloads data into `data/00_raw/`.

```bash
# Research datasets (FiQA, FinQA, ConvFinQA, TAT-QA)
python scripts/01_ingest/ingest_research_datasets.py

# Stack Exchange Money Q&A (~200 MB download)
python scripts/01_ingest/ingest_stack_exchange.py

# Reddit finance subreddits (Arctic Shift API, parallel, resumable)
python scripts/01_ingest/ingest_reddit.py                  # all 6 subreddits
python scripts/01_ingest/ingest_reddit.py --subreddit tax   # single subreddit
python scripts/01_ingest/ingest_reddit.py --workers 8       # more parallelism

# Authoritative sources (SEC, CFPB, FINRA)
python scripts/01_ingest/ingest_authoritative_docs.py
```

**Verify:**

```bash
ls data/00_raw/
# Expected: fiqa/  finqa/  convfinqa/  tatqa/  money.stackexchange.com/  reddit/  authoritative/
```

---

## Step 4: Structure the Data

Parsing scripts read from `data/00_raw/` and write JSONL to `data/01_structured/`.

```bash
# Research datasets
python scripts/02_structure/structure_research_datasets.py

# Stack Exchange
python scripts/02_structure/structure_stack_exchange.py --min-score 5

# Reddit
python scripts/02_structure/structure_reddit.py                          # all subreddits
python scripts/02_structure/structure_reddit.py --subreddit investing     # single subreddit
python scripts/02_structure/structure_reddit.py --min-post-score 20      # stricter filter
python scripts/02_structure/structure_reddit.py --sample 100             # quick test run

# Authoritative docs (includes LLM question rewriting by default)
python scripts/02_structure/structure_authoritative_docs.py
python scripts/02_structure/structure_authoritative_docs.py --skip-rewrite  # skip LLM step
```

**Verify:**

```bash
wc -l data/01_structured/*.jsonl
# Should list record counts for each source
```

### Inspect Data Quality

Run quality inspection **before** spending on synthesis:

```bash
# Inspect all structured datasets
python scripts/02_structure/inspect_quality.py

# Inspect a specific file
python scripts/02_structure/inspect_quality.py --file data/01_structured/reddit.jsonl

# Reddit-specific quality analysis
python scripts/02_structure/inspect_reddit_quality.py
```

The inspector reports: source distribution, text length stats, field completeness, quality flags (short/empty/boilerplate/link-heavy answers), and a 0-100 quality score with actionable recommendations.

---

## Step 5: Estimate Cost (Optional)

Before running full synthesis, measure actual API cost on a small sample:

```bash
# Check your OpenRouter balance
python scripts/estimate_cost.py balance

# Run 100 records and extrapolate to full dataset
python scripts/estimate_cost.py estimate --sample 100 --total 212503

# Monitor spend in real-time during a long run
python scripts/estimate_cost.py monitor --interval 60
```

---

## Step 6: Synthesize Tutoring Data

This step calls LLM APIs (via OpenRouter, using `grok-4.1-fast`) to generate for each record:
1. **Learner Profile** -- inferred knowledge level, background, goals, misconceptions
2. **Tutoring Strategy** -- pedagogical approach, key concepts, analogies, follow-up questions
3. **Synthetic Response** -- a model tutoring response

These fields are consumed by the eval harness as scoring context (LLM judge prompts, concept coverage checklist, correctness reference).

The pipeline auto-detects numerical/tabular reasoning records (FinQA, TAT-QA, ConvFinQA) and uses specialized prompts that include financial context and conversation history.

```bash
# Quick test (50 records)
python scripts/03_synthesize/synthesize_tsr.py --sample 50

# Full run
python scripts/03_synthesize/synthesize_tsr.py

# With options
python scripts/03_synthesize/synthesize_tsr.py \
    --max-concurrent 5 \
    --checkpoint-every 50
```

The synthesis is **resumable** -- if interrupted, re-run the same command to continue from the last checkpoint.

**Verify:**

```bash
wc -l data/02_synthesized/*.jsonl
```

---

## Step 7: Validate and Package

```bash
python scripts/04_validate/validate_schema.py
python scripts/04_validate/create_dataset_card.py
```

**Verify:**

```bash
ls data/03_packaged/
# Expected: quant_tutor_benchmark.jsonl  DATASET_CARD.md
```

---

## Step 8: Upload to HuggingFace

```bash
python scripts/05_upload/upload_to_huggingface.py \
    --repo-id your-org/quant-tutor-benchmark

# Or make it public
python scripts/05_upload/upload_to_huggingface.py \
    --repo-id your-org/quant-tutor-benchmark \
    --public
```

**Verify:** Open `https://huggingface.co/datasets/your-org/quant-tutor-benchmark` in your browser.

---

## Project Structure

```
benchmark/
├── configs/
│   └── prompts.yaml           # LLM prompt templates (general + numerical)
├── data/
│   ├── 00_raw/                # Downloaded raw data
│   ├── 01_structured/         # Parsed JSONL files (StructuredQA schema)
│   ├── 02_synthesized/        # LLM-augmented data (FinalBenchmarkRecord schema)
│   └── 03_packaged/           # Final validated output
├── scripts/
│   ├── 01_ingest/             # Data download scripts
│   ├── 02_structure/          # Parsing + quality inspection scripts
│   ├── 03_synthesize/         # LLM synthesis pipeline (TSR)
│   ├── 04_validate/           # Schema validation & dataset card
│   ├── 05_upload/             # HuggingFace upload
│   ├── estimate_cost.py       # API cost estimation & monitoring
│   └── lib/                   # Shared utilities
│       ├── schemas.py         # Pydantic models (StructuredQA, FinalBenchmarkRecord)
│       └── llm_utils.py       # OpenRouter API utilities
├── tests/                     # Unit tests
├── notebooks/                 # EDA notebooks
├── .env.template              # Environment template
├── .pre-commit-config.yaml    # Pre-commit hooks
├── pyproject.toml             # Tool configuration
└── requirements.txt           # Dependencies
```

## Output Record Schema

Each record in the final dataset (`FinalBenchmarkRecord`) contains:

| Field Group | Fields | Purpose |
|:---|:---|:---|
| Identifiers | `id`, `source_id`, `source_dataset` | Track provenance |
| Original Q&A | `title`, `question`, `reference_answer` | Ground truth for evaluation |
| Metadata | `tags`, `source_url`, `creation_date` | Filtering and analysis |
| Context | `context`, `conversation_history` | For numerical/conversational tasks |
| Learner Profile | `knowledge_level`, `financial_background`, `learning_goals`, `potential_misconceptions`, `emotional_context` | Eval judge context: assess if agent pitched response at right level |
| Tutoring Strategy | `approach`, `steps`, `key_concepts`, `analogies_or_examples`, `follow_up_questions` | Eval scoring: concept coverage checklist + judge rubric |
| Synthetic Response | `synthetic_response` | Eval scoring: correctness reference |
| Synthesis Metadata | `teacher_model`, `synthesis_timestamp` | Reproducibility |

## CLI Quick Reference

All scripts support `--help` for full option listing.

| Flag | Description |
|------|-------------|
| `--sample N` | Process only N records (for testing) |
| `--help` | Show all available options |

### Synthesis-specific options:

| Flag | Description |
|------|-------------|
| `--max-concurrent N` | Max parallel API calls (default: 5) |
| `--checkpoint-every N` | Save progress every N records (default: 50) |
| `--reset` | Clear checkpoint and start fresh |
| `--no-checkpoint` | Disable checkpointing |

### Reddit ingest options:

| Flag | Description |
|------|-------------|
| `--subreddit NAME` | Download a specific subreddit |
| `--workers N` | Parallel download threads (default: 4) |

### Authoritative docs options:

| Flag | Description |
|------|-------------|
| `--skip-rewrite` | Skip LLM question rewriting (use raw headings) |

## Data Sources

| Source | Type | Description | Pipeline Status |
|:---|:---|:---|:---|
| FiQA | Research | Financial QA from forums (BeIR benchmark) | Ingest + Structure + Synthesize |
| FinQA | Research | Numerical reasoning over financial reports | Ingest + Structure + Synthesize (numerical prompts) |
| ConvFinQA | Research | Conversational financial QA | Ingest + Structure + Synthesize (numerical prompts + history) |
| TAT-QA | Research | Tabular and textual QA | Ingest + Structure + Synthesize (numerical prompts) |
| Money.SE | Community | Stack Exchange personal finance Q&A | Ingest + Structure + Synthesize |
| Reddit | Community | 6 finance subreddits via Arctic Shift API | Ingest + Structure + Synthesize |
| SEC/CFPB/FINRA | Authoritative | Government financial education content | Ingest + Structure (with LLM rewrite) + Synthesize |

## Troubleshooting

**`ModuleNotFoundError`**: Make sure the virtual environment is activated (`source .venv/bin/activate`) and dependencies are installed (`pip install -r requirements.txt`).

**Reddit download slow or fails**: The script uses the Arctic Shift API with cursor-based pagination and is fully resumable. If interrupted, re-run and it will pick up where it left off. Use `--workers 1` if rate-limited.

**Synthesis interrupted**: The pipeline is checkpoint-based. Re-run the same `synthesize_tsr.py` command and it will continue from the last saved checkpoint.

**HuggingFace upload auth error**: Ensure `HF_TOKEN` is set in `.env` and has write permissions. Generate a token at https://huggingface.co/settings/tokens with "Write" access.

**Pre-commit hooks fail**: Run `pre-commit run --all-files` to see which check failed, then fix and re-commit.

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Pre-commit Hooks

The following checks run automatically on commit: code formatting (black), linting (ruff), schema validation, unit tests, trailing whitespace, YAML/JSON validation.

```bash
pre-commit run --all-files
```

## License

This project is for research and educational purposes.

- Code: Apache 2.0
- Dataset: CC-BY-4.0
- Source data licenses vary: Stack Exchange (CC BY-SA 4.0), Reddit (API terms), Research datasets (see originals), Government sources (public domain)

## Citation

```bibtex
@dataset{quant_tutor_benchmark_2026,
  title={QuantTutorBench: A Multi-Dimensional Benchmark for Evaluating Quantitative Finance Tutoring Agents},
  year={2026},
  description={Financial QA benchmark with synthesized tutoring components for evaluating AI tutor agents}
}
```
