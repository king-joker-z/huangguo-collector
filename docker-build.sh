#!/bin/sh
set -eu
cd "$(dirname "$0")"
IMAGE="${IMAGE:-jokermelove/huangguo-collector:1.11.5}"
TAR="${TAR:-huangguo-collector-1.11.5.tar}"

echo "Building $IMAGE ..."
docker build --platform linux/amd64 -t "$IMAGE" .
echo "Saving $TAR ..."
docker save "$IMAGE" -o "$TAR"
echo "Done. Import on NAS: docker load -i $TAR"






