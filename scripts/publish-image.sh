#!/bin/sh
set -eu

docker_cli=${DOCKER:-docker}
registry=${REGISTRY:-192.168.178.69:5555}
image_name=${IMAGE_NAME:-open-lisa-pro}
version=${VERSION:-$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml)}
commit=${APP_GIT_COMMIT:-$(git rev-parse --short=7 HEAD 2>/dev/null || printf unknown)}

if [ -z "$version" ]; then
  echo "Could not determine package version" >&2
  exit 1
fi

builder="openlisapro-publish-$$"
buildkit_config=$(mktemp "${TMPDIR:-/tmp}/openlisapro-buildkitd.XXXXXX.toml")

cleanup() {
  "$docker_cli" buildx rm "$builder" >/dev/null 2>&1 || true
  rm -f "$buildkit_config"
}
trap cleanup EXIT HUP INT TERM

cat > "$buildkit_config" <<EOF
debug = true
[registry."$registry"]
  http = true
EOF

"$docker_cli" buildx create \
  --name "$builder" \
  --driver docker-container \
  --buildkitd-config "$buildkit_config" \
  --bootstrap

"$docker_cli" buildx build \
  --builder "$builder" \
  --build-arg "APP_GIT_COMMIT=$commit" \
  --tag "$registry/$image_name:$version" \
  --tag "$registry/$image_name:latest" \
  --push \
  -f Dockerfile .
