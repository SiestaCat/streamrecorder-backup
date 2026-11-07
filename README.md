# Stream recorder io backup

Backup to mega

Docker build

```bash
docker build -t streamrecorderio-backup .
```

From container, run `rclone config` and add a new remote with name remote

Volumes:

```
/root/.config/rclone
/root/streamrecorder/files
/root/streamrecorder/cookie (file)
```

Save cookes file from container, copy curl request from browser and paste:

```bash
cat > /root/streamrecorder/cookie <<'EOF'
CONTENT HERE
EOF
```