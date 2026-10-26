---
layout: layouts/chapter.njk
title: The LLM gateway
part: 3
order: 1
summary: Why every model call in sed.i goes through one class, and what that class does about failures, cost and tracing.
verified: 2026-10-25
permalink: /llm/gateway/
---

## In one minute

Every call that sed.i makes to a language model or an embedding model goes through one Python class, `LLMClient`, in `content-queue-backend/app/core/llm_client.py`. The rest of the code names a task ("tagging", "synthesis") and the class picks the model, the provider (OpenAI by default, Amazon Bedrock as the alternative) and the timeout. For chat, a temporary error such as a rate limit is retried once on the same provider, and any failure after that is retried on the other provider. Embeddings never fall back, because vectors from two different models cannot be compared. Before each call the class checks a per-user daily spend counter in Redis, and after each call it adds the cost to that counter. A test walks the source code and fails if any call site forgets to pass a user id or to open a tracing span.

The point of the single class is that these concerns exist once, not twenty-seven times.

## The problem

The question this chapter answers: **why does sed.i put every model call behind one class instead of calling the OpenAI library where it is needed?**

The obvious design is to import `openai` in each task and call it. That works for a first feature. By the fifth feature it has these problems:

1. **No cost control.** Anyone who can trigger a background task can spend your OpenAI credit. A bug that loops, or one user who saves thousands of articles, is paid for by the owner. A limit has to be checked before each call, in the same way everywhere.
2. **Failures are handled differently in each place.** The OpenAI library retries silently by default, the task may retry on top of that, and a third layer may exist elsewhere. Nobody can say how many requests one failure produces.
3. **No attribution.** The bill says how much was spent, not on what or for whom. Tracing needs the same labels on every call.
4. **A provider change touches every file.** The decision record for this design (`docs/decisions/0003-llm-provider-strategy.md`) wanted the option to move chat to Bedrock without editing call sites.
5. **A silent wrong answer.** If one embedding call quietly used a different model, the database would hold vectors from two vector spaces, and search would return nonsense with no error.

## Mental model

Think of the gateway as the single cashier in a shop. Customers (tasks) say what they want ("tagging, please"). The cashier decides which supplier to order from, refuses to serve a customer whose daily tab is over the limit, calls the other supplier if the first is down, and writes every sale in the same ledger. Customers never talk to suppliers.

| Term | Meaning here |
|---|---|
| Task | A string such as `tagging` or `article_analysis`. It selects the model and labels the call in traces. |
| Provider | Who runs the model: `openai` or `bedrock` (Amazon's hosted models, reached with the `boto3` library). |
| Transient error | An error that may go away if you wait: rate limit, timeout, connection failure. |
| Fallback | Trying the other provider after the first one failed. |
| Budget | A USD ceiling per user per UTC day (`LLM_DAILY_BUDGET_USD_PER_USER`, default 5.0). |
| Span | One record in a trace (Braintrust, a tracing service for LLM calls) that carries a name, inputs, timings and metadata such as `user_id`. |
| Structured chat | A chat call whose answer must parse into a Pydantic model (a typed Python class). Uses the `instructor` library. |

{% fig "What happens to one call" %}
```text
task code
  | llm_client.chat(messages, task="tagging", user_id=U)
  v
check_budget(U)       Redis: llm_spend:U:<date>  >= ceiling ?
  |                     yes -> raise BudgetExceededError (no retry)
  v
pick model            LLM_MODEL_TAGGING_OPENAI -> "gpt-4o-mini"
  |
  v
primary provider ---- ok --------------------------+
  | transient error                                |
  v                                                |
wait 1 s, same provider ---- ok -------------------+
  | fails                                          |
  v                                                |
other provider ------ ok --------------------------+
  | fails -> exception reaches the task            |
  v                                                v
                                          record_spend(U, cost)
```
{% endfig %}

## Walkthrough: one chat call and one embed call

Everything below was run against the real `LLMClient` with `OPENAI_BASE_URL` pointing at the fake OpenAI server in `content-queue-backend/scripts/memprof/fake_openai.py`. No paid service was called. The fake server returns canned text and deterministic vectors, so the model output is not meaningful; the requests, the bookkeeping and the Redis writes are real. A small wrapper printed each HTTP request the library sent.

### Step 1: a chat call

```python
llm_client.chat(
    [{"role": "user", "content": "Suggest tags for: espresso"}],
    task=TASK_TAGGING, max_tokens=64, user_id="demo-user-1",
)
```

The class first calls `check_budget("demo-user-1")`. No counter exists yet, so it passes. Then `_resolve_model` builds the setting name `LLM_MODEL_TAGGING_OPENAI` from the task and provider and reads it from settings. The request the fake server received (captured):

```text
POST /v1/chat/completions
body: {"messages": [{"role": "user", "content": "Suggest tags for: espresso"}],
       "model": "gpt-4o-mini", "max_tokens": 64, "temperature": 0.0}
```

The server answered with 100 prompt tokens and 20 completion tokens (its fixed canned usage). The gateway multiplied those by the price table (see below) and wrote the cost to Redis: 100 x $0.15 per million + 20 x $0.60 per million = $0.000027.

### Step 2: an embed call

```python
llm_client.embed(["hello world", "second text"], user_id="demo-user-1")
```

Captured request:

```text
POST /v1/embeddings
body: {"input": ["hello world", "second text"],
       "model": "text-embedding-3-small", "encoding_format": "float"}
```

The result held 2 vectors of 1,536 numbers and a usage of 20 prompt tokens. Embeddings have no completion tokens, so the cost is 20 x $0.02 per million = $0.0000004.

### Step 3: what Redis holds

After both calls, the only key in the scratch Redis database was (captured):

```text
llm_spend:demo-user-1:2026-10-26   value 0.0000274   ttl 90000 seconds
```

The key is `llm_spend:<user id>:<UTC date>`. The value is the sum of both costs ($0.000027 + $0.0000004). The time to live is 90,000 seconds, which is 25 hours: longer than one UTC day, so the key is still readable at day's end and removes itself afterwards. The next UTC day uses a new key, which is how the counter resets without any job.

### Step 4: the same call when things go wrong

I replaced the provider-level method with a stub to force each failure, then called `chat()` (captured, logs shortened):

| Case | Provider calls made | Result |
|---|---|---|
| OpenAI raises `RateLimitError` both times | openai, openai, bedrock | Bedrock's answer returned, 1.0 s elapsed (the retry wait) |
| OpenAI raises `AuthenticationError` | openai, bedrock | Bedrock's answer returned, 0.0 s elapsed (no retry wait) |
| Counter set to $5.00, then a call with that `user_id` | none | `BudgetExceededError: User ... has spent $5.0000 today, at or above the $5.00 daily ceiling` |
| Redis address pointed at a dead port | openai | Normal answer, 0.71 s elapsed; two warnings logged, one from the check and one from the record |

The last row is the fail-open behaviour: when the gateway cannot reach Redis, it skips the budget instead of stopping the call.

### Step 5: structured chat

`structured_chat` asks for an answer that fits a Pydantic model. For OpenAI it wraps the client with `instructor`, which sends the model's JSON schema as a forced tool call, parses the reply, and re-asks the model with the validation error if parsing fails. Against the fake server, with a two-field model `{tags, summary}`, it returned a validated instance and recorded $0.000027 under the user's key. The next two chapters use this path.

## How it is built here

| Piece | Where |
|---|---|
| The class, price table, budget functions, tracing helper | `content-queue-backend/app/core/llm_client.py` |
| Per-task model settings, default provider, budget ceiling | `content-queue-backend/app/core/config.py` |
| The decision record | `docs/decisions/0003-llm-provider-strategy.md` |
| The guard test | `content-queue-backend/tests/test_llm_call_site_guard.py` |
| Query-embedding cache that wraps `embed` | `content-queue-backend/app/core/embedding_cache.py` |
| Unit tests for retries and fallback | `content-queue-backend/tests/test_llm_client.py` |

### Tasks and models

A task name is a constant (`TASK_TAGGING = "tagging"`). For chat tasks, the settings hold one model per provider. The table lists the chat tasks that matter to this part (read from `config.py`):

| Task | OpenAI model | Bedrock model |
|---|---|---|
| `tagging`, `entity_extraction`, `entity_dedup`, `memory_consolidation`, `routing`, `insight` | gpt-4o-mini | amazon.nova-micro-v1:0 |
| `summary`, `mcp_summary` | gpt-4o-mini | amazon.nova-lite-v1:0 |
| `article_analysis` | gpt-4o | amazon.nova-micro-v1:0 |
| `sql_gen`, `synthesis` | gpt-4o | a Claude Sonnet 4.5 model on Bedrock |

Embedding-only tasks (`embedding`, `chunk_embedding`, `tag_embedding`, and so on) have no model setting. They exist only as span labels, because `embed()` always uses `EMBED_PROVIDER`.

<!--src content-queue-backend/app/core/llm_client.py#281-283-->
```python
def _resolve_model(self, task: str, provider: str) -> str:
    attr = f"LLM_MODEL_{task.upper()}_{provider.upper()}"
    return getattr(settings, attr)
```

The setting name is built from the task string, so a typo in a task name fails at call time with `AttributeError`, not at import time.

### The client and its timeouts

<!--src content-queue-backend/app/core/llm_client.py#203-206-->
```python
# max_retries=0: the SDK's own silent retries are disabled so retry behavior
# is explicit in chat()'s same-provider-retry-then-fallback logic instead of
# hidden inside the client.
client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=30.0, max_retries=0)
```

Two settings matter. `timeout=30.0` bounds each request to 30 seconds. `max_retries=0` turns off the library's built-in retrying, which would otherwise hide extra requests from the gateway's own logic. Bedrock gets the equivalent through `Config(connect_timeout=5, read_timeout=30)`.

### Chat: retry once, then fall back

<!--src content-queue-backend/app/core/llm_client.py#351-389-->
```python
except TRANSIENT_CHAT_ERRORS as e:
    logger.warning(
        f"chat transient error on {primary} ({e}), retrying same provider"
    )
    time.sleep(_TRANSIENT_RETRY_BACKOFF_SECONDS)
    try:
        result = self._chat_with(
            primary,
            messages,
            model=model,
            task=task,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )
    except Exception as retry_e:
        logger.warning(
            f"chat retry failed on {primary} ({retry_e}), falling back to {fallback}"
        )
        result = self._chat_with(
            fallback,
            messages,
            model=model,
            task=task,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )
except Exception as e:
    logger.warning(f"chat failed on {primary} ({e}), retrying on {fallback}")
    result = self._chat_with(
        fallback,
        messages,
        model=model,
        task=task,
        max_tokens=max_tokens,
        temperature=temperature,
        response_format=response_format,
    )
```

Read it as three attempts at most. A transient error gets a second try on the same provider after a 1-second wait, then one try on the other provider. Any other error (bad key, malformed request, content policy) skips the second try and goes to the other provider at once, because repeating the same request cannot fix it. If the fallback also fails, its exception propagates to the task.

The transient errors are a short tuple, shared with the tasks:

<!--src content-queue-backend/app/core/llm_client.py#50-55-->
```python
TRANSIENT_CHAT_ERRORS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
)
_TRANSIENT_RETRY_BACKOFF_SECONDS = 1.0
```

### Embed: no fallback

<!--src content-queue-backend/app/core/llm_client.py#304-311-->
```python
if user_id is not None:
    check_budget(user_id)
if isinstance(texts, str):
    texts = [texts]
result = self._embed_with(settings.EMBED_PROVIDER, texts, model=model)
if user_id is not None:
    record_spend(user_id, estimate_cost_usd(result.model, result.prompt_tokens))
return result
```

`embed` has no `try` at all. The reason is geometric: a vector from `text-embedding-3-small` and a vector from Titan have the same length (1,536) but mean different things per axis. The database would accept both, and every distance between a stored vector and a query vector from the other model would be meaningless. A loud failure that a retry later fixes is better than a quiet corruption that needs a full re-embedding of the library to repair. The code says this in two places (the module docstring and `config.py`): changing `EMBED_PROVIDER` requires re-embedding all content.

### The budget

<!--src content-queue-backend/app/core/llm_client.py#175-184-->
```python
r = _get_redis_client()
if r is None:
    return

spent = r.get(_spend_key(user_id))
if spent is not None and float(spent) >= settings.LLM_DAILY_BUDGET_USD_PER_USER:
    raise BudgetExceededError(
        f"User {user_id} has spent ${float(spent):.4f} today, "
        f"at or above the ${settings.LLM_DAILY_BUDGET_USD_PER_USER:.2f} daily ceiling"
    )
```

<!--src content-queue-backend/app/core/llm_client.py#190-198-->
```python
if cost_usd <= 0:
    return
r = _get_redis_client()
if r is None:
    return

key = _spend_key(user_id)
r.incrbyfloat(key, cost_usd)
r.expire(key, 60 * 60 * 25)  # 25h — outlives one UTC day, self-expires
```

The order is check, call, record. A call that starts under the ceiling can finish above it, so the ceiling is approximate by up to one call. `BudgetExceededError` is raised before the `try` in `chat()`, so the fallback logic never sees it and it is not retried: a retry would find the same counter. Tasks catch it and return a status of `budget_exceeded`.

Each of the two functions opens a fresh Redis connection and pings it (`_get_redis_client`). Measured on a local Redis, `check_budget` took about 4.3 ms per call. That is noise next to a model call of hundreds of milliseconds, but it is two extra connections per call.

### The price table

`_PRICE_PER_TOKEN_USD` maps an exact model id to a prompt and completion price per token. Examples: `gpt-4o-mini` at $0.15 and $0.60 per million tokens, `gpt-4o` at $2.50 and $10.00, `text-embedding-3-small` at $0.02, and the three Bedrock chat models. The table was last verified on 2026-07-21 according to its comment. An unknown model costs $0.00 with a warning, so a new model that is missing from the table is never blocked, and also never counted. The OpenAI figures in the Braintrust UI come from Braintrust's own price registry, so the table feeds only the Redis counter and the Bedrock spans.

One detail: for `chat()` and `embed()` the cost is computed from `result.model`, the model name the server reports, not the name the gateway asked for. I believe the real OpenAI chat API reports a dated name such as `gpt-4o-mini-2024-07-18`; I could not observe that because no real call was made (the fake server echoes the requested name). That name is not a key in the table, and I measured what the lookup does with it: `estimate_cost_usd("gpt-4o-mini-2024-07-18", 100, 20)` logs "No price entry" and returns $0.00, while `gpt-4o-mini` gives $0.000027. If the real API does report dated names, chat spend is under-counted. This is unverified and worth checking in a production trace. The `structured_chat` path is not affected, because it uses the configured model name.

### Tracing

When `BRAINTRUST_API_KEY` is set, the OpenAI client is wrapped with `braintrust.wrap_openai`, which records every request automatically as a child of whatever span is open. Bedrock cannot be wrapped that way, so its two methods open a span by hand and log token counts and estimated cost. Call sites open their own named span around the whole step so the cost can be grouped:

```python
with braintrust_span("tagging", input={"title": title}, metadata={"user_id": uid}):
    llm_client.structured_chat(..., user_id=uid)
```

`braintrust_span` is a no-op when the key is not set, so local development and tests need no tracing account.

### The guard test

Nothing in Python stops a developer from adding a call that omits `user_id`. That call would bypass the budget and appear in traces without an owner. `tests/test_llm_call_site_guard.py` closes the gap by reading the source, not by running it. It parses every file under `app/` into a syntax tree, tracks which `with braintrust_span(...)` blocks enclose each node, and reports a violation for every call to `llm_client.chat`, `.structured_chat` or `.embed` that

1. does not pass `user_id=`, or
2. is not inside a `braintrust_span` block, or
3. is inside one whose `metadata=` dict has no `"user_id"` key.

<!--src content-queue-backend/tests/test_llm_call_site_guard.py#112-124-->
```python
def visit_Call(self, node: ast.Call):
    method = _is_llm_client_call(node)
    if method is not None:
        has_user_id_kwarg = _call_has_user_id_kwarg(node)
        span_open_with_user_id = any(self._span_stack)
        span_open_at_all = len(self._span_stack) > 0

        if not has_user_id_kwarg:
            self.violations.append(
                f"{self.filename}:{node.lineno}: llm_client.{method}(...) "
                f"does not pass user_id= — bypasses per-user budget "
                f"enforcement (check_budget in llm_client.py)."
            )
```

To see it work, I wrote a made-up file with one correct call and three wrong ones and ran the guard's visitor over it (captured):

```python
def good(uid):
    with braintrust_span("tag", metadata={"user_id": uid}):
        return llm_client.chat([], task="tagging", user_id=uid)

def no_span(uid):
    return llm_client.chat([], task="tagging", user_id=uid)

def no_user():
    with braintrust_span("tag", metadata={"user_id": "x"}):
        return llm_client.embed("hi")

def span_without_user(uid):
    with braintrust_span("tag", input={"q": 1}):
        return llm_client.structured_chat([], response_model=None, task="t", user_id=uid)
```

```text
bad_task.py:8: llm_client.chat(...) is not wrapped in a `with braintrust_span(...):` block — no cost-attribution tracing.
bad_task.py:12: llm_client.embed(...) does not pass user_id= — bypasses per-user budget enforcement (check_budget in llm_client.py).
bad_task.py:16: llm_client.structured_chat(...) is wrapped in braintrust_span(...), but none of the enclosing span(s) have metadata={'user_id': ...} — cost attribution won't be grouped by user.
```

The correct function produced no line. (The output lines are long; they are shown as the test prints them.) Run on the real code, the test passes: counted with the same helper, there are 27 gateway call sites in 15 files (10 `structured_chat`, 10 `embed`, 7 `chat`).

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| One class that every call uses | Call the SDK directly; a library such as LiteLLM | One place for budget, retry and tracing. The ADR rejected LiteLLM because the layer is small enough to own and because owning it was the point of learning Bedrock. |
| OpenAI default, Bedrock as the second provider | OpenAI only; Anthropic API directly | Two providers give a fallback and a price comparison. The ADR also names learning AWS as a goal, which is a project reason, not an engineering one. Opinion: the fallback has value only if the second provider's quality has been measured on each task, and the ADR's own migration trigger (scores within 5%) shows that was the plan. |
| Per-task model selection | One model for everything | A tagging call does not need the most expensive model; SQL generation and synthesis do. An input-token price gap of about 17x separates gpt-4o from gpt-4o-mini ($2.50 against $0.15 per million). |
| Retry once, then fall back | Retry several times with growing waits; never retry | A rate limit often clears in a second. Several retries multiply latency and load. Fallback after one retry bounds the added delay to about a second plus one more call. |
| No fallback for embeddings | Fall back to Bedrock Titan | Mixed vector spaces. See above. |
| Budget in Redis, checked before the call | Count in Postgres; rely on the provider's own limits | Redis increments are atomic and fast, and the key expires without a cleanup job. A provider-side limit is account-wide, not per user. |
| Budget fails open | Fail closed (refuse calls when Redis is down) | Redis is already needed for Celery, so an outage stops the pipeline anyway. Failing open keeps the interactive paths that do not need Celery working. |
| SDK retries off, timeout 30 s | Library defaults | Makes the number of requests per failure countable. |
| AST test as guard | Code review; a wrapper that makes `user_id` mandatory | Cheap, and it caught a real regression (19 of 33 sites correct after an earlier hardening pass, per `ARCHITECTURE.md`). A mandatory keyword argument would be stronger, since a call would not run without it; the AST test is a safety net that can be sidestepped by an alias. |

## How it fails

| Failure | What happens | Evidence |
|---|---|---|
| Rate limit or timeout on `chat()` | One retry after 1 s, then the other provider | Run above |
| Both providers fail on `chat()` | The second provider's exception reaches the task | Inferred from the code |
| Hung provider | Each attempt waits up to 30 s, so one `chat()` can take about 30 + 1 + 30 s on OpenAI plus the Bedrock read timeout before failing | Computed from the timeouts, not measured |
| Budget reached | `BudgetExceededError`, no provider call, task records `budget_exceeded` | Run above |
| Redis down | Budget skipped, call proceeds | Run above |
| Embedding provider down | `embed()` raises; the calling task decides whether to retry | Inferred from the code |
| Rate limit inside `structured_chat` | See the next section: it does not reach the task as a rate-limit error | Run below |

### Structured chat does not use the chat() failure logic

`structured_chat` does not call `chat()`. It calls the `instructor` library directly, so it gets neither the same-provider retry nor the fallback. Its `max_retries` argument (default 2) is instructor's number of attempts, and I measured that instructor retries on any exception, not only on validation errors. I replaced the HTTP layer so the server always answered 429 and called `structured_chat` (captured): the fake server was asked **2 times**, and the exception that came out was `InstructorRetryException`, which is not in `TRANSIENT_CHAT_ERRORS`. This matters for tasks that catch the tuple. The [next chapter](/llm/tagging-and-analysis/) shows what that does to their retry logic.

Ten of the 27 call sites use `structured_chat`, including tagging, article analysis and memory consolidation. They are the ones that lose the gateway's resilience.

Instructor's usage accounting is correct in one respect I had doubted: when the first reply failed validation and the second succeeded, the Redis counter held the cost of both calls (in a test where each call reported 900 prompt and 150 completion tokens, the counter showed exactly twice the single-call cost of $0.00375).

## What is weak or missing

Ranked by how likely each is to matter.

1. **Retries stack and nothing counts them.** For a `chat()` call made inside a Celery task that retries on any exception, such as the summary task (`max_retries=3`), one outage with both providers down produces up to 3 provider calls per attempt and 4 attempts, so up to 12 provider calls for one summary, spread over about 7 minutes of task retry delays (60, 120 and 240 seconds). That is derived from the code, not measured end to end. Each layer is reasonable alone; together nobody owns the total.
2. **There is no circuit breaker.** A search of `app/` for "circuit" or "breaker" finds nothing. During a provider outage every call still pays the full timeout and then the fallback, instead of skipping the dead provider for a minute. A breaker (stop calling a provider for some seconds after N consecutive failures) is a small addition at the one place where all calls pass.
3. **`structured_chat` has no fallback and, as shown above, no transient-error retry at the gateway.** The class docstring and the ADR describe fallback as a gateway property; it holds for `chat()` only.
4. **The ceiling can be overshot.** The check and the write are separate steps. Many tasks running at once for one user can all pass the check before any records. The overshoot is bounded by concurrency times the cost of one call, which is small at the current prices, but it is not a hard cap. An atomic increment-then-check removes the race.
5. **Pricing can silently read as zero.** If the model name the server reports is not an exact key in the price table (measured for a dated name, not observed from the real API), cost is zero and the budget under-counts. The table is also hand-maintained.
6. **Fail-open is a policy, not a measurement.** When Redis is unreachable for a long time, nothing is limited. It is logged as a warning only.
7. **The guard test is textual.** It matches the exact name `llm_client`. An alias (`from ... import llm_client as c`) or a call through another object would escape it. It also exempts `embedding_cache.py` from the span requirement. A direct `openai` import elsewhere in `app/` would bypass the gateway entirely; today none exists (checked by search).
8. **The Bedrock path is less exercised.** The ADR itself says JSON mode on Bedrock is emulated by appending an instruction to the prompt, "less reliable than OpenAI's constrained decoding". The evaluation scores in the ADR's migration trigger were the gate for switching, and the default remains OpenAI.

{% say %}
Every model call in sed.i goes through one class. A task says what it is doing, like tagging or synthesis, and the class picks the model and provider, enforces a 30-second timeout, and counts the cost against a per-user daily budget in Redis. If a chat call hits a rate limit it waits a second and retries once, then tries the other provider. Embeddings never fall back, because vectors from two different models can't be compared and would quietly corrupt search. A test reads the source code and fails the build if any call site forgets the user id or the tracing span. What I'd flag as weak: the retries stack with the task-level retries, there is no circuit breaker, and structured calls, which go through the instructor library, skip the fallback entirely.
{% endsay %}

## Check yourself

{% qa "Why does embed() have no fallback to the second provider when chat() does?" %}
Vectors from different embedding models live in different spaces. Both have 1,536 numbers, so the database would accept them, but distances between a stored vector and a query vector from the other model carry no meaning, and search would return wrong results without any error. A loud failure is repairable by retrying; mixed vectors need the whole library re-embedded.
{% endqa %}

{% qa "A user reaches the daily ceiling. Why is BudgetExceededError not retried, and what does the task do?" %}
The counter will not drop until the next UTC day, so a retry would hit the same condition and waste a retry slot. The gateway raises the error before its retry logic, and tasks catch it and return a status of `budget_exceeded`.
{% endqa %}

{% qa "What happens to budget enforcement when Redis is down, and what is the argument for that choice?" %}
The check and the record both log a warning and skip. The argument: Redis already carries the Celery queue, so its outage stops the pipeline anyway; blocking the remaining interactive calls on top of that adds little safety. The cost is that nothing is limited during the outage.
{% endqa %}

{% qa "The guard test passes. Name one kind of call that would still escape it." %}
A call through an alias or another object, for example `from app.core.llm_client import llm_client as c` and then `c.chat(...)`, since the test matches the literal name `llm_client`. A direct `import openai` outside the gateway would escape it too.
{% endqa %}

{% qa "A task retries three times with Celery and calls chat(), which retries once and falls back. How many provider calls can one failure cause, and what would you change?" %}
Up to 4 task attempts times 3 provider calls, which is 12. To control it, either give the whole operation one retry budget, or add a circuit breaker so a provider that failed many times in a row is skipped for a short time.
{% endqa %}

{% qa "Why does structured_chat matter for the retry story even though it shares the class with chat()?" %}
It does not go through chat(), so it has no same-provider retry and no fallback. It retries inside instructor on any exception, and a rate limit comes out wrapped as `InstructorRetryException`, which tasks that catch `TRANSIENT_CHAT_ERRORS` do not recognise.
{% endqa %}
