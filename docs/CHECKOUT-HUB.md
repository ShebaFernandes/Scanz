# Checkout hub: queue-side payment recovery

## What is implemented

The new Checkout help entry preserves My purchases, optional card scanning, UPI lookup, order rescue, approvals, analytics and existing refund workflows. Sign-in remains mandatory. The new path is:

1. Scan locally or enter only the last four card digits / a UPI ID.
2. Provide an Indian mobile number and acknowledge that the connection is simulated.
3. Enter a short-lived on-screen **demo** code. No SMS is sent, no bank OTP is accepted as a required credential, and neither phone possession nor bank ownership is verified.
4. Select an explicitly synthetic checkout transaction and choose information-only help or authorize one conditional simulated reversal.
5. Compare the transaction reference, merchant, amount, reported debit and merchant-acquirer outcome.
6. Produce distinct states: merchant paid, reversal accepted, simulated credit confirmed, unresolved partner evidence, or credit still pending.

A failed-payment reversal is a payment-partner operation against the original transfer, not a merchant refund of money that the merchant never received. This mock models that distinction; it has no real authority to execute either.

## Mock scenarios and measurable demonstration

- **Mall Grocery Counter:** simulated bank debit and merchant credit acknowledgements arrive. No reversal is requested. Receipt is explicitly unsuitable as proof of real payment.
- **Mall Fashion Counter:** bank debit and final merchant non-credit are confirmed; an authorized mock reversal is accepted, then a separate mock credit acknowledgement completes the case.
- **Mall Electronics Counter:** reversal accepted but credit never confirmed. The case remains open as TRACKING_CREDIT, never marked resolved.
- **Mall Cafe Counter:** no definitive partner result. Keep checking until the 60-second demo investigation deadline, then save evidence for review. Never reverse on a reported debit alone.

The background worker schedules four-second mock investigation stages, a separate credit stage and bounded follow-up. Timers are a demo service target, not RBI refund deadlines. Progress survives reload/restart. Repeated requests reuse the same payment case and reversal reference. Status-only intent never requests a reversal. The mock automatic limit is INR 1,000; larger amounts require review. A persisted transaction snapshot protects against reference, ownership or amount changes during execution.

## Implemented security boundaries

- Existing server-side authentication, account ownership, CSRF and origin checks apply to every new API.
- Full card numbers are rejected server-side. Scans stay on device; only last four digits are used. UPI identifiers are hashed for local grouping. They confer no bank access.
- Phone data is limited to the last four digits for a challenge; no full phone number is persisted by the hub. No SMS service receives it.
- Demo codes are salted scrypt hashes, expire after two minutes and are single-use. Incorrect attempts are limited to five and persisted even on rejection. Request frequency is bounded to five per account per 15 minutes and a minimum 30-second resend interval.
- Checkout grants are random, hash-stored, account/instrument-scoped and expire after ten minutes. They are held in browser memory, not localStorage. Existing authenticated session is required in addition to the grant.
- Transactions and case history are account-isolated. A grant for one account or instrument cannot select another account's transactions.
- No AI call, payment API, bank connection, SMS, actual reversal or funds movement occurs.

This is an engineering prototype, not a certified financial product. Demo OTP is deliberately visible and MUST NOT be promoted as bank authentication or production second-factor authentication.

## What a real deployment requires

Real queue-side resolution needs a contracted merchant/acquirer/PSP or issuer integration. Consumer card digits, UPI IDs and a self-entered mobile number cannot supply account history or reversal authority. Use provider-hosted authorization and the provider's supported bank/account verification. A genuine bank OTP must stay inside the authorized provider's flow, never our generic form. Application phone verification is a separate service and not proof of bank ownership.

A trusted merchant transaction ID / UPI reference must be mapped to the authenticated customer and merchant transaction. Validate amount, currency, merchant, network identifiers and any provider-approved payer association. Only a trusted, authenticated partner response can change financial state. Production callbacks require signature verification, timestamp/replay controls and idempotent reconciliation. A debit notification or uploaded screenshot is not adequate settlement evidence.

Execute only operations authorized by the partner contract and customer/merchant mandate. Do not pretend to reverse an unsettled transaction through a merchant refund API. No successful merchant credit and reversal may complete incompatibly. If a response is lost, query the original operation/reference before retrying. Actual refunds/reversals must return to the original permitted payment route; this app must not collect replacement bank destinations or advance money to manufacture an instant refund.

Before live enablement: provider onboarding and permitted scopes; HTTPS and Secure cookies; managed encrypted credentials; appropriate SMS sender/template onboarding if phone verification is added; audit and operational monitoring; privacy notice and specific consent; data minimization, retention/deletion and incident procedures; applicable Indian privacy/payment, outsourcing, data-location and card-data obligations assessed with the authorized partner and qualified compliance counsel. RBI/PCI compliance is not established merely by these prototype controls.

## Official reference boundaries

[RBI failed-transaction circular, 20 September 2019](https://m.rbi.org.in/commonman/English/Scripts/Notification.aspx?Id=3074) sets different outer limits for different failure categories and asks participants to pursue faster resolution. Its table distinguishes UPI transfer beneficiary non-credit (T+1) from merchant confirmation failures (T+5), and includes card PoS merchant-confirmation failures (T+5). These are not promises that an independent app can force instant bank credit. Actual classification and applicable amendments must be validated by the regulated partner; this prototype calculates no statutory compensation or legal deadline.

[NPCI UPI FAQs](https://www.npci.org.in/what-we-do/upi/faqs) describe debited-but-failed/pending payment issues and participant complaint routes. The provider integration must support those routes. A support acknowledgement and a reversal acceptance must stay distinct from a bank credit acknowledgement.

## Demonstration of a lost response

The optional “Test a system failure” control sets `simulate_response_loss` on a new mock case request. The backend commits the case and its audit event, then returns HTTP 504 deliberately. The client retries with the same payment ID. The backend finds the original case, records reuse and returns that case; the worker continues it normally. This simulates a response lost after saving a case, not a real provider outage. A dedicated integration test verifies one case and one mock reversal acceptance after retry and completion. Repeating an already existing case does not inject a new timeout.

The resolution desk shows measured elapsed time, actual check count, recorded reversal-reference count, event history and a receipt-style cashier copy derived from the same case object. The cashier copy is an evidence mirror for the demonstration, not a separate authenticated merchant portal or valid payment receipt. Checkout cases also appear in My requests and can be reopened. Design uses navy/white, readable form steps and phone-responsive layouts; no model calls are made for the interface or the recovery demo.
