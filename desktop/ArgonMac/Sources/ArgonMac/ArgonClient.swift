import Foundation

/// Where the server is and how to prove you may ask it.
///
/// Reads the same `~/.config/argon/desktop.json` the Python readout used, so
/// replacing that stack costs no reconfiguration — the file is already there,
/// already `chmod 600`, and already correct.
struct ArgonConfig {
  let bases: [URL]
  let token: String

  static func load() -> ArgonConfig? {
    let env = ProcessInfo.processInfo.environment
    if let url = env["ARGON_URL"], let token = env["ARGON_TOKEN"],
      let parsed = URL(string: url)
    {
      return ArgonConfig(bases: [parsed], token: token)
    }

    let path = FileManager.default.homeDirectoryForCurrentUser
      .appendingPathComponent(".config/argon/desktop.json")
    guard let data = try? Data(contentsOf: path),
      let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      let token = object["token"] as? String, !token.isEmpty
    else {
      return nil
    }

    // `url` may be a single string or a list, and the file on disk also carries
    // a `remoteUrl` — the LAN address and the tunnel, so the readout keeps
    // working when he is out of the house. Order is preserved: first reachable
    // base wins, and the local one being first is what keeps it fast at home.
    var candidates: [String] = []
    if let single = object["url"] as? String { candidates = [single] }
    if let many = object["url"] as? [String] { candidates = many }
    if let many = object["urls"] as? [String] { candidates += many }
    if let remote = object["remoteUrl"] as? String { candidates.append(remote) }

    let bases = candidates.compactMap { URL(string: $0.trimmingCharacters(in: .whitespaces)) }
    return bases.isEmpty ? nil : ArgonConfig(bases: bases, token: token)
  }
}

enum ArgonClientError: LocalizedError {
  case notConfigured
  case unauthorized
  case unreachable(String)

  var errorDescription: String? {
    switch self {
    case .notConfigured:
      return "No ~/.config/argon/desktop.json"
    case .unauthorized:
      return "Token rejected"
    case .unreachable(let detail):
      return detail
    }
  }
}

actor ArgonClient {
  private let session: URLSession

  init() {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.timeoutIntervalForRequest = 8
    configuration.waitsForConnectivity = false
    session = URLSession(configuration: configuration)
  }

  /// Try each configured base in turn.
  ///
  /// The LAN address is fast when he is home and simply absent when he is not,
  /// so falling through to the tunnel matters more than it sounds — without it
  /// the readout is blank exactly when he is out and most wants it.
  private func request(
    _ path: String,
    method: String = "GET",
    body: [String: Any]? = nil
  ) async throws -> Data {
    guard let config = ArgonConfig.load() else { throw ArgonClientError.notConfigured }

    var lastError: Error = ArgonClientError.unreachable("no route")
    for base in config.bases {
      guard let url = URL(string: path, relativeTo: base) else { continue }
      var request = URLRequest(url: url)
      request.httpMethod = method
      request.setValue("Bearer \(config.token)", forHTTPHeaderField: "Authorization")
      if let body {
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      }

      do {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
          lastError = ArgonClientError.unreachable("bad response")
          continue
        }
        if http.statusCode == 401 { throw ArgonClientError.unauthorized }
        guard (200..<300).contains(http.statusCode) else {
          lastError = ArgonClientError.unreachable("HTTP \(http.statusCode)")
          continue
        }
        return data
      } catch let error as ArgonClientError {
        throw error  // A rejected token is not something another base will fix.
      } catch {
        lastError = error
        continue
      }
    }
    throw lastError
  }

  /// One poll: everything both surfaces need, assembled into one value.
  ///
  /// Tasks and inbox are allowed to fail independently. A dead Google grant
  /// should not blank the focus readout, which is served from local state.
  func fetch() async -> ArgonSnapshot {
    var snapshot = ArgonSnapshot.empty
    snapshot.capturedAt = Date()

    do {
      let data = try await request("/v1/tasks")
      let response = try JSONDecoder().decode(ArgonTasksResponse.self, from: data)
      snapshot.mode = response.state.mode
      snapshot.currentTask = response.state.currentTask
      snapshot.workMinutes = response.state.workSessionMinutes

      let calendar = Calendar.autoupdatingCurrent
      let startOfToday = calendar.startOfDay(for: Date())
      let pending = response.tasks.filter { !$0.done }
      snapshot.overdue = pending.filter { ($0.dueDay.map { $0 < startOfToday }) ?? false }
      snapshot.today = pending.filter { $0.dueDay.map(calendar.isDateInToday) ?? false }
      snapshot.laterCount = pending.count - snapshot.overdue.count - snapshot.today.count
    } catch {
      snapshot.error = error.localizedDescription
    }

    if let data = try? await request("/v1/inbox") {
      let response = try? JSONDecoder().decode(ArgonInboxResponse.self, from: data)
      snapshot.waiting = (response?.items ?? []).filter(\.isWaiting)
    }

    if let data = try? await request("/v1/status"),
      let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    {
      if let ios = object["ios"] as? [String: Any] {
        let desired = ios["desired"] as? [String: Any]
        let actual = ios["actual"] as? [String: Any]
        snapshot.shielded = (actual?["shielded"] as? Bool) ?? false
        snapshot.focusReason = (desired?["reason"] as? String) ?? ""
      }
      if let agenda = object["agenda"] as? [[String: Any]], let first = agenda.first {
        snapshot.nextEvent = ArgonAgendaEvent(
          id: (first["id"] as? String) ?? UUID().uuidString,
          summary: (first["summary"] as? String) ?? "(untitled)",
          when: (first["when"] as? String) ?? ""
        )
      }
    }

    return snapshot
  }

  /// Start or complete a task. Routed through Argon's own endpoint rather than
  /// mutating anything locally, so the Mac, the phone and the check-in gate all
  /// learn about it from the same write.
  func act(taskId: String, action: String) async throws {
    let encoded =
      taskId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? taskId
    _ = try await request("/v1/tasks/\(encoded)", method: "PATCH", body: ["action": action])
  }

  func answer(itemId: String, action: String) async {
    _ = try? await request("/v1/inbox/\(itemId)/answer", method: "POST", body: ["action": action])
  }
}
