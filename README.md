# Stream recorder io backup

Backup to mega

Docker build

```bash
docker build -t streamrecorderio-backup .
```

Volumes:

```
/root/.config
/root/streamrecorder/files
/root/streamrecorder/cookie (file)
```

Save cookes file from alpine easypanel web interface:

```bash
cat > /root/streamrecorder/cookie <<'EOF'
CONTENT HERE
EOF
```