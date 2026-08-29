import SwiftUI

@main
struct CodexAutoResumeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup { ContentView().environmentObject(model) }
            .defaultSize(width: 980, height: 680)
        Settings { SettingsView() }
    }
}

struct SettingsView: View {
    @StateObject private var loginItem = LoginItemManager()

    var body: some View {
        Form {
            Section("启动") {
                Toggle("登录后自动打开续航", isOn: Binding(
                    get: { loginItem.isEnabled },
                    set: { loginItem.setEnabled($0) }
                )).disabled(!loginItem.canChange)
                Text(loginItem.statusText).font(.caption).foregroundStyle(.secondary)
            }
            Section("本地数据") {
                LabeledContent("数据位置", value: "~/.codex-auto-resume")
                LabeledContent("运行方式", value: "纯本地")
                Text("不连接开发者服务器，也不创建云端用户账户。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped).padding().frame(width: 480, height: 360)
        .onAppear { loginItem.refresh() }
        .alert("登录启动设置未更改", isPresented: Binding(
            get: { loginItem.errorMessage != nil },
            set: { if !$0 { loginItem.errorMessage = nil } }
        )) {
            Button("知道了") { loginItem.errorMessage = nil }
        } message: { Text(loginItem.errorMessage ?? "未知错误") }
    }
}
