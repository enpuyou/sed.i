---
type: design
status: active
last_updated: 2026-10-26
consumer: user
---

# Feature: Public profile shows only what it needs

## What changed

A public profile page (`/<username>`) now receives only the owner's username and display
name. Email, internal id, verification status and signup date are no longer sent to
visitors. Public articles and records also no longer include the owner's internal id or
processing errors.

A private profile and a profile that does not exist now look the same to visitors: both
show "Profile not found." This prevents anyone from checking which usernames exist.

## How to find it

Open `/<username>` while signed out.
