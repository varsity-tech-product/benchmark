# Production Deployment

Production runs on the VPS as Linux user `bench`.

Runtime layout:

- deploy path: `/home/bench/benchmark`
- service: `quanttutor`
- app entrypoint: `cd /home/bench/benchmark/bench && ../.venv/bin/python -m server --host 127.0.0.1 --port 8000 --docker`
- public SSH policy: `bench` SSH access stays governed by `/etc/security/access.conf`
- GitHub deployment path: self-hosted runner on the VPS with labels `production,bench-vps`

Initial VPS setup:

```bash
sudo usermod -aG docker bench
sudo install -m 0644 /home/bench/benchmark/deploy/quanttutor.service /etc/systemd/system/quanttutor.service
sudo systemctl daemon-reload
sudo systemctl enable quanttutor
```

The GitHub runner service runs as `bench`. The deployment workflow syncs the
checked-out repository into `/home/bench/benchmark`, installs Python
dependencies into `/home/bench/benchmark/.venv`, rebuilds Docker images when
the Dockerfiles change, restarts `quanttutor`, and checks `/health`.
Manual workflow runs default to code sync, restart, and health check only.
Use the workflow inputs to request Docker image rebuilds, dependency installs,
or systemd unit reinstalls during manual maintenance runs.

Required sudoers entry for workflow restarts:

```sudoers
Cmnd_Alias QTB_DEPLOY = /usr/bin/install -m 0644 /home/bench/benchmark/deploy/quanttutor.service /etc/systemd/system/quanttutor.service, /usr/bin/systemctl daemon-reload, /usr/bin/systemctl restart quanttutor, /usr/bin/systemctl is-active quanttutor, /usr/bin/systemctl status quanttutor --no-pager, /usr/bin/journalctl -u quanttutor --no-pager -n 50, /usr/bin/journalctl -u quanttutor --no-pager -n 5
bench ALL=(root) NOPASSWD: QTB_DEPLOY
```

The workflow uses a self-hosted runner so GitHub Actions deployment traffic
stays local to the VPS. This preserves the existing `bench` SSH IP allowlist.
