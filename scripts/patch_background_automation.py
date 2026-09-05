#!/usr/bin/env python3
"""Add native, recurring background refresh scheduling to SideStore."""

from __future__ import annotations

from pathlib import Path
import sys


TASK_IDENTIFIER = "com.SideStore.SideStore.automatic-refresh"
MARKER = "[AUTO_REFRESH] REGISTER_PASS"

SCHEDULE_MODEL = r'''
enum AutomaticRefreshSchedule
{
    static let dailyKey = "automaticRefreshDaily"
    static let frequencyKey = "automaticRefreshFrequency"
    static let weekdayKey = "automaticRefreshWeekday"
    static let minutesKey = "automaticRefreshMinutes"
    static let pendingKey = "automaticRefreshPendingDate"
    static let configurationKey = "automaticRefreshPendingConfiguration"

    static func frequency(defaults: UserDefaults = .standard) -> String {
        if let saved = defaults.string(forKey: frequencyKey),
           ["interval", "daily", "weekly"].contains(saved) { return saved }
        return defaults.bool(forKey: dailyKey) ? "daily" : "interval"
    }

    static func weekday(defaults: UserDefaults = .standard) -> Int {
        let saved = defaults.object(forKey: weekdayKey) as? Int ?? 2
        return (1...7).contains(saved) ? saved : 2
    }

    static func configuration(defaults: UserDefaults = .standard, calendar: Calendar = .current) -> String {
        "\(frequency(defaults: defaults)):\(weekday(defaults: defaults)):\(defaults.object(forKey: minutesKey) as? Int ?? 600):\(calendar.timeZone.identifier)"
    }

    static func requestDate(after now: Date, replacePending: Bool, defaults: UserDefaults = .standard,
                            calendar: Calendar = .current) -> Date {
        if !replacePending,
           defaults.string(forKey: configurationKey) == configuration(defaults: defaults, calendar: calendar),
           let pending = defaults.object(forKey: pendingKey) as? Date {
            return pending
        }
        return nextDate(after: now, defaults: defaults, calendar: calendar)
    }

    static func nextDate(after now: Date, defaults: UserDefaults = .standard,
                         calendar: Calendar = .current) -> Date
    {
        let frequency = Self.frequency(defaults: defaults)
        guard frequency != "interval" else {
            return now.addingTimeInterval(6 * 60 * 60)
        }
        let saved = defaults.object(forKey: minutesKey) as? Int ?? 600
        let minutes = (0..<1440).contains(saved) ? saved : 600
        var components = DateComponents(hour: minutes / 60, minute: minutes % 60, second: 0)
        if frequency == "weekly" { components.weekday = weekday(defaults: defaults) }
        return calendar.nextDate(after: now,
            matching: components,
            matchingPolicy: .nextTime, repeatedTimePolicy: .first)
            ?? now.addingTimeInterval(6 * 60 * 60)
    }
}
'''

SCHEDULE_UI = r'''
#if !os(tvOS)
private struct AutomaticRefreshScheduleView: View
{
    @State private var enabled = UserDefaults.standard.isBackgroundRefreshEnabled
    @State private var frequency = AutomaticRefreshSchedule.frequency()
    @AppStorage(AutomaticRefreshSchedule.weekdayKey) private var weekday = 2
    @AppStorage(AutomaticRefreshSchedule.minutesKey) private var minutes = 600
    @State private var errorMessage: String?
    @State private var pendingDate = UserDefaults.standard.object(forKey: AutomaticRefreshSchedule.pendingKey) as? Date
    @State private var backgroundAvailable = UIApplication.shared.backgroundRefreshStatus == .available
    @Environment(\.scenePhase) private var scenePhase

    private var time: Binding<Date> {
        Binding(get: {
            let value = (0..<1440).contains(minutes) ? minutes : 600
            return Calendar.current.date(bySettingHour: value / 60, minute: value % 60,
                                         second: 0, of: Date()) ?? Date()
        }, set: { date in
            let parts = Calendar.current.dateComponents([.hour, .minute], from: date)
            minutes = (parts.hour ?? 10) * 60 + (parts.minute ?? 0)
        })
    }

    var body: some View {
        Form {
            Section {
                Toggle("Background Refresh", isOn: $enabled)
                Picker("Schedule", selection: $frequency) {
                    Text("Every Six Hours").tag("interval")
                    Text("Daily").tag("daily")
                    Text("Weekly").tag("weekly")
                }
                .disabled(!enabled)
                if frequency == "weekly" {
                    Picker("Day", selection: $weekday) {
                        ForEach(0..<7, id: \.self) { offset in
                            let day = (Calendar.current.firstWeekday - 1 + offset) % 7 + 1
                            Text(Calendar.current.weekdaySymbols[day - 1]).tag(day)
                        }
                    }
                    .disabled(!enabled)
                    Label("Weekly refresh may run after free-account apps expire.", systemImage: "exclamationmark.triangle")
                        .foregroundColor(.secondary)
                }
                if frequency != "interval" {
                    DatePicker("Preferred Time", selection: time, displayedComponents: .hourAndMinute)
                        .disabled(!enabled)
                }
            }
            Section {
                if enabled, let pendingDate {
                    AutomaticRefreshPendingDateView(date: pendingDate)
                }
                if let errorMessage {
                    Text(errorMessage).foregroundColor(.red)
                    SwiftUI.Button("Retry Scheduling", action: save)
                }
                if !backgroundAvailable {
                    Label("Background App Refresh Unavailable", systemImage: "exclamationmark.triangle")
                    SwiftUI.Button("Open Settings") {
                        if let url = URL(string: UIApplication.openSettingsURLString) {
                            UIApplication.shared.open(url)
                        }
                    }
                }
            }
        }
        .navigationTitle("Refresh Schedule")
        .onAppear { reschedule(replacePending: false) }
        .onChange(of: enabled) { _ in save() }
        .onChange(of: frequency) { value in
            UserDefaults.standard.set(value, forKey: AutomaticRefreshSchedule.frequencyKey)
            save()
        }
        .onChange(of: weekday) { _ in save() }
        .onChange(of: minutes) { _ in save() }
        .onChange(of: scenePhase) { phase in
            if phase == .active {
                enabled = UserDefaults.standard.isBackgroundRefreshEnabled
                backgroundAvailable = UIApplication.shared.backgroundRefreshStatus == .available
            }
        }
    }

    private func save() {
        UserDefaults.standard.isBackgroundRefreshEnabled = enabled
        reschedule(replacePending: true)
    }

    private func reschedule(replacePending: Bool) {
        errorMessage = (UIApplication.shared.delegate as? AppDelegate)?
            .scheduleAutomaticRefresh(replacePending: replacePending)
        pendingDate = UserDefaults.standard.object(forKey: AutomaticRefreshSchedule.pendingKey) as? Date
    }
}

private struct AutomaticRefreshPendingDateView: View {
    let date: Date
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Earliest Eligible Refresh")
            Text(date, style: .date).foregroundColor(.secondary)
            Text(date, style: .time).foregroundColor(.secondary)
        }
    }
}
#endif
'''


def die(message: str) -> None:
    raise SystemExit(message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_app_delegate(sidestore: Path) -> None:
    path = sidestore / "AltStore" / "AppDelegate.swift"
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        verify_app_delegate(text)
        return

    text = replace_once(
        text,
        "import UserNotifications\n",
        "import UserNotifications\nimport BackgroundTasks\n",
        "BackgroundTasks import",
    )

    state = r'''
private final class AutomaticRefreshTaskState: @unchecked Sendable
{
    private let task: BGProcessingTask
    private let lock = NSLock()
    private var operation: BackgroundRefreshAppsOperation?
    private var didFinish = false

    init(task: BGProcessingTask)
    {
        self.task = task
    }

    var isFinished: Bool
    {
        self.lock.lock()
        defer { self.lock.unlock() }
        return self.didFinish
    }

    func attach(operation: BackgroundRefreshAppsOperation)
    {
        self.lock.lock()
        let shouldCancel = self.didFinish
        if !shouldCancel
        {
            self.operation = operation
        }
        self.lock.unlock()

        if shouldCancel
        {
            operation.cancel()
        }
    }

    func expire()
    {
        self.lock.lock()
        let operation = self.operation
        self.lock.unlock()

        operation?.cancel()
        self.finish(success: false)
    }

    func finish(success: Bool)
    {
        self.lock.lock()
        guard !self.didFinish else
        {
            self.lock.unlock()
            return
        }
        self.didFinish = true
        self.operation = nil
        self.lock.unlock()

        self.task.expirationHandler = nil
        self.task.setTaskCompleted(success: success)
    }
}

'''
    text = replace_once(
        text,
        "@UIApplicationMain\nfinal class AppDelegate",
        SCHEDULE_MODEL + state + "@UIApplicationMain\nfinal class AppDelegate",
        "automatic refresh task state",
    )

    text = replace_once(
        text,
        "    private var pendingImportIPAURL: URL?\n",
        "    private var pendingImportIPAURL: URL?\n"
        "    private var bootTask: Task<Void, Never>?\n",
        "boot task property",
    )

    text = replace_once(
        text,
        '''        Task.detached {
            debugLog("[AppDelegate] Boot sequence starting...")
            await AppBootManager.shared.performBootSequence()
            debugLog("[AppDelegate] Boot sequence completed.")
        }
''',
        '''        self.bootTask = Task.detached {
            debugLog("[AppDelegate] Boot sequence starting...")
            await AppBootManager.shared.performBootSequence()
            debugLog("[AppDelegate] Boot sequence completed.")
        }
''',
        "retain transport boot task",
    )

    text = replace_once(
        text,
        (
            '''    private func prepareForBackgroundFetch()
    {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { (success, error) in
            // no-op
        }
'''
            + "        \n"
            + '''        #if DEBUG && targetEnvironment(simulator)
        UIApplication.shared.registerForRemoteNotifications()
        #endif
    }
'''
        ),
        f'''    private static let automaticRefreshTaskIdentifier = "{TASK_IDENTIFIER}"

    private func prepareForBackgroundFetch()
    {{
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) {{ (success, error) in
            // no-op
        }}

        #if !os(tvOS)
        let registered = BGTaskScheduler.shared.register(
            forTaskWithIdentifier: Self.automaticRefreshTaskIdentifier,
            using: nil
        ) {{ [weak self] task in
            guard let task = task as? BGProcessingTask else
            {{
                task.setTaskCompleted(success: false)
                return
            }}
            self?.handleAutomaticRefresh(task)
        }}

        if registered
        {{
            debugLog("[AUTO_REFRESH] REGISTER_PASS identifier=\\(Self.automaticRefreshTaskIdentifier)")
            self.scheduleAutomaticRefresh()
        }}
        else
        {{
            debugLog("[AUTO_REFRESH] REGISTER_FAIL identifier=\\(Self.automaticRefreshTaskIdentifier)")
        }}
        #endif

        #if DEBUG && targetEnvironment(simulator)
        UIApplication.shared.registerForRemoteNotifications()
        #endif
    }}

    @discardableResult
    func scheduleAutomaticRefresh(replacePending: Bool = false) -> String?
    {{
        #if !os(tvOS)
        guard UserDefaults.standard.isBackgroundRefreshEnabled else
        {{
            BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: Self.automaticRefreshTaskIdentifier)
            UserDefaults.standard.removeObject(forKey: "automaticRefreshPendingDate")
            debugLog("[AUTO_REFRESH] SCHEDULE_SKIP reason=disabled")
            return nil
        }}

        // Preserve an already eligible request when the app enters the background again.
        let nextDate = AutomaticRefreshSchedule.requestDate(after: Date(), replacePending: replacePending)
        let request = BGProcessingTaskRequest(identifier: Self.automaticRefreshTaskIdentifier)
        request.earliestBeginDate = nextDate
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false

        do
        {{
            try BGTaskScheduler.shared.submit(request)
            UserDefaults.standard.set(nextDate, forKey: "automaticRefreshPendingDate")
            UserDefaults.standard.set(AutomaticRefreshSchedule.configuration(), forKey: AutomaticRefreshSchedule.configurationKey)
            debugLog("[AUTO_REFRESH] SCHEDULE_PASS earliest=\\(nextDate) network_required=true external_power_required=false")
        }}
        catch
        {{
            debugLog("[AUTO_REFRESH] SCHEDULE_FAIL error=\\(error.localizedDescription)")
            return error.localizedDescription
        }}
        #endif
        return nil
    }}

    #if !os(tvOS)
    private func handleAutomaticRefresh(_ task: BGProcessingTask)
    {{
        debugLog("[AUTO_REFRESH] TRIGGER source=bgprocessing")
        UserDefaults.standard.removeObject(forKey: AutomaticRefreshSchedule.pendingKey)
        self.scheduleAutomaticRefresh(replacePending: true)

        let state = AutomaticRefreshTaskState(task: task)
        task.expirationHandler = {{
            debugLog("[AUTO_REFRESH] EXPIRED")
            state.expire()
        }}

        guard UserDefaults.standard.isBackgroundRefreshEnabled else
        {{
            debugLog("[AUTO_REFRESH] COMPLETE success=true reason=disabled")
            state.finish(success: true)
            return
        }}

        Task {{ @MainActor [weak self] in
            guard let self else
            {{
                state.finish(success: false)
                return
            }}

            await self.bootTask?.value
            guard !state.isFinished else {{ return }}
            self.startAutomaticRefresh(state: state)
        }}
    }}

    private func startAutomaticRefresh(state: AutomaticRefreshTaskState)
    {{
        func beginRefresh()
        {{
            guard !state.isFinished else {{ return }}

            let context = DatabaseManager.shared.persistentContainer.newBackgroundContext()
            let installedApps = context.performAndWait {{
                InstalledApp.fetchAppsForBackgroundRefresh(in: context)
            }}
            let includesSideStore = context.performAndWait {{
                installedApps.contains {{ $0.bundleIdentifier == StoreApp.altstoreAppID }}
            }}

            debugLog("[AUTO_REFRESH] ELIGIBLE_APPS count=\\(installedApps.count) includes_sidestore=\\(includesSideStore)")
            guard !installedApps.isEmpty else
            {{
                debugLog("[AUTO_REFRESH] COMPLETE success=true reason=no_eligible_apps")
                state.finish(success: true)
                return
            }}

            do
            {{
                let operation = try AppManager.shared.backgroundRefresh(
                    installedApps,
                    presentsNotifications: true
                ) {{ result in
                    let success: Bool
                    let resultCount: Int
                    switch result
                    {{
                    case .success(let results):
                        resultCount = results.count
                        success = results.values.allSatisfy {{ nestedResult in
                            if case .success = nestedResult {{ return true }}
                            return false
                        }}
                    case .failure:
                        resultCount = 0
                        success = false
                    }}

                    debugLog("[AUTO_REFRESH] COMPLETE success=\\(success) result_count=\\(resultCount)")
                    state.finish(success: success)
                }}
                state.attach(operation: operation)
                debugLog("[AUTO_REFRESH] OPERATION_START")
            }}
            catch
            {{
                debugLog("[AUTO_REFRESH] OPERATION_START_FAIL error=\\(error.localizedDescription)")
                state.finish(success: false)
            }}
        }}

        if DatabaseManager.shared.isStarted
        {{
            beginRefresh()
        }}
        else
        {{
            DatabaseManager.shared.start {{ error in
                if let error
                {{
                    debugLog("[AUTO_REFRESH] DATABASE_START_FAIL error=\\(error.localizedDescription)")
                    state.finish(success: false)
                }}
                else
                {{
                    beginRefresh()
                }}
            }}
        }}
    }}
    #endif
''',
        "modern background scheduler",
    )

    text = replace_once(
        text,
        '''    func applicationDidEnterBackground(_ application: UIApplication)
    {
        // Make sure to update SceneDelegate.sceneDidEnterBackground() as well.
''',
        '''    func applicationDidEnterBackground(_ application: UIApplication)
    {
        self.scheduleAutomaticRefresh()
        // Make sure to update SceneDelegate.sceneDidEnterBackground() as well.
''',
        "application background reschedule",
    )

    path.write_text(text, encoding="utf-8")
    verify_app_delegate(text)


def verify_app_delegate(text: str) -> None:
    required = [
        SCHEDULE_MODEL,
        "import BackgroundTasks",
        MARKER,
        TASK_IDENTIFIER,
        "BGProcessingTaskRequest",
        "requiresNetworkConnectivity = true",
        "requiresExternalPower = false",
        "await self.bootTask?.value",
        "InstalledApp.fetchAppsForBackgroundRefresh",
        "state.attach(operation: operation)",
        "[AUTO_REFRESH] EXPIRED",
        "operation?.cancel()",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        die(f"AppDelegate automation verification failed: {missing}")


def patch_scene_delegate(sidestore: Path) -> None:
    path = sidestore / "AltStore" / "SceneDelegate.swift"
    text = path.read_text(encoding="utf-8")
    marker = "scheduleAutomaticRefresh()"
    if marker not in text:
        text = replace_once(
            text,
            '''        // Make sure to update AppDelegate.applicationDidEnterBackground() as well.

        guard let oneMonthAgo''',
            '''        // Make sure to update AppDelegate.applicationDidEnterBackground() as well.
        (UIApplication.shared.delegate as? AppDelegate)?.scheduleAutomaticRefresh()

        guard let oneMonthAgo''',
            "scene background reschedule",
        )
        path.write_text(text, encoding="utf-8")
    if marker not in text:
        die("SceneDelegate automatic refresh scheduling missing")


def patch_settings(sidestore: Path) -> None:
    path = sidestore / "AltStore" / "Settings" / "SettingsViewController.swift"
    text = path.read_text(encoding="utf-8")
    if "private struct AutomaticRefreshScheduleView" in text:
        if SCHEDULE_UI not in text:
            die("Settings contains an outdated schedule patch; use the pinned clean checkout")
        return
    text = replace_once(text,
        "        settingsHeaderFooterView.button.isHidden = true\n",
        "        settingsHeaderFooterView.button.isHidden = true\n"
        "        settingsHeaderFooterView.button.removeTarget(nil, action: nil, for: .primaryActionTriggered)\n",
        "clear reused footer actions")
    text = replace_once(text,
        "        UserDefaults.standard.isBackgroundRefreshEnabled = sender.isOn\n",
        "        UserDefaults.standard.isBackgroundRefreshEnabled = sender.isOn\n"
        "        (UIApplication.shared.delegate as? AppDelegate)?.scheduleAutomaticRefresh(replacePending: true)\n",
        "reschedule on toggle")
    anchor = '                settingsHeaderFooterView.secondaryLabel.text = NSLocalizedString("Enable Background Refresh'
    start = text.index(anchor)
    text = text[:start] + '''                #if !os(tvOS)
                settingsHeaderFooterView.button.setTitle(NSLocalizedString("Refresh Schedule", comment: ""), for: .normal)
                settingsHeaderFooterView.button.addTarget(self, action: #selector(openRefreshSchedule), for: .primaryActionTriggered)
                settingsHeaderFooterView.button.isHidden = false
                #endif
''' + text[start:]
    text = replace_once(text, "    @IBAction func toggleIsBackgroundRefreshEnabled(_ sender: UISwitch)",
        '''    #if !os(tvOS)
    @objc func openRefreshSchedule()
    {
        let controller = UIHostingController(rootView: AutomaticRefreshScheduleView())
        self.show(controller, sender: nil)
    }
    #endif

    @IBAction func toggleIsBackgroundRefreshEnabled(_ sender: UISwitch)''', "schedule navigation")
    path.write_text(text + SCHEDULE_UI, encoding="utf-8")


def patch_background_operation(sidestore: Path) -> None:
    path = (
        sidestore
        / "SideStore"
        / "Core"
        / "Operations"
        / "StandaloneOperations"
        / "BackgroundRefreshAppsOperation.swift"
    )
    text = path.read_text(encoding="utf-8")
    marker = "[AUTO_REFRESH] AUTH_PREFLIGHT_PASS"
    if marker not in text:
        text = replace_once(
            text,
            '''    private let refreshIdentifier: String = UUID().uuidString
    private var runningApplications: Set<String> = []
''',
            '''    private let refreshIdentifier: String = UUID().uuidString
    private var runningApplications: Set<String> = []
    private let refreshGroupLock = NSLock()
    private var activeRefreshGroup: RefreshGroup?
''',
            "refresh group state",
        )

        text = replace_once(
            text,
            '''    init(installedApps: [InstalledApp], context: OperationContext) throws {
        self.installedApps = installedApps
        try super.init(context: context)
    }
''',
            '''    init(installedApps: [InstalledApp], context: OperationContext) throws {
        self.installedApps = installedApps
        try super.init(context: context)
    }

    override func cancel() {
        super.cancel()
        self.refreshGroupLock.lock()
        let group = self.activeRefreshGroup
        self.refreshGroupLock.unlock()
        group?.cancel()
    }
''',
            "refresh cancellation",
        )

        text = replace_once(
            text,
            '''        guard !self.installedApps.isEmpty else {
            let error = OperationError.noInstalledApps
            self.scheduleFinishedRefreshingNotification(for: .failure(error), delay: 0)
            throw error
        }

        if UserDefaults.standard.enableEMPforWireguard {
''',
            '''        guard !self.installedApps.isEmpty else {
            let error = OperationError.noInstalledApps
            self.scheduleFinishedRefreshingNotification(for: .failure(error), delay: 0)
            throw error
        }

        guard AuthManager.shared.isAuthenticated else {
            let error = NSError(
                domain: "com.SideStore.Authentication",
                code: 1004,
                userInfo: [NSLocalizedDescriptionKey: "Open SideStore and sign in before automatic refresh can run."]
            )
            debugLog("[AUTO_REFRESH] AUTH_PREFLIGHT_FAIL authenticated=false")
            self.scheduleFinishedRefreshingNotification(for: .failure(error), delay: 0)
            throw error
        }
        debugLog("[AUTO_REFRESH] AUTH_PREFLIGHT_PASS")

        if UserDefaults.standard.enableEMPforWireguard {
''',
            "background authentication preflight",
        )

        text = replace_once(
            text,
            '''            let filteredApps = await dbContext.perform {
                return self.installedApps.filter { !self.runningApplications.contains($0.bundleIdentifier) }
            }
''',
            '''            guard !self.isCancelled else { throw OperationError.cancelled }

            let filteredApps = await dbContext.perform {
                return self.installedApps.filter { !self.runningApplications.contains($0.bundleIdentifier) }
            }
''',
            "pre-refresh cancellation check",
        )

        text = replace_once(
            text,
            '''            let group = AppManager.shared.refresh(apps, presentingViewController: nil)
            group.beginInstallationHandler = { [weak self] (installedApp) in
''',
            '''            let group = AppManager.shared.refresh(apps, presentingViewController: nil)
            self.refreshGroupLock.lock()
            self.activeRefreshGroup = group
            let shouldCancel = self.isCancelled
            self.refreshGroupLock.unlock()
            if shouldCancel { group.cancel() }

            group.beginInstallationHandler = { [weak self] (installedApp) in
''',
            "attach cancellable refresh group",
        )

        text = replace_once(
            text,
            '''            group.completionHandler = { (results) in
                self.setProgress(100)
                continuation.resume(returning: results)
            }
''',
            '''            group.completionHandler = { (results) in
                self.refreshGroupLock.lock()
                self.activeRefreshGroup = nil
                self.refreshGroupLock.unlock()
                self.setProgress(100)
                continuation.resume(returning: results)
            }
''',
            "clear refresh group",
        )

        path.write_text(text, encoding="utf-8")

    required = [
        marker,
        "override func cancel()",
        "activeRefreshGroup",
        "group?.cancel()",
        "guard !self.isCancelled",
        "AuthManager.shared.isAuthenticated",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        die(f"background operation verification failed: {missing}")


def patch_info_plist(sidestore: Path) -> None:
    path = sidestore / "AltStore" / "Info.plist"
    text = path.read_text(encoding="utf-8")
    if "BGTaskSchedulerPermittedIdentifiers" not in text:
        text = replace_once(
            text,
            "\t<key>UIBackgroundModes</key>\n",
            "\t<key>BGTaskSchedulerPermittedIdentifiers</key>\n"
            "\t<array>\n"
            f"\t\t<string>{TASK_IDENTIFIER}</string>\n"
            "\t</array>\n"
            "\t<key>UIBackgroundModes</key>\n",
            "permitted background task identifier",
        )
        path.write_text(text, encoding="utf-8")

    if TASK_IDENTIFIER not in text or "<string>processing</string>" not in text:
        die("Info.plist background processing configuration missing")


def verify(sidestore: Path) -> None:
    verify_app_delegate(
        (sidestore / "AltStore" / "AppDelegate.swift").read_text(encoding="utf-8")
    )
    scene = (sidestore / "AltStore" / "SceneDelegate.swift").read_text(encoding="utf-8")
    if "scheduleAutomaticRefresh()" not in scene:
        die("SceneDelegate verification failed")
    settings = (sidestore / "AltStore/Settings/SettingsViewController.swift").read_text(encoding="utf-8")
    if SCHEDULE_UI not in settings or "#selector(openRefreshSchedule)" not in settings:
        die("Settings schedule verification failed")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_background_automation.py <sidestore-root>")
    sidestore = Path(sys.argv[1]).resolve()
    if not (sidestore / "AltStore.xcodeproj").is_dir():
        die(f"invalid SideStore checkout: {sidestore}")

    patch_app_delegate(sidestore)
    patch_scene_delegate(sidestore)
    patch_background_operation(sidestore)
    patch_info_plist(sidestore)
    patch_settings(sidestore)
    verify(sidestore)
    print("V30 background automation patch applied and verified")


if __name__ == "__main__":
    main()
