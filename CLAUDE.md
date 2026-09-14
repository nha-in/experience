# Local development

- Run the dev server with the `django` entry in `.claude/launch.json`. It starts Django on port 8000 and the Tailwind watcher together, and the preview tool reuses it if it is already running.
- Never start another `runserver` on a different port. If port 8000 is busy, check what holds it (`lsof -nP -iTCP:8000 -sTCP:LISTEN`) and reuse or stop it. Stop any server or watcher you start yourself before you finish.
- The server uses the `ohc_experience_demo` database. After switching branches, run `migrate` against it. If the new branch lacks a migration that was applied, recreate the database (`dropdb`, `createdb`, `migrate`, `seed_experience_demo --reset`) with the environment from `.claude/launch.json`.
- A worktree in another folder needs its own port and database; never point it at `ohc_experience_demo`.
