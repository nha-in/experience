# Local development

- Run the dev server with the `django` entry in `.claude/launch.json`. It starts Django on port 8000 and the Tailwind watcher together, and the preview tool reuses it if it is already running.
- Never start another `runserver` on a different port. If port 8000 is busy, check what holds it (`lsof -nP -iTCP:8000 -sTCP:LISTEN`) and reuse or stop it. Stop any server or watcher you start yourself before you finish.
- The server uses the `ohc_experience_demo` database. After switching branches, run `migrate` against it. If the new branch lacks a migration that was applied, recreate the database (`dropdb`, `createdb`, `migrate`, `seed_experience_demo --reset`) with the environment from `.claude/launch.json`.
- A worktree in another folder needs its own port and database; never point it at `ohc_experience_demo`.

# Dropdowns

- Every dropdown is searchable. `ohc_experience/static/js/searchable-select.js` turns each single-choice `select.ui-select` on any page into a type-to-filter combobox, and the select underneath still holds and posts the value. New dropdowns get this by being careui selects: render the field with `{% ui_field %}`, or give a hand-written `<select>` the `ui-select` class inside a `.ui-select-wrapper`. Never build a custom dropdown or add a select library.
- Keep a plain dropdown only when asked, with `data-native-select` (in a form, `forms.Select(attrs={"data-native-select": ""})`).
- A script that changes a select's value or options should dispatch `change` on the select, as `ohc_experience/static/js/pincode-lookup.js` does, so the search box shows the new choice.

# Rows

- A row that opens a page opens it on a click anywhere in the row, not only on its name. `ohc_experience/static/js/row-link.js` does this for every table row or list item holding a link marked `data-row-link`, and the row gets a pointer cursor. Mark the link to the row's page, usually the name, and keep it a real `<a href>`: keyboard and screen reader users reach the page through it, and htmx boosts it. Give the row `group` and the link `group-hover:text-primary` so the name lights up with the row, or `group-hover:underline` when the link is already primary. A table row with nowhere to go, like a team member or a permission, stays as it is.
- A card that is one `<a>` from edge to edge, like the queue's cards on a phone, already opens from anywhere and needs no marker.
