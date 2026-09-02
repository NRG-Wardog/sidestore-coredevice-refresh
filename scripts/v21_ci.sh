#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${MODE:?MODE must be preflight or build}"
BUILDER="${BUILDER:-${GITHUB_WORKSPACE:-/tmp}/builder}"
SIDESTORE_REF="${SIDESTORE_REF:-3dc127ee6f17aa20f0863becd07c0c6043d8dddd}"
MINIMUXER_REF="${MINIMUXER_REF:-ef8a54ccbd08a0b679a556df20121c1e7e13be7e}"
ROOT="${RUNNER_TEMP:-/tmp}/sidestore-v21-${MODE}"

on_error() {
  local line="$1" code="$2"
  echo "::error::v21 ${MODE} failed at line ${line} (exit ${code}): ${BASH_COMMAND}" >&2
}
trap 'on_error "$LINENO" "$?"' ERR

checkout_repo() {
  local url="$1" ref="$2" path="$3" recurse="${4:-no}"
  rm -rf "$path"
  git init -q "$path"
  git -C "$path" remote add origin "$url"
  git -C "$path" fetch -q --depth=1 origin "$ref"
  git -C "$path" checkout -q --detach FETCH_HEAD
  test "$(git -C "$path" rev-parse HEAD)" = "$ref"
  if [[ "$recurse" == yes ]]; then
    git -C "$path" submodule update --init --recursive --depth=1
  fi
}

parse_patched_swift() {
  local mux="$1"
  swiftc -frontend -parse "$mux/Sources/MinimuxerApi.swift"
  swiftc -frontend -parse "$mux/DeviceGateway/PairingProtocol.swift"
  swiftc -frontend -parse "$mux/DeviceGateway/libimobiledevice/LibimobiledeviceGateway.swift"
  swiftc -frontend -parse "$mux/Sources/Services/NetworkObserverService.swift"
  swiftc -frontend -parse "$mux/Sources/MinimuxerImpl.swift"
  swiftc -frontend -parse "$mux/Sources/Services/UsbmuxdProxyServer.swift"
}

verify_lockdown_graph() {
  local mux="$1"
  local api="$mux/Sources/MinimuxerApi.swift"
  local pairing="$mux/DeviceGateway/PairingProtocol.swift"
  local gateway="$mux/DeviceGateway/libimobiledevice/LibimobiledeviceGateway.swift"
  local package="$mux/Package.swift"
  local gateway_package="$mux/DeviceGateway/Package.swift"

  grep -Fq '[SS-V21-LOCKDOWN] backend=libimobiledevice' "$api"
  grep -Fq 'let resolvedBackend: GatewayBackend = .libimobiledevice' "$api"
  ! grep -Fq 'let resolvedBackend: GatewayBackend = .idevice' "$api"
  ! grep -Fq 'currentBackend == resolvedBackend' "$api"

  python3 - "$pairing" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
lockdown = text.find("if missingLockdownKeys.isEmpty")
remote = text.find("if missingRPKeys.isEmpty")
if lockdown < 0 or remote < 0 or lockdown >= remote:
    raise SystemExit("composite pairing file does not prefer Lockdown")
PY

  grep -Fq '[SS-V21-LOCKDOWN] pairing selected=' "$gateway"
  grep -Fq '[SS-V21-LOCKDOWN] lockdownd handshake start' "$gateway"
  grep -Fq '[SS-V21-LOCKDOWN] GetValue start key=' "$gateway"
  grep -Fq '[SS-V21-LOCKDOWN] GetValue pass key=' "$gateway"
  grep -Fq '[SS-V21-LOCKDOWN] UniqueDeviceID query pass' "$gateway"
  ! grep -Fq 'return cachedUDID' "$gateway"

  grep -Fq 'https://github.com/SideStore/em_proxy/releases/download/' "$package"
  grep -Fq 'https://github.com/SideStore/idevice/releases/download/' "$gateway_package"
  grep -Fq 'https://github.com/SideStore/libimobiledevice-xcframework/releases/download/' "$gateway_package"
  grep -Fq '7b9d269ec64027d73a50faa917cb18fa218c1fc9' "$gateway_package"

  ! grep -R -Fq 'path: "LocalBinary/EMProxy.xcframework"' "$package"
  ! grep -R -Fq 'path: "LocalBinary/IDevice.xcframework"' "$gateway_package"
  ! grep -R -Eq 'EMP-NAT44|EMP-TRANSIT|v14-rp-protocol-matrix' \
    "$mux/Sources" "$mux/DeviceGateway"
}

rm -rf "$ROOT"
mkdir -p "$ROOT"
cd "$ROOT"

python3 -m py_compile \
  "$BUILDER/scripts/patch_v21_backend.py" \
  "$BUILDER/scripts/patch_v21_gateway.py" \
  "$BUILDER/scripts/patch_v21_runtime.py" \
  "$BUILDER/scripts/verify_v21_lockdown.py"
if [[ "$MODE" == preflight ]]; then
  checkout_repo \
    https://github.com/SideStore/minimuxer.git \
    "$MINIMUXER_REF" \
    minimuxer

  for round in 1 2; do
    python3 "$BUILDER/scripts/patch_v21_backend.py" minimuxer
    python3 "$BUILDER/scripts/patch_v21_gateway.py" \
      minimuxer/DeviceGateway/libimobiledevice/LibimobiledeviceGateway.swift
    python3 "$BUILDER/scripts/patch_v21_runtime.py" minimuxer
  done
  python3 "$BUILDER/scripts/verify_v21_lockdown.py" minimuxer

  parse_patched_swift minimuxer
  verify_lockdown_graph minimuxer

  swift package --package-path minimuxer dump-package \
    > /tmp/v21-minimuxer-package.json
  swift package --package-path minimuxer/DeviceGateway dump-package \
    > /tmp/v21-gateway-package.json

  {
    echo 'SideStore v21 Lockdown-first preflight'
    echo "builder_commit=${GITHUB_SHA:-local}"
    echo "sidestore_ref=$SIDESTORE_REF"
    echo "minimuxer_ref=$MINIMUXER_REF"
    echo 'backend=libimobiledevice'
    echo 'pairing_policy=complete Lockdown record before RemotePairing'
    echo 'transport=LocalDevVPN peer -> fake usbmuxd -> libimobiledevice -> lockdownd'
    echo 'native_builds=none'
    echo 'dead_routes_removed=RPPairing matrix,NAT44,IKEv2 local-address transit'
    echo 'preflight=PASS'
  } | tee /tmp/v21-preflight.txt
  exit 0
fi

if [[ "$MODE" != build ]]; then
  echo "unsupported MODE=$MODE" >&2
  exit 2
fi

checkout_repo \
  https://github.com/SideStore/SideStore.git \
  "$SIDESTORE_REF" \
  SideStore \
  yes

MUX="$ROOT/SideStore/Dependencies/minimuxer"
test -d "$MUX"
test "$(git -C "$MUX" rev-parse HEAD)" = "$MINIMUXER_REF"

for round in 1 2; do
  python3 "$BUILDER/scripts/patch_v21_backend.py" "$MUX"
  python3 "$BUILDER/scripts/patch_v21_gateway.py" \
    "$MUX/DeviceGateway/libimobiledevice/LibimobiledeviceGateway.swift"
  python3 "$BUILDER/scripts/patch_v21_runtime.py" "$MUX"
done
python3 "$BUILDER/scripts/verify_v21_lockdown.py" "$MUX"

parse_patched_swift "$MUX"
verify_lockdown_graph "$MUX"

if ! command -v ldid >/dev/null 2>&1; then
  brew install ldid
fi
if ! command -v xcbeautify >/dev/null 2>&1; then
  brew install xcbeautify
fi

# Resolve once without deleting caches. A second attempt is allowed only for a
# transient package-network failure; no source or native component is rebuilt.
for attempt in 1 2; do
  if (
    cd SideStore
    xcodebuild \
      -resolvePackageDependencies \
      -project AltStore.xcodeproj \
      -scheme SideStore
  ); then
    break
  fi
  test "$attempt" -lt 2
  sleep 2
done

(
  cd SideStore
  mkdir -p build/logs
  set -o pipefail
  NSUnbufferedIO=YES make build \
    2>&1 | tee -a build/logs/build.log | xcbeautify --renderer github-actions
  make fakesign | tee -a build/logs/build.log
  make ipa | tee -a build/logs/build.log
)

IPA="$ROOT/SideStore/SideStore.ipa"
test -f "$IPA"
unzip -t "$IPA" >/tmp/v21-unzip-test.txt

rm -rf /tmp/v21-ipa
mkdir -p /tmp/v21-ipa
unzip -q "$IPA" -d /tmp/v21-ipa
APP=/tmp/v21-ipa/Payload/SideStore.app
test -d "$APP"
find "$APP" -type f -size +32k -print0 \
  | xargs -0 strings \
  > /tmp/v21-embedded-strings.txt

for marker in \
  '[SS-V21-LOCKDOWN] backend=libimobiledevice' \
  '[SS-V21-LOCKDOWN] pairing selected=' \
  '[SS-V21-LOCKDOWN] fake usbmuxd listening' \
  '[SS-V21-LOCKDOWN] lockdownd handshake start' \
  '[SS-V21-LOCKDOWN] GetValue start key=' \
  '[SS-V21-LOCKDOWN] GetValue pass key=' \
  '[SS-V21-LOCKDOWN] UniqueDeviceID query pass'; do
  grep -Fq "$marker" /tmp/v21-embedded-strings.txt
done

! grep -Eq 'EMP-NAT44|EMP-TRANSIT|v14-rp-protocol-matrix' \
  /tmp/v21-embedded-strings.txt

SHA="$(shasum -a 256 "$IPA" | awk '{print $1}')"
SIZE="$(stat -f '%z' "$IPA")"
{
  echo 'SideStore v21 Lockdown-first'
  echo "builder_commit=${GITHUB_SHA:-unknown}"
  echo "sidestore_ref=$SIDESTORE_REF"
  echo "minimuxer_ref=$MINIMUXER_REF"
  echo "ipa_size=$SIZE"
  echo "ipa_sha256=$SHA"
  echo 'backend=libimobiledevice'
  echo 'pairing_policy=lockdown-first'
  echo 'native_rust_builds=0'
  echo 'dsym_archive=skipped'
  echo 'verification=PASS'
} | tee /tmp/v21-verification.txt
