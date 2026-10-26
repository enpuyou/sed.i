"""Synthetic article page generator (HTML, deterministic per seed)."""

import random

W = (
    "river stone morning garden quietly walked beside ancient bridge where children played "
    "under tall green trees and old farmers sold fresh bread market weather paper system "
    "memory design public season history"
).split()


def gen(target: int, seed: int = 3) -> bytes:
    rnd = random.Random(seed)
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Synthetic page %d</title>'
        '<meta name="description" content="A synthetic article for memory profiling.">'
        '<meta property="og:title" content="Synthetic %d"><meta property="og:image" content="https://cdn.example.com/og.jpg">'
        '<link rel="stylesheet" href="/static/site.css"><script>window.dataLayer=window.dataLayer||[];%s</script></head><body>'
        % (seed, seed, "var a=1;" * 200)
    )
    nav = (
        '<header class="site"><nav><ul>'
        + "".join(
            f'<li class="nav-item"><a href="/section/{i}" class="nav-link">Section {i}</a></li>'
            for i in range(60)
        )
        + "</ul></nav></header>"
    )
    parts = [
        head,
        nav,
        '<main><div class="wrap"><article class="post"><h1>Synthetic article</h1><p class="byline">By Test Author</p>',
    ]
    size = sum(len(p) for p in parts)
    i = 0
    while size < target:
        i += 1
        if i % 17 == 0:
            s = f'<h2 id="s{i}">Section heading {i}</h2>'
        elif i % 31 == 0:
            s = f'<figure class="fig"><img src="/img/{i}.jpg" alt="img {i}" width="600"><figcaption>Figure {i}</figcaption></figure>'
        elif i % 37 == 0:
            s = f'<figure><img src="https://cdn.example.com/p/{i}.png" alt="abs {i}"></figure>'
        elif i % 23 == 0:
            s = (
                "<script>"
                + "track(%d);" % i * 10
                + '</script><div class="ad"><div><div><span>ad</span></div></div></div>'
            )
        else:
            n = rnd.randint(40, 120)
            s = (
                '<div class="blk"><p>'
                + " ".join(rnd.choice(W) for _ in range(n))
                + f' <a href="https://example.com/p{i}">ref {i}</a> '
                + " ".join(rnd.choice(W) for _ in range(15))
                + ".</p></div>"
            )
        parts.append(s)
        size += len(s)
    parts.append(
        '</article></div></main><aside class="rail">'
        + "".join(
            f'<div class="card"><a href="/r/{i}">Related {i}</a></div>'
            for i in range(40)
        )
        + '</aside><footer><p>Footer text</p><script src="/static/app.js"></script></footer></body></html>'
    )
    return "".join(parts).encode()
