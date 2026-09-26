"""
Pilot dataset: 10 synthetic user activity snapshots for memory consolidation eval.

Each case has:
  - activity_str: exactly what gets passed to the consolidation prompt
  - ideal: what a 5-score profile should cover on each dimension
  - floor: what a 1-score profile looks like (used for rubric calibration)
  - tags: which patterns this case exercises

Timestamps are generated relative to now() at import time so they stay
accurate whenever the eval runs. Each case defines its own fabricated
reading timeline (e.g. "burst in one day", "spread over two weeks").

Labels were written by the eval author BEFORE running any variant.
Review and approve labels before treating scores as ground truth.

REVIEW STATUS: PENDING — labels not yet approved by user.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _ago(days: float = 0, hours: float = 0) -> str:
    """Return a relative timestamp string like '3d ago' or '2h ago'."""
    total_hours = days * 24 + hours
    if total_hours < 1:
        return f"{int(total_hours * 60)}m ago"
    if total_hours < 24:
        return f"{int(total_hours)}h ago"
    return f"{int(total_hours / 24)}d ago"


def _build_cases() -> list[dict]:
    return [
        # -------------------------------------------------------------------------
        # Case 1: Heavy saver, never reads
        # All 10 articles saved in a single burst 3 days ago — no reads at all.
        # Tests: behavioral_pattern (save burst), trajectory
        # -------------------------------------------------------------------------
        {
            "key": "heavy_saver_no_reads",
            "tags": ["behavioral_pattern", "backlog"],
            "activity_str": f"""\
Saved but never opened (10 articles — backlog signal):
  [machine learning, neural networks, transformers] The Illustrated Transformer  (3200w, saved {_ago(days=3, hours=2)})
  [AI safety, alignment, RLHF] RLHF: From Human Feedback to Superhuman Models  (4100w, saved {_ago(days=3, hours=2)})
  [AI safety, scalable oversight, debate] Debate as an Alignment Strategy  (2800w, saved {_ago(days=3, hours=1)})
  [machine learning, interpretability, mechanistic] Circuits: A New Approach to Neural Net Interpretability  (5500w, saved {_ago(days=3, hours=1)})
  [AI safety, alignment, corrigibility] The Alignment Problem in Practice  (3900w, saved {_ago(days=3)})
  [AI safety, mesa-optimization, inner alignment] Risks from Learned Optimization  (6200w, saved {_ago(days=3)})
  [machine learning, RLHF, reward modeling] Reward Model Ensembles for RLHF  (2100w, saved {_ago(days=3)})
  [AI safety, evals] ARC Evals: What We Test and Why  (1800w, saved {_ago(days=2, hours=23)})
  [alignment research, AI safety] MIRI's Research Agenda  (4400w, saved {_ago(days=2, hours=23)})
  [AI safety, coordination, governance] International AI Governance Frameworks  (3300w, saved {_ago(days=2, hours=22)})""",
            "ideal": {
                "specificity": "current_focus names AI safety or alignment research — not just 'AI'. "
                               "Bonus if it notes the specific angle: technical alignment, not policy.",
                "trajectory": "memory_text notes the user is building a reading foundation on AI "
                              "alignment/safety — but has not yet engaged with any of it. Flags this "
                              "as unresolved interest or deliberate queue-building, not active study.",
                "depth_asymmetry": "No reads at all — profile should note that engagement depth is "
                                    "unknown. reading_velocity should be 'browsing'. Should NOT claim "
                                    "depth on any topic.",
                "behavioral_pattern": "All 10 saves happened in a 4-hour burst 3 days ago — profile "
                                       "should name this: 'saved an entire curriculum on AI safety in "
                                       "a single session without opening any of it — burst-saving "
                                       "behavior suggesting deliberate queue-building or a moment of "
                                       "strong intent that has not yet converted to reading.'",
                "faithfulness": "Must not claim any articles were read or highlighted — none were.",
            },
            "floor": {
                "description": "Lists AI safety and ML as topics. States reading_velocity='browsing'. "
                               "No mention of the save burst or the save-but-never-read pattern.",
            },
        },

        # -------------------------------------------------------------------------
        # Case 2: Deep reader, narrow topic, concentrated session
        # 5 articles read in 2 days, in clear pedagogical order (intro → advanced).
        # Tests: specificity, depth_asymmetry, sequencing signal
        # -------------------------------------------------------------------------
        {
            "key": "deep_reader_narrow",
            "tags": ["depth_asymmetry", "specificity", "highlights", "sequencing"],
            "activity_str": f"""\
Read deeply (with highlights, in reading order):
  [context engineering, LLM agents, prompts] Why Context Engineering? — Nextra  (946w, read 100%, {_ago(days=2, hours=4)})
    > "context window as working memory — what you put in determines what the model can do"
    > "attention budget: every token competes; irrelevant context degrades signal"
  [context engineering, agent design, tool use] Effective Context Engineering for AI Agents  (3098w, read 95%, {_ago(days=2, hours=3)})
    > "the agent's job is to maintain a compressed but sufficient world-model in context"
    > "tool schemas consume budget; trim aggressively for long-horizon tasks"
    > "separating observation space from action space prevents context bleed"
  [agent systems, harness design, evaluation] Harness Design for Long-Running Application Development  (4435w, read 88%, {_ago(days=1, hours=6)})
    > "harness = the scaffolding that keeps an agent honest across multi-turn tasks"
    > "sprint contracts: agree on what 'done' means before the agent starts"
    > "observable runtime: every tool call logged with inputs, outputs, latency"
  [AI engineering, Claude SDK, agent orchestration] Building Agents with the Claude Agent SDK  (2063w, read 100%, {_ago(days=1, hours=4)})
    > "subagent isolation: each spawned agent starts cold — no memory bleed from parent"
    > "use foreground for dependent work, background for independent parallel tasks"
  [LLM evals, benchmarks, agent eval] Lecture 11. Make the Agent's Runtime Observable  (1127w, read 90%, {_ago(hours=18)})
    > "observability is not logging — it is the ability to answer 'why did the agent do that'"

Reading lists (1):
  - "Agent Engineering Reference"

Topic clusters active this window:
  - context engineering (4 articles)
  - agent systems (5 articles)""",
            "ideal": {
                "specificity": "current_focus is 'context engineering and agent systems for production "
                               "LLM applications' or equivalent. Not 'AI' or 'machine learning'.",
                "trajectory": "User read intro → intermediate → advanced articles in a single 2-day "
                              "session, in clear pedagogical sequence. The progression from 'Why "
                              "Context Engineering' to harness design to observability suggests "
                              "deliberate self-study, not casual browsing. The reading list named "
                              "'Agent Engineering Reference' reinforces systematic intent.",
                "depth_asymmetry": "All reads are deep (88–100%) with dense highlights. Uniformly "
                                    "deep engagement on a single technical thread — no asymmetry to "
                                    "name, but should note the consistency.",
                "behavioral_pattern": "5 articles, all read near-completely, 14 highlights across "
                                       "them, completed in 2 days in a sequential order that mirrors "
                                       "a curriculum. Annotation pattern suggests active synthesis.",
                "faithfulness": "Highlight quotes must match activity string exactly. Read% values "
                                 "must be used accurately (88–100%).",
            },
            "floor": {
                "description": "current_focus='AI engineering'. reading_velocity='deep'. memory_text "
                               "says 'user is interested in context engineering and agents'. No "
                               "sequencing observation, no trajectory.",
            },
        },

        # -------------------------------------------------------------------------
        # Case 3: Topic shift mid-window
        # Economics saves early in the window → AI engineering deep reads later.
        # Tests: trajectory (detecting a pivot), depth_asymmetry, sequencing
        # -------------------------------------------------------------------------
        {
            "key": "topic_shift",
            "tags": ["trajectory", "specificity", "shift", "sequencing"],
            "activity_str": f"""\
Read without highlights (in reading order):
  [economics, AI and jobs] What 81,000 People Told Us About the Economics of AI  (2375w, read 45%, {_ago(days=12)})
  [economics, inflation] US Inflation Rose to 3.8% in April  (1022w, read 14%, {_ago(days=11)})

Read deeply (with highlights, in reading order):
  [AI engineering, context engineering] Effective Context Engineering for AI Agents  (3098w, read 80%, {_ago(days=3)})
    > "attention budget: every token competes"
    > "separate observation space from action space"
  [AI engineering, agent systems] Building Agents with the Claude Agent SDK  (2063w, read 100%, {_ago(days=2)})
    > "subagent isolation: each spawned agent starts cold"
  [MLOps, model deployment] 25 Top MLOps Tools You Need to Know in 2026  (3897w, read 80%, {_ago(days=1)})
    > "experiment tracking is not optional at scale"

Saved but never opened (5 articles — backlog signal):
  [labor economics] Focus Areas for The Anthropic Institute  (2633w, saved {_ago(days=13)})
  [political economics] A Bone-Headed Move: Trump's Battle with Powell  (1026w, saved {_ago(days=13)})
  [economics, fertility] Why Fertility Rates Are Declining  (625w, saved {_ago(days=12)})
  [AI engineering, skills] SkillOpt: Executive Strategy for Self-Evolving Agent Skills  (13637w, saved {_ago(days=4)})
  [AI engineering, observability] Harness Design for Long-Running Application Development  (4435w, saved {_ago(days=3)})""",
            "ideal": {
                "specificity": "current_focus should reflect where engagement actually went: AI "
                               "engineering / agent systems. Economics was saved and shallowly read "
                               "early in the window; the user pivoted to AI engineering 3 days ago "
                               "and has been deep there since.",
                "trajectory": "Clear temporal shift: economics articles saved and skimmed 11–13 "
                              "days ago, then a gap, then AI engineering articles read deeply "
                              "starting 3 days ago. The shift is visible in both the timestamps "
                              "and the read depth. Profile should name the pivot explicitly.",
                "depth_asymmetry": "Deep on AI engineering (80–100%, highlights, recent). Shallow "
                                    "on economics (14–45%, no highlights, 11+ days ago). "
                                    "Both the quality and recency asymmetry matter.",
                "behavioral_pattern": "Economics phase: save + skim, no highlights. AI engineering "
                                       "phase: deep reads with highlights. Two distinct modes, "
                                       "separated by a week-long gap. The gap itself is a signal — "
                                       "something changed (possibly started a new project).",
                "faithfulness": "Must not claim equal depth across both domains. Economics read% "
                                 "values (14%, 45%) and timestamps (11–13d ago) must be accurate.",
            },
            "floor": {
                "description": "current_focus='AI and economics'. reading_velocity='deep'. memory_text "
                               "says 'user is interested in AI and economic impacts'. No topic shift "
                               "named, no asymmetry, no temporal structure.",
            },
        },

        # -------------------------------------------------------------------------
        # Case 4: Job search — reading list intent + concentrated prep
        # All career prep reads within 48 hours. List names are explicit signals.
        # Tests: trajectory (explicit intent), behavioral_pattern
        # -------------------------------------------------------------------------
        {
            "key": "intent_reading_list",
            "tags": ["trajectory", "behavioral_pattern", "lists"],
            "activity_str": f"""\
Read deeply (with highlights, in reading order):
  [career development, interview prep, STAR method] How to Prepare for an Interview | Grow with Google  (1528w, read 100%, {_ago(days=2, hours=3)})
    > "STAR method: Situation, Task, Action, Result — answer every behavioral question this way"
    > "research the company's recent news and product launches before the interview"
    > "prepare 3 stories that demonstrate impact, leadership, and dealing with failure"
  [AI engineering, forward-deployed, roles] What Does a Forward Deployed Engineer Actually Do?  (1900w, read 90%, {_ago(days=2, hours=1)})
    > "FDEs sit at the customer boundary — half engineer, half solutions architect"
    > "you own the integration end-to-end: scoping, building, debugging in prod"
  [career development, resume, tech roles] Writing a Resume That Gets Past ATS  (1100w, read 85%, {_ago(days=1, hours=18)})
    > "keyword density matters: mirror the job description language exactly"

Read without highlights (in reading order):
  [AI companies, hiring, culture] What Anthropic Looks for in Engineers  (800w, read 60%, {_ago(days=1, hours=12)})
  [career development, salary negotiation] How to Negotiate Your First Tech Offer  (1400w, read 55%, {_ago(days=1, hours=6)})

Saved but never opened (3 articles — backlog signal):
  [AI engineering, agent systems] Building Production Agent Systems  (2800w, saved {_ago(days=3)})
  [career development, portfolio] Building a Portfolio That Gets Noticed  (950w, saved {_ago(days=2, hours=6)})
  [networking, jobs, referrals] How to Get Referrals at Tech Companies  (1200w, saved {_ago(days=2, hours=5)})

Reading lists (2):
  - "AI Engineer & Forward Deployed Engineer"
  - "Job Prep 2026"

Topic clusters active this window:
  - career development (5 articles)
  - AI engineering roles (3 articles)""",
            "ideal": {
                "specificity": "current_focus is 'AI engineering / forward-deployed engineering "
                               "career preparation' — combines the role domain with the explicit "
                               "job-search intent visible in the list names and read sequence.",
                "trajectory": "Two reading lists ('AI Engineer & Forward Deployed Engineer', "
                              "'Job Prep 2026') plus a 48-hour concentrated reading session "
                              "covering interview prep, FDE role description, resume, and salary "
                              "negotiation in that order — the sequence is a job-search checklist. "
                              "Profile should state: actively preparing to apply for AI engineering "
                              "or FDE roles imminently.",
                "depth_asymmetry": "Actionable prep content read deeply (85–100%): interview "
                                    "technique, role description, ATS tips. Contextual content "
                                    "read more shallowly (55–60%): culture fit, salary ranges. "
                                    "Suggests prioritizing execution over background research.",
                "behavioral_pattern": "All reads concentrated in a 48-hour window with a "
                                       "logical progression (role research → interview prep → "
                                       "resume → negotiation). This is a goal-directed sprint, "
                                       "not ambient reading.",
                "faithfulness": "List names must be quoted accurately. STAR method and FDE "
                                 "definition claims must trace to actual highlight text shown.",
            },
            "floor": {
                "description": "current_focus='career development'. memory_text says 'user is "
                               "reading about career preparation and AI engineering'. No job "
                               "search timeline, no sprint pattern, no list intent signal.",
            },
        },

        # -------------------------------------------------------------------------
        # Case 5: Technical deep + news skimmer
        # Distributed systems read across 5 days; news skimmed same days in passing.
        # Tests: depth_asymmetry (canonical case)
        # -------------------------------------------------------------------------
        {
            "key": "technical_deep_news_skim",
            "tags": ["depth_asymmetry", "behavioral_pattern"],
            "activity_str": f"""\
Read deeply (with highlights, in reading order):
  [distributed systems, consensus, Raft] Understanding the Raft Consensus Algorithm  (4200w, read 95%, {_ago(days=5)})
    > "leader election requires a majority quorum — split-brain is prevented by quorum, not locks"
    > "log replication: leader sends entries, followers append, commit only after majority ACK"
    > "Raft separates leader election from log replication — easier to reason about than Paxos"
  [system design, message queues, Kafka] Design a Distributed Message Queue  (3100w, read 88%, {_ago(days=4)})
    > "partition key determines which consumer sees which messages — choose carefully"
    > "at-least-once delivery is the safe default; idempotent consumers handle duplicates"
  [database internals, B-trees, LSM] How RocksDB Works  (5800w, read 80%, {_ago(days=2)})
    > "LSM trades read amplification for write amplification — choose based on workload"
    > "compaction is the cost you pay for fast writes; tune it or it will tune you"

Read without highlights (in reading order):
  [politics, US economy, tariffs] Trump's Tariff Policy: What Happens Next  (900w, read 22%, {_ago(days=5)})
  [news, Federal Reserve, interest rates] Fed Holds Rates Steady Amid Uncertainty  (700w, read 18%, {_ago(days=4)})
  [news, AI industry, funding] Anthropic Raises $2B Series E  (500w, read 35%, {_ago(days=3)})
  [news, tech industry, layoffs] Meta Cuts 3,000 Roles in Engineering  (600w, read 28%, {_ago(days=1)})

Saved but never opened (2 articles — backlog signal):
  [distributed systems, CRDTs, eventual consistency] CRDTs Explained  (3400w, saved {_ago(days=3)})
  [database internals, PostgreSQL, MVCC] How Postgres Handles Concurrency  (4100w, saved {_ago(days=1)})""",
            "ideal": {
                "specificity": "current_focus is 'distributed systems and database internals — "
                               "specifically consensus algorithms, message queues, and storage "
                               "engines' or equivalent precision.",
                "trajectory": "One deep technical article per day for 5 days (Raft → Kafka → "
                              "RocksDB), with CRDTs and MVCC saved but not yet read — a clear "
                              "systematic study progression through distributed systems fundamentals. "
                              "News is consumed the same days but at a fraction of the depth.",
                "depth_asymmetry": "This is the canonical asymmetry case. Deep on distributed "
                                    "systems/DB (80–95%, dense technical highlights on algorithms "
                                    "and tradeoffs). Skims news the same days (18–35%, zero "
                                    "highlights). Two qualitatively different reading modes running "
                                    "in parallel.",
                "behavioral_pattern": "Highlights on technical content are about mechanisms and "
                                       "tradeoffs, not summaries. News reads appear the same days "
                                       "as deep reads — likely ambient scanning between study "
                                       "sessions, not a separate interest.",
                "faithfulness": "News articles at 18–35% must not be described as 'moderately "
                                 "engaged'. Technical highlights must be referenced accurately.",
            },
            "floor": {
                "description": "reading_velocity='deep'. memory_text says 'user is interested in "
                               "distributed systems and keeps up with tech news'. No asymmetry "
                               "between technical depth and news skimming named.",
            },
        },

        # -------------------------------------------------------------------------
        # Case 6: Philosophy curriculum — coherent saves, zero opens
        # All 10 saves within 90 minutes one evening, 5 days ago.
        # Tests: behavioral_pattern (single-session burst), trajectory
        # -------------------------------------------------------------------------
        {
            "key": "backlog_accumulator",
            "tags": ["behavioral_pattern", "backlog", "trajectory"],
            "activity_str": f"""\
Saved but never opened (10 articles — backlog signal):
  [philosophy, ethics, moral philosophy] What We Owe to Each Other — Scanlon  (6800w, saved {_ago(days=5, hours=1.5)})
  [philosophy, consciousness, qualia] The Hard Problem of Consciousness  (4200w, saved {_ago(days=5, hours=1.4)})
  [philosophy, free will, determinism] Free Will: A Very Short Introduction  (3100w, saved {_ago(days=5, hours=1.3)})
  [philosophy, ethics, effective altruism] Doing Good Better — MacAskill Summary  (2900w, saved {_ago(days=5, hours=1.2)})
  [philosophy, AI ethics, moral patients] Could AI Systems Be Moral Patients?  (3800w, saved {_ago(days=5, hours=1.1)})
  [philosophy, epistemology, knowledge] What Is Knowledge? — Stanford Encyclopedia  (5100w, saved {_ago(days=5, hours=1.0)})
  [philosophy, personal identity, continuity] Personal Identity and What Matters  (4400w, saved {_ago(days=5, hours=0.9)})
  [philosophy, utilitarianism, Singer] Peter Singer's Expanding Circle  (2200w, saved {_ago(days=5, hours=0.8)})
  [philosophy, metaethics, moral realism] Moral Realism Without Foundations  (3700w, saved {_ago(days=5, hours=0.7)})
  [philosophy, logic, reasoning] An Introduction to Formal Logic  (4900w, saved {_ago(days=5, hours=0.6)})""",
            "ideal": {
                "specificity": "current_focus is 'philosophy — ethics, consciousness, and "
                               "metaethics'. Not just 'philosophy' (too broad).",
                "trajectory": "10 thematically coherent philosophy articles saved in a single "
                              "90-minute session 5 days ago — none opened since. This is a "
                              "deliberate curriculum built in one burst. The 5-day gap without "
                              "any reads suggests either waiting for a future study block or "
                              "the intent hasn't yet converted to action.",
                "depth_asymmetry": "No reads. reading_velocity must be 'browsing'. Profile "
                                    "should explicitly note that depth is unknown.",
                "behavioral_pattern": "Single-session burst (90 minutes) of coherent saves "
                                       "with zero follow-through in 5 days. The thematic "
                                       "coherence (ethics, consciousness, free will, formal "
                                       "logic — a philosophy 101 syllabus) suggests intentional "
                                       "curation, not random saving.",
                "faithfulness": "Must not claim any articles were read. Must not infer "
                                 "engagement beyond what the saves show.",
            },
            "floor": {
                "description": "current_focus='philosophy'. reading_velocity='browsing'. "
                               "memory_text says 'user is interested in philosophy and ethics'. "
                               "No burst pattern, no 5-day gap, no curriculum signal.",
            },
        },

        # -------------------------------------------------------------------------
        # Case 7: Re-reader — returning to transformer papers with new lens
        # Foundation papers re-read over 3 days; orphan highlights on older articles.
        # Tests: behavioral_pattern (re-engagement), sequencing
        # -------------------------------------------------------------------------
        {
            "key": "re_reader",
            "tags": ["behavioral_pattern", "faithfulness", "sequencing"],
            "activity_str": f"""\
Read deeply (with highlights, in reading order):
  [machine learning, transformers, attention] Attention Is All You Need — Annotated  (8200w, read 70%, {_ago(days=3)})
    > "multi-head attention lets the model attend to different representation subspaces simultaneously"
    > "positional encoding: without it, the model has no notion of sequence order"
    > "the encoder builds context; the decoder generates autoregressively using that context"
  [machine learning, BERT, pretraining] BERT: Pre-training of Deep Bidirectional Transformers  (5100w, read 55%, {_ago(days=2)})
    > "masked language modeling forces the model to understand context from both directions"
    > "next sentence prediction was later shown to be less useful than MLM alone"
  [machine learning, GPT, autoregressive] Language Models are Few-Shot Learners  (6300w, read 40%, {_ago(days=1)})
    > "in-context learning: the model 'learns' from examples in the prompt without weight updates"

Highlights on older articles (6 highlights):
  > "the residual stream is the core data structure — everything reads from and writes to it"
  > "attention heads specialize: some track syntax, some track semantics, some track position"
  > "superposition hypothesis: more features than dimensions, represented in interference patterns"
  > "Q, K, V are linear projections — the dot-product is not magical, it is just similarity"
  > "layer normalization stabilizes training but changes what the residual stream represents"
  > "induction heads: the mechanism behind in-context learning in transformers"

No new saves this window.
No reading lists created this window.""",
            "ideal": {
                "specificity": "current_focus is 'transformer architecture and mechanistic "
                               "interpretability' — the orphan highlights on residual streams, "
                               "induction heads, and superposition point to interpretability "
                               "specifically, not just transformers in general.",
                "trajectory": "Re-reading foundational transformer papers (day 1: Attention, "
                              "day 2: BERT, day 3: GPT-3) in historical order while simultaneously "
                              "annotating older articles about mechanistic interpretability concepts "
                              "(residual stream, induction heads, superposition). The parallel "
                              "activity suggests the user encountered mechanistic interpretability "
                              "and is now re-reading the originals through that lens.",
                "depth_asymmetry": "Three foundational papers at 40–70% read with highlights — "
                                    "not read completely, suggesting re-reading for specific "
                                    "concepts rather than cover-to-cover. No shallow reads to "
                                    "contrast. No new saves — entirely revisiting existing material.",
                "behavioral_pattern": "No new saves + re-reading canon papers in sequence + "
                                       "annotating older articles = active synthesis mode. "
                                       "Contrast with exploration (new saves) or skimming (low "
                                       "read%, no highlights). This user is consolidating, not "
                                       "collecting.",
                "faithfulness": "Must not claim which specific older articles the orphan "
                                 "highlights came from — that information is not in the activity "
                                 "string. Must not overstate read% (40–70% is partial, not 'thorough').",
            },
            "floor": {
                "description": "current_focus='machine learning'. reading_velocity='deep'. "
                               "memory_text says 'user is reading about transformers and BERT'. "
                               "No re-engagement pattern, no interpretability trajectory, no "
                               "sequencing observation.",
            },
        },

        # -------------------------------------------------------------------------
        # Case 8: Eclectic browser — many domains, all shallow, no pattern
        # Reads spread across 7 days with no thematic thread.
        # Tests: specificity (hard case — genuinely unclear)
        # -------------------------------------------------------------------------
        {
            "key": "eclectic_browser",
            "tags": ["behavioral_pattern", "specificity", "hard_case"],
            "activity_str": f"""\
Read without highlights (in reading order):
  [music, culture, identity] Bad Bunny's All-American Super Bowl Halftime Show  (1364w, read 40%, {_ago(days=7)})
  [software, simplicity, UX] TextEdit and the Relief of Simple Software  (1096w, read 30%, {_ago(days=6)})
  [politics, media, ideology] The Californian Ideology  (8465w, read 20%, {_ago(days=5)})
  [economics, labor, precarity] Notes on AI, Labor, and China  (4264w, read 25%, {_ago(days=4)})
  [personal development, productivity] How To Be Organised in 2025  (2986w, read 11%, {_ago(days=3)})
  [cybersecurity, data breach] Cyber Extortion Group Targets Salesforce Customers  (446w, read 100%, {_ago(days=2)})
  [food culture, identity] Is There Such a Thing as Too Much Good Taste?  (1940w, read 43%, {_ago(days=2)})
  [sports, media, branding] How Ted Turner Transformed the Braves  (517w, read 35%, {_ago(days=1)})
  [AI, slot machines, LLMs] Pluralistic: LLMs are Slot-Machines  (1620w, read 35%, {_ago(hours=18)})
  [philosophy, taste, morality] Against Optimization  (1200w, read 28%, {_ago(hours=6)})

Saved but never opened (0 articles).
No reading lists.
No highlights.""",
            "ideal": {
                "specificity": "No coherent theme across 7 days — profile should acknowledge "
                               "this honestly rather than forcing a focus. 'No dominant focus "
                               "area this window — reading across culture, politics, tech "
                               "criticism, and AI' is correct. Forcing 'cultural and tech "
                               "criticism' as a focus is overreach.",
                "trajectory": "One article per day across unrelated domains — no trajectory "
                              "is discernible. Profile should not invent one. This looks like "
                              "ambient browsing or daily news-following, not directed study.",
                "depth_asymmetry": "All reads are shallow (11–43%). The cybersecurity article "
                                    "at 100% is only 446 words — short enough that completion "
                                    "is not a depth signal. No highlights anywhere. reading_velocity "
                                    "must be 'browsing' or 'fast'.",
                "behavioral_pattern": "One article per day, unrelated domains, shallow reads, "
                                       "zero highlights, zero saves — ambient information "
                                       "consumption. Profile should name this mode explicitly "
                                       "rather than mapping it to fake interests.",
                "faithfulness": "Must not invent a coherent interest. The cybersecurity article "
                                 "at 100% must be noted as short (446w), not as a deep engagement "
                                 "signal.",
            },
            "floor": {
                "description": "current_focus='culture and technology'. reading_velocity='fast'. "
                               "memory_text says 'user has broad interests across technology, "
                               "culture, and politics'. Presents eclecticism as a coherent profile.",
            },
        },

        # -------------------------------------------------------------------------
        # Case 9: Synthesizer — annotating while skimming, building across 3 lists
        # Low read% but dense highlights; older-article highlights span weeks back.
        # Tests: behavioral_pattern (annotation-while-skimming), trajectory
        # -------------------------------------------------------------------------
        {
            "key": "synthesizer",
            "tags": ["behavioral_pattern", "trajectory", "highlights"],
            "activity_str": f"""\
Read deeply (with highlights, in reading order):
  [AI policy, governance, regulation] Focus Areas for The Anthropic Institute  (2633w, read 45%, {_ago(days=5)})
    > "economic diffusion of AI benefits is not automatic — requires active policy"
    > "resilience of AI systems under adversarial conditions is understudied"
  [AI safety, automated alignment] Automated Alignment Researchers  (2033w, read 38%, {_ago(days=4)})
    > "scalable oversight: can we supervise models smarter than us?"
    > "weak-to-strong supervision: use weak model to label, strong model to generalize"
  [AI economics, labor, displacement] What 81,000 People Told Us About the Economics of AI  (2375w, read 30%, {_ago(days=3)})
    > "junior positions see the highest displacement risk — not senior roles"
    > "career-stage concerns dominate geographic patterns in AI adoption anxiety"

Read without highlights (in reading order):
  [AI safety, alignment] Natural Language Autoencoders  (1797w, read 25%, {_ago(days=2)})
  [AI governance, policy] Trustworthy Agents in Practice  (1991w, read 20%, {_ago(days=1)})

Highlights on older articles (12 highlights, spanning past 3 weeks):
  > "the agent's job is not to complete tasks but to maintain user trust while completing tasks"
  > "alignment is not a technical problem with a technical solution — it requires ongoing governance"
  > "the coordination problem in AI development is structurally similar to nuclear non-proliferation"
  > "economic incentives and safety incentives are not inherently opposed — reframe the tradeoff"
  > "capability evaluation must precede deployment — not the other way around"
  > "interpretability tools are prerequisites for auditable AI, not nice-to-haves"

Reading lists (3):
  - "AI Policy & Governance"
  - "Alignment Research"
  - "Economic Impacts of AI"

No new saves this window.""",
            "ideal": {
                "specificity": "current_focus is 'AI governance, safety policy, and the "
                               "economics of AI deployment' — distinct from technical AI safety "
                               "research. The reading lists and highlights point to policy-oriented "
                               "synthesis across these three domains.",
                "trajectory": "Three organized reading lists + 12 older-article highlights "
                              "spanning 3 weeks + new reads that extract policy-relevant quotes "
                              "= building a synthesized cross-domain view, likely for writing, "
                              "advocacy, or research output. The 3-week annotation span suggests "
                              "a project that predates this window.",
                "depth_asymmetry": "Low read% (20–45%) but high highlight density on what was "
                                    "read. This is annotation-while-skimming — extracting key "
                                    "claims without finishing articles. Different from passive "
                                    "skimming (no highlights) and deep reading (high read%).",
                "behavioral_pattern": "Low read% + high highlight density + 3 organized lists "
                                       "+ 12 orphan highlights spanning weeks = active "
                                       "synthesis project. Not browsing, not studying — curating "
                                       "a knowledge base across domains for a specific output.",
                "faithfulness": "Must use accurate read% values (20–45%). Highlight quotes "
                                 "must match activity string exactly.",
            },
            "floor": {
                "description": "current_focus='AI safety'. reading_velocity='browsing'. "
                               "memory_text says 'user is interested in AI policy and safety "
                               "research'. No annotation pattern named, no synthesis trajectory.",
            },
        },

        # -------------------------------------------------------------------------
        # Case 10: New user, sparse signal
        # 2 articles, both read in the same hour yesterday.
        # Tests: faithfulness (resist hallucination under thin data)
        # -------------------------------------------------------------------------
        {
            "key": "sparse_new_user",
            "tags": ["faithfulness", "behavioral_pattern", "sparse"],
            "activity_str": f"""\
Read deeply (with highlights, in reading order):
  [machine learning, CNNs, computer vision] Convolutional Neural Network | TensorFlow Core  (2005w, read 91%, {_ago(hours=22)})
    > "filters learn to detect edges, then shapes, then higher-level features"
    > "pooling reduces spatial dimensions while preserving dominant features"

Read without highlights (in reading order):
  [machine learning, Python, libraries] Getting Started with PyTorch  (1200w, read 60%, {_ago(hours=21)})

No new saves beyond the read articles.
No reading lists.
No highlights on older articles.""",
            "ideal": {
                "specificity": "current_focus is 'machine learning fundamentals — CNNs and "
                               "computer vision'. Should NOT overspecify beyond what 2 articles "
                               "in one hour support.",
                "trajectory": "Two ML articles read back-to-back in one hour yesterday — looks "
                              "like early-stage ML learning. Profile should express appropriate "
                              "uncertainty: 'appears to be in early stages of learning ML/deep "
                              "learning' not 'preparing to build production CV systems'. One "
                              "session is not a trajectory.",
                "depth_asymmetry": "One deep read (91%), one moderate (60%), both in the same "
                                    "hour. Too little data to establish a pattern. Profile should "
                                    "not invent one.",
                "behavioral_pattern": "Minimal signal: 2 articles, 2 highlights, 1 hour. Profile "
                                       "should acknowledge the thinness: 'insufficient activity "
                                       "to characterize reading patterns reliably — profile will "
                                       "become more accurate with more sessions.'",
                "faithfulness": "Guard rail case. Profile must not:\n"
                                 "- Claim more reads than shown (only 2 articles, 1 hour)\n"
                                 "- Invent a career goal from 2 ML articles\n"
                                 "- Describe a reading pattern from one session\n"
                                 "- Claim highlights beyond the 2 shown\n"
                                 "A hallucinating profile on sparse data is the worst failure mode.",
            },
            "floor": {
                "description": "current_focus='machine learning'. reading_velocity='deep'. "
                               "memory_text says 'user is learning ML and CV, showing strong "
                               "engagement and is likely building toward a CV project'. "
                               "Overclaims from 2 articles read in one hour.",
            },
        },
    ]


CASES = _build_cases()

# Convenience lookup
CASES_BY_KEY = {c["key"]: c for c in CASES}
