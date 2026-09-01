#!/usr/bin/env python3
"""Make v14 SwiftPM resolution deterministic without destroying resolved package state."""

from pathlib import Path
import sys

OLD = '''for attempt in 1 2 3; do
  rm -rf "$HOME/Library/Caches/org.swift.swiftpm"
  if (cd SideStore && xcodebuild -resolvePackageDependencies -project AltStore.xcodeproj -scheme SideStore); then
    break
  fi
  test "$attempt" -lt 3
  sleep 3
done
'''

NEW = '''OPENSSL_ARTIFACT="$HOME/Library/Caches/org.swift.swiftpm/artifacts/https___github_com_krzyzanowskim_OpenSSL_releases_download_3_6_2000_OpenSSL_xcframework_zip"
RESOLVED=0
for attempt in 1 2 3; do
  # Xcode runner images can contain a stale/incomplete binary-artifact
  # directory. Remove only that artifact. Deleting the complete SwiftPM cache
  # between retries invalidates the package graph and caused the old
  # RemotePairingKit product mismatch to mask the real state.
  rm -rf "$OPENSSL_ARTIFACT"
  rm -rf "$HOME/Library/Developer/Xcode/DerivedData"/*/SourcePackages/artifacts/openssl 2>/dev/null || true
  if (cd SideStore && xcodebuild \\
      -resolvePackageDependencies \\
      -disablePackageRepositoryCache \\
      -project AltStore.xcodeproj \\
      -scheme SideStore); then
    RESOLVED=1
    break
  fi
  test "$attempt" -lt 3
  sleep 3
done
test "$RESOLVED" -eq 1
'''


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_v14_spm_resolution.py <v14-ci-script>")

    path = Path(sys.argv[1])
    if not path.exists():
        die(f"missing v14 CI script: {path}")

    text = path.read_text()
    if NEW in text:
        print("v14 deterministic SwiftPM resolver already present and verified")
        return
    if text.count(OLD) != 1:
        die(f"expected exactly one legacy SwiftPM retry block, found {text.count(OLD)}")

    path.write_text(text.replace(OLD, NEW, 1))
    final = path.read_text()
    required = [
        "OPENSSL_ARTIFACT=",
        "-disablePackageRepositoryCache",
        'test "$RESOLVED" -eq 1',
    ]
    missing = [marker for marker in required if marker not in final]
    if missing:
        die(f"v14 SwiftPM resolver verification failed: {missing}")
    if 'rm -rf "$HOME/Library/Caches/org.swift.swiftpm"' in final:
        die("v14 SwiftPM resolver still destroys the complete package cache")

    print("v14 deterministic SwiftPM OpenSSL artifact resolver applied and verified")


if __name__ == "__main__":
    main()
