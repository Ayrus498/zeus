#!/usr/bin/env bash
# Same as build-deb.sh (the name with an underscore works too).
exec "$(dirname "$0")/build-deb.sh" "$@"
