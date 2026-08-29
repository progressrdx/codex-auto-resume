import ServiceManagement
import SwiftUI

@MainActor
final class LoginItemManager: ObservableObject {
    @Published private(set) var isEnabled = false
    @Published private(set) var canChange = true
    @Published private(set) var statusText = "正在读取系统设置…"
    @Published var errorMessage: String?

    private let service = SMAppService.mainApp

    init() { refresh() }

    func refresh() {
        switch service.status {
        case .enabled:
            isEnabled = true
            canChange = true
            statusText = "已开启；下次登录 Mac 后自动打开续航。"
        case .requiresApproval:
            isEnabled = false
            canChange = true
            statusText = "等待你在“系统设置 → 通用 → 登录项”中批准。"
        case .notRegistered:
            isEnabled = false
            canChange = true
            statusText = "已关闭；登录后不会自动打开。"
        case .notFound:
            isEnabled = false
            canChange = false
            statusText = "当前安装位置不支持登录启动，请先将应用移到“应用程序”。"
        @unknown default:
            isEnabled = false
            canChange = false
            statusText = "系统暂时无法确认登录启动状态。"
        }
    }

    func setEnabled(_ enabled: Bool) {
        errorMessage = nil
        do {
            if enabled {
                if service.status == .notRegistered { try service.register() }
            } else if service.status != .notRegistered && service.status != .notFound {
                try service.unregister()
            }
            refresh()
        } catch {
            refresh()
            errorMessage = enabled
                ? "未能开启。请把应用移到“应用程序”后重试，并检查系统的登录项设置。"
                : "未能关闭。请前往“系统设置 → 通用 → 登录项”关闭续航。"
        }
    }
}
