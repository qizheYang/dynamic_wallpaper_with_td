import XCTest
@testable import WallpaperCore

private final class FixtureCredentials: CredentialStore {
    var origin: String = "https://provider.example:443"
    func read(reference: String, endpoint: URL) throws -> String {
        guard try credentialOrigin(endpoint) == origin else { throw CredentialError.originMismatch }
        return "fixture-key-never-a-real-secret"
    }
    func save(secret: String, reference: String, endpoint: URL) throws { origin = try credentialOrigin(endpoint) }
    func delete(reference: String) throws {}
}

private final class FixtureTransport: AgentTransport {
    var request: URLRequest?
    var response: Data
    var status = 200
    init(_ response: Data) { self.response = response }
    func send(_ request: URLRequest, maxResponseBytes: Int) async throws -> (Data, HTTPURLResponse) {
        self.request = request
        return (response, HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: nil)!)
    }
}

private final class FixtureCreationRunner: CreationCommandRunning {
    var commands: [CreationCommand] = []
    var failExport = false
    var attempt: URL?
    func run(_ command: CreationCommand) async throws -> CreationCommandResult {
        commands.append(command)
        let fm = FileManager.default
        if command.label == "prepare" {
            let job = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: command.arguments[3]))) as! [String: Any]
            let output = job["output_dir"] as! String
            let attemptID = command.arguments[5]
            attempt = command.directory.appendingPathComponent(output + "/attempts/" + attemptID)
            try fm.createDirectory(at: attempt!, withIntermediateDirectories: true)
            try Data("{\"state\":\"rendering\"}".utf8).write(to: attempt!.appendingPathComponent("state.json"))
        }
        if command.label == "finalize", let attempt = attempt {
            let bundle = attempt.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("bundles/" + attempt.lastPathComponent)
            try fm.createDirectory(at: bundle, withIntermediateDirectories: true)
            for name in ["manifest.json", "loop.mp4", "cover.png"] { try Data("fixture".utf8).write(to: bundle.appendingPathComponent(name)) }
            try Data("{\"state\":\"complete\"}".utf8).write(to: attempt.appendingPathComponent("state.json"))
        }
        return CreationCommandResult(status: command.label == "export" && failExport ? 3 : 0,
                                     stdout: Data(), stderrLog: command.logDirectory.appendingPathComponent("fixture.log"))
    }
}

final class AgentTests: XCTestCase {
    var root: URL!
    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("wallpaper-agent-test-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        for path in ["skills/td-wallpaper/SKILL.md", "jobs/gradient_seed42_720p24.json", "td/shaders/soft_gradient.frag",
                     "td/calibration/soft_gradient-v1-td2025.33230.json"] {
            let url = root.appendingPathComponent(path)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data("fixture scoped context \(path)".utf8).write(to: url)
        }
    }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    func config(_ kind: AgentProviderKind = .openAICompatible) -> AgentProviderConfiguration {
        AgentProviderConfiguration(kind: kind, endpoint: URL(string: "https://provider.example/v1/messages")!,
                                   model: "user-selected-model", credentialReference: "fixture")
    }
    var proposal: String { "{\"kind\":\"soft_gradient\",\"brief\":\"Slow dusk, personal use\",\"questions\":[],\"parameters\":{\"seed\":42,\"motion_cycles\":2}}" }

    func testOpenAIRequestUsesBoundedTypedProposalAndActualScopedContext() async throws {
        let transport = FixtureTransport(try JSONSerialization.data(withJSONObject: ["choices": [
            ["finish_reason": "stop", "message": ["role": "assistant", "content": proposal]]]]))
        let result = try await AgentService(credentials: FixtureCredentials(), transport: transport)
            .propose(request: "Use a slow dusk gradient; personal use", configuration: config(), resourcesURL: root)
        XCTAssertEqual(result.kind, .softGradient)
        XCTAssertEqual(result.parameters, SoftGradientParameters(seed: 42, motionCycles: 2))
        let http = try XCTUnwrap(transport.request)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: http.httpBody!) as? [String: Any])
        XCTAssertEqual(body["max_completion_tokens"] as? Int, 2048)
        XCTAssertNil(body["tools"])
        let messages = try XCTUnwrap(body["messages"] as? [[String: Any]])
        let text = messages.compactMap { $0["content"] as? String }.joined(separator: "\n")
        XCTAssertTrue(text.contains("fixture scoped context skills/td-wallpaper/SKILL.md"))
        XCTAssertFalse(String(decoding: http.httpBody!, as: UTF8.self).contains("fixture-key-never-a-real-secret"))
        XCTAssertEqual(http.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-key-never-a-real-secret")
    }

    func testAnthropicShapeAndClarification() async throws {
        let text = "{\"kind\":\"clarification\",\"brief\":\"Usage is unclear\",\"questions\":[\"Personal or commercial?\"],\"parameters\":null}"
        let transport = FixtureTransport(try JSONSerialization.data(withJSONObject: ["stop_reason": "end_turn",
                                                                                  "content": [["type": "text", "text": text]]]))
        let result = try await AgentService(credentials: FixtureCredentials(), transport: transport)
            .propose(request: "A gradient", configuration: config(.anthropic), resourcesURL: root)
        XCTAssertEqual(result.kind, .clarification); XCTAssertNil(result.parameters)
        XCTAssertEqual(transport.request?.value(forHTTPHeaderField: "anthropic-version"), "2026-10-21")
        let body = try JSONSerialization.jsonObject(with: transport.request!.httpBody!) as! [String: Any]
        XCTAssertEqual(body["max_tokens"] as? Int, 2048); XCTAssertNotNil(body["system"])
    }

    func testUnsupportedStyleIsExplicitAndInvalidProposalsCannotBecomeJobs() throws {
        let response = Data("{\"kind\":\"new_template_required\",\"brief\":\"Smoke needs a new reviewed template\",\"questions\":[],\"parameters\":null}".utf8)
        XCTAssertEqual(try AuthoringProposal.parse(response).kind, .newTemplateRequired)
        for value in [proposal.replacingOccurrences(of: "\"seed\":42", with: "\"seed\":true"),
                      proposal.replacingOccurrences(of: "\"motion_cycles\":2", with: "\"motion_cycles\":9"),
                      proposal.replacingOccurrences(of: "\"seed\":42", with: "\"shell\":\"execute\",\"seed\":42"),
                      "```json\n" + proposal + "\n```"] {
            XCTAssertThrowsError(try AuthoringProposal.parse(Data(value.utf8)))
        }
    }

    func testEndpointPolicyAndCredentialOrigin() throws {
        for url in ["http://provider.example/v1", "https://key@provider.example/v1", "https://provider.example/v1?key=secret"] {
            XCTAssertThrowsError(try credentialOrigin(URL(string: url)!))
        }
        XCTAssertEqual(try credentialOrigin(URL(string: "https://PROVIDER.example/path")!), "https://provider.example:443")
        XCTAssertThrowsError(try FixtureCredentials().read(reference: "fixture", endpoint: URL(string: "https://other.example")!))
    }

    func testResponseLimitsAndHTTPBodyAreNotExposedAsErrors() async throws {
        let transport = FixtureTransport(Data("secret-provider-error-body".utf8)); transport.status = 401
        do {
            _ = try await AgentService(credentials: FixtureCredentials(), transport: transport)
                .propose(request: "Gradient", configuration: config(), resourcesURL: root)
            XCTFail("401 must fail")
        } catch { XCTAssertFalse(error.localizedDescription.contains("secret-provider-error-body")) }
        transport.status = 200; transport.response = Data(repeating: 65, count: 262_145)
        do {
            _ = try await AgentService(credentials: FixtureCredentials(), transport: transport)
                .propose(request: "Gradient", configuration: config(), resourcesURL: root)
            XCTFail("oversize response must fail")
        } catch { XCTAssertTrue(error.localizedDescription.contains("size limit")) }
    }

    func testContextCannotReadSymlinkOutsideScopedRoot() throws {
        let target = root.appendingPathComponent("skills/td-wallpaper/SKILL.md")
        try FileManager.default.removeItem(at: target)
        try FileManager.default.createSymbolicLink(at: target, withDestinationURL: URL(fileURLWithPath: "/etc/hosts"))
        XCTAssertThrowsError(try AgentService.loadContext(root))
    }

    func creationConfig() throws -> CreationConfiguration {
        for path in ["pipeline/__main__.py", "td/run_td.py", "td/wallpaper_template.toe",
                     "TD.app/Contents/MacOS/TouchDesigner"] {
            let file = root.appendingPathComponent(path)
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data("fixture".utf8).write(to: file)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
        }
        return CreationConfiguration(repository: root, python: URL(fileURLWithPath: "/usr/bin/true"),
                                     ffmpeg: URL(fileURLWithPath: "/usr/bin/true"), ffprobe: URL(fileURLWithPath: "/usr/bin/true"),
                                     touchDesignerApplication: root.appendingPathComponent("TD.app"))
    }

    func testCreationUsesOnlyFixedSequentialCommandsAndUniqueOutputs() async throws {
        let runner = FixtureCreationRunner()
        let configuration = try creationConfig()
        let service = CreationService(runner: runner)
        let result = try await service.generate(parameters: .init(seed: 7, motionCycles: 3), configuration: configuration)
        XCTAssertEqual(runner.commands.map(\.label), ["prepare", "export", "finalize"])
        XCTAssertTrue(result.path.contains("/bundles/app-"))
        XCTAssertEqual(runner.commands[1].arguments.prefix(2), ["td/run_td.py", "export"])
        XCTAssertNil(runner.commands[0].environment["OPENAI_API_KEY"])
        let second = try await service.generate(parameters: .init(), configuration: configuration)
        XCTAssertNotEqual(result, second)
    }

    func testExportFailureCancelsAttemptAndNeverFinalizes() async throws {
        let runner = FixtureCreationRunner(); runner.failExport = true
        do {
            _ = try await CreationService(runner: runner).generate(parameters: .init(), configuration: creationConfig())
            XCTFail("expected failed export")
        } catch { XCTAssertTrue(error.localizedDescription.contains("exit 3")) }
        XCTAssertEqual(runner.commands.map(\.label), ["prepare", "export", "cancel"])
    }

    func command(_ path: String, _ arguments: [String], timeout: TimeInterval = 3, limit: Int = 1024) -> CreationCommand {
        CreationCommand(executable: URL(fileURLWithPath: path), arguments: arguments, directory: root,
                        environment: ["PATH": "/usr/bin:/bin"], timeout: timeout,
                        logDirectory: root.appendingPathComponent(UUID().uuidString), label: "test", outputLimit: limit)
    }

    func testRealProcessCaptureAndHardDiagnosticLimit() async throws {
        let runner = LocalCreationCommandRunner()
        let output = try await runner.run(command("/usr/bin/printf", ["hello"]))
        XCTAssertEqual(output.status, 0); XCTAssertEqual(String(decoding: output.stdout, as: UTF8.self), "hello")
        let flood = command("/usr/bin/yes", [], limit: 1024)
        do { _ = try await runner.run(flood); XCTFail("output limit must stop child") }
        catch { XCTAssertTrue(error.localizedDescription.contains("output limit")) }
        let size = try flood.logDirectory.appendingPathComponent("test.stdout.log").resourceValues(forKeys: [.fileSizeKey]).fileSize
        XCTAssertLessThanOrEqual(try XCTUnwrap(size), 1024)
    }

    func testRealProcessTimeoutAndTaskCancellationAreBounded() async throws {
        let runner = LocalCreationCommandRunner()
        let started = Date()
        do { _ = try await runner.run(command("/bin/sleep", ["20"], timeout: 0.1)); XCTFail("timeout") }
        catch { XCTAssertTrue(error.localizedDescription.contains("time limit")) }
        let command = command("/bin/sleep", ["20"])
        let task = Task { try await runner.run(command) }
        try await Task.sleep(nanoseconds: 100_000_000); task.cancel()
        do { _ = try await task.value; XCTFail("cancel") } catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertLessThan(Date().timeIntervalSince(started), 8)
    }
}
