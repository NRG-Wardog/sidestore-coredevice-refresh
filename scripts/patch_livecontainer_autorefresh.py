#!/usr/bin/env python3
"""Integrate host-owned scheduled refresh into pinned LiveContainer sources.

The embedded SideStore process cannot own BGTaskScheduler registration: iOS
registers background tasks against the containing LiveContainer application.
This patch keeps the existing LiveProcess/XPC refresh bridge and moves the
task registration, schedule UI, and persisted result state into the host.
"""

from __future__ import annotations

from pathlib import Path
import sys


TASK_ID = "com.kdt.livecontainer.sidestore.automatic-refresh"
MARKER = "[LIVE_CONTAINER_REFRESH] REGISTER_PASS"


def die(message: str) -> None:
    raise SystemExit(f"patch_livecontainer_autorefresh: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0:
        die(f"missing {label}")
    if count > 1:
        die(f"ambiguous {label}: {count} matches")
    return text.replace(old, new, 1)


BRIDGE = r'''

/// Narrow host-facing bridge. The refresh still executes in the embedded
/// SideStore through the existing LiveProcess/XPC path.
@available(iOS 17.0, *)
public enum LiveContainerRefreshBridge {
    public static func refreshAllApps() async throws {
        try await RefreshHandler.shared.startRefresh(
            identifier: "LiveContainerScheduledRefresh",
            mangledName: "9SideStore20RefreshAllAppsIntentV"
        )
    }
}
'''


HOST_SCHEDULER = f'''

private enum LiveContainerAutoRefreshScheduler {{
    static let taskIdentifier = "{TASK_ID}"
    static let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") ?? .standard
    static let enabledKey = "liveContainerAutoRefreshEnabled"
    static let frequencyKey = "liveContainerAutoRefreshFrequency"
    static let weekdayKey = "liveContainerAutoRefreshWeekday"
    static let minutesKey = "liveContainerAutoRefreshMinutes"
    static let lastResultKey = "liveContainerAutoRefreshLastResult"
    static let lastDateKey = "liveContainerAutoRefreshLastDate"
    static let historyKey = "liveContainerAutoRefreshHistory"

    private static func record(source: String, result: String, detail: String = "") {{
        let entry: [String: String] = [
            "date": ISO8601DateFormatter().string(from: Date()),
            "source": source,
            "result": result,
            "detail": String(detail.prefix(300))
        ]
        var history = defaults.array(forKey: historyKey) as? [[String: String]] ?? []
        history.insert(entry, at: 0)
        defaults.set(Array(history.prefix(50)), forKey: historyKey)
        defaults.set(result, forKey: lastResultKey)
        defaults.set(Date(), forKey: lastDateKey)
    }}

    static func register() {{
        BGTaskScheduler.shared.register(forTaskWithIdentifier: taskIdentifier, using: nil) {{ task in
            guard let task = task as? BGProcessingTask else {{ return }}
            let operation = Task {{
                do {{
                    print("[LIVE_CONTAINER_REFRESH] TASK_START")
                    try await LiveContainerRefreshBridge.refreshAllApps()
                    record(source: "scheduled", result: "success")
                    print("[LIVE_CONTAINER_REFRESH] TASK_COMPLETE success=true")
                    task.setTaskCompleted(success: true)
                }} catch {{
                    record(source: "scheduled", result: "failure", detail: error.localizedDescription)
                    print("[LIVE_CONTAINER_REFRESH] TASK_COMPLETE success=false error=\\(error.localizedDescription)")
                    task.setTaskCompleted(success: false)
                }}
            }}
            task.expirationHandler = {{
                operation.cancel()
                print("[LIVE_CONTAINER_REFRESH] TASK_EXPIRED")
            }}
        }}
        print("{MARKER}")
    }}

    static func runNow() {{
        Task {{
            do {{
                print("[LIVE_CONTAINER_REFRESH] MANUAL_START")
                try await LiveContainerRefreshBridge.refreshAllApps()
                record(source: "manual", result: "success")
                print("[LIVE_CONTAINER_REFRESH] MANUAL_COMPLETE success=true")
            }} catch {{
                record(source: "manual", result: "failure", detail: error.localizedDescription)
                print("[LIVE_CONTAINER_REFRESH] MANUAL_COMPLETE success=false error=\\(error.localizedDescription)")
            }}
        }}
    }}

    static func schedule() {{
        BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: taskIdentifier)
        guard defaults.bool(forKey: enabledKey) else {{
            print("[LIVE_CONTAINER_REFRESH] SCHEDULE_DISABLED")
            return
        }}
        let request = BGProcessingTaskRequest(identifier: taskIdentifier)
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false
        request.earliestBeginDate = nextDate(after: Date())
        do {{
            try BGTaskScheduler.shared.submit(request)
            print("[LIVE_CONTAINER_REFRESH] SCHEDULE_PASS date=\\(request.earliestBeginDate?.timeIntervalSince1970 ?? 0)")
        }} catch {{
            print("[LIVE_CONTAINER_REFRESH] SCHEDULE_FAIL error=\\(error.localizedDescription)")
        }}
    }}

    private static func nextDate(after now: Date) -> Date {{
        let frequency = defaults.string(forKey: frequencyKey) ?? "interval"
        if frequency == "interval" {{ return now.addingTimeInterval(6 * 60 * 60) }}
        let minutes = max(0, min(1439, defaults.object(forKey: minutesKey) as? Int ?? 600))
        var components = DateComponents(hour: minutes / 60, minute: minutes % 60, second: 0)
        if frequency == "weekly" {{
            components.weekday = max(1, min(7, defaults.object(forKey: weekdayKey) as? Int ?? 2))
        }}
        return Calendar.autoupdatingCurrent.nextDate(after: now, matching: components,
            matchingPolicy: .nextTime, repeatedTimePolicy: .first)
            ?? now.addingTimeInterval(6 * 60 * 60)
    }}
}}
'''


SETTINGS_VIEW = r'''
import SwiftUI

struct LCEmbeddedSideStoreRefreshView: View {
    private let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") ?? .standard
    @State private var history: [[String: String]] = []
    @AppStorage("liveContainerAutoRefreshEnabled", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var enabled = false
    @AppStorage("liveContainerAutoRefreshFrequency", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var frequency = "interval"
    @AppStorage("liveContainerAutoRefreshWeekday", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var weekday = 2
    @AppStorage("liveContainerAutoRefreshMinutes", store: UserDefaults(suiteName: "group.com.SideStore.SideStore")) private var minutes = 600

    private var time: Binding<Date> {
        Binding(get: {
            let calendar = Calendar.autoupdatingCurrent
            return calendar.date(bySettingHour: minutes / 60, minute: minutes % 60, second: 0, of: Date()) ?? Date()
        }, set: { value in
            let parts = Calendar.autoupdatingCurrent.dateComponents([.hour, .minute], from: value)
            minutes = (parts.hour ?? 10) * 60 + (parts.minute ?? 0)
            notifyScheduleChanged()
        })
    }

    var body: some View {
        Form {
            Section {
                Toggle("Scheduled refresh", isOn: Binding(get: { enabled }, set: {
                    enabled = $0
                    notifyScheduleChanged()
                }))
                Button("Refresh SideStore now", action: notifyManualRefresh)
                Picker("Frequency", selection: Binding(get: { frequency }, set: {
                    frequency = $0
                    notifyScheduleChanged()
                })) {
                    Text("Every six hours").tag("interval")
                    Text("Daily").tag("daily")
                    Text("Weekly").tag("weekly")
                }.disabled(!enabled)
                if frequency == "weekly" {
                    Picker("Weekday", selection: Binding(get: { weekday }, set: {
                        weekday = $0
                        notifyScheduleChanged()
                    })) {
                        ForEach(1...7, id: \.self) { day in
                            Text(Calendar.autoupdatingCurrent.weekdaySymbols[day - 1]).tag(day)
                        }
                    }.disabled(!enabled)
                }
                if frequency != "interval" {
                    DatePicker("Preferred time (local)", selection: time, displayedComponents: .hourAndMinute)
                        .disabled(!enabled)
                }
                Text("The host app owns this schedule. iOS may start it later than the requested time.")
                    .font(.caption).foregroundColor(.secondary)
            }
            if let result = defaults.string(forKey: "liveContainerAutoRefreshLastResult") {
                Section("Last result") {
                    Text(result.capitalized)
                    if let date = defaults.object(forKey: "liveContainerAutoRefreshLastDate") as? Date {
                        Text(date.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundColor(.secondary)
                    }
                }
            }
            Section("History") {
                if history.isEmpty {
                    Text("No refreshes recorded")
                        .foregroundColor(.secondary)
                } else {
                    ForEach(Array(history.prefix(20).enumerated()), id: \.offset) { _, entry in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(entry["source"]?.capitalized ?? "Unknown") - \(entry["result"]?.capitalized ?? "Unknown")")
                            Text(entry["date"] ?? "")
                                .font(.caption)
                                .foregroundColor(.secondary)
                            if let detail = entry["detail"], !detail.isEmpty {
                                Text(detail).font(.caption2).foregroundColor(.secondary)
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("SideStore refresh")
        .onAppear { history = defaults.array(forKey: "liveContainerAutoRefreshHistory") as? [[String: String]] ?? [] }
    }

    private func notifyScheduleChanged() {
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshScheduleChanged"), object: nil)
    }

    private func notifyManualRefresh() {
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil)
    }
}
'''


def patch_support(root: Path) -> None:
    path = root / "SideStoreSupport" / "SideStore.swift"
    text = path.read_text(encoding="utf-8")
    if "LiveContainerRefreshBridge" not in text:
        text = replace_once(text, "\nclass RefreshHandler: NSObject, RefreshServer {", BRIDGE + "\nclass RefreshHandler: NSObject, RefreshServer {", "refresh bridge insertion")
        path.write_text(text, encoding="utf-8")


def patch_host_delegate(root: Path) -> None:
    path = root / "LiveContainerSwiftUI" / "App" / "AppDelegate.swift"
    text = path.read_text(encoding="utf-8")
    if "import BackgroundTasks" not in text:
        text = replace_once(text, "import Intents\n", "import Intents\nimport BackgroundTasks\nimport SideStoreSupport\n", "host imports")
    if "LiveContainerAutoRefreshScheduler.register()" not in text:
        text = replace_once(text, "        application.shortcutItems = nil\n", "        application.shortcutItems = nil\n        LiveContainerAutoRefreshScheduler.register()\n        LiveContainerAutoRefreshScheduler.schedule()\n        NotificationCenter.default.addObserver(forName: Notification.Name(\"LiveContainerAutoRefreshScheduleChanged\"), object: nil, queue: .main) { _ in\n            LiveContainerAutoRefreshScheduler.schedule()\n        }\n        NotificationCenter.default.addObserver(forName: Notification.Name(\"LiveContainerAutoRefreshRunNow\"), object: nil, queue: .main) { _ in\n            LiveContainerAutoRefreshScheduler.runNow()\n        }\n", "host scheduler startup")
        text = replace_once(text, "    func application(_ application: UIApplication, configurationForConnecting", "    func applicationDidEnterBackground(_ application: UIApplication) {\n        LiveContainerAutoRefreshScheduler.schedule()\n    }\n\n    func application(_ application: UIApplication, configurationForConnecting", "host background reschedule")
        text = replace_once(text, "class SceneDelegate:", HOST_SCHEDULER + "\nclass SceneDelegate:", "host scheduler implementation")
    path.write_text(text, encoding="utf-8")


def patch_host_info(root: Path) -> None:
    path = root / "LiveContainer" / "Info.plist"
    text = path.read_text(encoding="utf-8")
    key = "<key>BGTaskSchedulerPermittedIdentifiers</key>"
    if TASK_ID not in text:
        insertion = f"\t{key}\n\t<array>\n\t\t<string>{TASK_ID}</string>\n\t</array>\n"
        closing = "</dict>\n</plist>"
        text = replace_once(text, closing, insertion + closing, "host background task plist insertion")
        path.write_text(text, encoding="utf-8")


def patch_project(root: Path) -> None:
    path = root / "LiveContainer.xcodeproj" / "project.pbxproj"
    text = path.read_text(encoding="utf-8")
    build_marker = "173545AF2E2C7913001B3B4C /* SideStoreSupport.framework in Embed Frameworks */"
    if "SideStoreSupport.framework in Frameworks" not in text:
        text = replace_once(text, "/* Begin PBXBuildFile section */\n", "/* Begin PBXBuildFile section */\n\tA17ECAFE2DCA000000000001 = {isa = PBXBuildFile; fileRef = 173545A82E2C7913001B3B4C /* SideStoreSupport.framework */; };\n", "host framework link build file")
        text = replace_once(text, "17554B6A2DA165D8004C6D90 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t);", "17554B6A2DA165D8004C6D90 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t\tA17ECAFE2DCA000000000001 /* SideStoreSupport.framework in Frameworks */,\n\t\t\t);", "host framework link phase")
        path.write_text(text, encoding="utf-8")


def patch_settings(root: Path) -> None:
    settings = root / "LiveContainerSwiftUI" / "Views" / "Settings" / "LCSettingsView.swift"
    text = settings.read_text(encoding="utf-8")
    link = '''                if store == .SideStore {
                    Section {
                        NavigationLink {
                            LCEmbeddedSideStoreRefreshView()
                        } label: {
                            Text("SideStore scheduled refresh")
                        }
                    }
                }
'''
    if "LCEmbeddedSideStoreRefreshView" not in text:
        text = replace_once(text, "            Form {\n", "            Form {\n" + link, "host refresh settings link")
        settings.write_text(text, encoding="utf-8")

    view = root / "LiveContainerSwiftUI" / "Views" / "Settings" / "LCEmbeddedSideStoreRefreshView.swift"
    if not view.exists():
        view.write_text(SETTINGS_VIEW.lstrip(), encoding="utf-8")


def verify(root: Path) -> None:
    delegate = (root / "LiveContainerSwiftUI" / "App" / "AppDelegate.swift").read_text(encoding="utf-8")
    support = (root / "SideStoreSupport" / "SideStore.swift").read_text(encoding="utf-8")
    settings = (root / "LiveContainerSwiftUI" / "Views" / "Settings" / "LCEmbeddedSideStoreRefreshView.swift").read_text(encoding="utf-8")
    info = (root / "LiveContainer" / "Info.plist").read_text(encoding="utf-8")
    project = (root / "LiveContainer.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
    required = [
        (delegate, "BGTaskScheduler.shared.register", "host registration"),
        (delegate, "LiveContainerRefreshBridge.refreshAllApps", "host refresh bridge"),
        (delegate, "requiresNetworkConnectivity = true", "network requirement"),
        (delegate, "TASK_COMPLETE success=true", "success diagnostics"),
        (delegate, "LiveContainerAutoRefreshScheduler.runNow()", "manual refresh dispatch"),
        (support, "public enum LiveContainerRefreshBridge", "public bridge"),
        (support, "RefreshHandler.shared.startRefresh", "embedded SideStore refresh"),
        (settings, "liveContainerAutoRefreshFrequency", "schedule persistence"),
        (settings, "Refresh SideStore now", "manual refresh control"),
        (settings, "LiveContainerAutoRefreshRunNow", "manual refresh notification"),
        (delegate, "record(source: \"scheduled\"", "scheduled history"),
        (delegate, "record(source: \"manual\"", "manual history"),
        (delegate, "liveContainerAutoRefreshHistory", "history persistence"),
        (delegate, "MANUAL_COMPLETE", "manual diagnostics"),
        (info, TASK_ID, "permitted task identifier"),
        (project, "A17ECAFE2DCA000000000001", "host framework link"),
    ]
    missing = [label for content, needle, label in required if needle not in content]
    if missing:
        die("verification failed: " + ", ".join(missing))


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_livecontainer_autorefresh.py <livecontainer-root>")
    root = Path(sys.argv[1]).resolve()
    if not (root / "LiveContainer.xcodeproj").exists():
        die(f"not a LiveContainer checkout: {root}")
    patch_support(root)
    patch_host_delegate(root)
    patch_host_info(root)
    patch_project(root)
    patch_settings(root)
    verify(root)
    print("LiveContainer host auto-refresh patch applied and verified")


if __name__ == "__main__":
    main()
