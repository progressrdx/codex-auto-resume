import Foundation

struct CommandResult: Sendable {
    let value: AnySendable
    let output: String
}

struct AnySendable: @unchecked Sendable {
    let value: Any
}

enum LocalCommandError: LocalizedError {
    case backendMissing
    case invalidResponse(String)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .backendMissing: return "本地后端不完整，请重新安装应用。"
        case .invalidResponse(let message): return message
        case .failed(let message): return message
        }
    }
}

actor CommandRunner {
    private func backendURL() throws -> URL {
        if let resource = Bundle.main.resourceURL {
            let packaged = resource.appendingPathComponent("Backend/codex-auto-resume-cli")
            if FileManager.default.isExecutableFile(atPath: packaged.path) { return packaged }
        }
        if ProcessInfo.processInfo.environment["CODEX_RESUME_DEV"] == "1" {
            return URL(fileURLWithPath: "/usr/bin/python3")
        }
        throw LocalCommandError.backendMissing
    }

    func run(_ arguments: [String]) async throws -> CommandResult {
        let executable = try backendURL()
        let process = Process()
        process.executableURL = executable
        process.arguments = executable.path == "/usr/bin/python3"
            ? ["-m", "codex_resume"] + arguments : arguments
        let stdout = Pipe(), stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr
        try process.run()
        let outData = read(stdout.fileHandleForReading)
        let errorData = read(stderr.fileHandleForReading)
        process.waitUntilExit()
        let output = String(decoding: outData, as: UTF8.self)
        let error = String(decoding: errorData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        guard process.terminationStatus == 0 else {
            throw LocalCommandError.failed(error.isEmpty ? "本地操作失败，请确认 Codex App 已打开。" : error)
        }
        guard let data = output.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) else {
            throw LocalCommandError.invalidResponse("本地后端返回了无法识别的数据。")
        }
        return CommandResult(value: AnySendable(value: json), output: output)
    }

    private func read(_ handle: FileHandle) -> Data {
        (try? handle.readToEnd()) ?? Data()
    }
}
