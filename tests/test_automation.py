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
         "AltStore/Info.plist", "AltStore/Settings/SettingsViewController.swift",
         "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"]


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
                         automation.patch_settings, automation.verify]
            for patch in functions:
                patch(root)
            first = {name: (root / name).read_bytes() for name in FILES}
            for patch in functions:
                patch(root)
            self.assertEqual(first, {name: (root / name).read_bytes() for name in FILES})
            if SWIFTC:
                for name in FILES:
                    if name.endswith(".swift"):
                        result = subprocess.run([SWIFTC, "-frontend", "-parse", str(root / name)],
                                                capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                self.check_scheduler(root)
                if platform.system() == "Darwin":
                    self.check_swiftui(root)

    def check_swiftui(self, root):
        # Compile the actual generated view with SwiftUI, including the upstream
        # Button name collision. Only the UIKit/application boundary is mocked.
        source = r'''
import Foundation
import SwiftUI
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
let keys = ["testEnabled", AutomaticRefreshSchedule.frequencyKey, AutomaticRefreshSchedule.weekdayKey,
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
precondition(scheduler.submitted!.requiresNetworkConnectivity)
precondition(!scheduler.submitted!.requiresExternalPower)
precondition(app.scheduleAutomaticRefresh() == nil)
precondition(scheduler.submitted!.earliestBeginDate == accepted)
scheduler.shouldFail = true
precondition(app.scheduleAutomaticRefresh(replacePending: true) != nil)
precondition(defaults.object(forKey: AutomaticRefreshSchedule.pendingKey) as! Date == accepted)
defaults.isBackgroundRefreshEnabled = false
precondition(app.scheduleAutomaticRefresh() == nil)
precondition(scheduler.cancelled)
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


if __name__ == "__main__":
    unittest.main()
