import Foundation
import FamilyControls
import Security
import UIKit
import UserNotifications
import WidgetKit

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

struct ArgonAgendaEvent: Decodable {
  let summary: String
  let start: String
}

struct ArgonStatusResponse: Decodable {
  let mode: String
  let currentTask: String?
  let workSessionMinutes: Int?
  let lockInMinutes: Int?
  let ios: ArgonIOSState?
  let agenda: [ArgonAgendaEvent]?

  enum CodingKeys: String, CodingKey {
    case mode
    case currentTask = "current_task"
    case workSessionMinutes = "work_session_minutes"
    case lockInMinutes = "lock_in_minutes"
    case ios
    case agenda
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    mode = try container.decode(String.self, forKey: .mode)
    currentTask = try? container.decode(String.self, forKey: .currentTask)
    workSessionMinutes = try? container.decode(Int.self, forKey: .workSessionMinutes)
    lockInMinutes = try? container.decode(Int.self, forKey: .lockInMinutes)
    ios = try? container.decode(ArgonIOSState.self, forKey: .ios)
    agenda = try? container.decode([ArgonAgendaEvent].self, forKey: .agenda)
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
  private(set) var nextEvent: ArgonWidgetSnapshot.Event?
  @Published private(set) var acUnitsCache: [ArgonACUnit] = []
  /// Which address actually answered. Shown in Settings because "it works at
  /// home and not on cellular" was invisible for weeks: the app had one
  /// address, and nothing on screen said which one it was using.
  @Published private(set) var activeAddress = ""

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
  /// Whichever base answered last. Process-local on purpose: it must not
  /// survive walking out of the house with the LAN address still pinned.
  private var pinnedBase: URL? {
    didSet { activeAddress = pinnedBase?.host ?? "" }
  }

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
      nextEvent = status.agenda?.first.flatMap { event in
        ArgonServerDate.parse(event.start).map {
          ArgonWidgetSnapshot.Event(summary: event.summary, start: $0)
        }
      }

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

        // Diagnostics only when something is actually being metered or a
        // reconcile failed. Reporting on every poll would bury the interesting
        // entries under a few thousand identical ones.
        if desired.isMetered || result.error != nil {
          await reportDiagnostics("reconcile", note: result.message)
        }
      }
      publishWidgetSnapshot()
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

  /// What the afternoon planning screen should ask about.
  func fetchPlanner() async -> ArgonPlannerPayload? {
    guard let request = makeRequest(path: "/v1/planner") else { return nil }
    do {
      return try JSONDecoder().decode(ArgonPlannerPayload.self, from: try await perform(request))
    } catch {
      // Quiet: the planner is a bonus on top of the task list, and a failure
      // here must not replace a more useful error already on screen.
      return nil
    }
  }

  /// Apply the decisions, and record that today has been planned.
  ///
  /// Sent even when nothing was chosen. "I looked and nothing needs moving" is
  /// an answer, and without recording it the screen reopens on next launch.
  @discardableResult
  func submitPlan(
    done: [String],
    carry: [String],
    add: [[String: Any]],
    chem: Bool,
    startAt: String? = nil
  ) async -> Bool {
    guard var request = makeRequest(path: "/v1/planner", method: "POST") else { return false }
    var body: [String: Any] = ["done": done, "carry": carry, "add": add, "chem": chem]
    // Sent even as null: clearing the time has to cancel the armed jobs, not
    // leave a block waiting for a moment he has moved away from.
    body["start_at"] = startAt as Any? ?? NSNull()
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try? JSONSerialization.data(withJSONObject: body)
    do {
      _ = try await perform(request)
      await refreshTasks()
      return true
    } catch {
      lastError = "Could not save the plan: \(error.localizedDescription)"
      return false
    }
  }

  /// Every adopted air conditioner, with live state.
  func acUnits() async -> [ArgonACUnit] {
    guard let request = makeRequest(path: "/v1/ac") else { return [] }
    do {
      let data = try await perform(request)
      let response = try JSONDecoder().decode(ArgonACResponse.self, from: data)
      acUnitsCache = response.units
      return response.units
    } catch {
      lastError = "Could not reach the AC: \(error.localizedDescription)"
      return []
    }
  }

  /// Change one unit. `power` may be true, false, or nil for toggle.
  ///
  /// Toggle is resolved on the server rather than here: one Action Button press
  /// means "the other thing", and deciding that on the phone would mean reading
  /// the state first and racing a second press against the first.
  @discardableResult
  func setAC(
    mac: String,
    power: Bool? = nil,
    targetC: Int? = nil,
    mode: String? = nil,
    fan: Int? = nil
  ) async -> ArgonACUnit? {
    let encoded = mac.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? mac
    guard var request = makeRequest(path: "/v1/ac/\(encoded)", method: "POST") else { return nil }

    var body: [String: Any] = [:]
    body["power"] = power.map { $0 as Any } ?? ("toggle" as Any)
    if let targetC { body["target_c"] = targetC }
    if let mode { body["mode"] = mode }
    if let fan { body["fan"] = fan }

    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try? JSONSerialization.data(withJSONObject: body)
    do {
      let data = try await perform(request)
      let unit = try JSONDecoder().decode(ArgonACUnit.self, from: data)
      lastError = nil
      return unit
    } catch {
      lastError = "AC command failed: \(error.localizedDescription)"
      return nil
    }
  }

  /// Turn a focus mode on or off from a switch, without asking Argon to.
  ///
  /// Goes straight to the server rather than through a message. Asking the
  /// model to set a mode means hoping it calls the tool, and after 11 PM that
  /// tool deliberately refuses until he confirms in a second message — a good
  /// guard against Argon deciding to lock his phone, and a bad way to reach a
  /// switch he is holding.
  @discardableResult
  func setFocusMode(
    _ mode: String,
    allowanceMinutes: Int? = nil,
    perHours: Int? = nil
  ) async -> Bool {
    guard var request = makeRequest(path: "/v1/ios/mode", method: "POST") else { return false }
    var body: [String: Any] = ["mode": mode, "reason": "set from the phone"]
    if let allowanceMinutes { body["allowance_min"] = allowanceMinutes }
    if let perHours { body["allowance_per_hours"] = perHours }

    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try? JSONSerialization.data(withJSONObject: body)
    do {
      _ = try await perform(request)
      lastError = nil
      // Apply it now rather than waiting for the next poll, so the switch
      // moving and the shield changing feel like one action.
      _ = await refreshStatus()
      return true
    } catch {
      lastError = "Could not set \(mode): \(error.localizedDescription)"
      return false
    }
  }

  /// Send everything about Screen Time that only exists on the device.
  ///
  /// The state report carries a mode, a version and one error string, which
  /// answers "did it work" and nothing about why. Every question worth asking
  /// about weekend mode — is Family Controls authorised, was the profile found,
  /// how many apps is it actually metering, is monitoring running, is the
  /// shield up — was answerable only here, which is why diagnosing it meant
  /// guessing.
  func reportDiagnostics(_ kind: String, note: String? = nil) async {
    guard var request = makeRequest(path: "/v1/ios/diagnostics", method: "POST") else { return }

    let selection = SharedData.argonMeteredSelection()
    var payload: [String: Any] = [
      "kind": kind,
      "app_version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?",
      "build": Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?",
      "screen_time_authorized":
        AuthorizationCenter.shared.authorizationStatus == .approved,
      "profile_name": profileName,
      "desired_mode": desiredMode,
      "shielded": shielded,
      "metered_monitoring": ArgonMetered.isMonitoring,
      "metered_apps": selection?.applicationTokens.count ?? 0,
      "metered_categories": selection?.categoryTokens.count ?? 0,
      "override_active": ArgonOverride.isActive,
    ]
    if let terms = ArgonMetered.Terms.current {
      payload["metered_minutes"] = terms.minutes
      payload["metered_window_hours"] = terms.windowHours
      payload["metered_budget_spent"] = terms.spent
    }
    if let note { payload["note"] = note }
    if let lastError { payload["last_error"] = lastError }

    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
    _ = try? await perform(request)
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

  /// Hand the widgets what they render, and ask WidgetKit to redraw.
  ///
  /// Called after every sync rather than on a timer: the widget cannot fetch
  /// for itself (the API token lives in this app's keychain, not the shared
  /// group), so this is the only thing keeping it current.
  private func publishWidgetSnapshot() {
    let calendar = Calendar.autoupdatingCurrent
    let startOfToday = calendar.startOfDay(for: Date())

    // Today and overdue only. A widget showing the full backlog is a list you
    // stop reading, which makes it worth exactly nothing.
    let relevant = tasks.filter { task in
      guard !task.done else { return false }
      guard let due = task.dueDay else { return false }
      return due < startOfToday || calendar.isDateInToday(due)
    }

    let items = relevant.map { task -> ArgonWidgetSnapshot.Item in
      let overdue = (task.dueDay.map { $0 < startOfToday }) ?? false
      return ArgonWidgetSnapshot.Item(
        id: task.id,
        title: task.title,
        priority: task.priority,
        dueLabel: overdue ? "Overdue" : "Today",
        overdue: overdue,
        running: task.startedAt != nil
      )
    }

    ArgonWidgetStore.write(
      ArgonWidgetSnapshot(
        capturedAt: Date(),
        mode: taskDashboardState.mode,
        currentTask: taskDashboardState.currentTask,
        shielded: shielded,
        items: items,
        nextEvent: nextEvent,
        waitingMessages: 0
      )
    )
    WidgetCenter.shared.reloadAllTimelines()
  }

  private func applyTaskDashboard(_ dashboard: ArgonTasksResponse) {
    tasks = dashboard.tasks
    taskDashboardState = dashboard.state
    if let data = try? JSONEncoder().encode(dashboard) {
      defaults.set(data, forKey: Keys.taskDashboardCache)
    }
    // Every path that changes the task list lands here — refresh, add, tick
    // off, reprioritise — so this is the one place the widgets need updating.
    publishWidgetSnapshot()
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

  private func normalized(_ value: String) -> URL? {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
      .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    guard let url = URL(string: trimmed),
      let scheme = url.scheme?.lowercased(),
      scheme == "http" || scheme == "https"
    else {
      return nil
    }
    return url
  }

  private var normalizedServerURL: URL? { normalized(serverURL) }

  /// Every address worth trying, in preference order.
  ///
  /// The app used to have exactly one. Set it to the LAN address once — which
  /// is the fast, obvious thing to do at home — and Argon becomes unreachable
  /// everywhere else, including on cellular, which reads as "the server is
  /// down" rather than "this address only exists at home".
  ///
  /// The public address is always in the list and is not editable, so it
  /// cannot be lost by typing over it.
  var bases: [URL] {
    var found: [URL] = []
    for candidate in [normalizedServerURL, normalized(Self.publicURL)] {
      guard let candidate else { continue }
      if !found.contains(candidate) { found.append(candidate) }
    }
    return found
  }

  static let publicURL = "https://argon.agentneon.dev"

  private func makeRequest(path: String, method: String = "GET") -> URLRequest? {
    // Whichever base answered last, so the usual request is a single attempt.
    let ordered = pinnedBase.map { pin in [pin] + bases.filter { $0 != pin } } ?? bases
    guard let baseURL = ordered.first, !apiToken.isEmpty,
      let url = URL(string: path, relativeTo: baseURL)
    else {
      lastError = "Enter the Argon server URL and API token."
      return nil
    }
    var request = URLRequest(url: url)
    request.httpMethod = method
    // Short: a LAN address that is not on this network has to fail fast so the
    // fallback is tried while he is still looking at the screen. Two minutes
    // of spinner is indistinguishable from broken.
    request.timeoutInterval = baseURL.host?.hasPrefix("192.168.") == true ? 4 : 30
    request.setValue("Bearer \(apiToken)", forHTTPHeaderField: "Authorization")
    return request
  }

  /// Rebuild a request against a different base, keeping method, headers, body.
  private func rebased(_ request: URLRequest, onto base: URL) -> URLRequest? {
    guard let original = request.url,
      let path = URLComponents(url: original, resolvingAgainstBaseURL: false)
        .map({ $0.percentEncodedPath + ($0.percentEncodedQuery.map { "?" + $0 } ?? "") }),
      let url = URL(string: path, relativeTo: base)
    else {
      return nil
    }
    var copy = request
    copy.url = url
    copy.timeoutInterval = base.host?.hasPrefix("192.168.") == true ? 4 : 30
    return copy
  }

  /// Send a request, falling back to the other base if this one cannot be
  /// reached at all.
  ///
  /// Only connection failures fall through. A 401 or a 500 is the server
  /// answering, and retrying that elsewhere would just ask a second machine
  /// the same question.
  private func perform(_ request: URLRequest) async throws -> Data {
    do {
      let data = try await attempt(request)
      if let host = request.url { pinnedBase = base(of: host) }
      return data
    } catch let error as URLError where Self.unreachable.contains(error.code) {
      let current = request.url.flatMap(base(of:))
      for alternative in bases where alternative != current {
        guard let retry = rebased(request, onto: alternative) else { continue }
        do {
          let data = try await attempt(retry)
          // Pin it so the next request goes straight here rather than waiting
          // on the dead address again.
          pinnedBase = alternative
          return data
        } catch {
          continue
        }
      }
      throw error
    }
  }

  /// URLErrors that mean "this address is not answering", as opposed to the
  /// server answering with something unwelcome.
  private static let unreachable: Set<URLError.Code> = [
    .cannotConnectToHost, .cannotFindHost, .timedOut,
    .networkConnectionLost, .notConnectedToInternet, .dnsLookupFailed,
    .secureConnectionFailed, .resourceUnavailable,
  ]

  private func base(of url: URL) -> URL? {
    bases.first { candidate in
      url.host == candidate.host && url.port == candidate.port
    }
  }

  private func attempt(_ request: URLRequest) async throws -> Data {
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
