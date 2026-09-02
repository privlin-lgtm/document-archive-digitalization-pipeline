#!/bin/sh
set -e

# Named volumes can pre-date this image's non-root user. A full recursive
# chown of a large archive on every start is expensive — only fix the
# mount point when appuser cannot write it.
if [ -d /data/documents ] && ! gosu appuser test -w /data/documents 2>/dev/null; then
  chown appuser:appuser /data/documents 2>/dev/null || true
fi

exec gosu appuser "$@"
