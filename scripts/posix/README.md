# POSIX automation (macOS / Linux)

Ready-to-edit unit files for running aifolimizer skills on a schedule, the
non-Windows equivalent of the Task Scheduler setup in
[`../AUTOMATION.md`](../AUTOMATION.md). The actual work is done by
[`../run-claude-skill.sh`](../run-claude-skill.sh) (try Claude → free-LLM
fallback → push to Telegram). One scheduler entry per skill; no per-skill code.

Each template has two placeholders to replace:

- `__REPO__`  → absolute path to your clone (e.g. `/home/you/aifolimizer`)
- `__HOME__`  → your home dir (e.g. `/home/you` or `/Users/you`)

First make the runner executable:

```bash
chmod +x scripts/run-claude-skill.sh
```

## macOS - launchd

```bash
sed "s|__REPO__|$PWD|g; s|__HOME__|$HOME|g" \
  scripts/posix/com.aifolimizer.daily-briefing.plist \
  > ~/Library/LaunchAgents/com.aifolimizer.daily-briefing.plist
launchctl load ~/Library/LaunchAgents/com.aifolimizer.daily-briefing.plist
launchctl start com.aifolimizer.daily-briefing   # test now
```

## Linux - systemd (user)

```bash
mkdir -p ~/.config/systemd/user
sed "s|__REPO__|$PWD|g" scripts/posix/aifolimizer-daily-briefing.service \
  > ~/.config/systemd/user/aifolimizer-daily-briefing.service
cp scripts/posix/aifolimizer-daily-briefing.timer \
  ~/.config/systemd/user/aifolimizer-daily-briefing.timer
systemctl --user daemon-reload
systemctl --user enable --now aifolimizer-daily-briefing.timer
systemctl --user start aifolimizer-daily-briefing.service   # test now
```

## Either OS - cron one-liner

```cron
0 7 * * 1-5 /path/to/aifolimizer/scripts/run-claude-skill.sh daily-briefing
```

## Adding more skills

Copy a template, swap `daily-briefing` for the skill name (`top-trades-today`,
`position-review`, `momentum-scanner`, `pead-tracker`, `perf-optimizer`), and
adjust the schedule. The runner is skill-agnostic.
