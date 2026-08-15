import Foundation
import Security
import UIKit
import UserNotifications

private enum ArgonKeychain {
  static let service = "com.niranjanj.argon"
  static let account = "api-token"

  static func read() -> String {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
      kSecReturnData as String: true,
      kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var result: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
      let data = result as? Data
    else {
      return ""
    }
    return String(data: data, encoding: .utf8) ?? ""
  }

  static func write(_ value: String) {
    let identity: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
    SecItemDelete(identity as CFDictionary)
    guard !value.isEmpty, let data = value.data(using: .utf8) else {
      return
    }
    var item = identity
    item[kSecValueData as String] = data
    SecItemAdd(item as CFDictionary, nil)
  }
}

struct ArgonActualState: Decodable {
  let mode: String
  let version: Int
  let shielded: Bool
  let lastSeen: String?

  enum CodingKeys: String, CodingKey {
    case mode
    case version
    case shielded
    case lastSeen = "last_seen"
  }
}

struct ArgonIOSState: Decodable {
  let desired: ArgonDesiredMode?
  let actual: ArgonActualState?

  private enum CodingKeys: String, CodingKey {
    case desired
    case actual
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    desired = try? container.decode(ArgonDesiredMode.self, forKey: .desired)
    actual = try? container.decode(ArgonActualState.self, forKey: .actual)
  }
}

struct ArgonStatusResponse: Decodable {
  let mode: String
  let currentTask: String?
  let workSessionMinutes: Int?
  let lockInMinutes: Int?
  let ios: ArgonIOSState?

  enum CodingKeys: String, CodingKey {
    case mode
    case currentTask = "current_task"
    case workSessionMinutes = "work_session_minutes"
    case lockInMinutes = "lock_in_minutes"
    case ios
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    mode = try container.decode(String.self, forKey: .mode)
    currentTask = try? container.decode(String.self, forKey: .currentTask)
    workSessionMinutes = try? container.decode(Int.self, forKey: .workSessionMinutes)
    lockInMinutes = try? container.decode(Int.self, forKey: .lockInMinutes)
    ios = try? container.decode(ArgonIOSState.self, forKey: .ios)
  }
}

private struct ArgonChatResponse: Decodable {
  let reply: String
}

private struct ArgonErrorResponse: Decodable {
  let error: String
}

@MainActor
final class ArgonBridge: ObservableObject {
  static let shared = ArgonBridge()

  @Published var serverURL: String {
    didSet { defaults.set(serverURL, forKey: Keys.serverURL) }
  }
  @Published var profileName: String {
    didSet { defaults.set(profileName, forKey: Keys.profileName) }
  }
  @Published private(set) var mode = "offline"
  @Published private(set) var desiredMode = "off"
  @Published private(set) var desiredReason = ""
  @Published private(set) var desiredExpiry: Date?
  @Published private(set) var currentTask: String?
  @Published private(set) var workMinutes = 0
  @Published private(set) var lockMinutes = 0
  @Published private(set) var connectionState = "Not paired"
  @Published private(set) var lastSync: Date?
  @Published private(set) var lastError: String?
  @Published private(set) var deviceToken: String
  @Published private(set) var isSendingMessage = false
  @Published private(set) var shielded = false
  @Published private(set) var tasks: [ArgonTask] = []
  @Published private(set) var taskDashboardState = ArgonTaskDashboardState.empty
  @Published private(set) var isLoadingTasks = false
  @Published private(set) var taskMutationIDs: Set<String> = []

  var apiToken: String {
    get { ArgonKeychain.read() }
    set {
      ArgonKeychain.write(newValue.trimmingCharacters(in: .whitespacesAndNewlines))
      connectionState = newValue.isEmpty ? "Not paired" : "Ready to connect"
      objectWillChange.send()
    }
  }

  var isConfigured: Bool {
    !apiToken.isEmpty && normalizedServerURL != nil
  }

  private enum Keys {
    static let serverURL = "argon.serverURL"
    static let profileName = "argon.profileName"
    static let deviceToken = "argon.deviceToken"
    static let taskDashboardCache = "argon.taskDashboard.cache"
  }

  private let defaults = UserDefaults.standard
  private var pollingTimer: Timer?

  private init() {
    // The public HTTPS endpoint, not the old SSH tunnel to 127.0.0.1:3995.
    // A TestFlight build runs on a phone that is not tethered to the Mac, so a
    // loopback default meant the app installed and could reach nothing at all.
    serverURL =
      defaults.string(forKey: Keys.serverURL)
      ?? "https://argon.agentneon.dev"
    profileName =
      defaults.string(forKey: Keys.profileName)
      ?? "Argon Lockdown"
    deviceToken = defaults.string(forKey: Keys.deviceToken) ?? ""
    if let data = defaults.data(forKey: Keys.taskDashboardCache),
      let cached = try? JSONDecoder().decode(ArgonTasksResponse.self, from: data)
    {
      tasks = cached.tasks
      taskDashboardState = cached.state
    }
    #if DEBUG
      if let injectedToken = ProcessInfo.processInfo.environment["ARGON_API_TOKEN"],
        !injectedToken.isEmpty
      {
        ArgonKeychain.write(injectedToken)
      }
    #endif
    if !ArgonKeychain.read().isEmpty {
      connectionState = "Ready to connect"
    }
  }

  func requestRemoteNotifications() {
    UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) {
      _, error in
      Task { @MainActor in
        if let error {
          self.lastError = error.localizedDescription
        }
        UIApplication.shared.registerForRemoteNotifications()
      }
    }
  }

  func updateDeviceToken(_ data: Data) {
    let token = data.map { String(format: "%02x", $0) }.joined()
    deviceToken = token
    defaults.set(token, forKey: Keys.deviceToken)
    Task {
      await registerDevice()
    }
  }

  func reportRemoteRegistrationError(_ error: Error) {
    lastError = "Push registration failed: \(error.localizedDescription)"
  }

  func connect() {
    lastError = nil
    connectionState = "Connecting…"
    requestRemoteNotifications()
    Task {
      if !deviceToken.isEmpty {
        await registerDevice()
      }
      _ = await refreshStatus()
    }
  }

  func startMonitoring() {
    pollingTimer?.invalidate()
    guard isConfigured else { return }
    Task {
      _ = await refreshStatus()
    }
    pollingTimer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { _ in
      Task { @MainActor in
        _ = await self.refreshStatus()
      }
    }
  }

  func stopMonitoring() {
    pollingTimer?.invalidate()
    pollingTimer = nil
  }

  @discardableResult
  func refreshStatus() async -> Bool {
    guard let request = makeRequest(path: "/v1/status") else {
      connectionState = "Needs setup"
      return false
    }

    do {
      let data = try await perform(request)
      let status = try JSONDecoder().decode(ArgonStatusResponse.self, from: data)
      mode = status.mode
      currentTask = status.currentTask
      workMinutes = status.workSessionMinutes ?? 0
      lockMinutes = status.lockInMinutes ?? 0
      connectionState = "Connected"
      lastSync = Date()
      lastError = nil

      if let desired = status.ios?.desired {
        desiredMode = desired.mode
        desiredReason = desired.reason
        desiredExpiry = desired.expiryDate
        let result = ArgonReconciler.shared.reconcile(
          desired,
          profileName: profileName
        )
        shielded = result.shielded
        if let failure = result.error {
          lastError = failure
        }
        // Reported either way — silence on failure looks exactly like a phone
        // that is switched off, and Argon would assume the lock had landed.
        await report(result)
      }
      return true
    } catch let decodingError as DecodingError {
      connectionState = "Protocol mismatch"
      lastError =
        "Argon returned an incompatible status payload: \(decodingError.localizedDescription)"
      return false
    } catch {
      connectionState = "Offline"
      lastError = error.localizedDescription
      return false
    }
  }

  func sendMessage(_ message: String) async -> String? {
    let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty,
      var request = makeRequest(path: "/v1/chat", method: "POST")
    else {
      return nil
    }

    isSendingMessage = true
    defer { isSendingMessage = false }
    request.httpBody = try? JSONSerialization.data(withJSONObject: ["message": trimmed])
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")

    do {
      let data = try await perform(request)
      lastError = nil
      return try JSONDecoder().decode(ArgonChatResponse.self, from: data).reply
    } catch {
      lastError = error.localizedDescription
      return nil
    }
  }

  @discardableResult
  func refreshTasks() async -> Bool {
    guard let request = makeRequest(path: "/v1/tasks") else { return false }
    isLoadingTasks = true
    defer { isLoadingTasks = false }

    do {
      let data = try await perform(request)
      applyTaskDashboard(try JSONDecoder().decode(ArgonTasksResponse.self, from: data))
      lastError = nil
      return true
    } catch {
      lastError = "Tasks could not refresh: \(error.localizedDescription)"
      return false
    }
  }

  @discardableResult
  func addTask(
    title: String,
    priority: String,
    subject: String?,
    due: Date?,
    estimateMinutes: Int?
  ) async -> Bool {
    var body: [String: Any] = [
      "title": title,
      "priority": priority,
      "source": "manual",
    ]
    if let subject, !subject.isEmpty { body["subject"] = subject }
    if let due { body["due"] = Self.taskDateFormatter.string(from: due) }
    if let estimateMinutes { body["time_estimate_min"] = estimateMinutes }

    do {
      let dashboard = try await performTaskRequest(path: "/v1/tasks", method: "POST", body: body)
      applyTaskDashboard(dashboard)
      lastError = nil
      return true
    } catch {
      lastError = "Task could not be added: \(error.localizedDescription)"
      return false
    }
  }

  @discardableResult
  func startTask(_ task: ArgonTask) async -> Bool {
    guard !taskMutationIDs.contains(task.id) else { return false }
    let previousState = taskDashboardState
    taskMutationIDs.insert(task.id)
    taskDashboardState.currentTask = task.title
    defer { taskMutationIDs.remove(task.id) }

    do {
      let dashboard = try await performTaskRequest(
        path: taskPath(task.id),
        method: "PATCH",
        body: ["action": "start"]
      )
      applyTaskDashboard(dashboard)
      lastError = nil
      return true
    } catch {
      taskDashboardState = previousState
      lastError = "Task could not start: \(error.localizedDescription)"
      return false
    }
  }

  @discardableResult
  func completeTask(_ task: ArgonTask) async -> Bool {
    guard !taskMutationIDs.contains(task.id) else { return false }
    let previousTasks = tasks
    let previousState = taskDashboardState
    taskMutationIDs.insert(task.id)
    tasks.removeAll { $0.id == task.id }
    if taskDashboardState.currentTask == task.title {
      taskDashboardState.currentTask = nil
    }
    defer { taskMutationIDs.remove(task.id) }

    do {
      let dashboard = try await performTaskRequest(
        path: taskPath(task.id),
        method: "PATCH",
        body: ["action": "complete"]
      )
      applyTaskDashboard(dashboard)
      lastError = nil
      return true
    } catch {
      tasks = previousTasks
      taskDashboardState = previousState
      lastError = "Task could not complete: \(error.localizedDescription)"
      return false
    }
  }

  @discardableResult
  func updateTask(_ task: ArgonTask, priority: String? = nil, due: String? = nil) async
    -> Bool
  {
    guard !taskMutationIDs.contains(task.id) else { return false }
    var body: [String: Any] = [:]
    if let priority { body["priority"] = priority }
    if let due { body["due"] = due }
    guard !body.isEmpty else { return false }

    let previousTasks = tasks
    taskMutationIDs.insert(task.id)
    if let index = tasks.firstIndex(where: { $0.id == task.id }), let priority {
      tasks[index].priority = priority
    }
    defer { taskMutationIDs.remove(task.id) }

    do {
      let dashboard = try await performTaskRequest(
        path: taskPath(task.id),
        method: "PATCH",
        body: body
      )
      applyTaskDashboard(dashboard)
      lastError = nil
      return true
    } catch {
      tasks = previousTasks
      lastError = "Task could not update: \(error.localizedDescription)"
      return false
    }
  }

  /// Tell the server about a local emergency release, so Argon stops asking for
  /// a block instead of the two of them fighting every 20 seconds. Best effort:
  /// the local override in ArgonOverride already holds without any network.
  /// Tell the server a release was engaged, or lifted early.
  ///
  /// `minutes <= 0` is *not* "cancel" to the server — it falls back to the
  /// configured default and engages one. Cancelling has to say `clear`
  /// explicitly, or turning the switch off would start a two-hour override
  /// instead of ending it.
  nonisolated func reportEmergencyOverride(minutes: Int) {
    Task { @MainActor in
      guard var request = self.makeRequest(path: "/v1/ios/override", method: "POST") else {
        return
      }
      let payload: [String: Any] =
        minutes > 0
        ? ["minutes": minutes, "source": "phone"]
        : ["clear": true, "source": "phone"]
      request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      _ = try? await self.perform(request)
      _ = await self.refreshStatus()
    }
  }

  private func registerDevice() async {
    guard !deviceToken.isEmpty,
      var request = makeRequest(path: "/v1/ios/register", method: "POST")
    else {
      return
    }

    #if DEBUG
      let environment = "sandbox"
    #else
      let environment = "production"
    #endif

    request.httpBody = try? JSONSerialization.data(
      withJSONObject: [
        "device_token": deviceToken,
        "environment": environment,
        "app_version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
          ?? "unknown",
      ]
    )
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")

    do {
      _ = try await perform(request)
    } catch {
      lastError = "Device registration failed: \(error.localizedDescription)"
    }
  }

  private func report(_ result: ArgonReconcileResult) async {
    guard var request = makeRequest(path: "/v1/ios/state", method: "POST") else {
      return
    }
    var payload: [String: Any] = [
      "mode": result.mode,
      "version": result.version,
      "shielded": result.shielded,
      "applied_at": ISO8601DateFormatter().string(from: Date()),
      "battery": UIDevice.current.batteryLevel,
    ]
    // Sent only on failure, so Argon can say *why* a block never landed
    // instead of just reporting the phone as unreachable.
    if let failure = result.error {
      payload["error"] = failure
    }
    request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    _ = try? await perform(request)
  }

  private func applyTaskDashboard(_ dashboard: ArgonTasksResponse) {
    tasks = dashboard.tasks
    taskDashboardState = dashboard.state
    if let data = try? JSONEncoder().encode(dashboard) {
      defaults.set(data, forKey: Keys.taskDashboardCache)
    }
  }

  private func performTaskRequest(
    path: String,
    method: String,
    body: [String: Any]
  ) async throws -> ArgonTasksResponse {
    guard var request = makeRequest(path: path, method: method) else {
      throw URLError(.badURL)
    }
    request.httpBody = try JSONSerialization.data(withJSONObject: body)
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    let data = try await perform(request)
    return try JSONDecoder().decode(ArgonTasksResponse.self, from: data)
  }

  private func taskPath(_ id: String) -> String {
    var allowed = CharacterSet.urlPathAllowed
    allowed.remove(charactersIn: "/?#")
    let encoded = id.addingPercentEncoding(withAllowedCharacters: allowed) ?? id
    return "/v1/tasks/\(encoded)"
  }

  private static let taskDateFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = .autoupdatingCurrent
    formatter.dateFormat = "yyyy-MM-dd"
    return formatter
  }()

  private var normalizedServerURL: URL? {
    let value = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
      .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    guard let url = URL(string: value),
      let scheme = url.scheme?.lowercased(),
      scheme == "http" || scheme == "https"
    else {
      return nil
    }
    return url
  }

  private func makeRequest(path: String, method: String = "GET") -> URLRequest? {
    guard let baseURL = normalizedServerURL, !apiToken.isEmpty,
      let url = URL(string: path, relativeTo: baseURL)
    else {
      lastError = "Enter the Argon server URL and API token."
      return nil
    }
    var request = URLRequest(url: url)
    request.httpMethod = method
    request.timeoutInterval = 120
    request.setValue("Bearer \(apiToken)", forHTTPHeaderField: "Authorization")
    return request
  }

  private func perform(_ request: URLRequest) async throws -> Data {
    let (data, response) = try await URLSession.shared.data(for: request)
    guard let httpResponse = response as? HTTPURLResponse else {
      throw URLError(.badServerResponse)
    }
    guard (200..<300).contains(httpResponse.statusCode) else {
      if let response = try? JSONDecoder().decode(ArgonErrorResponse.self, from: data) {
        throw NSError(
          domain: "ArgonBridge",
          code: httpResponse.statusCode,
          userInfo: [NSLocalizedDescriptionKey: response.error]
        )
      }
      throw URLError(.badServerResponse)
    }
    return data
  }
}
