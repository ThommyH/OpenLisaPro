#!/bin/sh
set -eu

version=${1:-}
if [ -z "$version" ]; then
  echo "Usage: $0 VERSION" >&2
  exit 2
fi

docker_cli=${DOCKER:-docker}
dockhand_container=${DOCKHAND_CONTAINER:-dockhand}
target_container=${DOCKHAND_TARGET_CONTAINER:-openlisapro-replica}
app_url=${DOCKHAND_APP_URL:-http://127.0.0.1:5050}

label() {
  "$docker_cli" inspect --format "{{ index .Config.Labels \"$1\" }}" "$target_container"
}

project=$(label com.docker.compose.project)
service=$(label com.docker.compose.service)
compose_files=$(label com.docker.compose.project.config_files)
compose_file=${compose_files%%,*}

if [ -z "$project" ] || [ -z "$service" ] || [ -z "$compose_file" ]; then
  echo "Container '$target_container' is not part of a Compose project." >&2
  exit 1
fi

if ! "$docker_cli" exec "$dockhand_container" test -f "$compose_file"; then
  echo "Dockhand cannot access the Compose file recorded on '$target_container': $compose_file" >&2
  exit 1
fi

is_version() {
  python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("version") == sys.argv[1] else 1)' "$version"
}

if current=$(curl -fsS --connect-timeout 2 --max-time 4 "$app_url/api/version" 2>/dev/null) && printf '%s' "$current" | is_version; then
  echo "Dockhand deployment '$project/$service' already runs OpenLisaPro $version."
  exit 0
fi

echo "Pulling the Compose image for Dockhand deployment '$project/$service'."
"$docker_cli" exec "$dockhand_container" docker compose \
  -p "$project" -f "$compose_file" pull "$service"

echo "Recreating '$target_container' with the pulled image."
"$docker_cli" exec "$dockhand_container" docker compose \
  -p "$project" -f "$compose_file" up -d --no-deps --force-recreate --pull never "$service"

attempt=0
while [ "$attempt" -lt 60 ]; do
  if current=$(curl -fsS --connect-timeout 2 --max-time 4 "$app_url/api/version" 2>/dev/null) && printf '%s' "$current" | is_version; then
    echo "Dockhand deployment is running OpenLisaPro $version."
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 1
done

echo "Dockhand container was recreated, but $app_url did not report version $version within 60 seconds." >&2
exit 1
