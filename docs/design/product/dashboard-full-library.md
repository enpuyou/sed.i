---
type: design
status: active
last_updated: 2026-10-26
consumer: user
---

# Feature: Full library on the dashboard

## What problem it solves

The dashboard showed only the newest 50 saved items. Older items were missing, and the
status and tag filters only looked at those 50.

## How to find it

Dashboard, and the "Add content" picker on a list page.

## How it works (user perspective)

The dashboard loads your whole library. The first 100 items appear immediately and the
rest are added below as they arrive. The counter, for example "(230 / 230 items)",
reflects the full library. Filters and tag filters apply to every item.

## What it does NOT do

No visible change to layout, no "load more" button.

## How to test it

1. With more than 100 saved items, open the dashboard. All items appear and the counter shows the full total.
2. Choose "read" in the filter. Read items older than the newest 50 are listed.
3. Open the list page, choose add content. Items beyond the newest 50 are selectable.

## Known limits

If a later page fails to load, items already loaded stay visible with an error and a retry.
Loading stops at 10,000 items.
