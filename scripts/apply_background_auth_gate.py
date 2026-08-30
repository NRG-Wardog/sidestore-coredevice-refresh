#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply_background_auth_gate.py <BackgroundRefreshAppsOperation.swift>")

p = Path(sys.argv[1])
s = p.read_text()
marker = "[SS-AUTH-GATE] background refresh authentication gate active"

if marker in s:
    required = [
        "AuthManager.shared.isAuthenticated",
        "Skipping background refresh before EMProxy/minimuxer work",
        "com.SideStore.Authentication",
    ]
    missing = [x for x in required if x not in s]
    if missing:
        raise SystemExit(f"Auth gate marker present but patch incomplete: {missing}")
    print("Background authentication gate already present and verified")
    raise SystemExit(0)

old = '''        guard !self.installedApps.isEmpty else {
            let error = RefreshError(.noInstalledApps)
            self.scheduleFinishedRefreshingNotification(for: .failure(error), delay: 0)
            throw error
        }

        if UserDefaults.standard.enableEMPforWireguard {
            try await startEMProxy()
        }
'''

new = '''        guard !self.installedApps.isEmpty else {
            let error = RefreshError(.noInstalledApps)
            self.scheduleFinishedRefreshingNotification(for: .failure(error), delay: 0)
            throw error
        }

        // Background refresh has no presenting view controller, so it cannot safely
        // complete an interactive Apple sign-in flow. Fail before starting EMProxy,
        // RemotePairing or signing work when credentials are absent. Foreground refresh
        // remains unchanged and can continue through the normal AuthFlowHandler UI.
        debugLog("[SS-AUTH-GATE] background refresh authentication gate active")
        guard AuthManager.shared.isAuthenticated else {
            let error = NSError(
                domain: "com.SideStore.Authentication",
                code: 1004,
                userInfo: [NSLocalizedDescriptionKey: "You are not signed in. Open SideStore and sign in before background refresh can run."]
            )
            debugLog("[SS-AUTH-GATE] Skipping background refresh before EMProxy/minimuxer work: authenticated=false")
            self.scheduleFinishedRefreshingNotification(for: .failure(error), delay: 0)
            throw error
        }

        if UserDefaults.standard.enableEMPforWireguard {
            try await startEMProxy()
        }
'''

if old not in s:
    raise SystemExit("Could not locate BackgroundRefreshAppsOperation preflight block")
s = s.replace(old, new, 1)
p.write_text(s)

patched = p.read_text()
required = [
    marker,
    "guard AuthManager.shared.isAuthenticated else",
    "Skipping background refresh before EMProxy/minimuxer work",
    "code: 1004",
]
missing = [x for x in required if x not in patched]
if missing:
    raise SystemExit(f"Background auth gate verification failed: {missing}")

print("Background authentication gate applied and verified")
