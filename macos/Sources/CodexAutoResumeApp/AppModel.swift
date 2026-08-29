import Foundation
import SwiftUI

struct Conversation: Identifiable, Hashable {
    let id: String
    let title: String
    let detail: String
    let archived: Bool
}

struct Assessment {
    let canMonitor: Bool
    let state: String
    let reason: String
}

struct MonitorRow: Identifiable {
    var id: String { threadID }
    let threadID: String
    let enabled: Bool
    let status: String
    let reason: String
    let attempts: Int
}

@MainActor
final class AppModel: ObservableObject {
    @Published var conversations: [Conversation] = []
    @Published var selection: Conversation?
    @Published var assessment: Assessment?
    @Published var monitors: [MonitorRow] = [] {
        didSet {
            AppLifecycleState.shared.activeMonitorCount = monitors.filter(\.enabled).count
            AppLifecycleState.shared.hasUncertainOperation = monitors.contains {
                $0.status == "uncertain" || $0.status == "sending"
            }
        }
    }
    @Published var isBusy = false
    @Published var message = "准备进行本机只读检查"
    @Published var errorMessage: String?
    @Published var doctorReady = false
    @Published var confirmed = false
    @Published var maxResumes = 3

    private let runner = CommandRunner()

    func bootstrap() async {
        await perform("正在连接本机 Codex App…") {
            async let doctor = runner.run(["doctor"])
            async let list = runner.run(["list"])
            async let status = runner.run(["status"])
            let (doctorResult, listResult, statusResult) = try await (doctor, list, status)
            doctorReady = dictionary(doctorResult)["ipc"] as? String == "connected"
            conversations = array(listResult).compactMap(conversation)
            monitors = array(statusResult).compactMap(monitor)
            message = "已连接 · 找到 \(conversations.count) 个本地对话"
        }
    }

    func inspectSelection() async {
        guard let selection else { return }
        confirmed = false
        assessment = nil
        await perform("正在只读识别所选任务…") {
            let result = try await runner.run(["check", selection.id])
            let row = dictionary(result)
            assessment = Assessment(canMonitor: row["canMonitor"] as? Bool == true,
                                    state: row["taskState"] as? String ?? "unknown",
                                    reason: row["reason"] as? String ?? "无法确认任务状态")
            message = "检查完成；此次没有开启托管或发送消息"
        }
    }

    func start() async {
        guard let selection, assessment?.canMonitor == true, confirmed else { return }
        await perform("正在为所选任务开启本地托管…") {
            _ = try await runner.run(["start", selection.id, "--max-resumes", String(maxResumes)])
            confirmed = false
            message = "已开启；只监控当前选中的任务"
            await refreshStatus()
        }
    }

    func stop(_ row: MonitorRow) async {
        await perform("正在停止后续自动续跑…") {
            _ = try await runner.run(["stop", row.threadID])
            message = "已停止后续续跑；不会打断 App 中正在执行的任务"
            await refreshStatus()
        }
    }

    func refreshStatus() async {
        do { monitors = array(try await runner.run(["status"])).compactMap(monitor) }
        catch { errorMessage = error.localizedDescription }
    }

    private func perform(_ progress: String, operation: () async throws -> Void) async {
        guard !isBusy else { return }
        isBusy = true; errorMessage = nil; message = progress
        do { try await operation() } catch { errorMessage = error.localizedDescription; message = "操作未完成" }
        isBusy = false
    }

    private func dictionary(_ result: CommandResult) -> [String: Any] {
        result.value.value as? [String: Any] ?? [:]
    }

    private func array(_ result: CommandResult) -> [[String: Any]] {
        result.value.value as? [[String: Any]] ?? []
    }

    private func conversation(_ row: [String: Any]) -> Conversation? {
        guard let id = row["id"] as? String else { return nil }
        let title = (row["name"] as? String) ?? (row["preview"] as? String) ?? "未命名对话"
        let cwd = (row["cwd"] as? String).flatMap { URL(fileURLWithPath: $0).lastPathComponent }
        return Conversation(id: id, title: title, detail: cwd ?? "本地任务", archived: row["archived"] as? Bool == true)
    }

    private func monitor(_ row: [String: Any]) -> MonitorRow? {
        guard let id = row["thread_id"] as? String else { return nil }
        return MonitorRow(threadID: id, enabled: (row["enabled"] as? NSNumber)?.boolValue == true,
                          status: row["status"] as? String ?? "unknown",
                          reason: row["reason"] as? String ?? "暂无状态说明",
                          attempts: (row["attempts"] as? NSNumber)?.intValue ?? 0)
    }
}
