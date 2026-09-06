"""Static and idempotence checks for the LiveContainer integration patch."""

from pathlib import Path
import importlib.util
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "livecontainer_patch", ROOT / "scripts" / "patch_livecontainer_autorefresh.py"
)
patch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(patch)


class LiveContainerPatchTests(unittest.TestCase):
    def test_patch_script_compiles_and_has_required_contract(self):
        source = (ROOT / "scripts" / "patch_livecontainer_autorefresh.py").read_text()
        for marker in (
            "BGTaskScheduler.shared.register",
            "LiveContainerRefreshBridge",
            "requiresNetworkConnectivity = true",
            "liveContainerAutoRefreshFrequency",
            "liveContainerAutoRefreshHistory",
            "LiveContainerAutoRefreshHistoryChanged",
            "reloadHistory()",
            "Refresh SideStore now",
            "MANUAL_COMPLETE",
            "BGTaskSchedulerPermittedIdentifiers",
            "SideStoreSupport.framework in Frameworks",
        ):
            self.assertIn(marker, source)
        workflow = (ROOT / ".github/workflows/livecontainer-build.yml").read_text(encoding="utf-8")
        self.assertIn("module.patch_console_log", workflow)
        self.assertIn("LIVE_REFRESH_LOG_RETENTION_V1", workflow)

    def test_generated_host_fragments_are_idempotent(self):
        # Exercise the generator's own insertion helpers without requiring an
        # Xcode installation or network access in the repository test job.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SideStoreSupport").mkdir(parents=True)
            (root / "LiveContainerSwiftUI/App").mkdir(parents=True)
            (root / "LiveContainerSwiftUI/Views/Settings").mkdir(parents=True)
            (root / "LiveContainer.xcodeproj").mkdir()
            (root / "LiveContainer").mkdir(parents=True)
            (root / "SideStoreSupport/SideStore.swift").write_text(
                "import Foundation\n\nclass RefreshHandler: NSObject, RefreshServer {\n"
            )
            (root / "LiveContainerSwiftUI/App/AppDelegate.swift").write_text(
                "import UIKit\nimport SwiftUI\nimport Intents\n\n@objc class AppDelegate: UIResponder, UIApplicationDelegate {\n"
                "    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? ) -> Bool {\n"
                "        application.shortcutItems = nil\n        return true\n    }\n\n"
                "    func application(_ application: UIApplication, configurationForConnecting connectingSceneSession: UISceneSession, options: UIScene.ConnectionOptions) -> UISceneConfiguration { return UISceneConfiguration() }\n}\n\nclass SceneDelegate: NSObject {}\n"
            )
            (root / "LiveContainer/Info.plist").write_text("<?xml version=\"1.0\"?><plist><dict></dict>\n</plist>\n")
            (root / "LiveContainer.xcodeproj/project.pbxproj").write_text(
                "/* Begin PBXBuildFile section */\n"
                "17413FB22D9C0BAE00F3F928 /* Frameworks */ = {\n"
                "\t\t\tisa = PBXFrameworksBuildPhase;\n"
                "\t\t\tbuildActionMask = 2147483647;\n"
                "\t\t\tfiles = (\n"
                "\t\t\t);\n"
                "};\n"
                "17554B6A2DA165D8004C6D90 /* Frameworks */ = {\n"
                "\t\t\tisa = PBXFrameworksBuildPhase;\n"
                "\t\t\tbuildActionMask = 2147483647;\n"
                "\t\t\tfiles = (\n"
                "\t\t\t);\n"
                "};\n"
                "/* Begin PBXTargetDependency section */\n"
                "/* End PBXTargetDependency section */\n"
                "17413FB42D9C0BAE00F3F928 /* LiveContainerSwiftUI */ = {\n"
                "\t\t\tisa = PBXNativeTarget;\n"
                "\t\t\tbuildConfigurationList = 17413FBE2D9C0BAE00F3F928;\n"
                "\t\t\tbuildPhases = ();\n"
                "\t\t\tdependencies = (\n"
                "\t\t\t);\n"
                "\t\t\tfileSystemSynchronizedGroups = (\n"
                "\t\t\t\t17413FB62D9C0BAE00F3F928 /* LiveContainerSwiftUI */\n"
                "\t\t\t);\n"
                "};\n"
            )
            (root / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift").write_text(
                "struct LCSettingsView: View {\n    var body: some View {\n        NavigationView {\n            Form {\n            }\n        }\n    }\n}\n"
            )
            patch.patch_support(root)
            patch.patch_host_delegate(root)
            patch.patch_host_info(root)
            patch.patch_project(root)
            patch.patch_settings(root)
            patch.verify(root)
            first = { path: path.read_text() for path in root.rglob("*") if path.is_file() }
            patch.patch_support(root)
            patch.patch_host_delegate(root)
            patch.patch_host_info(root)
            patch.patch_project(root)
            patch.patch_settings(root)
            self.assertEqual(first, {path: path.read_text() for path in root.rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
