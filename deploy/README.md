# Deploy — screddy-consult (SRC box), hermes user

Per docs/2026-08-21-digest-rework-design.md decision 3: the digest runs as
systemd user timers on screddy-consult, next to the corpus DB. This replaced
both the old TMN-box units (box decommissioned 2026-08-21) and the claude.ai
routine path.

    rsync -a --delete --exclude .git --exclude __pycache__ --exclude .claude-harness \
        ./ hermes@screddy-consult:~/daily-digest/
    # .env: start from .env.example. RDS_URL creds come from the box's
    # /root/.jarvis-corpus-roles.env (shawn_jarvis role).
    ssh screddy-hermes 'mkdir -p ~/.config/systemd/user && \
        cp ~/daily-digest/deploy/daily-digest-*.{service,timer} ~/.config/systemd/user/ && \
        systemctl --user daemon-reload && \
        systemctl --user enable --now daily-digest-morning.timer daily-digest-retro.timer'

Verify: `DRY_RUN=1 ~/shawn-corpus/.venv/bin/python3 morning.py` renders without
posting. Timers: 07:00 (morning) and 21:00 (retro) Asia/Makassar — adjust the
OnCalendar lines if Shawn's timezone base changes.
