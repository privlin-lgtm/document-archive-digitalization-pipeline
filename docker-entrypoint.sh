#!/bin/sh
set -e

# Named volumes (document_data, mounted at /data/documents) can pre-date
# this image's non-root user: Docker doesn't touch a volume's existing
# content ownership on mount, so a volume first created back when this
# image ran as root stays root-owned even after the image switches to a
# non-root user -- the app then fails PermissionError on its very first
# write, for a reason that isn't obvious from the error alone. Fix it here,
# as root (this entrypoint is the only thing that still runs as root, and
# only for this one step), before dropping to appuser for the real command.
chown -R appuser:appuser /data/documents 2>/dev/null || true

# The actual application process (this exec'd command and everything it
# spawns) runs as appuser from here on -- verified via
# `docker compose exec app cat /proc/1/status`, not just `docker exec ...
# id`, since the latter is a *separate* process that Docker starts as the
# image's default user (root, because there's no Dockerfile USER directive
# -- there can't be, or this script couldn't chown above). That's the
# accepted, standard cost of this pattern (the same one official postgres/
# redis images use): an interactive `docker exec` session defaults to
# root, but anyone with `docker exec` access already has host-level Docker
# access at least as powerful, so this doesn't weaken the actual security
# boundary the non-root user exists to provide -- limiting what a remote
# exploit of the running process itself can do.
exec gosu appuser "$@"
