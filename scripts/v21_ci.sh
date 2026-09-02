#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${MODE:?MODE must be prepare or build}"
BUILDER="${BUILDER:-${GITHUB_WORKSPACE:-/tmp}/builder}"
SIDESTORE_DIR="${SIDESTORE_DIR:-${GITHUB_WORKSPACE:-/tmp}/work/SideStore}"
SIDESTORE_REF="${SIDESTORE_REF:-3dc127ee6f17aa20f0863becd07c0c6043d8dddd}"
MINIMUXER_REF="${MINIMUXER_REF:-ef8a54ccbd08a0b679a556df20121c1e7e13be7e}"
PREPARE_STAMP="${RUNNER_TEMP:-/tmp}/v21-adaptive-prepare.ok"
MUX="$SIDESTORE_DIR/Dependencies/minimuxer"

on_error() {
  local line="$1" code="$2"
  echo "::error::v21 adaptive ${MODE} failed at line ${line} (exit ${code}): ${BASH_COMMAND}" >&2
}
trap 'on_error "$LINENO" "$?"' ERR

parse_patched_swift() {
  swiftc -frontend -parse "$MUX/DeviceGateway/PairingProtocol.swift"
  swiftc -frontend -parse "$MUX/DeviceGateway/idevice/IdeviceGateway.swift"
  swiftc -frontend -parse "$MUX/Sources/Services/NetworkObserverService.swift"
}

verify_adaptive_graph() {
  local pairing="$MUX/DeviceGateway/PairingProtocol.swift"
  local gateway="$MUX/DeviceGateway/idevice/IdeviceGateway.swift"
  local api="$MUX/Sources/MinimuxerApi.swift"
  local package="$MUX/Package.swift"
  local gateway_package="$MUX/DeviceGateway/Package.swift"

  grep -Fq 'private static var currentBackend: GatewayBackend = .idevice' "$api"
  ! grep -Fq 'let resolvedBackend: GatewayBackend = .libimobiledevice' "$api"

  python3 - "$pairing" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
rp = text.find("if missingRPKeys.isEmpty")
lock = text.find("if missingLockdownKeys.isEmpty")
if rp < 0 or lock < 0 or rp >= lock:
    raise SystemExit("PairingProtocol no longer matches upstream RP-first semantics")
PY

  for marker in \
    '[SS-V21-ADAPT] pairing rp=' \
    '[SS-V21-ADAPT] composite route=coredevice-first rp-fallback' \
    '[SS-V21-ADAPT] core provider PASS' \
    '[SS-V21-ADAPT] tunnel_create_usb START' \
    '[SS-V21-ADAPT] tunnel_create_usb PASS' \
    '[SS-V21-ADAPT] stock RPPairing PASS' \
    '[SS-V21-ADAPT] service route=RSD' \
    '[SS-V21-ADAPT] UDID_PASS' \
    '[SS-V21-ADAPT] personalized DDI route=RSD' \
    '[SS-V21-ADAPT] adaptive transport READY'; do
    grep -Fq "$marker" "$gateway"
  done

  grep -Fq 'idevice_tcp_provider_new(' "$gateway"
  grep -Fq 'tunnel_create_usb(provider, &adapter, &handshake)' "$gateway"
  grep -Fq 'tunnel_create_rppairing(' "$gateway"
  grep -Fq 'idevice_provider_free(provider)' "$gateway"
  ! grep -Fq 'return try performWithTcpService(connect: connectLockdown' "$gateway"

  grep -Fq 'https://github.com/SideStore/em_proxy/releases/download/' "$package"
  grep -Fq 'https://github.com/SideStore/idevice/releases/download/' "$gateway_package"
  ! grep -Fq 'path: "LocalBinary/EMProxy.xcframework"' "$package"
  ! grep -Fq 'path: "LocalBinary/IDevice.xcframework"' "$gateway_package"
  ! grep -R -Eq 'EMP-NAT44|EMP-TRANSIT|v14-rp-protocol-matrix|SS-V18-CDT|SS-V15-TLS' \
    "$MUX/Sources" "$MUX/DeviceGateway"
}

validate_checkout() {
  test -d "$SIDESTORE_DIR/.git"
  git -C "$MUX" rev-parse --is-inside-work-tree >/dev/null
  test "$(git -C "$SIDESTORE_DIR" rev-parse HEAD)" = "$SIDESTORE_REF"
  test "$(git -C "$MUX" rev-parse HEAD)" = "$MINIMUXER_REF"
}

resolve_packages_deterministically() {
  local openssl_artifact="$HOME/Library/Caches/org.swift.swiftpm/artifacts/https___github_com_krzyzanowskim_OpenSSL_releases_download_3_6_2000_OpenSSL_xcframework_zip"
  local resolved=0
  local attempt

  for attempt in 1 2 3; do
    # GitHub macOS runner / SwiftPM can leave this binary artifact directory
    # behind while another package-resolution path attempts the same download.
    # Remove only the conflicting OpenSSL artifact, never the whole SwiftPM cache.
    rm -rf "$openssl_artifact"
    rm -rf "$HOME/Library/Developer/Xcode/DerivedData"/*/SourcePackages/artifacts/openssl 2>/dev/null || true

    if (
      cd "$SIDESTORE_DIR"
      xcodebuild \
        -resolvePackageDependencies \
        -disablePackageRepositoryCache \
        -project AltStore.xcodeproj \
        -scheme SideStore
    ); then
      resolved=1
      break
    fi

    test "$attempt" -lt 3
    sleep 2
  done

  test "$resolved" -eq 1
  echo "v21 deterministic SwiftPM resolution PASS"
}

prepare() {
  validate_checkout
  python3 -m py_compile "$BUILDER/scripts/patch_v21_adaptive_coredevice.py"
  python3 "$BUILDER/scripts/patch_v21_adaptive_coredevice.py" "$MUX"
  python3 "$BUILDER/scripts/patch_v21_adaptive_coredevice.py" "$MUX"
  parse_patched_swift
  verify_adaptive_graph
  swift package --package-path "$MUX" dump-package >/tmp/v21-minimuxer-package.json
  swift package --package-path "$MUX/DeviceGateway" dump-package >/tmp/v21-gateway-package.json

  local patch_sha
  patch_sha="$(shasum -a 256 "$BUILDER/scripts/patch_v21_adaptive_coredevice.py" | awk '{print $1}')"
  {
    echo "sidestore_ref=$SIDESTORE_REF"
    echo "minimuxer_ref=$MINIMUXER_REF"
    echo "patch_sha=$patch_sha"
  } >"$PREPARE_STAMP"

  {
    echo 'SideStore v21 adaptive transport source gate'
    echo "builder_commit=${GITHUB_SHA:-local}"
    echo "sidestore_ref=$SIDESTORE_REF"
    echo "minimuxer_ref=$MINIMUXER_REF"
    echo 'backend=idevice'
    echo 'composite_policy=CoreDeviceProxy first; stock RPPairing fallback'
    echo 'rp_only=stock tunnel_create_rppairing'
    echo 'lockdown_only=CoreDeviceProxy via tunnel_create_usb'
    echo 'services=RSD after either tunnel'
    echo 'nat44=disabled'
    echo 'custom_tls=disabled'
    echo 'native_rust_builds=0'
    echo 'source_gate=PASS'
  } | tee /tmp/v21-source-gate.txt
}

build_once() {
  validate_checkout
  test -f "$PREPARE_STAMP"
  verify_adaptive_graph

  if ! command -v ldid >/dev/null 2>&1; then
    HOMEBREW_NO_AUTO_UPDATE=1 brew install ldid
  fi

  resolve_packages_deterministically

  mkdir -p "$SIDESTORE_DIR/build/logs"
  (
    cd "$SIDESTORE_DIR"
    set -o pipefail
    NSUnbufferedIO=YES make -B build 2>&1 | tee -a build/logs/build.log
    make fakesign 2>&1 | tee -a build/logs/build.log
    make ipa 2>&1 | tee -a build/logs/build.log
  )

  local ipa="$SIDESTORE_DIR/SideStore.ipa"
  test -f "$ipa"
  unzip -t "$ipa" >/tmp/v21-unzip-test.txt

  rm -rf /tmp/v21-ipa
  mkdir -p /tmp/v21-ipa
  unzip -q "$ipa" -d /tmp/v21-ipa
  local app=/tmp/v21-ipa/Payload/SideStore.app
  test -d "$app"
  find "$app" -type f -size +32k -print0 | xargs -0 strings >/tmp/v21-embedded-strings.txt

  for marker in \
    '[SS-V21-ADAPT] pairing rp=' \
    '[SS-V21-ADAPT] composite route=coredevice-first rp-fallback' \
    '[SS-V21-ADAPT] tunnel_create_usb PASS' \
    '[SS-V21-ADAPT] stock RPPairing PASS' \
    '[SS-V21-ADAPT] service route=RSD' \
    '[SS-V21-ADAPT] UDID_PASS'; do
    grep -Fq "$marker" /tmp/v21-embedded-strings.txt
  done

  grep -Fq 'tunnel_create_usb' /tmp/v21-embedded-strings.txt
  grep -Fq 'tunnel_create_rppairing' /tmp/v21-embedded-strings.txt
  ! grep -Eq 'EMP-NAT44|EMP-TRANSIT|v14-rp-protocol-matrix' /tmp/v21-embedded-strings.txt

  local sha size
  sha="$(shasum -a 256 "$ipa" | awk '{print $1}')"
  size="$(stat -f '%z' "$ipa")"
  {
    echo 'SideStore v21 adaptive CoreDevice/RPPairing'
    echo "builder_commit=${GITHUB_SHA:-unknown}"
    echo "sidestore_ref=$SIDESTORE_REF"
    echo "minimuxer_ref=$MINIMUXER_REF"
    echo "ipa_size=$size"
    echo "ipa_sha256=$sha"
    echo 'backend=idevice'
    echo 'composite_policy=coredevice-first-rp-fallback'
    echo 'native_rust_builds=0'
    echo 'verification=PASS'
  } | tee /tmp/v21-verification.txt
}

case "$MODE" in
  prepare) prepare ;;
  build) build_once ;;
  *) echo "unsupported MODE=$MODE" >&2; exit 2 ;;
esac
