# Driftwood Server Operations

This document is a standalone operations guide for deploying, monitoring, and maintaining Driftwood on the server.

## Access

- SSH into the server:
```bash
ssh -i ~/.ssh/id_ed25519 root@188.166.16.34
```
- Dashboard URL:
```text
http://188.166.16.34:8501
```

## Check Service Status

```bash
systemctl status driftwood-scheduler
systemctl status driftwood-dashboard
```

## View Live Logs

```bash
journalctl -u driftwood-scheduler -f
journalctl -u driftwood-dashboard -f
```

## Restart Services

```bash
systemctl restart driftwood-scheduler
systemctl restart driftwood-dashboard
```

## Deploy Updates

1. Push changes from Mac to GitHub.
2. On the server, run:

```bash
cd /root/driftwood
git pull
systemctl restart driftwood-scheduler
systemctl restart driftwood-dashboard
```

## Run Locally on Mac

```bash
cd ~/Desktop/projects/apps/driftwood
source .venv/bin/activate
./start.sh
```

## Emergency Stop

```bash
systemctl stop driftwood-scheduler
systemctl stop driftwood-dashboard
```
