# Token usage and cost — final full-dataset run

The final run performs no external model/API calls. It uses deterministic Python,
Decimal arithmetic, local CSV/image reads, and SHA-256-verified document evidence.

| Provider/model | Calls | Input tokens | Output tokens | Total tokens | Estimated API cost |
|---|---:|---:|---:|---:|---:|
| None (deterministic replay) | 0 | 0 | 0 | 0 | USD 0 |

Requests: 250. Average runtime tokens per request: 0. Average API cost per request: USD 0.
Local compute/electricity costs are not measured or included.

Development used Codex for code and visual document inspection. Development input/output
tokens and attributable account cost are unavailable from this session and are NOT claimed
to be zero. Visual facts are cached document extractions, not predicted request labels.
The final run reopens and decodes all linked PNG files and verifies their hashes before reuse.
Unseen images optionally invoke local Tesseract OCR; no OCR invocation was needed in this run.
