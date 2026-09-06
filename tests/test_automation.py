"""Exercise generated Swift and apply the patch twice to pinned upstream files."""
from pathlib import Path
import importlib.util
import os
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("automation", ROOT / "scripts/patch_background_automation.py")
automation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(automation)
SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc")
SOURCE = Path(os.environ.get("SIDESTORE_TEST_SOURCE", ROOT.parent / "SideStore-source-timepicker"))
REF = "394bb4eb331cb4afc23517af2fc847ec103af57f"
FILES = ["AltStore/AppDelegate.swift", "AltStore/SceneDelegate.swift",
         "AltStore/Managing Apps/AppManager.swift",
         "AltStore/Info.plist", "AltStore/Settings/SettingsViewController.swift",
         "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift",
         "SideStore/Utils/iostreams/ConsoleLog.swift"]


class AutomationTests(unittest.TestCase):
    @unittest.skipUnless(SWIFTC, "Swift compiler required")
    def test_schedule_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.swift"
            path.write_text("import Foundation\n" + automation.SCHEDULE_MODEL + r'''
let domain = "schedule-test-" + UUID().uuidString
let defaults = UserDefaults(suiteName: domain)!
defer { defaults.removePersistentDomain(forName: domain) }
var calendar = Calendar(identifier: .gregorian)
calendar.timeZone = TimeZone(identifier: "America/New_York")!
func date(_ value: String) -> Date { ISO8601DateFormatter().date(from: value)! }
func expect(_ input: String, _ output: String) {
    let actual = AutomaticRefreshSchedule.nextDate(after: date(input), defaults: defaults, calendar: calendar)
    precondition(actual == date(output), "\(input): got \(actual), expected \(output)")
}
// Existing installs retain six-hour scheduling.
expect("2026-01-01T13:00:00Z", "2026-01-01T19:00:00Z")
defaults.set(true, forKey: AutomaticRefreshSchedule.dailyKey)
expect("2026-01-01T13:00:00Z", "2026-01-01T15:00:00Z")
expect("2026-01-01T15:00:00Z", "2026-01-02T15:00:00Z")
defaults.set(0, forKey: AutomaticRefreshSchedule.minutesKey)
expect("2026-01-01T23:00:00Z", "2026-01-02T05:00:00Z")
defaults.set(1439, forKey: AutomaticRefreshSchedule.minutesKey)
expect("2026-01-01T23:00:00Z", "2026-01-02T04:59:00Z")
defaults.set(-1, forKey: AutomaticRefreshSchedule.minutesKey)
expect("2026-01-01T13:00:00Z", "2026-01-01T15:00:00Z")
defaults.set(1440, forKey: AutomaticRefreshSchedule.minutesKey)
expect("2026-01-01T13:00:00Z", "2026-01-01T15:00:00Z")
// A missing local time advances to the next valid time on the same day.
defaults.set(150, forKey: AutomaticRefreshSchedule.minutesKey)
expect("2026-03-08T05:00:00Z", "2026-03-08T07:00:00Z")
// The repeated hour uses the first occurrence.
defaults.set(90, forKey: AutomaticRefreshSchedule.minutesKey)
expect("2026-11-01T04:00:00Z", "2026-11-01T05:30:00Z")
expect("2026-11-01T05:30:00Z", "2026-11-02T06:30:00Z")
calendar.timeZone = TimeZone(secondsFromGMT: 0)!
expect("2026-01-01T00:00:00Z", "2026-01-01T01:30:00Z")
precondition(UserDefaults(suiteName: domain)!.integer(forKey: AutomaticRefreshSchedule.minutesKey) == 90)
// Entering the background must not postpone an eligible or future request.
let pending = date("2026-01-01T01:30:00Z")
defaults.set(pending, forKey: AutomaticRefreshSchedule.pendingKey)
defaults.set(AutomaticRefreshSchedule.configuration(defaults: defaults, calendar: calendar),
             forKey: AutomaticRefreshSchedule.configurationKey)
for now in ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"] {
    precondition(AutomaticRefreshSchedule.requestDate(after: date(now), replacePending: false,
        defaults: defaults, calendar: calendar) == pending)
}
precondition(AutomaticRefreshSchedule.requestDate(after: date("2026-01-02T00:00:00Z"), replacePending: true,
    defaults: defaults, calendar: calendar) == date("2026-01-02T01:30:00Z"))
// Changed settings and time zones invalidate the old pending date.
defaults.set(120, forKey: AutomaticRefreshSchedule.minutesKey)
precondition(AutomaticRefreshSchedule.requestDate(after: date("2026-01-02T00:00:00Z"), replacePending: false,
    defaults: defaults, calendar: calendar) == date("2026-01-02T02:00:00Z"))
defaults.set(90, forKey: AutomaticRefreshSchedule.minutesKey)
calendar.timeZone = TimeZone(identifier: "America/New_York")!
precondition(AutomaticRefreshSchedule.requestDate(after: date("2026-01-02T00:00:00Z"), replacePending: false,
    defaults: defaults, calendar: calendar) == date("2026-01-02T06:30:00Z"))
// Legacy daily preferences survive upgrade; explicit choices override them.
precondition(AutomaticRefreshSchedule.frequency(defaults: defaults) == "daily")
defaults.set("weekly", forKey: AutomaticRefreshSchedule.frequencyKey)
defaults.set(600, forKey: AutomaticRefreshSchedule.minutesKey)
expect("2026-01-01T13:00:00Z", "2026-01-05T15:00:00Z")
expect("2026-01-05T14:59:00Z", "2026-01-05T15:00:00Z")
expect("2026-01-05T15:00:00Z", "2026-01-12T15:00:00Z")
// Sunday and Saturday retain Calendar's weekday numbering in every locale.
defaults.set(1, forKey: AutomaticRefreshSchedule.weekdayKey)
expect("2026-01-01T13:00:00Z", "2026-01-04T15:00:00Z")
defaults.set(7, forKey: AutomaticRefreshSchedule.weekdayKey)
expect("2026-01-01T13:00:00Z", "2026-01-03T15:00:00Z")
defaults.set(0, forKey: AutomaticRefreshSchedule.weekdayKey)
expect("2026-01-01T13:00:00Z", "2026-01-05T15:00:00Z")
defaults.set(8, forKey: AutomaticRefreshSchedule.weekdayKey)
expect("2026-01-01T13:00:00Z", "2026-01-05T15:00:00Z")
defaults.set(1, forKey: AutomaticRefreshSchedule.weekdayKey)
defaults.set(150, forKey: AutomaticRefreshSchedule.minutesKey)
expect("2026-03-08T05:00:00Z", "2026-03-08T07:00:00Z")
defaults.set(90, forKey: AutomaticRefreshSchedule.minutesKey)
expect("2026-11-01T04:00:00Z", "2026-11-01T05:30:00Z")
expect("2026-11-01T05:30:00Z", "2026-11-08T06:30:00Z")
// A changed weekday invalidates a previously accepted weekly request.
defaults.set(date("2026-01-04T06:30:00Z"), forKey: AutomaticRefreshSchedule.pendingKey)
defaults.set(AutomaticRefreshSchedule.configuration(defaults: defaults, calendar: calendar),
             forKey: AutomaticRefreshSchedule.configurationKey)
defaults.set(2, forKey: AutomaticRefreshSchedule.weekdayKey)
precondition(AutomaticRefreshSchedule.requestDate(after: date("2026-01-01T00:00:00Z"), replacePending: false,
    defaults: defaults, calendar: calendar) == date("2026-01-05T06:30:00Z"))
defaults.set("daily", forKey: AutomaticRefreshSchedule.frequencyKey)
expect("2026-01-01T00:00:00Z", "2026-01-01T06:30:00Z")
defaults.set("interval", forKey: AutomaticRefreshSchedule.frequencyKey)
expect("2026-01-01T00:00:00Z", "2026-01-01T06:00:00Z")
defaults.set("invalid", forKey: AutomaticRefreshSchedule.frequencyKey)
precondition(AutomaticRefreshSchedule.frequency(defaults: defaults) == "daily")
print("Schedule date tests passed")
''', encoding="utf-8")
            binary = Path(directory) / "schedule-tests"
            subprocess.run([SWIFTC, str(path), "-o", str(binary)], check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)

    @unittest.skipUnless((SOURCE / ".git").exists(), "Pinned SideStore checkout required")
    def test_patch_application_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in FILES:
                contents = subprocess.run(["git", "-C", str(SOURCE), "show", f"{REF}:{name}"],
                                         check=True, capture_output=True).stdout
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(contents)
            functions = [automation.patch_app_delegate, automation.patch_scene_delegate,
                         automation.patch_background_operation, automation.patch_info_plist,
                         automation.patch_settings, automation.patch_manual_refresh,
                         automation.patch_console_log, automation.verify]
            for patch in functions:
                patch(root)
            first = {name: (root / name).read_bytes() for name in FILES}
            for patch in functions:
                patch(root)
            self.assertEqual(first, {name: (root / name).read_bytes() for name in FILES})
            manager = (root / "AltStore/Managing Apps/AppManager.swift").read_text(encoding="utf-8")
            section = manager[manager.index("    func refresh(_ installedApps:"):manager.index("    func activate(")]
            self.assertIn("recordManualHistory: Bool = true", section)
            self.assertLess(section.index("try await self.pipelineRunner.perform"),
                            section.index("AutomaticRefreshHistory.finishManual"))
            self.assertIn("results: actualGroup.results", section)
            self.assertIn("AutomaticRefreshHistory.record(.failed", section)
            background = (root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift").read_text()
            self.assertIn("recordManualHistory: false", background)
            settings = (root / "AltStore/Settings/SettingsViewController.swift").read_text()
            self.assertIn("Preferred Time (Local)", settings)
            self.assertIn("TimeZone.autoupdatingCurrent.identifier", settings)
            app_delegate = (root / "AltStore/AppDelegate.swift").read_text()
            self.assertIn("AutomaticRefreshSchedule.requestDate(after: Date(), replacePending: replacePending)", app_delegate)
            self.assertIn("request.earliestBeginDate = nextDate", app_delegate)
            self.assertIn("scheduled_utc=", app_delegate)
            if SWIFTC:
                for name in FILES:
                    if name.endswith(".swift"):
                        result = subprocess.run([SWIFTC, "-frontend", "-parse", str(root / name)],
                                                capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                self.check_scheduler(root)
                self.check_history_and_lifecycle(root)
                self.check_manual_refresh_entry(root, section)
                if platform.system() == "Darwin":
                    self.check_swiftui(root)

    def check_manual_refresh_entry(self, root, section):
        path = root / "manual-entry.swift"
        path.write_text("import Foundation\n" + automation.SCHEDULE_MODEL + r'''
func debugLog(_ message: String) {}
class UIViewController {}
class InstalledApp { let bundleIdentifier = "test.app" }
class Context { var error: Error? }
class RefreshGroup {
    let context: Context
    var results = [String: Result<InstalledApp, Error>]()
    var activeTask: Task<Void, Never>?
    var completionHandler: (([String: Result<InstalledApp, Error>]) -> Void)?
    init(context: Context) { self.context = context }
}
enum Operation { case refresh(InstalledApp) }
class Runner {
    var shouldThrow = false
    var shouldFail = false
    func perform(_ operations: [Operation], handler: Int, group: RefreshGroup) async throws {
        if shouldThrow { throw NSError(domain: "test", code: 1) }
        for case .refresh(let app) in operations {
            group.results[app.bundleIdentifier] = shouldFail ? .failure(NSError(domain: "test", code: 2)) : .success(app)
        }
        group.completionHandler?(group.results)
    }
}
class Manager {
    let pipelineRunner = Runner()
    func makePipelineHandler(presentingViewController: UIViewController?) -> Int { 0 }
    func makeAuthenticatedContext(presentingViewController: UIViewController?) -> Context { Context() }
''' + section + r'''
}
@main struct Test {
    static func main() async {
        defer { AutomaticRefreshHistory.clear() }
        let manager = Manager()
        for mode in 0..<4 {
            AutomaticRefreshHistory.clear()
            manager.pipelineRunner.shouldThrow = mode == 1
            manager.pipelineRunner.shouldFail = mode == 2
            let group = RefreshGroup(context: Context())
            var callbacks = 0
            group.completionHandler = { _ in callbacks += 1 }
            let actual = manager.refresh([InstalledApp()], presentingViewController: nil,
                group: group, recordManualHistory: mode != 3)
            await actual.activeTask?.value
            precondition(actual === group && callbacks == 1)
            let entries = AutomaticRefreshHistory.load()
            if mode == 3 { precondition(entries.isEmpty) }
            else {
                precondition(entries.count == 2)
                precondition(entries[0].event == (mode == 0 ? .completed : .failed))
                precondition(entries[1].event == .started)
                precondition(entries.allSatisfy { $0.sourceTitle == "Manual" })
            }
        }
    }
}
''')
        executable = root / "manual-entry-test"
        result = subprocess.run([SWIFTC, "-parse-as-library", str(path), "-o", str(executable)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        subprocess.run([str(executable)], check=True)

    @unittest.skipUnless(SWIFTC, "Swift compiler required")
    def test_manual_history_results_and_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.swift"
            path.write_text("import Foundation\n" + automation.SCHEDULE_MODEL + r'''
let defaults = UserDefaults(suiteName: "manual-history-" + UUID().uuidString)!
defer { AutomaticRefreshHistory.clear(defaults: defaults) }
let run = UUID()
AutomaticRefreshHistory.record(.started, runID: run, defaults: defaults, source: .manual)
precondition(AutomaticRefreshHistory.load(defaults: defaults)[0].sourceTitle == "Manual")
AutomaticRefreshHistory.finishManual(runID: run, expected: ["a", "b"],
    results: ["a": Result<Int, Error>.success(1), "b": .success(2)], defaults: defaults)
precondition(AutomaticRefreshHistory.load(defaults: defaults)[0].event == .completed)
precondition(AutomaticRefreshHistory.load(defaults: defaults)[0].sourceTitle == "Manual")
// A late duplicate cannot overwrite the terminal result.
AutomaticRefreshHistory.finishManual(runID: run, expected: ["a"],
    results: [String: Result<Int, Error>](), defaults: defaults)
precondition(AutomaticRefreshHistory.load(defaults: defaults).count == 2)
AutomaticRefreshHistory.finishManual(runID: UUID(), expected: ["a", "b"],
    results: ["a": Result<Int, Error>.success(1)], defaults: defaults)
precondition(AutomaticRefreshHistory.load(defaults: defaults)[0].event == .failed)
AutomaticRefreshHistory.finishManual(runID: UUID(), expected: ["a", "b"],
    results: ["a": Result<Int, Error>.success(1), "b": .failure(NSError(domain: "test", code: 1))], defaults: defaults)
precondition(AutomaticRefreshHistory.load(defaults: defaults)[0].event == .failed)
AutomaticRefreshHistory.finishManual(runID: UUID(), expected: [],
    results: [String: Result<Int, Error>](), defaults: defaults)
precondition(AutomaticRefreshHistory.load(defaults: defaults)[0].event == .skipped)
AutomaticRefreshHistory.record(.started, defaults: defaults)
precondition(AutomaticRefreshHistory.load(defaults: defaults)[0].sourceTitle == "Scheduled")
// Previously stored entries have no source key; decoding must retain them.
var old = try JSONSerialization.jsonObject(with: defaults.data(forKey: AutomaticRefreshHistory.key)!) as! [[String: Any]]
for index in old.indices { old[index].removeValue(forKey: "source") }
defaults.set(try JSONSerialization.data(withJSONObject: old), forKey: AutomaticRefreshHistory.key)
precondition(AutomaticRefreshHistory.load(defaults: defaults).count == old.count)
precondition(AutomaticRefreshHistory.load(defaults: defaults).allSatisfy { $0.sourceTitle == "Scheduled" })
''')
            executable = Path(directory) / "manual-test"
            result = subprocess.run([SWIFTC, str(path), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(executable)], check=True)

    def check_swiftui(self, root):
        # Compile the actual generated view with SwiftUI, including the upstream
        # Button name collision. Only the UIKit/application boundary is mocked.
        source = r'''
import Foundation
import SwiftUI
import UserNotifications
final class Button {}
extension UserDefaults {
    var isBackgroundRefreshEnabled: Bool {
        get { bool(forKey: "testEnabled") }
        set { set(newValue, forKey: "testEnabled") }
    }
}
@MainActor final class UIApplication {
    enum Status { case available, denied }
    static let shared = UIApplication()
    static let openSettingsURLString = "app-settings:"
    var backgroundRefreshStatus = Status.available
    var delegate: AnyObject? = AppDelegate()
    func open(_ url: URL) {}
}
@MainActor final class AppDelegate {
    func scheduleAutomaticRefresh(replacePending: Bool = false) -> String? { nil }
}
''' + automation.SCHEDULE_MODEL + automation.SCHEDULE_UI
        path = root / "schedule-ui.swift"
        path.write_text(source, encoding="utf-8")
        result = subprocess.run([SWIFTC, "-typecheck", "-target",
                                 f"{platform.machine()}-apple-macosx14.0", str(path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def check_scheduler(self, root):
        delegate = (root / "AltStore/AppDelegate.swift").read_text(encoding="utf-8")
        start = delegate.index("    @discardableResult\n    func scheduleAutomaticRefresh")
        end = delegate.index("    #if !os(tvOS)\n    private func handleAutomaticRefresh", start)
        source = "import Foundation\n" + automation.SCHEDULE_MODEL + r'''
extension UserDefaults {
    var isBackgroundRefreshEnabled: Bool {
        get { bool(forKey: "testEnabled") }
        set { set(newValue, forKey: "testEnabled") }
    }
}
func debugLog(_ message: String) {}
final class BGProcessingTaskRequest {
    var earliestBeginDate: Date?
    var requiresNetworkConnectivity = false
    var requiresExternalPower = true
    init(identifier: String) {}
}
final class BGTaskScheduler {
    static let shared = BGTaskScheduler()
    var submitted: BGProcessingTaskRequest?
    var shouldFail = false
    var cancelled = false
    func submit(_ request: BGProcessingTaskRequest) throws {
        if shouldFail { throw NSError(domain: "scheduler-test", code: 1) }
        submitted = request
    }
    func cancel(taskRequestWithIdentifier: String) { cancelled = true; submitted = nil }
}
final class Delegate {
    static let automaticRefreshTaskIdentifier = "test"
''' + delegate[start:end] + r'''
}
let defaults = UserDefaults.standard
let keys = ["testEnabled", AutomaticRefreshHistory.key, AutomaticRefreshSchedule.frequencyKey, AutomaticRefreshSchedule.weekdayKey,
            AutomaticRefreshSchedule.pendingKey, AutomaticRefreshSchedule.configurationKey,
            AutomaticRefreshSchedule.dailyKey, AutomaticRefreshSchedule.minutesKey]
for key in keys { defaults.removeObject(forKey: key) }
defer { for key in keys { defaults.removeObject(forKey: key) } }
let app = Delegate()
let scheduler = BGTaskScheduler.shared
defaults.isBackgroundRefreshEnabled = true
precondition(app.scheduleAutomaticRefresh() == nil)
let accepted = defaults.object(forKey: AutomaticRefreshSchedule.pendingKey) as! Date
precondition(scheduler.submitted!.earliestBeginDate == accepted)
precondition(AutomaticRefreshHistory.load().count == 1)
precondition(AutomaticRefreshHistory.load().first!.event == .scheduled)
precondition(AutomaticRefreshHistory.load().first!.eligibleDate == accepted)
precondition(scheduler.submitted!.requiresNetworkConnectivity)
precondition(!scheduler.submitted!.requiresExternalPower)
precondition(app.scheduleAutomaticRefresh() == nil)
precondition(scheduler.submitted!.earliestBeginDate == accepted)
precondition(AutomaticRefreshHistory.load().count == 1)
scheduler.shouldFail = true
precondition(app.scheduleAutomaticRefresh(replacePending: true) != nil)
precondition(AutomaticRefreshHistory.load().first!.event == .scheduleFailed)
precondition(defaults.object(forKey: AutomaticRefreshSchedule.pendingKey) as! Date == accepted)
defaults.isBackgroundRefreshEnabled = false
precondition(app.scheduleAutomaticRefresh() == nil)
precondition(scheduler.cancelled)
precondition(AutomaticRefreshHistory.load().first!.event == .disabled)
precondition(defaults.object(forKey: AutomaticRefreshSchedule.pendingKey) == nil)
scheduler.shouldFail = false
defaults.isBackgroundRefreshEnabled = true
precondition(app.scheduleAutomaticRefresh() == nil)
precondition(scheduler.submitted != nil)
print("Scheduler boundary tests passed")
'''
        path = root / "scheduler.swift"
        path.write_text(source, encoding="utf-8")
        binary = root / "scheduler-tests"
        result = subprocess.run([SWIFTC, str(path), "-o", str(binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def check_history_and_lifecycle(self, root):
        delegate = (root / "AltStore/AppDelegate.swift").read_text(encoding="utf-8")
        start = delegate.index("private final class AutomaticRefreshTaskState")
        end = delegate.index("@UIApplicationMain", start)
        handler_start = delegate.index("    private func handleAutomaticRefresh")
        handler_end = delegate.index("    private func startAutomaticRefresh", handler_start)
        handler = delegate[handler_start:handler_end].replace("private func handleAutomaticRefresh", "func handleAutomaticRefresh")
        source = "import Foundation\nimport Dispatch\n" + automation.SCHEDULE_MODEL + r'''
final class BGProcessingTask {
    var expirationHandler: (() -> Void)?
    var results: [Bool] = []
    func setTaskCompleted(success: Bool) { results.append(success) }
}
final class BackgroundRefreshAppsOperation {
    var cancelled = false
    var onCancel: (() -> Void)?
    func cancel() { cancelled = true; onCancel?() }
}
''' + delegate[start:end] + r'''
func debugLog(_ message: String) {}
extension UserDefaults {
    var isBackgroundRefreshEnabled: Bool {
        get { bool(forKey: "historyTestEnabled") }
        set { set(newValue, forKey: "historyTestEnabled") }
    }
}
struct AlertSound { static let `default` = AlertSound() }
final class UNMutableNotificationContent {
    var title = ""
    var body = ""
    var sound: AlertSound?
}
struct UNNotificationRequest {
    let identifier: String
    let content: UNMutableNotificationContent
    let trigger: Int?
}
final class UNUserNotificationCenter {
    static let shared = UNUserNotificationCenter()
    static func current() -> UNUserNotificationCenter { shared }
    var requests: [UNNotificationRequest] = []
    var fail = false
    func add(_ request: UNNotificationRequest, withCompletionHandler completion: (Error?) -> Void) {
        requests.append(request)
        completion(fail ? NSError(domain: "notification-test", code: 1) : nil)
    }
}
final class EventDelegate {
    let bootTask: Task<Void, Never>? = nil
    func scheduleAutomaticRefresh(replacePending: Bool) {}
    private func startAutomaticRefresh(state: AutomaticRefreshTaskState) {}
''' + handler + r'''
}
let domain = "history-test-" + UUID().uuidString
let defaults = UserDefaults(suiteName: domain)!
defer { defaults.removePersistentDomain(forName: domain); AutomaticRefreshHistory.clear() }
defaults.set(Data("corrupt".utf8), forKey: AutomaticRefreshHistory.key)
precondition(AutomaticRefreshHistory.load(defaults: defaults).isEmpty)
let runID = UUID()
AutomaticRefreshHistory.record(.started, runID: runID, detail: String(repeating: "x", count: 700), defaults: defaults)
precondition(AutomaticRefreshHistory.load(defaults: defaults).first!.detail.count == 500)
AutomaticRefreshHistory.record(.expired, runID: runID, defaults: defaults)
AutomaticRefreshHistory.record(.completed, runID: runID, defaults: defaults)
precondition(AutomaticRefreshHistory.load(defaults: defaults).map(\.event) == [.expired, .started])
precondition(AutomaticRefreshHistory.load(defaults: UserDefaults(suiteName: domain)!).count == 2)
AutomaticRefreshHistory.clear(defaults: defaults)
DispatchQueue.concurrentPerform(iterations: 150) { index in
    AutomaticRefreshHistory.record(.scheduled, detail: "\(index)", defaults: defaults)
}
let entries = AutomaticRefreshHistory.load(defaults: defaults)
precondition(entries.count == 100)
precondition(Set(entries.map(\.id)).count == 100)
AutomaticRefreshHistory.clear(defaults: defaults)
precondition(AutomaticRefreshHistory.load(defaults: defaults).isEmpty)

// Expiration must win over synchronous cancellation completion.
AutomaticRefreshHistory.clear()
let task = BGProcessingTask()
private let state = AutomaticRefreshTaskState(task: task)
precondition(state.begin())
precondition(!state.begin())
let operation = BackgroundRefreshAppsOperation()
operation.onCancel = { state.finish(success: false) }
state.attach(operation: operation)
state.expire()
state.finish(success: true)
precondition(task.results == [false])
precondition(operation.cancelled)
precondition(AutomaticRefreshHistory.load().map(\.event) == [.expired, .started])

// A late attachment is cancelled, without introducing a new history outcome.
let late = BackgroundRefreshAppsOperation()
state.attach(operation: late)
precondition(late.cancelled)
let completeTask = BGProcessingTask()
private let completeState = AutomaticRefreshTaskState(task: completeTask)
precondition(completeState.begin())
DispatchQueue.concurrentPerform(iterations: 20) { _ in completeState.finish(success: true, detail: "Apps refreshed: 2") }
precondition(completeTask.results == [true])
precondition(AutomaticRefreshHistory.load().filter { $0.runID == completeState.runID && $0.event.isTerminal }.count == 1)
// Actual handler: disabled launches never alert; real launches request an immediate alert.
AutomaticRefreshHistory.clear()
let observer = EventDelegate()
UserDefaults.standard.isBackgroundRefreshEnabled = false
observer.handleAutomaticRefresh(BGProcessingTask())
precondition(UNUserNotificationCenter.shared.requests.isEmpty)
precondition(AutomaticRefreshHistory.load().first!.event == .skipped)
UserDefaults.standard.isBackgroundRefreshEnabled = true
observer.handleAutomaticRefresh(BGProcessingTask())
precondition(UNUserNotificationCenter.shared.requests.count == 1)
let alert = UNUserNotificationCenter.shared.requests[0]
precondition(alert.trigger == nil)
precondition(alert.content.sound != nil)
precondition(alert.content.title == "Scheduled refresh started")
precondition(AutomaticRefreshHistory.load().first!.event == .started)
UNUserNotificationCenter.shared.fail = true
observer.handleAutomaticRefresh(BGProcessingTask())
precondition(AutomaticRefreshHistory.load().first!.event == .notificationFailed)
precondition(UNUserNotificationCenter.shared.requests[1].identifier != alert.identifier)
UserDefaults.standard.removeObject(forKey: "historyTestEnabled")
print("History and lifecycle tests passed")
'''
        path = root / "history.swift"
        path.write_text(source, encoding="utf-8")
        binary = root / "history-tests"
        result = subprocess.run([SWIFTC, str(path), "-o", str(binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == "__main__":
    unittest.main()
