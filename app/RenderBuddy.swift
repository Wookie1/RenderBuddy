// RenderBuddy's window: turns the STEVE connector on and off, pairs it with STEVE, and removes it.
// make_app.sh compiles this with swiftc and puts the program folder's path in Info.plist (RBFolder).
import AppKit
import SwiftUI

let home = FileManager.default.homeDirectoryForCurrentUser
let stateDir = home.appendingPathComponent(".renderbuddy")
let programDir = URL(fileURLWithPath: (Bundle.main.object(forInfoDictionaryKey: "RBFolder") as? String)
    ?? home.appendingPathComponent("RenderBuddy").path)
let logFile = stateDir.appendingPathComponent("connector.log")
let accent = Color(red: 0.91, green: 0.52, blue: 0.10)  // the sun gear's orange

enum Phase: Equatable {
    case notPaired, off, starting, confirming, ready, rendering(Double, String)
}

/// Runs a program and returns its exit code and everything it printed.
func runProgram(_ path: String, _ args: [String]) async -> (Int32, String) {
    await withCheckedContinuation { done in
        DispatchQueue.global().async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: path)
            process.arguments = args
            process.currentDirectoryURL = programDir
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            do {
                try process.run()
            } catch {
                done.resume(returning: (-1, error.localizedDescription))
                return
            }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            done.resume(returning: (process.terminationStatus, String(decoding: data, as: UTF8.self)))
        }
    }
}

func readJSON(_ url: URL) -> [String: Any] {
    guard let data = try? Data(contentsOf: url),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
    return object
}

@MainActor
final class Model: ObservableObject {
    @Published var phase: Phase = .off
    @Published var fingerprint = ""
    @Published var pairCode = ""
    @Published var pairing = false       // the pairing form is showing (first run, or Pair Again)
    @Published var working = false       // a pairing request is under way
    @Published var problem: String?
    @Published var lastStop: String?
    @Published var idleMinutes = 30
    @Published var askRemove = false
    private var startedAt: Date?
    private var timer: Timer?

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    var python: String { programDir.appendingPathComponent(".venv/bin/python").path }

    var runningPid: Int32? {
        guard let text = try? String(contentsOf: stateDir.appendingPathComponent("connector.pid"), encoding: .utf8),
              let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines)), kill(pid, 0) == 0 else { return nil }
        return pid
    }

    func refresh() {
        let config = readJSON(stateDir.appendingPathComponent("config.json"))
        if let minutes = config["idle_quit_minutes"] as? Int { idleMinutes = minutes }
        if config["connector"] == nil {
            phase = .notPaired
            return
        }
        let status = readJSON(stateDir.appendingPathComponent("status.json"))
        guard runningPid != nil else {
            if let started = startedAt, Date().timeIntervalSince(started) < 6 {
                phase = .starting
            } else {
                phase = .off
                startedAt = nil
                lastStop = (status["state"] as? String) == "off" ? status["why"] as? String : nil
            }
            return
        }
        switch status["state"] as? String {
        case "confirming":
            phase = .confirming
            if let value = status["fingerprint"] as? String { fingerprint = value }
        case "idle":
            phase = .ready
        case "rendering":
            phase = .rendering(status["progress"] as? Double ?? 0, status["message"] as? String ?? "")
        default:
            phase = .starting
        }
    }

    func turnOn() {
        problem = nil
        startedAt = Date()
        phase = .starting
        let folder = programDir.path.replacingOccurrences(of: "'", with: "'\\''")
        // Only the last command goes to the background, with every output redirected, so this returns at once.
        let command = "cd '\(folder)' || exit 1; mkdir -p \"$HOME/.renderbuddy\"; " +
            "nohup .venv/bin/python renderbuddy.py run >> \"$HOME/.renderbuddy/connector.log\" 2>&1 < /dev/null &"
        Task {
            let (code, output) = await runProgram("/bin/sh", ["-c", command])
            if code != 0 { problem = "RenderBuddy didn't start: \(output)" }
        }
    }

    func turnOff() {
        if let pid = runningPid { kill(pid, SIGTERM) }
        startedAt = nil
        phase = .off
    }

    func pair() {
        let code = pairCode.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !code.isEmpty else { return }
        working = true
        problem = nil
        if runningPid != nil { turnOff() }
        Task {
            let (status, output) = await runProgram(python, ["renderbuddy.py", "pair", code])
            working = false
            let found = output.range(of: "SHA256:[A-Za-z0-9+/=_-]+", options: .regularExpression).map { String(output[$0]) }
            if status == 0, let found {
                fingerprint = found
                copy(found)
                pairCode = ""
                pairing = false
                turnOn()
            } else {
                let last = output.split(separator: "\n").last.map(String.init) ?? "No answer."
                problem = "That didn't work. \(last) A code works once and expires after 10 minutes, so get a fresh one."
            }
        }
    }

    func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    func showLog() {
        if !FileManager.default.fileExists(atPath: logFile.path) {
            FileManager.default.createFile(atPath: logFile.path, contents: nil)
        }
        NSWorkspace.shared.open(logFile)
    }

    /// Moves RenderBuddy to the Trash: the program folder, its settings, and this app.
    func remove(keepRenders: Bool) {
        turnOff()
        let files = FileManager.default
        var note = ""
        let renders = programDir.appendingPathComponent("renders")
        if keepRenders, files.fileExists(atPath: renders.path) {
            let movies = home.appendingPathComponent("Movies")
            var target = movies.appendingPathComponent("RenderBuddy renders")
            if files.fileExists(atPath: target.path) {
                target = movies.appendingPathComponent("RenderBuddy renders \(Int(Date().timeIntervalSince1970))")
            }
            try? files.createDirectory(at: movies, withIntermediateDirectories: true)
            if (try? files.moveItem(at: renders, to: target)) != nil {
                note = "\n\nYour renders are in Movies → \(target.lastPathComponent)."
            }
        }
        for url in [programDir, stateDir, Bundle.main.bundleURL] where files.fileExists(atPath: url.path) {
            try? files.trashItem(at: url, resultingItemURL: nil)
        }
        let alert = NSAlert()
        alert.messageText = "RenderBuddy is in the Trash"
        alert.informativeText = "One last step: in STEVE, remove the RenderBuddy connector under Settings → Connectors." + note
        alert.runModal()
        NSApp.terminate(nil)
    }

    func open(_ url: URL) {
        switch url.host?.lowercased() {
        case "stop": turnOff()
        case "uninstall": askRemove = true
        default: if runningPid == nil && phase != .notPaired { turnOn() }
        }
    }
}

// MARK: - Views

struct ContentView: View {
    @EnvironmentObject var model: Model

    var body: some View {
        VStack(spacing: 0) {
            header
            Group {
                if model.phase == .notPaired || model.pairing {
                    PairView()
                } else {
                    StatusView()
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 8)
            Spacer(minLength: 16)
            Divider()
            footer
        }
        .frame(width: 400, height: 470)
        .confirmationDialog("Remove RenderBuddy from this Mac?", isPresented: $model.askRemove) {
            Button("Remove, Keep My Renders") { model.remove(keepRenders: true) }
            Button("Remove Everything", role: .destructive) { model.remove(keepRenders: false) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("RenderBuddy, its Python setup and its settings go to the Trash. You can keep your renders: they move to Movies.")
        }
        .onOpenURL { model.open($0) }
    }

    var header: some View {
        HStack(spacing: 14) {
            Image(nsImage: NSApp.applicationIconImage)
                .resizable()
                .frame(width: 56, height: 56)
            VStack(alignment: .leading, spacing: 2) {
                Text("RenderBuddy").font(.title2.weight(.semibold))
                Text("Renders your STEVE designs in Blender, on this Mac")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(.horizontal, 24)
        .padding(.top, 28)
        .padding(.bottom, 16)
    }

    var footer: some View {
        HStack(spacing: 16) {
            Button { model.showLog() } label: { Label("Log", systemImage: "doc.text") }
            if model.phase != .notPaired && !model.pairing {
                Button { model.pairing = true } label: { Label("Pair Again", systemImage: "link") }
            }
            Spacer()
            Button(role: .destructive) { model.askRemove = true } label: { Label("Remove…", systemImage: "trash") }
                .foregroundStyle(.red)
        }
        .buttonStyle(.borderless)
        .font(.callout)
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }
}

struct StatusView: View {
    @EnvironmentObject var model: Model

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            card
            switch model.phase {
            case .off:
                Button { model.turnOn() } label: {
                    Label("Turn On", systemImage: "power").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)
            case .ready, .rendering, .confirming:
                Button { model.turnOff() } label: {
                    Label("Turn Off", systemImage: "power").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            default:
                EmptyView()
            }
            if let problem = model.problem {
                Text(problem).font(.callout).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    var card: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Circle()
                    .fill(dotColor)
                    .frame(width: 10, height: 10)
                    .shadow(color: dotColor.opacity(0.7), radius: 4)
                Text(title).font(.headline)
                Spacer()
                if model.phase == .starting || model.phase == .confirming {
                    ProgressView().controlSize(.small)
                }
            }
            if case .rendering(let progress, _) = model.phase {
                ProgressView(value: progress).tint(accent)
            }
            Text(detail)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if model.phase == .confirming && !model.fingerprint.isEmpty {
                HStack {
                    Text(model.fingerprint)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button("Copy") { model.copy(model.fingerprint) }.controlSize(.small)
                }
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(0.06)))
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(nsColor: .controlBackgroundColor))
                .shadow(color: .black.opacity(0.08), radius: 3, y: 1)
        )
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.primary.opacity(0.08)))
    }

    var dotColor: Color {
        switch model.phase {
        case .ready: return .green
        case .rendering: return accent
        case .starting, .confirming: return .yellow
        default: return .gray
        }
    }

    var title: String {
        switch model.phase {
        case .ready: return "On and ready"
        case .rendering(let progress, _): return "Rendering · \(Int(progress * 100))%"
        case .starting: return "Starting…"
        case .confirming: return "Waiting for STEVE"
        default: return "Off"
        }
    }

    var detail: String {
        switch model.phase {
        case .ready:
            return model.idleMinutes > 0
                ? "STEVE can send render jobs now. RenderBuddy turns itself off after \(model.idleMinutes) minutes without jobs."
                : "STEVE can send render jobs now."
        case .rendering(_, let message):
            return message.isEmpty ? "Working on a job from STEVE." : message
        case .starting:
            return "Connecting to STEVE."
        case .confirming:
            return "In STEVE, paste this fingerprint to confirm it's your Mac. It's already on your clipboard."
        default:
            if let why = model.lastStop { return "Turned off: \(why)." }
            return "Turn it on when you want STEVE to render on this Mac."
        }
    }
}

struct PairView: View {
    @EnvironmentObject var model: Model

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Connect to STEVE").font(.headline)
            VStack(alignment: .leading, spacing: 10) {
                step(1, "In STEVE, open **Settings → Connectors** and click **Pair**.")
                step(2, "Copy the pairing code and paste it below.")
                step(3, "STEVE asks you to confirm a fingerprint. RenderBuddy copies it for you.")
                step(4, "Allow RenderBuddy's actions. For safety, let **agents'** jobs wait for your OK.")
            }
            TextField("Pairing code", text: $model.pairCode)
                .textFieldStyle(.roundedBorder)
                .font(.system(.title3, design: .monospaced))
                .controlSize(.large)
                .onSubmit { model.pair() }
            HStack {
                if model.phase != .notPaired {
                    Button("Cancel") { model.pairing = false; model.problem = nil }.controlSize(.large)
                }
                Button { model.pair() } label: {
                    HStack {
                        if model.working { ProgressView().controlSize(.small) }
                        Text(model.working ? "Pairing…" : "Pair")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)
                .disabled(model.working || model.pairCode.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let problem = model.problem {
                Text(problem).font(.callout).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    func step(_ number: Int, _ text: LocalizedStringKey) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "\(number).circle.fill").foregroundStyle(accent)
            Text(text).font(.callout).fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct RenderBuddyApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = Model()

    var body: some Scene {
        Window("RenderBuddy", id: "main") {
            ContentView()
                .environmentObject(model)
                .tint(accent)
        }
        .windowResizability(.contentSize)
        .windowStyle(.hiddenTitleBar)
    }
}
