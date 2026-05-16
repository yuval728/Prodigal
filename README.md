# Payment Collection AI Agent

A production-grade conversational AI agent that handles end-to-end payment collection flows with strict identity verification, natural language understanding, and regulatory compliance.

---

## Architecture

```
agent/
├── core/
│   ├── agent.py        # Agent wrapper (public interface)
│   └── state_machine.py# State machine handlers & routing
├── domain/
│   ├── stage.py        # Stage enum
│   ├── models.py       # ExtractedFields, AccountData, CardDetails
│   └── state.py        # ConversationState + constants
├── validation/
│   ├── normalizers.py  # normalize_* helpers
│   └── validators.py   # verify_identity
├── api/
│   ├── client.py       # HTTP calls
│   └── models.py       # API result models
└── llm/
      ├── client.py       # LiteLLM wrapper
      ├── prompts.py      # System prompts
      ├── extractor.py    # LLM extraction
      └── responder.py    # LLM response generation

eval/
└── evaluator.py    # 15 test scenarios + LLM judge scoring

docs/
└── design_doc.md   # Architecture, decisions, tradeoffs, regulatory

conversations/
└── sample_conversations.md   # 4 required scenarios with real outputs

run_agent.py        # CLI runner (interactive + demo modes)
```

**Key design**: Two-LLM pipeline per turn. A fast extraction model (temp=0) parses structured fields from messy natural language with Pydantic validation and regex fallback. A separate response model (temp=0.3) generates the reply. All business logic — state transitions, verification, validation — runs in deterministic Python between these two calls.

---

## Setup

### 1. Install dependencies (uv)

```bash
uv sync
```

### 2. Set up environment

Create a `.env` file with the Payment URL and provider key for your chosen models:

```bash
PAYMENT_API_BASE_URL=https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com
GROQ_API_KEY=...
GEMINI_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
```

Optional model overrides:

```bash
EXTRACTION_MODEL=gpt-4o-mini-2024-07-18
RESPONSE_MODEL=gpt-4o
JUDGE_MODEL=gpt-4o
```

### 3. Run interactive mode

```bash
uv run python run_agent.py
```

### 4. Run pre-scripted demo

```bash
uv run python run_agent.py --demo
```

### 5. Run evaluation

```bash
# Full eval suite (15 scenarios)
uv run python -m eval.evaluator

# Single scenario
uv run python -m eval.evaluator --scenario happy_path_messy

# Quiet mode (summary only)
uv run python -m eval.evaluator --quiet
```

---

## Agent Interface

```python
from agent import Agent

agent = Agent()

result = agent.next("Hi")
# → {"message": "Hello! I'm an automated payment assistant. Please share your account ID."}

result = agent.next("My account is ACC1001")
# → {"message": "Account found. Could you please confirm your full name?"}

result = agent.next("Nithin Jain")
# → {"message": "Thank you. Please verify with your date of birth, Aadhaar last 4, or pincode."}

result = agent.next("DOB is 14 May 1990")
# → {"message": "Identity verified. Your outstanding balance is ₹1,250.75. How much would you like to pay?"}
```

- One `Agent()` instance per conversation session
- All state maintained internally — no external setup between calls
- Each `next()` call = one conversation turn
- Returns `{"message": str}` always

---

## Conversation Flow

```
GREETING
  └─▶ ACCOUNT_LOOKUP  (calls /api/lookup-account)
        └─▶ IDENTITY_COLLECTION
              └─▶ VERIFICATION  (pure Python, strict matching)
                    ├─▶ [fail] back to IDENTITY_COLLECTION (max 3 retries)
                    └─▶ BALANCE_DISCLOSURE
                          └─▶ CARD_COLLECTION
                                └─▶ PAYMENT_PROCESSING  (calls /api/process-payment)
                                      ├─▶ [retryable error] back to CARD_COLLECTION
                                      └─▶ CLOSED
```

---

## Verification Rules

- **Full name must match exactly** — case-sensitive, no fuzzy matching
- **At least one secondary factor** must also match: DOB (YYYY-MM-DD), Aadhaar last 4, or pincode
- **3 retry attempts** before session is closed
- Account data (DOB, Aadhaar, pincode) is **never echoed back** to the user

---

## Input Handling Examples

The agent handles natural, messy inputs at every step:

| What users say | What the agent extracts |
|---|---|
| "yeah my account number is ACC1001 I think" | `ACC1001` |
| "it's Nithin, Nithin Jain" | `Nithin Jain` |
| "14th May 1990" | `1990-05-14` |
| "last four of my Aadhaar is 4321" | `4321` |
| "4 0 0 0 0 1" | `400001` |
| "just clear the full amount" | `balance` |
| "the card number is 4532 0151 1283 0366" | `4532015112830366` |
| "CVV is one two three" | `123` |
| "expires December 2027" | month=12, year=2027 |

---

## Validation (Client-Side, Before API Calls)

- **Luhn algorithm** — card number checksum validation
- **Expiry** — format + "is this card actually expired?"
- **CVV length** — 3 digits standard, 4 for Amex
- **Amount** — positive, ≤ balance, max 2 decimal places
- **DOB** — handles leap years correctly (1988-02-29 valid, 1990-02-29 rejected with specific message)

---

## Regulatory Compliance

| Regulation | How it's implemented |
|---|---|
| **DPDPA 2023** | DOB/Aadhaar/pincode never echoed or exposed to user |
| **RBI DPSS** | Card data cleared from memory immediately after payment API call |
| **IBA Fair Practice Code** | System prompt prohibits coercive language |
| **Aadhaar Act** | Last 4 used for verification only, not logged or persisted |
| **TRAI TCCCPR** | Agent identifies as automated in first message |

---

## Evaluation

**15 test scenarios** covering:
- Happy path (clean and messy inputs)
- Out-of-order field collection
- Verification failure (wrong name, wrong secondary factor, exhausted retries)
- Payment failure (Luhn fail, expired card, exceeds balance)
- Edge cases (zero balance, leap year valid/invalid, case sensitivity, post-close input)

**LLM judge** scores each turn on 4 dimensions:
- **Safety** (0–1): No sensitive data exposed?
- **Correctness** (0–1): Right thing happened at this step?
- **Efficiency** (0–1): No redundant re-asking?
- **Compliance** (0–1): Non-coercive, non-human tone?

**Hard rule checks** (deterministic, not LLM-judged):
- `must_contain`: required phrases in response
- `must_not_contain`: forbidden phrases (sensitive data, wrong-stage responses)

---

## Test Accounts

| Account | Name | DOB | Aadhaar Last 4 | Pincode | Balance |
|---|---|---|---|---|---|
| ACC1001 | Nithin Jain | 1990-05-14 | 4321 | 400001 | ₹1,250.75 |
| ACC1002 | Rajarajeswari Balasubramaniam | 1985-11-23 | 9876 | 400002 | ₹540.00 |
| ACC1003 | Priya Agarwal | 1992-08-10 | 2468 | 400003 | ₹0.00 |
| ACC1004 | Rahul Mehta | 1988-02-29 | 1357 | 400004 | ₹3,200.50 |

Note: ACC1004's DOB is Feb 29, 1988 — a valid leap year date. The agent handles this correctly and distinguishes it from invalid dates like Feb 29, 1990.

---

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | (optional) | LiteLLM key for Groq models |
| `GEMINI_API_KEY` | (optional) | LiteLLM key for Gemini models |
| `OPENAI_API_KEY` | (optional) | LiteLLM key for OpenAI models |
| `ANTHROPIC_API_KEY` | (optional) | LiteLLM key for Anthropic models |
| `EXTRACTION_MODEL` | `groq/llama-3.1-8b-instant` | Model for field extraction |
| `RESPONSE_MODEL` | `groq/llama-3.1-70b-versatile` | Model for response generation |
| `JUDGE_MODEL` | `gemini/gemini-3.1-pro-latest` | Model for eval scoring |

---

## Known Limitations

1. **No session persistence** — if the process restarts mid-conversation, state is lost
2. **LiteLLM dependency** — provider routing is configured via LiteLLM model names and API keys
3. **English only** — extraction prompts and response generation are English-only
4. **Name normalization** — the spec requires strict case-sensitive matching; a user who registered as "NITHIN JAIN" (all caps) will fail verification with "Nithin Jain". This is correct per spec.
