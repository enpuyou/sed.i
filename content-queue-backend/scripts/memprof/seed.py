# ruff: noqa: E741
"""Deterministic seeding of synthetic library rows directly into Postgres (COPY).

Same seed -> identical rows for before and after runs. search_vector is populated
by the production BEFORE INSERT trigger (tsvector_update), not by this script.
Text uses a ~30k-word Zipf vocabulary, so tsvector size is closer to production than
a tiny vocabulary would give.
"""

import datetime as dt
import io
import random
import uuid
import zlib

import numpy as np
import psycopg2

USERS = {  # key -> (email, n_items)
    "u100": ("memprof-u100@example.test", 100),
    "u500": ("memprof-u500@example.test", 500),
    "u2000": ("memprof-u2000@example.test", 2000),
    "uw": ("memprof-worker@example.test", 0),
}
PASSWORD = "Passw0rd!memprof"
TOPICS = [
    "python",
    "ai",
    "design",
    "history",
    "science",
    "music",
    "economics",
    "writing",
    "health",
    "startups",
    "climate",
    "philosophy",
]
EPOCH = dt.datetime(
    2026, 10, 1, tzinfo=dt.timezone.utc
)  # fixed so rows are identical across runs

_SYL = [
    "ba",
    "ke",
    "lo",
    "mi",
    "ra",
    "su",
    "ti",
    "vo",
    "na",
    "pe",
    "do",
    "fa",
    "gi",
    "hu",
    "jo",
    "ze",
    "wa",
    "cu",
    "ny",
    "xo",
]


def _vocab(n=30000):
    rnd = random.Random(11)
    seen, out = set(), []
    while len(out) < n:
        w = "".join(rnd.choice(_SYL) for _ in range(rnd.randint(2, 5)))
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _esc(s):
    return (
        s.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _arr(l):
    return "{" + ",".join('"%s"' % x for x in l) + "}"


def _embs(k=200):
    cent = np.random.default_rng(1).standard_normal((20, 1536))
    out = []
    for i in range(k):
        g = np.random.default_rng(1000 + i)
        v = cent[i % 20] + 0.8 * g.standard_normal(1536)
        v /= np.linalg.norm(v)
        out.append("[" + ",".join(f"{x:.6f}" for x in v) + "]")
    return out


def _article(rnd, vocab, cw, idx, target=40_000):
    parts = [f"<h1>Item {idx}</h1>"]
    size = len(parts[0])
    p = 0
    while size < target:
        p += 1
        if p % 12 == 0:
            s = f"<h2>{' '.join(rnd.choices(vocab, cum_weights=cw, k=4))}</h2>"
        else:
            s = (
                "<p>"
                + " ".join(rnd.choices(vocab, cum_weights=cw, k=rnd.randint(60, 140)))
                + ".</p>"
            )
        parts.append(s)
        size += len(s)
    return "".join(parts)


def seed_user(cur, key, hashed_pw, vocab, cw, embs, log=print):
    email, n = USERS[key]
    rnd = random.Random(zlib.crc32(key.encode()))
    uid = uuid.UUID(int=rnd.getrandbits(128), version=4)
    cur.execute(
        "insert into users(id,email,username,hashed_password,full_name,is_active,is_verified,is_public,"
        "is_queue_public,is_crates_public,reading_patterns) values(%s,%s,%s,%s,'Memprof',true,true,false,false,false,'{}'::jsonb)",
        (str(uid), email, key, hashed_pw),
    )
    if n == 0:
        return {"user_id": str(uid), "items": 0}
    items, chunks = io.StringIO(), io.StringIO()
    ids = []
    for i in range(n):
        iid = str(uuid.UUID(int=rnd.getrandbits(128), version=4))
        ids.append(iid)
        txt = _article(rnd, vocab, cw, i)
        tags = rnd.sample(TOPICS, 3)
        atags = rnd.sample(TOPICS, 3)
        created = EPOCH - dt.timedelta(hours=rnd.randint(1, 24 * 200))
        is_read = rnd.random() < 0.10
        read_at = (created + dt.timedelta(hours=2)).isoformat() if is_read else r"\N"
        title = f"Title {i} about {tags[0]} " + " ".join(
            rnd.choices(vocab, cum_weights=cw, k=4)
        )
        desc = f"Description {i} " + " ".join(rnd.choices(vocab, cum_weights=cw, k=40))
        words = len(txt.split())
        cols = [
            iid,
            str(uid),
            f"http://127.0.0.1:9200/seed/{key}/{i}",
            "web",
            title,
            desc,
            f"http://127.0.0.1:9200/img/{i}.jpg",
            "article",
            "Author %d" % (i % 30),
            r"\N",
            _esc(txt),
            str(words),
            str(max(1, words // 200)),
            embs[i % len(embs)],
            "t" if is_read else "f",
            "f",
            read_at,
            r"\N",
            "completed",
            r"\N",
            created.isoformat(),
            created.isoformat(),
            "0.0",
            _arr(tags),
            "Summary " + " ".join(rnd.choices(vocab, cum_weights=cw, k=40)),
            _arr(atags),
            "general",
            "{}",
            "f",
            r"\N",
            r"\N",
            r"\N",
        ]
        items.write("\t".join(cols) + "\n")
        if rnd.random() < 0.30:
            for c in range(5):
                chunks.write(
                    "\t".join(
                        [
                            str(uuid.UUID(int=rnd.getrandbits(128), version=4)),
                            iid,
                            str(uid),
                            str(c),
                            _esc(" ".join(rnd.choices(vocab, cum_weights=cw, k=300))),
                            embs[(i + c) % len(embs)],
                            created.isoformat(),
                        ]
                    )
                    + "\n"
                )
    items.seek(0)
    cur.copy_expert(
        "copy content_items(id,user_id,original_url,submitted_via,title,description,thumbnail_url,"
        "content_type,author,published_date,full_text,word_count,reading_time_minutes,embedding,"
        "is_read,is_archived,read_at,deleted_at,processing_status,processing_error,created_at,"
        "updated_at,read_position,tags,summary,auto_tags,content_vertical,vertical_metadata,"
        "is_public,search_vector,s3_key,entities_analyzed_at) from stdin",
        items,
    )
    chunks.seek(0)
    cur.copy_expert(
        "copy content_chunks(id,content_item_id,user_id,chunk_index,text,embedding,created_at) from stdin",
        chunks,
    )
    out = {"user_id": str(uid), "items": n}
    if key == "u2000":
        lid = str(uuid.UUID(int=rnd.getrandbits(128), version=4))
        cur.execute(
            "insert into lists(id,name,description,owner_id,is_shared,is_public) values(%s,'All items','memprof',%s,false,false)",
            (lid, str(uid)),
        )
        m = io.StringIO()
        for iid in ids:
            m.write(f"{iid}\t{lid}\t{uid}\n")
        m.seek(0)
        cur.copy_expert(
            "copy content_list_membership(content_item_id,list_id,added_by) from stdin",
            m,
        )
        out["list_id"] = lid
    cur.execute(
        "select count(*), count(embedding), count(search_vector), sum(length(full_text))/1e6, "
        "avg(pg_column_size(search_vector)) from content_items where user_id=%s",
        (str(uid),),
    )
    r = cur.fetchone()
    out.update(
        rows=r[0],
        with_embedding=r[1],
        with_search_vector=r[2],
        full_text_mb=float(r[3]),
        avg_search_vector_bytes=float(r[4] or 0),
    )
    log(f"seeded {key}: {out}")
    return out


def seed_all(dsn, hashed_pw, keys, log=print):
    vocab = _vocab()
    ws = [1.0 / (r + 1) for r in range(len(vocab))]
    cw, acc = [], 0.0
    for w in ws:
        acc += w
        cw.append(acc)
    embs = _embs()
    conn = psycopg2.connect(dsn)
    info = {}
    try:
        cur = conn.cursor()
        for k in USERS:
            if k in keys or k == "uw":
                info[k] = seed_user(cur, k, hashed_pw, vocab, cw, embs, log)
        conn.commit()
    finally:
        conn.close()
    return info
