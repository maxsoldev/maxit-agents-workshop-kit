---
name: one-task-per-trip
description: Decide the status of a trip task. Use when an email or message is about a business trip, a forwarded travel approval (DRA) or a cancellation.
---

# One task per trip

1. Call `rules_get(usecase="travel", name="priorities")` and `rules_get(usecase="travel", name="travel_rules")`.
2. Find the trip: who, where, when. If the dates are missing, call `events_list` for that month and use
   the event dates.
3. Pick the status:
   - The trip is cancelled: `cancelled`. Stop there: no options, no messages.
   - A travel number is in the text (e.g. "DRA: MPDL/0000002101/..."): `ready_to_book`.
   - No travel number yet ("Reiseantrag kommt noch", "I will submit it"): `waiting_for_request`. Do not book.
   - Only a rough price is asked and there is no travel request yet: kind `estimate`, status `open`.
4. If someone else may go instead, add a second task of kind `open_question`.
5. Write the reason for the status in `reasons`.

To make your own skill: copy this folder, change `name`, `description` and the steps.
