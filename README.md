# Scan-to-Resolve

**Current entry:** Create an account in the web app, then sign in. There are no default passwords or automatic ownership of old demo records. New sample purchases belong to your account. Staff tools require a locally assigned operator role and explicit customer-case assignments. Sarvam is paused, refunds are mocked, and Paytm MCP is prepared but disconnected. See the **Real accounts and order access** and **Prepared Paytm MCP evidence integration** sections below for setup.

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

## Reliability, outcomes, and the control room

The sidebar now includes **Control room** and **Outcomes**. Both work offline; no Sarvam calls are made. The UI defaults to offline mode and disables live-AI controls while `AI_LIVE_ENABLED=false`.

### Failure recovery lab

In **Control room**, select a scenario and click **Start offline case**:

- **Lost refund acknowledgement:** the mock provider accepts one refund in a separate persistent ledger, while its response is lost. A scheduled reconciliation retrieves the same reference by its stable request key; it never blindly submits again.
- **Delayed status update:** a persisted retry resumes tracking the existing refund.
- **Worker interruption:** pauses only the chosen case after saving its checkpoint. **Resume checkpoint** restores the saved step and reference. This button simulates an interruption; it does not stop the server. Tests also recreate startup/DB connections between acceptance and recovery to verify actual persistence.
- **Normal resolution**, **Human approval required**, and **Information only** provide other repeatable demonstrations. Offline intent is a structured fixture, not natural-language interpretation.

After a refund reaches processing, **Repeat status event** and **Late status event** demonstrate receipt deduplication and monotonic state updates. **Duplicate request** records a duplicate delivery without creating another workflow. Real customer request idempotency remains enforced by unique order-to-case and order-to-refund constraints.

### Outcome dashboard

Click **Run offline evaluation** to execute 24 fixtures in an isolated in-memory database through the same production state machine. Reports are saved to the app database, but evaluation orders/cases never appear in customer history. Inspect each case's expected state, actual state, assertions, policy evidence, and event log, or export the full JSON report.

The suite covers 12 categories, two variants each: normal refunds, automatic-limit boundaries (1000.00 / 1000.01 INR), approval, approval-and-resume, pending/failed payments, fulfilled orders, information only, existing refunds, timeout, delay, and interruption. Completed refunds also receive repeated and stale events.

Metrics are explicitly offline workflow results, not Sarvam accuracy. Correct outcomes require state, refund count, provider count, amount, zero model usage, and handoff evidence checks to pass. Autonomous resolution uses only fixtures designated eligible for automatic resolution. Human counts include a fixture that receives a simulated approval. Timing is measured in virtual simulation ticks; no bank settlement latency or actual human decision time is claimed.

### Implementation

`backend/reliability.py` implements the separate mock-provider ledger, stable operation keys, bounded reconciliation, event receipts, and recovery telemetry. `backend/operations.py` adds lab/evaluation endpoints. `frontend/components/OperationsView.tsx` implements the control room and outcome dashboard.

These are local prototype semantics. A real payment provider requires its own idempotency and reconciliation protocol, signature verification, and an external-call outbox. The simulator deliberately stores provider and local state separately in the same SQLite database to reproduce lost acknowledgements without network calls.

## Order Rescue Teammate (submission priorities)

The default **Order rescue** workspace starts from a simulated UPI payment event instead of a card scan. The original card workflow, control room, and refund evaluation dashboard remain available.

### Three core demos

1. **Paid checkout → restore order.** Save a synthetic checkout (item, quantity, price, customer delivery details, and explicit customer outcome), emit a mock successful payment, wait for the configurable grace period, and watch the detector open a recovery case. Inventory is reserved, a merchant order is created, and its reference, amount, committed stock, and payment are verified before the case reaches `ORDER_RESTORED`.
2. **Order created, response lost.** Creation persists in a separate merchant ledger while the local acknowledgement is absent. Reconciliation looks up the stable `restore:<checkout>` operation key and reuses the original order. Stock and order creation must not be duplicated.
3. **1299 INR refund → human approval.** The detector opens the case using the saved refund preference. The same sample 1000 INR automatic limit applies. A specific approval resumes the existing refund workflow, and the human decision is counted.

### Proactive detection and transaction entry

The simulator is explicitly labelled and is not a real Paytm webhook endpoint. `/api/rescue/mock-events` validates a receipt against a saved checkout and deduplicates its event ID. Successful payments are checked by the background worker after the grace period. The detector rechecks for an original merchant order and does not open a recovery case if confirmation already arrived. Use **Delayed original confirmation** and the **Simulate original order confirmation** button to demonstrate this. The search field looks up only local demo transactions; it cannot query arbitrary UPI or bank history.

### Intent, compensation, and financial fencing

Offline customer outcomes are explicit structured preferences: restore, refund, status only, or clarify. Today-only delivery is checked before reservation and again before creation. No claim of new natural-language accuracy is made while Sarvam is paused. The Sarvam/OpenAI tool adapters now expose case-bound checkout, inventory, and restoration tools; stub-transport tests validate both provider loops without API credits. New restoration tools have not been live-model evaluated.

A customer can request a refund while stock is held. If no merchant order exists, the reservation is released and the refund path starts. If creation is uncertain, reconciliation must finish first. If an order was created, a reviewer must explicitly **cancel the merchant order and approve the refund**; cancellation releases committed stock before the refund can run. A confirmed order and refund cannot complete incompatibly. Once refund acceptance is recorded, restoration is blocked.

**Interrupt after stock reservation** saves a checkpoint; resume it or abandon known uncreated work to release stock. **Creation rejected** compensates its reservation. **Merchant lookup unavailable** retries three times, records the uncertain outcome, and preserves resources until a reliable lookup can resume. It does not blindly recreate an order or release possibly committed stock. **Two successful payments** exposes both records for review rather than issuing an automatic refund.

### Rescue scorecard

The scorecard executes 26 structured fixtures in an isolated database through the same backend engine. It measures correct expected outcomes separately from completed cases, customer-intent checks, actual approve/reject/cancel decisions, order/refund duplication, conflicting completions, inventory conservation, reconciliation, and verified restored order value. It saves and exports source records and event evidence.

The displayed baseline is a deliberately simple, status-aware refund-first **decision rule**, run on the same fixture inputs. It does not implement order restoration or partial-action recovery. The comparison is not an AI-versus-rules accuracy benchmark or a production performance claim. Earlier live Sarvam cases remain separate from this zero-credit offline suite.

Implementation: `backend/rescue.py` owns proactive detection, merchant/inventory records, restoration and compensation, scoped APIs, and scorecards. All systems are persistent simulations in the same SQLite database, not independently deployed production services. Add real authentication, provider verification, an external-call outbox, and authorized merchant integrations before using real records or moving money.

## Merchant extensions: six features without model credits

Open **Order rescue** and launch a **fresh** checkout to use the new category catalog. Existing cases and refunds are retained; historical checkouts are not rewritten with invented product attributes.

| Feature | What works in the demo | External dependency / limitation |
| --- | --- | --- |
| Voice interface | Start/stop browser speech recognition, editable transcript, English/Hindi selector, read-aloud status, explicit outcome review and confirmation | Browser speech support and microphone permission required. Recognition may use the browser vendor's service. No audio reaches our backend; no Sarvam speech/model calls. The small English/Hinglish/Hindi command matcher is deterministic and asks for a choice on ambiguity. This is not a multilingual model evaluation. |
| Multiple merchant categories | Groceries/Fresh Basket, electronics/Circuit Hub, fashion/Thread & Weave; category selector and payment-watch filter; persisted original checkout and category-specific product attributes | Synthetic catalogs and stock, not merchant APIs. Each demo checkout owns its catalog/inventory. |
| Alternative products | Price-matched, stock-backed suggestions with required dietary/connector/size compatibility; exclusion reasons; explicit customer consent; safe reservation replacement | Only equal-total substitutions. Premium products require a separate payment/refund workflow and remain blocked. Confirmed orders, accepted refunds, and uncertain creation cannot be overwritten. |
| Additional channels | Opt-in in-app, email, SMS and WhatsApp **simulated** outbox; milestone updates; durable deduplication; three-attempt bounded retry/backoff; failure injection; opt-out cancels queued messages | No actual message is sent; no recipients or channel credentials required. This is not connected to Meta, an SMS gateway, or an email service. |
| Delivery optimization | Rank three simulated carriers by cheapest/fastest/balanced; enforce distance, deadline, customer today-only constraint, merchant budget, dispatch readiness and available capacity | Fixed estimates, not live traffic or route optimization. Quotes expire after five minutes and are bound to checkout/SKU. Capacity is checked again and consumed once at creation, retained on timeout, and released on approved cancellation. If no plan was explicitly selected, the demo uses a 7km / 24-hour / INR60 merchant-budget default. Merchant bears cost, no additional customer charge. |
| Broader analytics | Separate **Analytics** navigation; category/time filters; verified restored value, accepted vs credited refunds, human decisions, open cases, consented alternatives, delivery costs, recorded completion intervals, India-date trend and channel retry counts; JSON export | Aggregates local demo records only, excludes isolated evaluation databases. Values are simulated, not real funds or projected ROI. Historical model tokens are displayed without triggering inference. Time filters select checkouts created in the window; trends group outcomes by original checkout date. |

### Suggested extension demo

1. Select **Electronics**, then **Stock unavailable**, and simulate a paid checkout. Wait for automatic detection and the customer-choice state.
2. In **Stock-backed alternatives**, inspect the excluded premium and incompatible variants. Tick the explicit same-total consent and choose the eligible alternative. Watch the saved SKU, reservation, and merchant order change; the paid total stays fixed.
3. For delivery exploration, launch **Interrupt after stock reservation**. Compare plans at 7km and a 120-minute deadline, then at a 30-minute deadline to see why no plan qualifies. Choosing a feasible plan resumes normal verification. The server rechecks everything before creation.
4. Opt into a simulated SMS/WhatsApp/email channel by checking its opt-in box. Queue **Fail once, then retry**, expand the outbox entry, and watch it move from retry scheduled to simulated delivered. **Fail to retry limit** stops at three attempts. No real messages leave the application.
5. Type “Don't refund. Just check the status.” into the voice transcript fallback. Review the status-only outcome and confirm explicitly. Try speech capture yourself in a supported browser; automated checks do not activate your microphone.
6. Open **Analytics**, filter Electronics / last 7 days, inspect recorded outcomes and delivery cost, and export the JSON evidence.

Implementation: `backend/commerce.py`, `frontend/components/CommercePanel.tsx`, `VoiceInterface.tsx`, `BusinessAnalytics.tsx`, and the offline matcher in `frontend/lib/voice.ts`. Schema changes are additive. Financial fences and stable order/refund identities remain enforced server-side. The demo uses SQLite write transactions to serialize stock/carrier mutations; a production multi-merchant deployment needs real tenant authorization and provider integrations.

### Additional zero-credit checks

```bash
.venv/bin/pytest backend/tests -q
node --test frontend/tests/voice.test.mjs
npm run build --prefix frontend
```

The voice matcher test command uses native TypeScript stripping (Node 22.18+ or Node 24). Commerce integration tests cover all three catalogs, consent and incompatible variants, reservation compensation on substitution/refund, fulfilled/refunded/uncertain-operation fences, delivery objectives and hard constraints, quote expiry/SKU/checkout binding, capacity changes at commit, timeout/cancellation capacity conservation, channel consent/deduplication/retry exhaustion/opt-out, and filtered analytics that exclude evaluation data. Provider tests use fake transports; do not run live-model smoke scripts while preserving credits.

## Android Tap-to-Resolve (presence-only prototype)

**Tap to resolve** now provides a scoped, five-minute, single-use handoff to an explicitly selected saved demo checkout. The native Android companion source is in [`android-tap`](android-tap/README.md). It detects compatible ISO-DEP presence without requesting card identifiers or payment application data, then opens the pairing link in the phone browser after confirmation. A second confirmation redeems the link. The desktop polls pairing status and can open that exact checkout. Tap does not create an order/refund or advance financial state; it is an entry method only. All payment operations remain mocked and no Sarvam calls are made.

Use USB port forwarding (`adb reverse tcp:3000 tcp:3000`) so the phone can reach the existing localhost frontend. It is not configured automatically; instructions and hardware troubleshooting are in the companion README. No LAN/firewall/public-hosting changes were made. Native Android SDK/JDK were absent on this Mac, so no APK build or physical-card validation is claimed. Web/backend tests and a clearly labelled software fallback are available without NFC hardware. Android NFC source reports are not hardware-attested and cannot authenticate bank-card ownership. Real debit cards are not associated with demo transactions; the customer explicitly selects a seeded-account checkout before tapping.

Implementation: `backend/tap.py` (hashed capabilities, owner-scoped status/cancel, expiry and transactional replay prevention), `frontend/components/TapToResolve.tsx` (pairing and fragment-only handoff), `android-tap` (native reader). New request schemas reject extra PAN/UID/expiry fields. Isolated offline tests cover scoping, cancellation, expiry, concurrent redemption, no financial mutations, and no model calls. Production use requires real authentication and authorized integrations; this remains a local hackathon demo.

## Simple customer interface (navy and white)

The default page is now **Home**, with one primary action (**Get help with a payment**) and two supporting actions (**Track my request**, **Tap my card**). The sample payment walkthrough is optional and collapsed. It offers three explicit outcomes (get the order, get a refund, check status) and runs the existing deterministic demo workflow with no model call.

Customer navigation is **Home / Get help / My requests / Tap my card**. Advanced screens remain under the expandable **Team tools** menu: **Manage orders** (previously Order rescue), **Approvals**, **Activity & recovery** (Control room), **Reports** (Analytics), and **Demo tests** (Outcomes). This is a UI grouping, not a new authorization boundary; the seeded demo account remains the same. Earlier rehearsal instructions that say “Order rescue” now start with **Team tools → Manage orders**.

Payment help follows **Find payment → Choose request → Track update**. The explicit refund/status choice remains visible. Model settings, example complaints, execution evidence, and the full timeline are moved into optional sections. Explanations use shorter wording and larger customer text; navy, white, and pale blue replace the green palette. Paytm stays simulated and Sarvam remains paused. The web tap flow uses **Create phone link / View my purchase** labels; native Android requirements are unchanged.

## Scan-only web entry and automatic full-card scanning

Tap/NFC navigation, homepage actions, and fragment handoff handling have been removed from the web app. **Scan my card** on Home opens the camera directly. Android/NFC source and old backend session records are retained, but are not part of the web customer flow.

The scanner no longer has a capture button. It continuously checks camera frames on-device with OpenCV, waits for a fully visible landscape card and stable position, rejects low-sharpness frames, and runs a reusable local Tesseract worker. Two matching high-confidence readings of a checksum-valid 13–19 digit card-number row are required before returning **only its last four digits** to the confirmation form. Dates, partial rows, multiple candidate numbers, and invalid checksums are rejected. Full card numbers and images are not persisted, logged, or sent to the backend or AI. Complete OCR text exists transiently in the browser worker while reading and is discarded. Camera tracks and worker resources are stopped when the scanner closes or finishes.

Use the whole **front** of a landscape card with its number visible, against a contrasting background. Hold it level, keep all four edges in view, and avoid glare. A card with its number only on the back should use manual last-four entry; the app does not ask to expose CVV. Portrait cards and unusual/split number layouts are not guaranteed. OCR assets download on first use, without any Sarvam calls. The returned suffix still requires user confirmation before demo lookup; scanning never proves card ownership or retrieves actual bank transactions.

Checks: `node --test frontend/tests/cardScan.test.mjs frontend/tests/cardVision.test.mjs` covers suffix/checksum/confidence/ambiguity/stability logic and actual OpenCV contour detection on synthetic full/cropped/empty card frames. These tests do not replace a physical camera/card rehearsal. Hardware scan quality must be checked on the target device. Browser camera access still requires user permission and localhost/HTTPS.

### Smoother scanner update

Detection now runs on lighter 640px frames independently of OCR, so the live card outline keeps updating during number recognition. The detector accepts smaller cards, closes small edge gaps, and tolerates modest rotation. Detected quadrilaterals are perspective-corrected from the full-resolution camera image before local OCR. Stability tolerances are more forgiving, short detection losses are debounced, and moving/removing a card invalidates in-flight OCR results. Complete-number checksum/confidence checks and two matching suffix readings remain required.

The camera and OCR assets prepare concurrently. The UI has three clear stages, a moving outline, plain-language guidance, retry, and camera switching. Continuous autofocus is requested only when the camera reports support; a light button appears only on cameras reporting torch support. Unsupported optional settings do not block scanning. Low sharpness prompts focus/lighting guidance; dark card color alone is not rejected. Actual capture quality remains device-dependent and must be rehearsed with the target camera. Only a last-four suffix leaves this component, and no Sarvam calls are used.

Scanner tests now include smaller and tilted cards, perspective correction, cropped/empty views, square-object rejection, stability, confidence, ambiguity, and suffix extraction. Run `node --test frontend/tests/cardScan.test.mjs frontend/tests/cardVision.test.mjs`.

### Scan without exact placement

The scanner now starts local OCR without waiting for an OpenCV outline, stable corners, or a sharpness score. OpenCV loads as an optional enhancement; missing/broken OpenCV or a failed perspective crop falls back to the original camera image. No outline means no blocker. The rigid placement rectangle is removed. The prompt is now **Show the number. We’ll scan it.** Two high-confidence, checksum-valid matching suffix readings and explicit last-four confirmation remain required. A reading window expires after 20 seconds rather than being cancelled on every detected hand movement. No image/card data is sent to a model.

Two regression tests specifically verify that camera images reach OCR when vision/outline detection is unavailable or fails. The scanner suite now has 11 passing checks. Physical camera/OCR performance must still be tested on the target device; these tests do not establish a successful real-card scan.

### Reproduced OCR rejection and real browser validation

A synthetic dark card reproduced a rejection: the number row was read at 95% confidence, but unrelated text reduced page confidence to 63%, below the old global cutoff. The acceptance path now checks confidence on the valid number row, while retaining complete-image ambiguity and checksum checks. Low-confidence logos/dates no longer veto a clear number. Dark/light normalization, bounded downscaling, and a second OCR segmentation mode address contrast/layout failures. Two matching reads and suffix confirmation remain required.

Worker, WASM core, and English language data are now served from `/ocr` by the app; no CDN language-model or worker fetch is required at runtime. `postinstall-ocr.cjs` copies installed package assets; the bundled English fast model and its license/source checksum are under `frontend/public/ocr`. This is free local OCR, not Sarvam inference.

Open `/scanner-check` for a hardware-free diagnostic. **Run scanner check** performs actual browser OCR on clearly synthetic dark/light/softened JPEGs through the same `createCardReader` and `readCardImage` helpers used by the camera. **Test automatic scanning** runs the actual scanner component with a synthetic Canvas video stream, including automatic frame selection, two readings, and suffix callback. This route never requests camera permission or uses a real card. It must not be described as real-card/hardware validation. Normal customers still use **Scan my card**.

## Real accounts and order access

The website now requires a real local email/password account. There are **no default passwords**. Register in the web app with a password of at least 12 characters; sign in and sign out using the session flow. Email is a login identifier; email verification, password recovery, MFA and a production identity-provider integration are not implemented. Existing demo orders/cases remain in SQLite, but are **not automatically claimed by new users**. Matching four card digits does not establish ownership. Create a new sample purchase from Get help when your account has no matching orders, or use Home's sample payment flow. Those records belong to your account only.

Passwords use salted scrypt (N=32768,r=8,p=1); only password hashes are stored. Sessions are 256-bit random opaque secrets stored as SHA-256 digests, expire after eight hours, rotate on sign-in, and are revoked on sign-out. Session cookies are HttpOnly, SameSite=Lax, and scoped to `/api`. A separately hashed per-session CSRF token is required on mutations; the client sends it as `X-CSRF-Token`. Browser origins are checked against explicit allowed origins. Persistent per-email/IP counters throttle login/registration. API records use no-store caching. Mutations/denials and security events produce an audit trail without passwords, raw session tokens, card images, full card numbers, or provider credentials.

Ownership is enforced server-side for card candidates, case lists/details, customer replies, approval/recovery actions, merchant checkouts, inventory alternatives, delivery plans, notifications, tap-session remnants, evidence and reports. New simulated card orders include the account ID in their identity. Guessing another order/case/checkout ID returns not-found. Background workers intentionally use an internal, request-independent context to resume multiple customers' cases; each persisted order retains its owner. The HTTP middleware admits no unauthenticated `/api` request except health and login/registration. The static scanner diagnostic remains public and uses synthetic data only.

### Staff access and explicit assignments

New users cannot select a role at signup. Staff tools are hidden for customer accounts, and their endpoints also require an operator role. Local project administrators can grant it **after the user creates an account**:

```bash
.venv/bin/python -m backend.manage_accounts promote-operator operator@example.com
```

This revokes that user's sessions; sign in again. Operators see their own demo cases and individually assigned customer cases, not all customers by default. Assign a specific case using the trusted local administration command:

```bash
.venv/bin/python -m backend.manage_accounts assign-case operator@example.com CASE-EXAMPLE
```

An assigned operator can review that case, approve an eligible simulated refund, and let the existing worker resume. Other unassigned orders remain inaccessible. No HTTP route grants roles, assigns cases, changes owner IDs, or claims old history. Old legacy cases may be individually assigned by the local administrator after reviewing the records; they are never auto-transferred to a newly registered customer.

### Deployment boundaries

This remains a local, single-configured-merchant prototype with SQLite-backed sessions/limits. For external deployment set `AUTH_COOKIE_SECURE=true`, serve exclusively over HTTPS, set exact `AUTH_ALLOWED_ORIGINS`, protect the database/backups and server environment, move secrets into a managed secret store, and use deployment-grade shared rate limits, identity recovery/verification and tenant administration. The default insecure cookie flag exists solely to support localhost HTTP. Do not expose this service through a public tunnel with local defaults. Staff role/assignment management is deliberately local rather than an unfinished public admin API.

## Prepared Paytm MCP evidence integration — OFF by default

The adapter in `backend/paytm_mcp.py` uses the official Python MCP SDK with Streamable HTTP. It supports server-provided authorized bearer credentials. It does not implement provider OAuth onboarding, token refresh, legacy SSE endpoints, or automatically request access. Obtain the correct access method and tool schemas from Paytm before enabling. No Paytm connection or Sarvam call is made by startup or the status screen.

Server-only settings (see `.env.example`):

```text
PAYTM_MCP_ENABLED=false
PAYTM_MCP_URL=https://mcp.paytmpayments.com/mcp
PAYTM_MCP_ACCESS_TOKEN=
PAYTM_MERCHANT_ID=
AI_LIVE_ENABLED=false
AI_ONE_SHOT=false
```

Never use `NEXT_PUBLIC_` for provider credentials. Do not paste tokens into chat or enter them into browser forms. The endpoint is restricted to HTTPS on `mcp.paytmpayments.com/mcp`, without credentials/query/fragment/redirects. Ambient HTTP proxy credentials are not used. Access-token acquisition/rotation must be handled by the authorized merchant's server process. All tools are server-allowlisted: only `fetch_order_status` and `fetch_refund_status` can be selected, and a tool explicitly advertising write behavior is rejected. Provider input schemas are checked before invoking; unknown required fields and remote schema references fail closed. No generic tool-execution endpoint exists. SDK sessions have no AI sampling callbacks.

After configuring authorized access, explicitly enable the adapter and restart. An operator can inspect **Team tools → Paytm connection → Check available tools**, which invokes discovery only. Status is local and reports configuration without exposing tokens. Paytm's actual account permissions/tool contracts still need verification; no live Paytm validation has been performed.

### Bound, single-order lookups

The browser/model cannot supply a provider order ID, merchant ID, refund amount or tool name. A trusted server-side import must map the local authenticated order to the actual merchant order. For a reviewed manual mapping by the local administrator:

```bash
.venv/bin/python -m backend.manage_accounts bind-paytm customer@example.com LOCAL-ORDER-ID PAYTM-ORDER-ID
```

The command checks the local account owns the order and uses the server's configured merchant ID. Add `--refund-id PROVIDER-REFUND-ID` only for an independently verified existing provider refund. A mock refund reference is never substituted. Trusted mappings are administrative assertions; they must come from your merchant checkout/payment integration, never a card scan or arbitrary customer claim.

`POST /api/integrations/paytm/orders/{local_order_id}/evidence?kind=payment` (or `kind=refund`) requires operator role, case/order access, valid session + CSRF, and a binding matching the configured merchant. It discovers the provider schema, constructs only recognized server-bound order/merchant/refund arguments, and validates them. Unsupported schema shapes are blocked for review rather than guessed. This prepared endpoint is intentionally not an automatic source for the resolution engine yet.

Provider responses must identify the bound order. Only known status values, that order reference, a response digest and read-only/source flags are returned/stored. Raw customer contact data, free-text instructions, and tool results are discarded. Evidence is **not applied to workflow state**: `applied_to_workflow=false`. It cannot create refunds, change mock payment status, mark a bank credit confirmed, or trigger order restoration. Refund execution remains the existing mock adapter. This prevents mixing real merchant evidence with the simulated financial ledger. A later authorized integration must explicitly normalize and reconcile provider contracts before live outcomes can be claimed.

### Verification

Run `.venv/bin/pytest backend/tests -q` and `npm run build --prefix frontend`. Security tests use isolated databases, real session requests, and fake MCP/provider transports. They cover unauthenticated access, password/session storage, CSRF/origin checks, expiry/logout/rotation, role escalation, throttling, same-last-four isolation, cross-account read/write paths, explicit staff assignment, multi-customer background processing, read-only discovery, endpoint/schema restrictions, provider-order mismatch, PII filtering and zero financial mutation. No live Sarvam/Paytm test or charge is required.

### My purchases: automatic mock merchant sync

After sign-in, My purchases is the main screen. It automatically syncs an account-scoped simulated merchant feed on entry and every 15 seconds while mounted. Refresh runs the same sync. Three clearly labelled sample purchases (Fresh Basket, Studio Supply, Daily Brew) are provisioned per account; this is not the user's bank history. New feed orders are imported by stable account/order keys, so repeated syncs and concurrent requests do not duplicate them. Existing case and refund records are preserved. A new merchant-feed entry appears on the next sync. This local prototype imports new orders; external provider webhooks, bank connectivity and reconciliation of later merchant status changes are not implemented.

Choose Get help with this purchase to use the existing offline support flow. View my request opens an existing case. Card scanning is an optional filter of saved purchases; a card with no linked sample orders offers Show all purchases. UPI lookup remains optional. Paytm, refunds and merchant feed are mocked; Sarvam remains paused.

CSV upload and its frontend parser have been removed. The old import POST endpoint returns 410. Previously imported data is preserved separately and is not mixed into merchant orders; authenticated legacy read/delete endpoints remain available for account-owned records only.

### Resolve now: pending-payment follow-up

New offline cases with a pending payment enter `WAITING_PAYMENT` immediately. The durable worker checks the local mock provider every 10 seconds and schedules the final check at a fixed two-minute deadline. Case details show check count, last check, next check/countdown and escalation deadline. These jobs continue without an open browser and resume from SQLite after server restart. Refreshing or submitting the same case does not extend the deadline. A definitive successful payment resumes the customer's original status/refund intent and revalidates merchant fulfillment, approval limits and duplicate-refund policy. A pending payment at the deadline becomes a specialist review with saved evidence; it does not silently refund. A failed payment cannot trigger a refund.

The synced Daily Brew sample has an explicit mock-provider fixture: on the third payment check, payment becomes successful and merchant cancellation is confirmed, allowing the ordinary eligible simulated-refund workflow to proceed. Other pending records do not turn successful automatically. Existing legacy pending cases sent to review can use Resolve now once if they have no previous payment-watch deadline; expired watches cannot be restarted to bypass escalation. All outcomes are simulated; no Sarvam request or live Paytm API is used. Server-only `PAYMENT_CHECK_SECONDS` (default 10) and `PAYMENT_ESCALATE_SECONDS` (default 120) configure newly started watches. Production refund credit timing is not demonstrated by these timers.

### Queue-side Checkout help hub

Checkout help is now the initial screen after sign-in. Card/UPI selection → mobile number → on-screen **demo OTP** → synthetic checkout transactions → verify merchant payment or conditionally request a simulated reversal. No bank account is connected and no SMS is sent. The code is a local demo, not bank authentication. Four explicit outcomes distinguish merchant-paid confirmation, reversal plus mock credit, accepted reversal awaiting bank credit, and uncertain payment requiring review after the fast-check deadline. Original order rescue, purchases, approvals, reports and refund workflows remain available. See [checkout architecture and live-integration boundaries](docs/CHECKOUT-HUB.md).

### No-sign-in local demo

`AUTH_DEMO_MODE=true` opens a fresh account-scoped guest workspace automatically with a server-issued HttpOnly session and CSRF protection. Refresh reuses that guest workspace; Start fresh demo revokes the session and opens a new one. Existing private accounts and records are not exposed. Guests cannot call Paytm MCP or select live AI, and guest bootstrap refuses to run when live AI or MCP is enabled. Keep this mode disabled for real integrations; it is intended only for local mocked demonstrations. Card/UPI checkout demo verification remains explicitly simulated.

### Explore Scanz

The More features directory lists 13 implemented customer capabilities and merchant demonstrations, with category filters and links. Voice, alternative products, delivery planning and channel updates live inside an order-rescue case: open the merchant demo, simulate a paid checkout, then inspect the case. Local guest workspaces receive a demo operator role scoped to their own synthetic records, so approval, evaluation, recovery and report screens are discoverable without signing in. Existing private users are not promoted. Live AI and Paytm integrations remain forbidden to guests, and shared grace-period settings are locked. Guest mode cannot bootstrap while live AI or MCP is enabled.

The directory is not a 15-feature banking product. It does not provide live bank transaction lookup, bank-authenticated OTP, subscriptions, EMI, fraud monitoring or virtual-card issuance. Do not present BIN/last-four lookup as bank access, or the local OTP challenge as bank ownership verification.


### Simplified customer interface

Home now asks what the customer needs: payment help, order help or request tracking. The checkout flow asks one question at a time, offers sample details for the demo, and lets users apply the visible demo OTP with one button while retaining server verification. Results show a plain-language status and three progress steps. Acknowledgement references, time/check metrics, raw event history, failure injection and cashier copy remain available behind expandable details/advanced demo options. The full feature directory and merchant tools remain accessible through More features; money and OTP are still simulated and Sarvam stays paused.

### Customer and merchant views

The header now has an explicit Customer view / Merchant demo switch. Customer navigation contains only customer home, purchases and request tracking, and its feature directory is limited to customer tools. Merchant navigation has its own overview, checkout request list, orders, approvals, recovery, reports and tests. The merchant checkout desk displays the saved cashier evidence for account-visible checkout cases, with a link back to the same request's customer update. The customer result no longer embeds the cashier copy.

This switch is a presentation aid for the local demonstration, not a separate merchant login or authorization boundary. Merchant mode is disabled for non-operator accounts; existing server-side session, ownership and staff checks remain authoritative. Local demo operators still see only their own/assigned synthetic records. No real merchant terminal, bank connection, payment or notification is added.
