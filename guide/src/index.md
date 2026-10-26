---
layout: layouts/base.njk
title: Home
permalink: /
---
<div class="home">
<h1>How sed.i works</h1>
<p class="dek">A study guide to the whole system: what each part does, why it was built that way, how it fails, and how to explain it.</p>

<p>sed.i is a read-it-later app. You paste a link; the backend fetches and cleans the article; the app gives you a reader, highlights, search across everything you have saved, and a place to write. Under that sit a queue and a worker, a Postgres database with vector search, and several uses of language models.</p>

<p>Each chapter starts with a one-minute summary, follows one real input through the code, then covers the design choices, the failure modes, and what is still weak. It ends with a spoken-style explanation and a few questions with the answers hidden. Examples are captured by running the code; anything illustrative is labelled.</p>

<p>Start with <a href="/orientation/what-is-sedi/">what sed.i is</a> and <a href="/orientation/system-on-one-page/">the system on one page</a>, then read in any order.</p>

<ol class="parts">
{% for group in collections.chapters | groupByPart %}
  <li>
    <span class="n">{{ group.part }}</span>
    <div>
      <h2>{{ group.part | partTitle }}</h2>
      <p>{{ group.part | partBlurb }}</p>
      <p class="links">{% for c in group.items %}<a href="{{ c.url }}">{{ c.data.title }}</a>{% endfor %}</p>
    </div>
  </li>
{% endfor %}
</ol>
</div>
