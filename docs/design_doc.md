# Design Document — Payment Collection AI Agent

## Architecture Overview

The agent is structured as an **LLM-orchestrated state machine**: a deterministic Python layer owns all state transitions and business logic, while LLM calls handle two specific tasks — natural language extraction and response generation. Neither task involves business logic.

```
User Input
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Agent.next()  ←  single entry point, one turn          │
│                                                         │
│  ┌──────────────┐    ┌──────────────────────────────┐   │
│  │  Extraction  │    │     State Machine Router     │   │
│  │  Layer (LLM) │───▶│  GREETING                    │   │
│  │              │    │  ACCOUNT_LOOKUP  ──▶ API     │   │
│  │  gpt-4o-mini │    │  IDENTITY_COLLECTION         │   │
│  │  temp=0      │    │  VERIFICATION  (pure Python) │   │
│  │  JSON output │    │  BALANCE_DISCLOSURE          │   │
│  └──────────────┘    │  CARD_COLLECTION             │   │
│                      │  PAYMENT_PROCESSING ──▶ API  │   │
│  ┌──────────────┐    │  CLOSED                      │   │
│  │  Response    │◀───┴──────────────────────────────┘   │
│  │  Layer (LLM) │                                       │
│  │  gpt-4o      │                                       │
│  │  temp=0.3    │                                       │
│  └──────────────┘                                       │
└─────────────────────────────────────────────────────────┘
    │
    ▼
{"message": str}
```

### Layer Separation

| Layer | Responsibility | LLM? |
|---|---|---|
| `extractor.py` | Parse structured fields from free-form text | Yes (gpt-4o-mini, temp=0) |
| `agent.py` | State machine, transitions, business logic | No |
| `validators.py` | Input validation, Luhn check, date parsing | No |
| `tools.py` | API calls with retry/backoff | No |
| `responder.py` | Generate natural language responses | Yes (gpt-4o, temp=0.3) |

---

## Key Design Decisions

### 1. LLM-Orchestrated State Machine, Not Pure LLM

The assignment warns against rigid state machines that break on messy input. It also warns against stateless LLM calls that can't enforce strict verification. The right design is a hybrid: LLM handles language variability, Python handles correctness.

The LLM cannot bypass verification — it only generates the response after Python has already determined the outcome. This is the critical constraint. A pure LLM agent could be manipulated into skipping steps via prompt injection or unusual phrasing.

### 2. Dedicated Extraction Layer

Most agent implementations make one LLM call per turn that both understands the input and generates the response. This conflates two very different tasks. A dedicated extraction layer (extraction = structured output, temp=0, JSON-only) and a separate response layer (natural language, temp=0.3) is more testable, more predictable, and mirrors real NLU/NLG architecture.

The extraction model is gpt-4o-mini — it's cheaper and faster for a classification/extraction task. Response generation uses gpt-4o for quality.

### 3. Verification is Pure Python

Verification logic (`verify_identity` in `validators.py`) contains zero LLM calls. It performs exact string comparison on name and secondary factors. This is non-negotiable: "strict matching" cannot mean "LLM judges whether these are similar enough." Case sensitivity is enforced as specified.

### 4. Client-Side Validation Before API Calls

The Luhn check, expiry validation, CVV length check, and amount validation all run before any API call. This provides better error messages (we know *why* the input is invalid, not just that the API rejected it) and reduces unnecessary network calls against the payment API.

### 5. Regulatory Compliance as a Design Constraint

Several design decisions are driven by Indian fintech regulations, not just the spec:

- **DPDPA 2023**: Account data (DOB, Aadhaar, pincode) is stored in `AccountData` but never echoed back to the user — not in verification failure messages, not in confirmations. The system prompt for the response layer explicitly prohibits this.
- **PCI-DSS adjacency / RBI DPSS**: Card data is held in `CardDetails` in memory only. `state.clear_card_details()` is called immediately after the payment API returns — before the response is generated. Card fields are never written to logs.
- **IBA Fair Practice Code**: The system prompt prohibits coercive or threatening language. The agent identifies itself as automated in the first message.
- **Aadhaar Act**: Last 4 digits are used only for verification and not persisted beyond the session object's lifetime.

---

## Tradeoffs Accepted

**Two LLM calls per turn (extraction + response)**: This doubles latency and cost versus a single call. The tradeoff is correctness and testability — the extraction call is independently testable and the response call has no business logic burden. In production, the extraction call could be replaced with a fine-tuned smaller model or even a rules-based parser for the most common patterns.

**No streaming**: The `next()` interface returns a complete string. Streaming would improve perceived latency but requires a generator interface, which the spec doesn't ask for.

**State in memory, not persisted**: `ConversationState` is a Python object. If the process crashes mid-conversation, state is lost. The spec doesn't require persistence, and adding Redis or a DB would complicate the interface contract. In production, a session ID would map to persisted state.

**No fuzzy name matching by design**: The spec requires strict matching. This is the right call for a financial verification context — fuzzy matching creates exploitable false-positive windows.

---

## Regulatory Considerations

This agent operates in India's financial services and debt collection space, which involves:

| Regulation | Requirement | Implementation |
|---|---|---|
| DPDPA 2023 | PII not echoed or exposed | Verified in system prompt + `must_not_contain` eval checks |
| RBI DPSS Guidelines | Card data not logged/persisted | `clear_card_details()` post-API call; no logging of card fields |
| IBA Fair Practice Code | Non-coercive debt collection | System prompt section on tone; eval compliance dimension |
| Aadhaar Act | Aadhaar data used only for auth | Not stored post-session, not logged, not echoed |
| TRAI TCCCPR | Agent must identify as automated | First message discloses automated nature |

---

## What I Would Improve With More Time

1. **Replace gpt-4o-mini extraction with a fine-tuned smaller model**: The extraction task is well-defined and has a bounded output space. A fine-tuned `phi-3-mini` or similar would be 10x cheaper and faster with better reliability on domain-specific patterns (Indian names, Aadhaar formats, regional date formats).

2. **Structured output validation with Pydantic**: The extraction layer currently uses JSON mode and manual parsing. Using `instructor` + Pydantic models would give stronger output guarantees and automatic retry on malformed extraction.

3. **Retry on extraction failure with fallback heuristics**: Currently, if extraction fails, the agent returns empty fields and asks the user to repeat themselves. A deterministic regex fallback (account ID, 4-digit sequences, 16-digit sequences) would be more resilient.

4. **Session persistence**: Map session IDs to serialized `ConversationState` in Redis with a TTL. This allows conversations to survive process restarts and enables audit logging without PII exposure.

5. **Audit logging pipeline**: Separate from application logs — structured events (turn start, verification attempt, API call, verification result, payment result) with PII stripped, shipped to a SIEM for compliance monitoring.

6. **Load testing against the API**: The current retry/backoff logic assumes a well-behaved upstream. Under real load, the payment API may rate-limit or have higher latency. Circuit breaker pattern (using `pybreaker` or similar) would prevent cascading failures.
