---
type: design
status: active
last_updated: 2026-10-25
consumer: user
---

# Feature: Lists load as you scroll

Written for someone who wants to understand and confidently explain this feature — not
an API reference or internal design doc.

---

## What problem it solves

Opening a list used to fetch every item in it at once. For a list of a few hundred
articles that meant a slow first load and a lot of memory used on the server for every
visit. The list now loads 50 items first and brings in more as you scroll.

## How to find it

Open any list from the sidebar or the Lists page. It applies to the normal list view and
to the narrow list column that appears next to the editor in writing mode.

## How it works (user perspective)

1. The list opens with the first 50 items. The header shows the real total, for
   example "213 items inside", not 50.
2. Scroll toward the bottom. Just before you reach it, the next 50 load. A small
   "Loading more · 50 of 213" line shows while they arrive.
3. This repeats until everything is loaded. The footer then disappears and the header
   total stays the same.
4. Removing or deleting an item takes it out of the view and lowers the total by one,
   without reloading.
5. If loading a page fails, the items already on screen stay, and an error line with
   Retry appears under them.

## What it does NOT do

- Lists with 50 items or fewer look exactly as before, with no footer.
- It does not change sorting (newest first) or any other list action.
- It does not pre-load pages you have not scrolled toward.

## How to test it

1. Make a list with more than 50 items. Open it: 50 rows, header shows the full count,
   footer reads "50 of N".
2. Scroll down: more rows appear and the footer count rises by 50 each time. At the end
   the footer is gone.
3. Delete one item: the header count drops by one and nothing reloads.
4. Turn off the network, scroll to the bottom: an error with Retry appears and the rows
   stay. Turn it back on and press Retry.
5. Open writing mode on the same list: scrolling the narrow list column loads more too.

## Known limits

- The "+ Add" dialog only knows which items are already in the list for the rows loaded
  so far. Adding an item that is already in the list is harmless (the server ignores it),
  but items further down may not show as already added.
- Adding items from the dialog reloads the list from the first page.
- Browsers without IntersectionObserver show a Load more button instead of loading on scroll.

## Tips

- The count in the header is the list's total, so it may be larger than the number of
  rows currently on screen.
