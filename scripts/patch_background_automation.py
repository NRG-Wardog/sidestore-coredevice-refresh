#!/usr/bin/env python3
"""Add native, recurring background refresh scheduling to SideStore."""

from __future__ import annotations

from pathlib import Path
import sys


TASK_IDENTIFIER = "com.SideStore.SideStore.automatic-refresh"
MARKER = "[AUTO_REFRESH] REGISTER_PASS"

SCHEDULE_MODEL = r'''
enum AutomaticRefreshEvent: String, Codable {
    case scheduled, scheduleFailed, disabled, started, completed, failed, expired, skipped, notificationFailed

    var title: String {
        switch self {
        case .scheduled: return "Schedule accepted"
        case .scheduleFailed: return "Scheduling failed"
        case .disabled: return "Schedule disabled"
        case .started: return "Refresh started"
        case .completed: return "Refresh completed"
        case .failed: return "Refresh failed"
        case .expired: return "Background time expired"
        case .skipped: return "Refresh skipped"
        case .notificationFailed: return "Start alert failed"
        }
    }

    var symbol: String {
        switch self {
        case .scheduled: return "calendar"
        case .disabled: return "pause.circle"
        case .started: return "arrow.clockwise"
        case .completed: return "checkmark.circle"
        case .failed, .scheduleFailed, .notificationFailed: return "exclamationmark.circle"
        case .expired: return "hourglass"
        case .skipped: return "minus.circle"
        }
    }

    var isTerminal: Bool { [.completed, .failed, .expired, .skipped].contains(self) }
}

enum RefreshHistorySource: String, Codable {
    case manual = "Manual"
    case scheduled = "Scheduled"
}

struct AutomaticRefreshHistoryEntry: Codable, Identifiable {
    let id: UUID
    let date: Date
    let event: AutomaticRefreshEvent
    let runID: UUID?
    let detail: String
    let eligibleDate: Date?
    let source: RefreshHistorySource?

    var sourceTitle: String { (source ?? .scheduled).rawValue }
}

enum AutomaticRefreshHistory {
    static let key = "automaticRefreshHistory"
    static let changed = Notification.Name("AutomaticRefreshHistoryChanged")
    private static let lock = NSLock()

    private static func read(_ defaults: UserDefaults) -> [AutomaticRefreshHistoryEntry] {
        guard let data = defaults.data(forKey: key),
              let entries = try? JSONDecoder().decode([AutomaticRefreshHistoryEntry].self, from: data) else { return [] }
        return Array(entries.prefix(100))
    }

    static func load(defaults: UserDefaults = .standard) -> [AutomaticRefreshHistoryEntry] {
        lock.lock()
        defer { lock.unlock() }
        return read(defaults)
    }

    static func record(_ event: AutomaticRefreshEvent, runID: UUID? = nil, detail: String = "",
                       eligibleDate: Date? = nil, now: Date = Date(), defaults: UserDefaults = .standard,
                       source: RefreshHistorySource = .scheduled) {
        lock.lock()
        var entries = read(defaults)
        if let runID, event.isTerminal,
           entries.contains(where: { $0.runID == runID && $0.event.isTerminal }) {
            lock.unlock()
            return
        }
        entries.insert(AutomaticRefreshHistoryEntry(id: UUID(), date: now, event: event,
            runID: runID, detail: String(detail.prefix(500)), eligibleDate: eligibleDate, source: source), at: 0)
        if let data = try? JSONEncoder().encode(Array(entries.prefix(100))) {
            defaults.set(data, forKey: key)
        }
        lock.unlock()
        NotificationCenter.default.post(name: changed, object: nil)
    }

    static func clear(defaults: UserDefaults = .standard) {
        lock.lock()
        defaults.removeObject(forKey: key)
        lock.unlock()
        NotificationCenter.default.post(name: changed, object: nil)
    }

    static func finishManual<T>(runID: UUID, expected: Set<String>, results: [String: Result<T, Error>],
                                defaults: UserDefaults = .standard) {
        if expected.isEmpty {
            record(.skipped, runID: runID, detail: "No apps selected.", defaults: defaults, source: .manual)
            return
        }
        var succeeded = 0
        var firstError: String?
        for identifier in expected {
            switch results[identifier] {
            case .success?: succeeded += 1
            case .failure(let error)?: firstError = firstError ?? error.localizedDescription
            case nil: firstError = firstError ?? "No completion result received for an app."
            }
        }
        record(succeeded == expected.count ? .completed : .failed, runID: runID,
               detail: "Refreshed \(succeeded) of \(expected.count) apps." + (firstError.map { " " + $0 } ?? ""),
               defaults: defaults, source: .manual)
    }
}

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
    @State private var history = AutomaticRefreshHistory.load()
    @State private var showingClearHistory = false
    @State private var notificationStatus = UNAuthorizationStatus.notDetermined
    @State private var notificationError: String?
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
            Section("Start Alert") {
                HStack {
                    Text("Notification Permission")
                    Spacer()
                    Text(notificationStatus == .authorized ? "Enabled" :
                         notificationStatus == .provisional ? "Quiet" : "Off")
                        .foregroundColor(.secondary)
                }
                if notificationStatus == .notDetermined {
                    SwiftUI.Button("Allow Notifications") {
                        Task {
                            do {
                                _ = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
                                await updateNotificationStatus()
                                notificationError = nil
                            } catch { notificationError = error.localizedDescription }
                        }
                    }
                } else if notificationStatus != .authorized {
                    SwiftUI.Button("Notification Settings") {
                        if let url = URL(string: UIApplication.openSettingsURLString) {
                            UIApplication.shared.open(url)
                        }
                    }
                }
                if let notificationError {
                    Text(notificationError).foregroundColor(.red)
                }
            }
            Section {
                if history.isEmpty {
                    Text("No refresh history").foregroundColor(.secondary)
                }
                ForEach(history) { entry in
                    VStack(alignment: .leading, spacing: 4) {
                        Label(entry.event.title, systemImage: entry.event.symbol)
                        Text(entry.sourceTitle)
                            .font(.caption.weight(.semibold))
                            .foregroundColor(entry.source == .manual ? .blue : .secondary)
                        Text(entry.date, format: .dateTime.year().month().day().hour().minute().second())
                            .font(.caption).foregroundColor(.secondary)
                        if let date = entry.eligibleDate {
                            Text("Eligible after \(date.formatted(date: .abbreviated, time: .shortened))")
                                .font(.caption).foregroundColor(.secondary)
                        }
                        if !entry.detail.isEmpty {
                            Text(entry.detail).font(.caption).foregroundColor(.secondary)
                        }
                    }
                    .padding(.vertical, 2)
                }
            } header: {
                HStack {
                    Text("History")
                    Spacer()
                    SwiftUI.Button { showingClearHistory = true } label: {
                        Image(systemName: "trash")
                    }
                    .accessibilityLabel("Clear History")
                    .disabled(history.isEmpty)
                }
            }
        }
        .navigationTitle("Refresh Schedule")
        .task { await updateNotificationStatus() }
        .onReceive(NotificationCenter.default.publisher(for: AutomaticRefreshHistory.changed).receive(on: RunLoop.main)) { _ in
            history = AutomaticRefreshHistory.load()
            pendingDate = UserDefaults.standard.object(forKey: AutomaticRefreshSchedule.pendingKey) as? Date
        }
        .confirmationDialog("Clear refresh history?", isPresented: $showingClearHistory, titleVisibility: .visible) {
            SwiftUI.Button("Clear History", role: .destructive) { AutomaticRefreshHistory.clear() }
        }
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
                history = AutomaticRefreshHistory.load()
                Task { await updateNotificationStatus() }
            }
        }
    }

    private func save() {
        UserDefaults.standard.isBackgroundRefreshEnabled = enabled
        reschedule(replacePending: true)
    }

    private func updateNotificationStatus() async {
        notificationStatus = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
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
    let runID = UUID()
    private let task: BGProcessingTask
    private let lock = NSLock()
    private var operation: BackgroundRefreshAppsOperation?
    private var didFinish = false
    private var didBegin = false

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

    func begin() -> Bool
    {
        self.lock.lock()
        defer { self.lock.unlock() }
        guard !self.didFinish && !self.didBegin else { return false }
        self.didBegin = true
        AutomaticRefreshHistory.record(.started, runID: self.runID)
        return true
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
        self.finish(success: false, event: .expired, detail: "iOS ended the background execution window.", cancelOperation: true)
    }

    func finish(success: Bool, event: AutomaticRefreshEvent? = nil, detail: String = "", cancelOperation: Bool = false)
    {
        self.lock.lock()
        guard !self.didFinish else
        {
            self.lock.unlock()
            return
        }
        self.didFinish = true
        let operation = self.operation
        self.operation = nil
        self.lock.unlock()

        AutomaticRefreshHistory.record(event ?? (success ? .completed : .failed), runID: self.runID, detail: detail)
        if cancelOperation { operation?.cancel() }
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
            AutomaticRefreshHistory.record(.scheduleFailed, detail: "Background task registration failed.")
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
            let hadPendingRequest = UserDefaults.standard.object(forKey: AutomaticRefreshSchedule.pendingKey) != nil
            BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: Self.automaticRefreshTaskIdentifier)
            UserDefaults.standard.removeObject(forKey: "automaticRefreshPendingDate")
            if hadPendingRequest {{ AutomaticRefreshHistory.record(.disabled) }}
            debugLog("[AUTO_REFRESH] SCHEDULE_SKIP reason=disabled")
            return nil
        }}

        // Preserve an already eligible request when the app enters the background again.
        let nextDate = AutomaticRefreshSchedule.requestDate(after: Date(), replacePending: replacePending)
        let previousDate = UserDefaults.standard.object(forKey: AutomaticRefreshSchedule.pendingKey) as? Date
        let request = BGProcessingTaskRequest(identifier: Self.automaticRefreshTaskIdentifier)
        request.earliestBeginDate = nextDate
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false

        do
        {{
            try BGTaskScheduler.shared.submit(request)
            UserDefaults.standard.set(nextDate, forKey: "automaticRefreshPendingDate")
            UserDefaults.standard.set(AutomaticRefreshSchedule.configuration(), forKey: AutomaticRefreshSchedule.configurationKey)
            if previousDate != nextDate || !AutomaticRefreshHistory.load().contains(where: {{ $0.event == .scheduled && $0.eligibleDate == nextDate }}) {{
                AutomaticRefreshHistory.record(.scheduled, eligibleDate: nextDate)
            }}
            debugLog("[AUTO_REFRESH] SCHEDULE_PASS earliest=\\(nextDate) network_required=true external_power_required=false")
        }}
        catch
        {{
            debugLog("[AUTO_REFRESH] SCHEDULE_FAIL error=\\(error.localizedDescription)")
            AutomaticRefreshHistory.record(.scheduleFailed, detail: error.localizedDescription)
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
            state.finish(success: true, event: .skipped, detail: "Background refresh is disabled.")
            return
        }}

        guard state.begin() else {{ return }}
        self.notifyAutomaticRefreshStarted(runID: state.runID)

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

    private func notifyAutomaticRefreshStarted(runID: UUID)
    {{
        let content = UNMutableNotificationContent()
        content.title = "SideStore refresh started"
        content.body = "iOS has started your scheduled background refresh."
        content.sound = .default
        let request = UNNotificationRequest(identifier: "automatic-refresh-start-" + runID.uuidString,
                                            content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) {{ error in
            if let error {{
                AutomaticRefreshHistory.record(.notificationFailed, runID: runID, detail: error.localizedDescription)
            }}
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
                state.finish(success: true, event: .skipped, detail: "No apps are eligible for refresh.")
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
                    var failureDetail = ""
                    switch result
                    {{
                    case .success(let results):
                        resultCount = results.count
                        success = results.values.allSatisfy {{ nestedResult in
                            switch nestedResult {{
                            case .success: return true
                            case .failure(let error):
                                failureDetail = error.localizedDescription
                                return false
                            }}
                        }}
                    case .failure(let error):
                        resultCount = 0
                        success = false
                        failureDetail = error.localizedDescription
                    }}

                    debugLog("[AUTO_REFRESH] COMPLETE success=\\(success) result_count=\\(resultCount)")
                    state.finish(success: success,
                        event: success && resultCount == 0 ? .skipped : nil,
                        detail: success ? "Apps refreshed: \\(resultCount)" : failureDetail)
                }}
                state.attach(operation: operation)
                debugLog("[AUTO_REFRESH] OPERATION_START")
            }}
            catch
            {{
                debugLog("[AUTO_REFRESH] OPERATION_START_FAIL error=\\(error.localizedDescription)")
                state.finish(success: false, detail: error.localizedDescription)
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
                    state.finish(success: false, detail: error.localizedDescription)
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
    text = replace_once(text, "import SwiftUI\n", "import SwiftUI\nimport UserNotifications\n", "notification settings import")
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
            '''            let group = AppManager.shared.refresh(apps, presentingViewController: nil, recordManualHistory: false)
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


def patch_manual_refresh(sidestore: Path) -> None:
    path = sidestore / "AltStore/Managing Apps/AppManager.swift"
    text = path.read_text(encoding="utf-8")
    start = text.index("    func refresh(_ installedApps:")
    end = text.index("    func activate(", start)
    section = text[start:end]
    marker = "let manualHistoryRunID"
    if marker not in section:
        section = replace_once(section, "group: RefreshGroup? = nil) -> RefreshGroup",
                               "group: RefreshGroup? = nil, recordManualHistory: Bool = true) -> RefreshGroup",
                               "manual history parameter")
        section = replace_once(section, "        actualGroup.activeTask = Task.detached {", '''        let manualHistoryRunID = recordManualHistory ? UUID() : nil
        let historyAppIDs = Set(installedApps.map { $0.bundleIdentifier })
        if let runID = manualHistoryRunID {
            AutomaticRefreshHistory.record(.started, runID: runID,
                detail: "Refreshing \\(historyAppIDs.count) apps.", source: .manual)
        }
        actualGroup.activeTask = Task.detached {''', "manual history start")
        section = replace_once(section,
            "                try await self.pipelineRunner.perform(installedApps.map { .refresh($0) }, handler: pipelineHandler, group: actualGroup)",
'''                try await self.pipelineRunner.perform(installedApps.map { .refresh($0) }, handler: pipelineHandler, group: actualGroup)
                if let runID = manualHistoryRunID {
                    AutomaticRefreshHistory.finishManual(runID: runID, expected: historyAppIDs, results: actualGroup.results)
                }''', "manual history results")
        section = replace_once(section, "                actualGroup.context.error = error",
'''                if let runID = manualHistoryRunID {
                    AutomaticRefreshHistory.record(.failed, runID: runID,
                        detail: error.localizedDescription, source: .manual)
                }
                actualGroup.context.error = error''', "manual history failure")
        text = text[:start] + section + text[end:]
        path.write_text(text, encoding="utf-8")
    for required in (marker, "recordManualHistory: Bool = true", "AutomaticRefreshHistory.finishManual", "source: .manual"):
        if required not in section:
            die(f"manual history verification failed: {required}")


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
    patch_manual_refresh(sidestore)
    patch_info_plist(sidestore)
    patch_settings(sidestore)
    verify(sidestore)
    print("V30 background automation patch applied and verified")


if __name__ == "__main__":
    main()
