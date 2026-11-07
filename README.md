# Stream recorder google drive backup

https://github.com/glotlabs/gdrive/tree/main
https://console.cloud.google.com/apis/api/drive.googleapis.com/metrics

Docker build

```bash
docker build -t streamrecorder-gdrive-backup .
```

Save credentials config

```bash
docker run --rm -it --init \
  --name gdrive_account_add \
  --cap-add=NET_ADMIN \
  --sysctl net.ipv4.conf.all.route_localnet=1 \
  -p 8085:9000 \
  -v ./gdrive_config:/root/.config \
  streamrecorder-gdrive-backup \
  gdrive account add
```

```bash
docker exec -it gdrive_account_add curl "URL_HERE"
```

Other gdrive commands

```bash
docker run --rm -it --init \
  -v ./gdrive_config:/root/.config \
  streamrecorder-gdrive-backup \
  gdrive account list
```

```bash
docker run --rm -it --init \
  -v ./gdrive_config:/root/.config \
  streamrecorder-gdrive-backup \
  gdrive files upload entrypoint.sh --parent 1Ydk8QdWb5epaPHIxqptepwz9PQvA4zcc
```

Create zip for easypanel:

```bash
zip project.zip Dockerfile entrypoint.sh script.py
```

Save cookes file from alpine easypanel web interface:

```bash
cat > /root/streamrecorder/cookie <<'EOF'
CONTENT HERE
EOF
```