#!/usr/bin/env bash
set -euo pipefail

MODE="${MODE:?MODE must be preflight or build}"
BUILDER="${BUILDER:-$GITHUB_WORKSPACE/builder}"
SIDESTORE_REF="${SIDESTORE_REF:-f3a18d61efae65b05dc935fff75b89225efdb0ff}"
MINIMUXER_REF="${MINIMUXER_REF:-71b957efc65e4687a94b1f1dac27fb3344c22936}"
IDEVICE_REF="${IDEVICE_REF:-61c27041f8d3d0be4cc3e046ee04501649c9d66e}"
EMPROXY_REF="${EMPROXY_REF:-6e117e140ca7cff4ff106bdefa18147552a0e592}"
ROOT="${RUNNER_TEMP:-/tmp}/sidestore-v14-${MODE}"
PATCH=/tmp/patch_v14_protocol_matrix.py

rm -rf "$ROOT"
mkdir -p "$ROOT"
cd "$ROOT"
gzip -dc "$BUILDER/scripts/patch_v14_protocol_matrix.py.gz" > "$PATCH"
python3 -m py_compile "$PATCH"

checkout_repo() {
  local url="$1" ref="$2" path="$3" recurse="${4:-no}"
  git init -q "$path"
  git -C "$path" remote add origin "$url"
  git -C "$path" fetch -q --depth=1 origin "$ref"
  git -C "$path" checkout -q --detach FETCH_HEAD
  if [[ "$recurse" == yes ]]; then
    git -C "$path" submodule update --init --recursive
  fi
  test "$(git -C "$path" rev-parse HEAD)" = "$ref"
}

apply_gateway_sources() {
  local mux="$1" sidestore_root="$2"
  python3 "$BUILDER/scripts/patch_v12_hybrid_backend.py" "$mux/Sources/MinimuxerApi.swift"
  python3 "$BUILDER/scripts/patch_v12_hybrid_idevice.py" "$mux/DeviceGateway/idevice/IdeviceGateway.swift"
  python3 "$BUILDER/scripts/apply_v12_boot_transport_fix.py" "$sidestore_root/SideStore/AppBootManager.swift"
  python3 "$BUILDER/scripts/patch_v13_swift_serialization.py" "$mux/DeviceGateway/idevice/IdeviceGateway.swift"
}

apply_initial_package_transition() {
  local mux="$1"
  # v12 first pins the upstream RemotePairingKit/IDevice inputs and selects the
  # local EMProxy. v13 then replaces the upstream IDevice target with the exact
  # locally built XCFramework. This transition is intentionally one-way.
  python3 "$BUILDER/scripts/patch_v12_packages.py" "$mux"
  python3 "$BUILDER/scripts/patch_v13_packages.py" "$mux"
}

verify_final_local_packages() {
  local mux="$1"
  # Never re-run patch_v12_packages.py here. Its verifier deliberately expects
  # the upstream IDevice release and therefore rejects the final v13/v14 local
  # binary package graph. patch_v13_packages.py is idempotent and is the final
  # package-graph verifier after native binary injection.
  python3 "$BUILDER/scripts/patch_v13_packages.py" "$mux"
  test -d "$mux/DeviceGateway/LocalBinary/IDevice.xcframework"
  test -d "$mux/LocalBinary/EMProxy.xcframework"
}

apply_v14() {
  local idevice_root="$1" gateway="$2" refresh="$3"
  python3 "$PATCH" \
    "$idevice_root/idevice/src/remote_pairing/mod.rs" \
    "$idevice_root/ffi/src/tunnel_provider.rs" \
    "$gateway" \
    "$refresh"
}

verify_source_barriers() {
  local idevice_root="$1" mux="$2" refresh="$3" emproxy="$4"
  local remote="$idevice_root/idevice/src/remote_pairing/mod.rs"
  local ffi="$idevice_root/ffi/src/tunnel_provider.rs"
  local gateway="$mux/DeviceGateway/idevice/IdeviceGateway.swift"

  grep -q 'peerConnectionsInfo' "$remote"
  grep -q 'owningProcessName.*CoreDeviceService' "$remote"
  grep -q 'SS-V14-CREATE-LISTENER' "$remote"
  grep -q 'MATRIX_START' "$ffi"
  grep -q 'fresh_pairverify_per_candidate=true' "$ffi"
  grep -q 'IP_BOUND_IF' "$ffi"
  grep -q 'IPV6_BOUND_IF' "$ffi"
  grep -q 'virtual-reflection' "$ffi"
  grep -q 'PAIRING_PASS' "$ffi"
  grep -q 'LISTENER_PASS' "$ffi"
  grep -q 'DYNAMIC_CONNECT_PASS' "$ffi"
  grep -q 'RSD_PASS' "$ffi"
  grep -q 'TRANSPORT_SELECT path=v14-rp-protocol-matrix' "$gateway"
  grep -q 'COREDEVICE_FALLBACK_START' "$gateway"
  grep -q '\[SS-V14-READY\] SIGNER_PASS' "$refresh"
  test "$(grep -Fc 'FLAG: READY_TO_AUTOMATION' "$refresh")" -eq 1
  ! grep -Eq 'EMP-V13-HAIRPIN|EMP-HAIRPIN|EMP-NAT46|physical-interface bridge active' "$emproxy"

  # Only stage/route metadata may be logged. Pairing material must not appear
  # in any v14 logging statement.
  local logs
  logs="$(grep -h 'SS-V14' "$remote" "$ffi" "$gateway" "$refresh" || true)"
  ! grep -Eq 'HostPrivateKey|RootPrivateKey|private_key=|certificate=|EscrowBag|encryption_key=' <<<"$logs"
}

if [[ "$MODE" == preflight ]]; then
  checkout_repo https://github.com/SideStore/minimuxer.git "$MINIMUXER_REF" minimuxer
  checkout_repo https://github.com/SideStore/idevice.git "$IDEVICE_REF" idevice yes
  checkout_repo https://github.com/SideStore/em_proxy.git "$EMPROXY_REF" em_proxy
  checkout_repo https://github.com/SideStore/SideStore.git "$SIDESTORE_REF" SideStore

  python3 "$BUILDER/scripts/patch_v12_hybrid_backend.py" minimuxer/Sources/MinimuxerApi.swift
  python3 "$BUILDER/scripts/patch_v12_hybrid_idevice.py" minimuxer/DeviceGateway/idevice/IdeviceGateway.swift
  python3 "$BUILDER/scripts/patch_v12_packages.py" minimuxer
  python3 "$BUILDER/scripts/apply_emproxy_diag.py" em_proxy/src/lib.rs
  python3 "$BUILDER/scripts/patch_v13_idevice_protocol.py" idevice
  python3 "$BUILDER/scripts/patch_v13_swift_serialization.py" minimuxer/DeviceGateway/idevice/IdeviceGateway.swift
  python3 "$BUILDER/scripts/patch_v13_emproxy_payload_diag.py" em_proxy/src/lib.rs
  python3 "$BUILDER/scripts/patch_v13_packages.py" minimuxer
  # Regression test: the final local package graph must remain idempotent and
  # must not be sent back through the v12 upstream-IDevice verifier.
  python3 "$BUILDER/scripts/patch_v13_packages.py" minimuxer
  grep -Fq 'path: "LocalBinary/IDevice.xcframework"' minimuxer/DeviceGateway/Package.swift
  ! grep -Eq '^[[:space:]]*url:[[:space:]]*"https://github.com/SideStore/idevice/releases/download/' minimuxer/DeviceGateway/Package.swift

  rm -rf SideStore/Dependencies/minimuxer
  mkdir -p SideStore/Dependencies
  cp -R minimuxer SideStore/Dependencies/minimuxer
  python3 "$BUILDER/scripts/apply_v12_boot_transport_fix.py" SideStore/SideStore/AppBootManager.swift

  apply_v14 idevice \
    minimuxer/DeviceGateway/idevice/IdeviceGateway.swift \
    SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift

  swiftc -frontend -parse minimuxer/Sources/MinimuxerApi.swift
  swiftc -frontend -parse minimuxer/DeviceGateway/idevice/IdeviceGateway.swift
  swiftc -frontend -parse SideStore/SideStore/AppBootManager.swift
  swiftc -frontend -parse SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift
  swift package --package-path minimuxer dump-package >/tmp/v14-minimuxer-package.json
  swift package --package-path minimuxer/DeviceGateway dump-package >/tmp/v14-gateway-package.json

  cargo test --locked --manifest-path idevice/idevice/Cargo.toml --lib \
    v13_frame_prefix_round_trip --no-default-features --features 'openssl,tcp'
  cargo check --locked --manifest-path idevice/ffi/Cargo.toml --features obfuscate
  cargo check --locked --manifest-path em_proxy/Cargo.toml --lib

  verify_source_barriers idevice minimuxer \
    SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift \
    em_proxy/src/lib.rs

  {
    echo 'SideStore v14 protocol matrix preflight'
    echo "sidestore_ref=$SIDESTORE_REF"
    echo "minimuxer_ref=$MINIMUXER_REF"
    echo "idevice_ref=$IDEVICE_REF"
    echo "emproxy_ref=$EMPROXY_REF"
    echo 'apple_parity=createListener.peerConnectionsInfo + same-host dynamic endpoint'
    echo 'matrix=fresh PairVerify/listener per candidate; virtual,en0-v6,en0-v4,utun,awdl,llw,loopback'
    echo 'package_transition=upstream pins -> local IDevice/EMProxy; final verifier=v13 only'
    echo 'ready_flag_policy=runtime refresh success only'
    echo 'preflight=PASS'
  } | tee /tmp/v14-local-tests.txt
  exit 0
fi

if [[ "$MODE" != build ]]; then
  echo "unsupported MODE=$MODE" >&2
  exit 2
fi

checkout_repo https://github.com/SideStore/SideStore.git "$SIDESTORE_REF" SideStore yes
checkout_repo https://github.com/SideStore/idevice.git "$IDEVICE_REF" idevice yes
checkout_repo https://github.com/SideStore/em_proxy.git "$EMPROXY_REF" em_proxy

test "$(git -C SideStore/Dependencies/minimuxer rev-parse HEAD)" = "$MINIMUXER_REF"

brew install ldid xcbeautify || true
if ! command -v bindgen >/dev/null 2>&1; then
  cargo install --locked bindgen-cli --version 0.72.1
fi
rustup target add aarch64-apple-ios

python3 "$BUILDER/scripts/patch_v13_idevice_protocol.py" idevice
# Apply source changes and perform the one-way upstream-to-local package transition
# before building the patched native libraries.
apply_gateway_sources SideStore/Dependencies/minimuxer SideStore
apply_initial_package_transition SideStore/Dependencies/minimuxer
apply_v14 idevice \
  SideStore/Dependencies/minimuxer/DeviceGateway/idevice/IdeviceGateway.swift \
  SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift

(
  cd idevice
  BINDGEN_EXTRA_CLANG_ARGS="--sysroot=$(xcrun --sdk iphoneos --show-sdk-path)" \
  IPHONEOS_DEPLOYMENT_TARGET=17.0 \
  cargo build --release --locked --target aarch64-apple-ios \
    --features obfuscate --manifest-path ffi/Cargo.toml
  test -f target/aarch64-apple-ios/release/libidevice_ffi.a
  test -f ffi/idevice.h
  rm -rf swift/IDevice.xcframework
  cp ffi/idevice.h swift/include/idevice.h
  xcodebuild -create-xcframework \
    -library target/aarch64-apple-ios/release/libidevice_ffi.a \
    -headers swift/include \
    -output swift/IDevice.xcframework
)

(
  cd em_proxy
  python3 "$BUILDER/scripts/apply_emproxy_diag.py" src/lib.rs
  python3 "$BUILDER/scripts/patch_v13_emproxy_payload_diag.py" src/lib.rs
  ! grep -Eq 'EMP-V13-HAIRPIN|EMP-HAIRPIN|EMP-NAT46|physical-interface bridge active' src/lib.rs
  BINDGEN_EXTRA_CLANG_ARGS="--sysroot=$(xcrun --sdk iphoneos --show-sdk-path)" \
  IPHONEOS_DEPLOYMENT_TARGET=15.0 \
  cargo build --release --locked --target aarch64-apple-ios
  test -f target/aarch64-apple-ios/release/libem_proxy.a
  rm -rf libs/EMProxy.xcframework
  mkdir -p libs
  xcodebuild -create-xcframework \
    -library target/aarch64-apple-ios/release/libem_proxy.a \
    -headers include \
    -output libs/EMProxy.xcframework
)

MUX=SideStore/Dependencies/minimuxer
mkdir -p "$MUX/DeviceGateway/LocalBinary" "$MUX/LocalBinary"
rm -rf "$MUX/DeviceGateway/LocalBinary/IDevice.xcframework" "$MUX/LocalBinary/EMProxy.xcframework"
cp -R idevice/swift/IDevice.xcframework "$MUX/DeviceGateway/LocalBinary/IDevice.xcframework"
cp -R em_proxy/libs/EMProxy.xcframework "$MUX/LocalBinary/EMProxy.xcframework"

# Re-run source patches idempotently after binary injection; all final source
# targets are now the exact files compiled into the IPA. The package graph is
# verified in its final local-binary state without invoking the incompatible
# v12 upstream-IDevice verifier again.
apply_gateway_sources "$MUX" SideStore
verify_final_local_packages "$MUX"
apply_v14 idevice \
  "$MUX/DeviceGateway/idevice/IdeviceGateway.swift" \
  SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift
python3 "$BUILDER/scripts/apply_background_auth_gate.py" \
  SideStore/SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift

verify_source_barriers idevice "$MUX" \
  SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift \
  em_proxy/src/lib.rs

swiftc -frontend -parse "$MUX/Sources/MinimuxerApi.swift"
swiftc -frontend -parse "$MUX/DeviceGateway/idevice/IdeviceGateway.swift"
swiftc -frontend -parse SideStore/SideStore/AppBootManager.swift
swiftc -frontend -parse SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift

for attempt in 1 2 3; do
  rm -rf "$HOME/Library/Caches/org.swift.swiftpm"
  if (cd SideStore && xcodebuild -resolvePackageDependencies -project AltStore.xcodeproj -scheme SideStore); then
    break
  fi
  test "$attempt" -lt 3
  sleep 3
done

(cd SideStore && python3 scripts/ci/workflow.py build)

IPA="$ROOT/SideStore/SideStore.ipa"
test -f "$IPA"
unzip -t "$IPA" >/tmp/v14-unzip-test.txt
rm -rf /tmp/v14-ipa
mkdir -p /tmp/v14-ipa
unzip -q "$IPA" -d /tmp/v14-ipa
APP=/tmp/v14-ipa/Payload/SideStore.app
test -d "$APP"
find "$APP" -type f -size +32k -print0 | xargs -0 strings > /tmp/v14-embedded-strings.txt
for marker in \
  '[SS-V14-CREATE-LISTENER]' \
  '[SS-V14-PROTOCOL] MATRIX_START' \
  '[SS-V14-PROTOCOL] PAIRING_PASS' \
  '[SS-V14-PROTOCOL] DYNAMIC_CONNECT_PASS' \
  '[SS-V14-READY] TRANSPORT_PASS' \
  '[SS-V14-READY] SIGNER_PASS' \
  'FLAG: READY_TO_AUTOMATION' \
  '[EMP-V13-PAYLOAD]' \
  '[EMP-NONBLOCK] UDP socket nonblocking mode enabled'; do
  grep -Fq "$marker" /tmp/v14-embedded-strings.txt
done
! grep -Eq 'EMP-V13-HAIRPIN|EMP-HAIRPIN|EMP-NAT46|physical-interface bridge active' /tmp/v14-embedded-strings.txt

SHA="$(shasum -a 256 "$IPA" | awk '{print $1}')"
SIZE="$(stat -f '%z' "$IPA")"
{
  echo 'SideStore v14 Protocol Matrix'
  echo "builder_commit=$GITHUB_SHA"
  echo "sidestore_ref=$SIDESTORE_REF"
  echo "minimuxer_ref=$MINIMUXER_REF"
  echo "idevice_ref=$IDEVICE_REF"
  echo "emproxy_ref=$EMPROXY_REF"
  echo "ipa_size=$SIZE"
  echo "ipa_sha256=$SHA"
  echo 'pairing_strategy=fresh PairVerify and createListener for every route'
  echo 'dynamic_routes=virtual,en0-v6,en0-v4,utun,awdl,llw,loopback'
  echo 'package_transition=upstream pins -> local IDevice/EMProxy; final verifier=v13 only'
  echo 'ready_flag=embedded but emitted only after real refresh success'
  echo 'verification=PASS'
} | tee /tmp/SideStore-v14-verification.txt
