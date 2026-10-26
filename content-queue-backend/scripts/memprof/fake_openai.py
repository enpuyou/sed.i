"""Fake OpenAI server (embeddings + chat/completions).

Mimics the real limit that matters here: /v1/embeddings returns HTTP 400 when a
single request carries more than 2048 inputs. Otherwise returns deterministic
1536-dim unit vectors (seeded by input hash). chat/completions returns a tool
call whose arguments are generated from the request's JSON schema (instructor
path) or canned text. NOT mimicked: the 300k-total-token and 8192-token-per-input
limits of the real API.
GET /stats returns counters; POST /reset zeroes them.
"""

import hashlib
import json
import time
import uuid

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
CENTERS = np.random.default_rng(1).standard_normal((20, 1536))
MAX_INPUTS = 2048
STATS = {
    "embed_calls": 0,
    "embed_inputs": 0,
    "embed_rejected_400": 0,
    "max_inputs_in_call": 0,
    "chat_calls": 0,
}


def vec(text):
    h = int(hashlib.md5(text.encode()).hexdigest(), 16)
    rng = np.random.default_rng(h % (2**32))
    v = CENTERS[h % 20] + 0.8 * rng.standard_normal(1536)
    v /= np.linalg.norm(v)
    return v.astype(np.float32).tolist()


@app.post("/v1/embeddings")
async def emb(req: Request):
    b = await req.json()
    inp = b["input"]
    if isinstance(inp, str):
        inp = [inp]
    STATS["embed_calls"] += 1
    STATS["max_inputs_in_call"] = max(STATS["max_inputs_in_call"], len(inp))
    if len(inp) > MAX_INPUTS:
        STATS["embed_rejected_400"] += 1
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "'$.input' is invalid. Please check the API reference: https://platform.openai.com/docs/api-reference.",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": None,
                }
            },
        )
    STATS["embed_inputs"] += len(inp)
    return {
        "object": "list",
        "model": b.get("model", "text-embedding-3-small"),
        "data": [
            {"object": "embedding", "index": i, "embedding": vec(str(t))}
            for i, t in enumerate(inp)
        ],
        "usage": {"prompt_tokens": 10 * len(inp), "total_tokens": 10 * len(inp)},
    }


def fill(schema, defs, name=""):
    if "$ref" in schema:
        return fill(defs[schema["$ref"].split("/")[-1]], defs, name)
    if "anyOf" in schema:
        opts = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return fill(opts[0], defs, name) if opts else None
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type")
    if t == "object":
        return {k: fill(v, defs, k) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        if schema.get("items", {}).get("type") == "string":
            return ["alpha beta", "gamma delta", "epsilon zeta"]
        return [
            fill(schema.get("items", {}), defs, f"{name}{i}") for i in range(2)
        ]  # distinct values per element
    if t == "string":
        return f"canned {name}"
    if t == "integer":
        return 1
    if t == "number":
        return 0.5
    if t == "boolean":
        return True
    return "x"


@app.post("/v1/chat/completions")
async def chat(req: Request):
    b = await req.json()
    STATS["chat_calls"] += 1
    msg = {"role": "assistant", "content": None}
    tools = b.get("tools")
    if tools:
        f = tools[0]["function"]
        params = f.get("parameters", {})
        args = fill(params, params.get("$defs", {}))
        msg["tool_calls"] = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": f["name"], "arguments": json.dumps(args)},
            }
        ]
        fr = "tool_calls"
    else:
        rf = (b.get("response_format") or {}).get("type")
        msg["content"] = (
            json.dumps(
                {
                    "tags": ["alpha", "beta"],
                    "summary": "canned summary",
                    "name": "canned",
                    "label": "canned",
                    "duplicates": [],
                }
            )
            if rf == "json_object"
            else "canned completion text"
        )
        fr = "stop"
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:8],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": b.get("model", "gpt-4o-mini"),
        "choices": [{"index": 0, "message": msg, "finish_reason": fr}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


@app.get("/stats")
def stats():
    return STATS


@app.post("/reset")
def reset():
    for k in STATS:
        STATS[k] = 0
    return STATS
