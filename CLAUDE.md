# Local development

- Run the dev server with the `django` entry in `.claude/launch.json`. It starts Django on port 8000 and the Tailwind watcher together, and the preview tool reuses it if it is already running.
- Never start another `runserver` on a different port. If port 8000 is busy, check what holds it (`lsof -nP -iTCP:8000 -sTCP:LISTEN`) and reuse or stop it. Stop any server or watcher you start yourself before you finish.
- The server uses the `ohc_experience_demo` database. After switching branches, run `migrate` against it. If the new branch lacks a migration that was applied, recreate the database (`dropdb`, `createdb`, `migrate`, `seed_experience_demo --reset`) with the environment from `.claude/launch.json`.
- A worktree in another folder needs its own port and database; never point it at `ohc_experience_demo`.
- "Spin up worktree" means: serve this worktree's changes for review on its own port, database and Redis index, without touching anything already running. Take the first free port above 8000, clone the demo data (`createdb -T ohc_experience_demo ohc_experience_wt_<branch>`) rather than reseeding, since `seed_experience_demo` needs an `LGD_API_KEY` that local shells do not have, then `migrate` the clone. Review it at `http://localhost:<port>`, not `127.0.0.1`: a cookie ignores the port, so the same host on two ports would sign you out of the other server. Leave the server running.

# Dropdowns

- Every dropdown is searchable. `ohc_experience/static/js/searchable-select.js` turns each single-choice `select.ui-select` on any page into a type-to-filter combobox, and the select underneath still holds and posts the value. New dropdowns get this by being careui selects: render the field with `{% ui_field %}`, or give a hand-written `<select>` the `ui-select` class inside a `.ui-select-wrapper`. Never build a custom dropdown or add a select library.
- Keep a plain dropdown only when asked, with `data-native-select` (in a form, `forms.Select(attrs={"data-native-select": ""})`).
- A dropdown lists only what can be chosen. Never group its options with `<optgroup>` or grouped Django choices, whose label sits in the list as a heading nobody can pick; `searchable-select.js` drops such a label anyway, but a plain dropdown still shows it. When some options belong together, lead them with one that picks them all, as "All milestones" does in the queue's Type filter.
- A script that changes a select's value or options should dispatch `change` on the select, as `ohc_experience/static/js/pincode-lookup.js` does, so the search box shows the new choice.

# Rows

- A row that opens a page opens it on a click anywhere in the row, not only on its name. `ohc_experience/static/js/row-link.js` does this for every table row or list item holding a link marked `data-row-link`, and the row gets a pointer cursor. Mark the link to the row's page, usually the name, and keep it a real `<a href>`: keyboard and screen reader users reach the page through it, and htmx boosts it. Give the row `group` and the link `group-hover:text-primary` so the name lights up with the row, or `group-hover:underline` when the link is already primary. A table row with nowhere to go, like a team member or a permission, stays as it is.
- A card that is one `<a>` from edge to edge, like the queue's cards on a phone, already opens from anywhere and needs no marker.

# Toasts

- What an action did, or why it was refused, is a Django message (`messages.success`, `messages.error`, …), and every shell shows its messages as toasts at the foot of the screen through `components/toaster.html`. Never put an action's result in an inline alert, and never build another toast or add a toast library.
- A message says what happened and to what, in the words of the button that did it: "M2 - HIP services assigned to you.", "Ticket TKT-2001 created.", "Query resolved." Never a catch-all like "Review updated." or "Saved." that one message covers for several actions.
- Success and info toasts fade after six seconds, held while pointed at or focused. Errors and warnings stay until dismissed.
- An htmx response raises a toast by ending with `{% include "components/messages.html" with oob=True %}`; `components/messages.html` explains the contract.
- An alert about the state of the page, such as "No callback URL saved", "Under review" or a form's error summary, is not a toast. It stays an inline `ui-alert` next to what it describes.
