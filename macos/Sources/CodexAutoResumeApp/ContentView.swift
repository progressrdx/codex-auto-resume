import SwiftUI

private let accent = Color(red: 0.07, green: 0.41, blue: 0.36)

struct ContentView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationSplitView {
            VStack(spacing: 0) {
                List(model.conversations.filter { !$0.archived }, selection: $model.selection) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.title).font(.body.weight(.medium)).lineLimit(2)
                        Text(item.detail).font(.caption).foregroundStyle(.secondary)
                    }.padding(.vertical, 5).tag(item)
                }
                Divider()
                Label("对话内容不会上传", systemImage: "lock.shield")
                    .font(.caption).foregroundStyle(.secondary).padding(12)
            }
            .navigationTitle("本地对话")
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    header
                    statusBand
                    if let selected = model.selection { selectedTask(selected) } else { emptySelection }
                    monitors
                }.padding(28).frame(maxWidth: 820, alignment: .leading)
            }.background(Color(nsColor: .windowBackgroundColor))
        }
        .tint(accent)
        .frame(minWidth: 900, minHeight: 620)
        .task { await model.bootstrap() }
        .onChange(of: model.selection) { _, _ in Task { await model.inspectSelection() } }
        .alert("未能完成", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("知道了") { model.errorMessage = nil }
        } message: { Text(model.errorMessage ?? "未知错误") }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 5) {
                Text("续航").font(.system(size: 31, weight: .semibold, design: .rounded))
                Text("Codex 任务本地续跑助手").foregroundStyle(.secondary)
            }
            Spacer()
            Label("仅在此 Mac", systemImage: "laptopcomputer.and.arrow.down")
                .font(.callout.weight(.medium)).padding(.horizontal, 12).padding(.vertical, 7)
                .background(accent.opacity(0.11), in: Capsule()).foregroundStyle(accent)
        }
    }

    private var statusBand: some View {
        HStack(spacing: 12) {
            Image(systemName: model.doctorReady ? "checkmark.circle.fill" : "circle.dotted")
                .font(.title2).foregroundStyle(model.doctorReady ? accent : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(model.doctorReady ? "本机连接就绪" : "本机状态检查").font(.headline)
                Text(model.message).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if model.isBusy { ProgressView().controlSize(.small) }
            Button("刷新") { Task { await model.bootstrap() } }.disabled(model.isBusy)
        }.padding(16).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private var emptySelection: some View {
        ContentUnavailableView("选择一个任务", systemImage: "cursorarrow.click.2",
                               description: Text("先进行只读识别；不会自动扫描并接管任务。"))
            .frame(maxWidth: .infinity, minHeight: 210)
            .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 16))
    }

    private func selectedTask(_ task: Conversation) -> some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("所选任务").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            Text(task.title).font(.title3.weight(.semibold))
            if let assessment = model.assessment {
                Label(stateLabel(assessment.state), systemImage: assessment.canMonitor ? "clock.badge.checkmark" : "hand.raised.fill")
                    .font(.headline).foregroundStyle(assessment.canMonitor ? accent : .orange)
                Text(assessment.reason).foregroundStyle(.secondary)
                if assessment.canMonitor {
                    Divider()
                    HStack {
                        Stepper("最多续跑 \(model.maxResumes) 次", value: $model.maxResumes, in: 1...10)
                        Spacer()
                        Toggle("我确认只托管这个任务", isOn: $model.confirmed).toggleStyle(.checkbox)
                    }
                    HStack {
                        Spacer()
                        Button("开启本地托管") { Task { await model.start() } }
                            .buttonStyle(.borderedProminent).disabled(!model.confirmed || model.isBusy)
                    }
                }
            } else { ProgressView("正在只读识别…").controlSize(.small) }
        }.padding(20).background(.background, in: RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(.separator.opacity(0.55)))
    }

    private var monitors: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Text("托管记录").font(.headline); Spacer(); Button("刷新状态") { Task { await model.refreshStatus() } }.buttonStyle(.link) }
            if model.monitors.isEmpty { Text("尚未托管任何任务").foregroundStyle(.secondary).padding(.vertical, 8) }
            ForEach(model.monitors) { row in
                HStack(spacing: 12) {
                    Circle().fill(row.enabled ? accent : Color.secondary.opacity(0.4)).frame(width: 8, height: 8)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(row.enabled ? "监控中 · 已尝试 \(row.attempts) 次" : "已停止").font(.callout.weight(.semibold))
                        Text(row.reason).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                    Spacer()
                    if row.enabled { Button("停止") { Task { await model.stop(row) } }.disabled(model.isBusy) }
                }.padding(12).background(.quaternary.opacity(0.22), in: RoundedRectangle(cornerRadius: 11))
            }
        }
    }

    private func stateLabel(_ state: String) -> String {
        ["running": "任务执行中，可以托管", "quota_limited": "额度暂停，可以等待恢复", "idle": "本轮已结束",
         "interrupted": "任务已停止", "archived": "任务已归档"][state] ?? "状态暂不可用"
    }
}
