#!/usr/bin/env bash
#
# reolink-set-focus.sh — set a Reolink camera's focus to a specific numeric value.
#
# Reolink splits auth by operation: *reads* (GetZoomFocus) work with query-param
# auth, but the *actuating* StartZoomFocus write needs a real session token
# (query-param auth returns rspCode -26 "ability error"). This script does the
# full two-step flow: Login -> token -> StartZoomFocus, and validates the focus
# value against the camera's advertised range first (fail-fast on bad input).
#
# Usage:
#   ./reolink-set-focus.sh <camera-url> <username> <password> <focus-value>
#
# Example (H00F hummingcam, RLC-811A):
#   ./reolink-set-focus.sh http://10.107.0.221:10000 admin '<ADMIN_PASSWORD>' 3065
#
# Notes:
#   - <camera-url> is the scheme+host+port only (no path), e.g.
#     http://10.107.0.221:10000 — the script appends /cgi-bin/api.cgi.
#   - Quote the password in single quotes if it contains ! & ? etc.
#   - Focus range is camera-specific; the script reads it live and rejects
#     out-of-range values before touching the lens.
#
# Exit codes:
#   0  focus set successfully
#   1  bad usage / arguments
#   2  camera unreachable or malformed response
#   3  login failed (bad credentials / no token)
#   4  focus value out of range
#   5  camera rejected the focus command
#
set -euo pipefail

# ── dependencies ────────────────────────────────────────────────────
for dep in curl python3; do
    if ! command -v "$dep" >/dev/null 2>&1; then
        echo "ERROR: required command '$dep' not found in PATH" >&2
        exit 1
    fi
done

# ── arguments (fail fast) ───────────────────────────────────────────
if [ "$#" -ne 4 ]; then
    echo "Usage: $0 <camera-url> <username> <password> <focus-value>" >&2
    echo "  e.g. $0 http://10.107.0.221:10000 admin '<ADMIN_PASSWORD>' 3065" >&2
    exit 1
fi

CAM_URL="${1%/}"          # strip any trailing slash
USER_NAME="$2"
PASSWORD="$3"
FOCUS="$4"

# camera-url must be scheme://host[:port] with no path
if ! printf '%s' "$CAM_URL" | grep -qE '^https?://[^/]+$'; then
    echo "ERROR: <camera-url> must be scheme://host[:port] with no path" >&2
    echo "       got: '$CAM_URL'  (e.g. http://10.107.0.221:10000)" >&2
    exit 1
fi

# focus value must be a non-negative integer
if ! printf '%s' "$FOCUS" | grep -qE '^[0-9]+$'; then
    echo "ERROR: <focus-value> must be a non-negative integer, got: '$FOCUS'" >&2
    exit 1
fi

API="${CAM_URL}/cgi-bin/api.cgi"

# Encode ONLY the characters that would break URL query parsing, and leave the
# rest literal. Reolink's query-param auth compares the password value as it
# arrives; percent-encoding password-legal punctuation like '!' makes the camera
# see the literal "%21" and reject it with rspCode -7 "login failed". So we keep
# a wide safe set (the manual working command used a raw '!'), and only escape
# the truly URL-breaking chars: space, & # ? + % and control chars.
urlencode_query() {
    # safe= keeps these literal; quote() still escapes space, &, #, ?, +, %, etc.
    python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe="!$*(),/:;=@~'"'"'-._"))' "$1"
}
USER_ENC="$(urlencode_query "$USER_NAME")"
PASS_ENC="$(urlencode_query "$PASSWORD")"

# token can contain URL-breaking chars too; encode it the same conservative way.
urlencode() { urlencode_query "$1"; }

# ── step 0: read the current focus + valid range (query-param auth) ──
echo ">> Reading current focus + range from $CAM_URL ..."
RANGE_JSON="$(curl -s --max-time 15 \
    "${API}?cmd=GetZoomFocus&user=${USER_ENC}&password=${PASS_ENC}" \
    -H "Content-Type: application/json" \
    -d '[{"cmd":"GetZoomFocus","action":1,"param":{"channel":0}}]' || true)"

if [ -z "$RANGE_JSON" ]; then
    echo "ERROR: no response from camera at $CAM_URL (unreachable/timeout)" >&2
    exit 2
fi

# Parse min/max/current with python (robust to firmware field layout).
read -r FMIN FMAX FCUR <<EOF
$(printf '%s' "$RANGE_JSON" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)[0]
except Exception as e:
    sys.stderr.write("ERROR: could not parse GetZoomFocus response: %s\n" % e); sys.exit(2)
if d.get("code") != 0:
    sys.stderr.write("ERROR: GetZoomFocus returned error: %s\n" % json.dumps(d.get("error", d))); sys.exit(2)
try:
    rng = d["range"]["ZoomFocus"]["focus"]["pos"]
    cur = d["value"]["ZoomFocus"]["focus"]["pos"]
    print(rng["min"], rng["max"], cur)
except Exception as e:
    sys.stderr.write("ERROR: focus range not present in response: %s\n" % e); sys.exit(2)
')
EOF

if [ -z "${FMAX:-}" ]; then
    echo "ERROR: failed to read focus range (see message above)" >&2
    echo "Raw response: $RANGE_JSON" >&2
    exit 2
fi

echo "   current focus=$FCUR   valid range=[$FMIN..$FMAX]   requested=$FOCUS"

# ── validate requested value against the live range (fail fast) ─────
if [ "$FOCUS" -lt "$FMIN" ] || [ "$FOCUS" -gt "$FMAX" ]; then
    echo "ERROR: focus value $FOCUS is out of range [$FMIN..$FMAX]" >&2
    exit 4
fi

# ── step 1: login -> session token ──────────────────────────────────
echo ">> Logging in to obtain a session token ..."
LOGIN_JSON="$(curl -s --max-time 15 "${API}?cmd=Login" \
    -H "Content-Type: application/json" \
    -d "[{\"cmd\":\"Login\",\"param\":{\"User\":{\"userName\":\"${USER_NAME}\",\"password\":\"${PASSWORD}\"}}}]" || true)"

if [ -z "$LOGIN_JSON" ]; then
    echo "ERROR: no response to Login from $CAM_URL" >&2
    exit 2
fi

TOKEN="$(printf '%s' "$LOGIN_JSON" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)[0]
except Exception as e:
    sys.stderr.write("ERROR: could not parse Login response: %s\n" % e); sys.exit(3)
if d.get("code") != 0:
    sys.stderr.write("ERROR: Login failed: %s\n" % json.dumps(d.get("error", d))); sys.exit(3)
try:
    print(d["value"]["Token"]["name"])
except Exception as e:
    sys.stderr.write("ERROR: no token in Login response: %s\n" % e); sys.exit(3)
' )" || {
    echo "Raw Login response: $LOGIN_JSON" >&2
    exit 3
}

if [ -z "$TOKEN" ]; then
    echo "ERROR: empty token from Login (bad credentials?)" >&2
    echo "Raw Login response: $LOGIN_JSON" >&2
    exit 3
fi
echo "   token acquired: ${TOKEN:0:8}..."

# ── step 2: set focus using the token ───────────────────────────────
echo ">> Setting focus to $FOCUS ..."
TOKEN_ENC="$(urlencode "$TOKEN")"
SET_JSON="$(curl -s --max-time 15 "${API}?cmd=StartZoomFocus&token=${TOKEN_ENC}" \
    -H "Content-Type: application/json" \
    -d "[{\"cmd\":\"StartZoomFocus\",\"action\":0,\"param\":{\"ZoomFocus\":{\"channel\":0,\"op\":\"FocusPos\",\"pos\":${FOCUS}}}}]" || true)"

if [ -z "$SET_JSON" ]; then
    echo "ERROR: no response to StartZoomFocus" >&2
    exit 2
fi

# Check the camera accepted it (code 0).
printf '%s' "$SET_JSON" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)[0]
except Exception as e:
    sys.stderr.write("ERROR: could not parse StartZoomFocus response: %s\n" % e); sys.exit(5)
if d.get("code") == 0:
    sys.exit(0)
sys.stderr.write("ERROR: camera rejected StartZoomFocus: %s\n" % json.dumps(d.get("error", d)))
sys.exit(5)
' || {
    echo "Raw response: $SET_JSON" >&2
    exit 5
}

echo "OK: focus command accepted (pos=$FOCUS). The lens moves asynchronously;"
echo "    re-run GetZoomFocus in a moment to confirm it settled."
exit 0
