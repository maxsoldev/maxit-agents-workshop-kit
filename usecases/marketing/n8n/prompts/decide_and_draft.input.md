Case input:
{{ JSON.stringify($('Case in').item.json.body.input) }}

Triage:
{{ JSON.stringify($('Triage fields').item.json.output) }}

Contact sheet rows of the sender (contacts_lookup):
{{ JSON.stringify($('Look up the sender in the contact sheet').item.json.output) }}

Earlier emails of the sender (inbox_history):
{{ JSON.stringify($json.output) }}
