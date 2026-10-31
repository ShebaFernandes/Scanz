# Scan-to-Resolve

A local hackathon application built with **Next.js, FastAPI, OpenCV.js, browser-local OCR, and real Sarvam/OpenAI tool calling**. Scan the last four visible digits of a card, select a demo purchase, and watch a durable support workflow investigate and track a simulated refund.

## Start

Requirements: Node.js 20.9+ and Python 3.11+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock.txt
npm install --prefix frontend
```

In one terminal at the project root:

```sh
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```sh
npm run dev --prefix frontend
```

Open http://127.0.0.1:3000. API documentation: http://127.0.0.1:8000/docs.

The frontend proxies `/api` to FastAPI. Override `API_ORIGIN` when starting Next.js if needed. Servers intentionally bind only to localhost.

## Enable the real AI teammate

A private, Git-ignored `.env` file is prepared in the project root. Open it locally and set:

```dotenv
AI_PROVIDER=sarvam
SARVAM_API_KEY=your-key-here
SARVAM_MODEL=sarvam-105b
RESOLVE_AGENT_MODE=ai
```

Do not paste your key into chat or put it in frontend code. The backend reads configuration dynamically; after saving a key, click **I’ve added my key — connect AI** or **Retry AI investigation**. No restart is required for a file-based key. A process environment variable takes precedence over `.env`; restart if changing a key exported into the server environment. Model availability and billing depend on your API account. To use the retained OpenAI adapter instead, set `AI_PROVIDER=openai` and configure `OPENAI_API_KEY`. No fallback between providers occurs.

Choose **Real AI** before starting a case. Without credentials, the case pauses in `AI_SETUP_REQUIRED`. **Offline demo** is an explicit alternative; the app never silently substitutes rules for the model. Existing offline cases retain their history; use **Investigate this case with real AI** on a resolved or review-required case to add model investigation without deleting existing refunds.

The model receives redacted complaint text, customer clarification replies, and results from case-bound tools. Card photos, card digits, keys, and unrelated orders are excluded from model context. Redaction covers common full-card-number and labelled PIN/CVV/OTP patterns; avoid entering sensitive data in complaints. The OpenAI adapter uses `store: false`; Sarvam uses its own provider retention policies. Neither setting implies zero provider retention.

## Standout feature: evidence-backed case briefs

The AI summarizes intent, findings, and the recommended next step with references to actual executed tool results. Click an **E-number** to inspect its source. Fabricated evidence IDs are rejected by the backend. **Copy handoff** and **Download case brief** package the summary and source records for human review. Recommendations are timestamped; the timeline and current system state remain authoritative as the refund progresses.

The teammate can ask a clarification question, wait for the customer's reply, then resume. Status-only requests can finish without issuing a refund. Try the English, Hindi, and Hinglish complaint examples; real model behavior must be evaluated with your configured account.

## Demo walkthrough

1. Click **Scan my card**, or use **demo card ending 4821**.
2. For a physical scan, cover every digit except the last four. Keep the card level. Images remain in the browser; never expose your CVV or PIN. Confirm the OCR result before continuing. OCR language assets download on first use, so the first scan requires internet. Manual entry works without OCR.
3. Your actual last four digits may have no matching purchases. The synthetic demo records use **4821**. A scan is a purchase-selection hint, not authentication.
4. Select **Urban Pantry / ₹799**. Choose **Real AI** and submit the complaint. The model selects investigative tools and, when appropriate, requests a mock refund. Backend policy checks run before submission. Model latency varies; the subsequent simulated refund lifecycle takes a few seconds. The explicit offline demo still follows the original roughly 14-second workflow.
5. Select **Studio Supply / ₹1,499** to trigger the sample ₹1,000 approval limit. Open **Human review**, approve, and watch the same case resume.
6. **Daily Brew** demonstrates a pending payment; **Paper & Pine** a fulfilled order; **Metro Market** a failed payment. These require specialist reconciliation and cannot be force-refunded through approval.
7. Refresh or restart the backend: cases, audit events, and scheduled transitions persist in SQLite. Reopening the same order returns the existing case and never creates a second refund.

## What's real and what's simulated

- **Real:** camera capture, OpenCV grayscale/blur/edge detection and card-region crop, local Tesseract OCR, Next.js UI, FastAPI endpoints, persistent SQLite workflow, backend policy checks, human review, live polling, event history, in-app updates.
- **Simulated:** Paytm payment responses, merchant order records, refund submission, provider processing and bank-credit confirmation. No money moves.
- **Teammate:** real model tool calling through Sarvam Chat Completions v2 or the OpenAI Responses API when Real AI mode is selected and a valid key is configured. Offline mode uses deterministic rules. Model-generated text is labelled; backend rules remain authoritative for refunds.
- **Identity:** one fixed demo customer. Human reviewer controls are intentionally exposed for the local demo. There is no production authentication or role separation. Do not expose this server publicly.
- **Card data:** the backend accepts exactly four digits. No image, full PAN, expiry, CVV, or PIN is stored. Demo order metadata and complaint text are persisted; do not paste sensitive card details into complaints.
- The scanner detects a likely card-shaped bounding box and crops it; it does not implement perspective correction. Keep cards level and use manual entry if detection fails.
- UI updates use 1.5-second polling. The worker uses persisted due times, so it resumes after restarts. The prototype simulates lifecycle transitions internally; it does not receive real provider webhooks.

## Architecture

```text
Camera → OpenCV.js → local OCR → confirmed last4
                                  ↓
Next.js UI → /api proxy → FastAPI → SQLite
                            ↓
                  Sarvam / OpenAI tool loop
                            ↓
                   case-bound tool gateway
                            ↓
                 persistent workflow worker
                            ↓
                  simulated payment lifecycle
                            ↓
              timeline / human review / refund tracking
```

`backend/main.py` contains the API, seed data, transactional state machine, workers, and case-bound tools. `backend/ai_agent.py` contains provider selection, the OpenAI loop, tool schemas, configuration, and redaction. `backend/sarvam_provider.py` implements Sarvam Chat Completions v2 with `api-subscription-key` authentication and tool-message replay. `frontend/components/EvidenceBrief.tsx` renders source-linked summaries and downloadable handoffs. `frontend/components/CardScanner.tsx` contains the image pipeline. `frontend/app/page.tsx` contains the customer and reviewer flows.

Refund amounts are integer paise. A unique order-to-case constraint and unique order-to-refund constraint prevent duplicate actions. Execution rechecks eligibility. All database write transitions use SQLite transactions. This is a single-machine prototype; use a proper job queue and production database when scaling.

## Verification

```sh
.venv/bin/pytest backend/tests -q
npm run typecheck --prefix frontend
npm run build --prefix frontend
```

## Next steps for real Paytm integration

Replace the simulated payment status and refund transitions with an adapter for your authorized merchant account. Add provider signing, webhook validation/deduplication, timeout reconciliation, and an outbox for external calls. Add real customer authentication and separate reviewer roles before using actual records. Obtain permitted card metadata from merchant records; never retrieve transactions by arbitrary card number. Provider refund confirmation must not be labelled bank credit without independent evidence.

## Model execution and failure behavior

- Up to ten model turns per investigation, one tool per response, 35-second HTTP request timeout, and at most three simultaneous case investigations per backend process.
- Every refund request requires four evidence reads in that investigation and a fresh backend policy check. Amounts and account/order identity are never model-supplied.
- Model tool requests and their results are persisted. Token usage is recorded per case; automatic retries that could consume further API credits are not performed after model errors.
- If the model fails before an action, the case pauses in `AI_ERROR`; retry or hand off explicitly. If a refund is already queued or processing, tracking continues even if the final summary fails.
- A ten-minute lease detects abandoned AI investigations after interruption. The local app uses one fixed customer and shared reviewer controls; it is not production-ready authentication.
- `backend/tests/test_ai_agent.py` tests both actual provider loops using a stub HTTP transport, including no-key handling, information-only behavior, clarification, blocked actions, evidence validation, and provider errors. These tests do not establish real model quality; run live cases after adding your key.
- Official integration reference: [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling).

## Local data

`backend/resolve.sqlite3` is ignored by Git. To reset the demo, stop the backend and delete that file plus its `-wal` and `-shm` files if present, then restart. Use `RESOLVE_DB` to select another database and `RESOLVE_STEP_SECONDS` to change simulated step duration (default: 2 seconds).

## Sarvam live verification

With your key configured, run `.venv/bin/python scripts/check_sarvam.py` to execute two live AI scenarios against a disposable temporary database. It sends only synthetic complaints and mock-order evidence, consumes Sarvam API credits, and never moves real money. It verifies a Hinglish refund request reaches simulated credit and an explicit status-only request finishes without a refund. Your main demo history is untouched.

Protocol reference: [Sarvam Chat Completions v2](https://docs.sarvam.ai/api-reference/chat/chat-completions-v2).
