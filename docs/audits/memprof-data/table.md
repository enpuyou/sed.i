# Memory benchmark: before vs after

- **before**: commit `b5f2e10`, source hash `674c20c90640cadb`, 2026-10-25T06:46:16.941980+00:00, run 45 min
- **after**: commit `working-tree (HEAD b5f2e10 + uncommitted changes)`, source hash `d154f5bc14c58e36`, 2026-10-25T21:13:30.067499+00:00, run 35 min

MB, median over repetitions (range in parentheses). Peak delta = peak RSS above the settled pre-scenario baseline; settled = macOS footprint 30 s after the work.

| Scenario | peak delta before | peak delta after | change | peak RSS before | peak RSS after | settled fp before | settled fp after | wall s before | wall s after |
|---|---|---|---|---|---|---|---|---|---|
| S1_ingest_n100 | 79.2 (77.5-80.5) | 3.1 (3-17.6) | -96% | 258.8 | 182.3 | 210.0 | 148.0 | 0.8 | 0.3 |
| S1_ingest_n500 | 456.8 (445.2-457.2) | 9.0 (9-9) | -98% | 636.5 | 188.3 | 513.0 | 154.0 | 2.8 | 0.3 |
| S1_ingest_n2000 | 1,232.6 (1225.5-1254.4) | 24.0 (24-24.1) | -98% | 1,412.2 | 203.5 | 1,017.0 | 168.0 | 10.9 | 0.6 |
| S2_recommended_n2000 | 558.1 (549.3-559.2) | 13.6 (13.5-13.6) | -98% | 738.5 | 191.9 | 413.0 | 157.0 | 2.1 | 0.1 |
| S3_list_content_n2000 | 641.4 (639.9-742.6) | 33.8 (32.9-34.9) | -95% | 819.3 | 212.2 | 556.0 | 177.0 | 2.2 | 0.2 |
| S3b_list_content_n2000_limit50 | - | 1.1 | skipped before  | | | | | | |
| S4_worker_article_100KB | 6.9 (6.6-7.6) | 6.0 (5.9-7) | -13% | 236.1 | 234.4 | 202.0 | 201.0 | 2.2 | 2.2 |
| S4_worker_article_1MB | 177.0 (149.2-215.7) | 74.0 (73.1-75) | -58% | 353.0 | 301.0 | 243.0 | 240.0 | 7.6 | 6.5 |
| S4_worker_article_5MB | 486.4 (478.3-611.4) | 287.9 (259.8-296.1) | -41% | 650.8 | 516.3 | 322.0 | 289.0 | 29.1 | 28.9 |
| S4_worker_article_20MB | 847.4 (802.1-892.7) | 592.8 (555.8-629.8) | -30% | 990.6 | 821.0 | 673.5 | 480.0 | 116.9 | 68.0 |
| S5_worker_article_50MB | 1,996.6 (1996.6-1996.6) | 625.5 (625.5-625.5) | -69% | 2,184.9 | 853.9 | 1,171.0 | 437.0 | 554.0 | 69.8 |
| S6_worker_nonhtml | 1,247.6 (1247.6-1247.6) | 0.0 (0-0) | -100% | 1,286.7 | 227.8 | 394.0 | 194.0 | 13.1 | 1.2 |
| S7_regression_small | 30.1 (29.5-30.6) | 17.3 (5.2-29.4) | -42% | 258.6 | 245.6 | 220.0 | 223.0 | 15.1 | 15.2 |

## Outcomes (first repetition)

| Scenario | before | after |
|---|---|---|
| S1_ingest_n100 | status [201] | status [201] |
| S1_ingest_n500 | status [201] | status [201] |
| S1_ingest_n2000 | status [201] | status [201] |
| S2_recommended_n2000 | status=200, items=20, bytes=29739 | status=200, items=20, bytes=29755 |
| S3_list_content_n2000 | status=200, items=2000, bytes=2984959 | status=200, items=2000, bytes=2984959 |
| S3b_list_content_n2000_limit50 | skipped: endpoint has no limit parameter at this commit | status=200, items=50, bytes=74552 |
| S4_worker_article_100KB | completed, words=12564, chunks=35, emb=True | completed, words=12564, chunks=35, emb=True |
| S4_worker_article_1MB | completed, words=132820, chunks=379, emb=True | completed, words=132820, chunks=379, emb=True |
| S4_worker_article_5MB | completed, words=667402, chunks=1916, emb=True | completed, words=667402, chunks=1916, emb=True |
| S4_worker_article_20MB | completed, words=2663292, chunks=0, emb=True | completed, words=1397559, chunks=4000, emb=True, err='Partial extraction: the page exceeded th' |
| S5_worker_article_50MB | completed, words=6654829, chunks=0, emb=True | completed, words=1397559, chunks=4000, emb=True, err='Partial extraction: the page exceeded th' |
| S6_worker_nonhtml | completed, words=None, chunks=0, emb=False, err='Could not extract article text' | completed, words=None, chunks=0, emb=False, err='Could not extract article text' |
| S7_regression_small | 20/20 completed, 20 with chunks (728 total), 20 embedded, 0 with error | 20/20 completed, 20 with chunks (728 total), 20 embedded, 0 with error |

## Watchdog kills

none
