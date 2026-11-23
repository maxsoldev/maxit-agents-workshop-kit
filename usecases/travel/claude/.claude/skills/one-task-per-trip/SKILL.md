---
name: one-task-per-trip
description: Decide the status of a trip task. Use when an email or message is about a business trip, a forwarded travel approval (DRA) or a cancellation.
---

# One task per trip

1. Read the rules `priorities` and `travel_rules` (you have all rules from step 1 of CLAUDE.md; else call
   `rules_get(usecase="travel", engine="claude", name="priorities,travel_rules")`).
2. Find the trip: who, where, when. If the dates are missing, call `events_list` for that month and use
   the event dates.
3. Pick the status from the status table in `priorities`, with the meaning the owner gives each word.
   Check in this order: is the trip called off? Is a travel number in the text (e.g. "DRA: MPDL/0000002101/...")?
   Or is there no travel request yet ("Reiseantrag kommt noch", "I will submit it")? Is only a rough price asked?
   A trip that is called off: stop there, no options, no messages. No travel number: never book.
4. If someone else may go instead, add a second task of kind `open_question`.
5. Write the reason for the status in `reasons`.

To make your own skill: copy this folder, change `name`, `description` and the steps.
