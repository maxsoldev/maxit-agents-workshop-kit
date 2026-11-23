# What a message is about (kind)

Give every email or message exactly one kind.

- `trip`: A business trip: a plan, a travel approval (DRA), a change, or a cancellation of a trip.
- `leave`: Someone is on leave (an approved absence from SAP).
- `estimate`: Someone asks only for a rough cost estimate for a possible trip. There is no travel request yet.
- `reminder`: Something to remember, nothing to book (e.g. a training or procurement request on paper).

## The other fields

- **person**: the full name of the team member it is about (see the team list).
- **event**: the event and the place (e.g. "E-Science-Tage, Heidelberg"), or in a few words what it is about.
  For a leave: "Leave".
- **date**: the first day, as YYYY-MM-DD: the start of the trip or the leave as the message gives it.
  Only event days in the message: the first day of the event. Empty if no day is known.
