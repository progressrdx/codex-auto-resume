import AppKit

@MainActor
final class AppLifecycleState {
    static let shared = AppLifecycleState()
    var activeMonitorCount = 0
    var hasUncertainOperation = false
    private init() {}
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        let state = MainActor.assumeIsolated { AppLifecycleState.shared }
        guard LifecyclePolicy.shouldWarn(activeMonitorCount: state.activeMonitorCount,
                                         hasUncertainOperation: state.hasUncertainOperation)
        else { return .terminateNow }

        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = state.hasUncertainOperation ? "有一项操作结果尚未确认" : "仍有任务在本地托管"
        if state.hasUncertainOperation {
            alert.informativeText = "退出界面不会清除防重复记录。请稍后重新打开续航并人工核对；后台不会自动重试。"
        } else {
            alert.informativeText = "退出续航界面不会停止 \(state.activeMonitorCount) 个后台监控。若要停止，请先取消并返回主界面点击“停止”。"
        }
        alert.addButton(withTitle: "仍然退出界面")
        alert.addButton(withTitle: "取消")
        return alert.runModal() == .alertFirstButtonReturn ? .terminateNow : .terminateCancel
    }
}

enum LifecyclePolicy {
    static func shouldWarn(activeMonitorCount: Int, hasUncertainOperation: Bool) -> Bool {
        activeMonitorCount > 0 || hasUncertainOperation
    }
}
