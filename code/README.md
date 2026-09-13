# Buy or Wait? — submission code

Python 3.11+ is required. Install the dependency and run from the cloned repository:

```text
python -m pip install -r code/requirements.txt
python code/main.py
```

`code.zip` contains the CONTENTS of `code/`; extract it into the repository's `code/`
directory. The dataset remains separate at the repository root, beside
`problem_statement.md`. The archive contains no dataset, data corpus, environment,
node_modules, compiled files or build directory.

Predictions are written to root `output.csv`. To select a different dataset explicitly:

```text
python code/main.py --dataset /path/to/dataset --output /path/to/output.csv
```

The folder containing `dataset/` must also contain `problem_statement.md`.
Run the same evaluation with `python code/evaluation/main.py`. Run tests from `code/`
using `python -m unittest discover -s tests -v`.

Architecture: ingestion, normalization, constrained evidence extraction, event
reconstruction, 90-day daily balance forecasts, payment plan ranking and independent
ledger validation. All monetary calculations use Decimal and supplied dated FX.
The final run uses no external data or model API calls. `evidence/visual_facts.json`
is the required SHA-256-addressed extraction cache for the supplied local images;
it contains document facts, not request predictions. The PNG files themselves are
excluded from this archive. New images require local Tesseract or reviewed evidence.

Evaluation code, token usage report and sample metrics are included. Large generated
audits, sample prediction CSVs and execution caches are excluded and are regenerated
by the entry point. Historical sample labels never enter the decision engine.

Known limitations: recurring expenses are inferred from history using a conservative
maximum of the latest three observations; messages use constrained English/Indonesian
patterns. The 25 samples match 60% on status, 64% on method and 4% on exact safe amount.
Validation success does not imply hidden-ground-truth accuracy. See evaluation/report.md.

Upload `code.zip`, root `output.csv` and root `log.txt` as three separate deliverables.
The log preserves development entries and response summaries; it is not a complete
export of internal application history.
