import Foundation

// The wire format Argon already serves. Mirrors argon/api/server.py — every
// field here is one the iOS app or the Python readout already consumes, so
// nothing new had to be added server-side for the Mac to exist.

struct ArgonTask: Codable, Identifiable, Equatable {
  let id: String
  let title: String
  let done: Bool
  let priority: String
  let subject: String?
  let due: String?
  let running: Bool?
  let runningMinutes: Int?
  let timeEstimateMin: Int?

  enum CodingKeys: String, CodingKey {
    case id, title, done, priority, subject, due, running
    case runningMinutes = "running_minutes"
    case timeEstimateMin = "time_estimate_min"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
    title = (try? c.decode(String.self, forKey: .title)) ?? "(untitled)"
    done = (try? c.decode(Bool.self, forKey: .done)) ?? false
    priority = (try? c.decode(String.self, forKey: .priority)) ?? "medium"
    subject = try? c.decode(String.self, forKey: .subject)
    due = try? c.decode(String.self, forKey: .due)
    running = try? c.decode(Bool.self, forKey: .running)
    runningMinutes = try? c.decode(Int.self, forKey: .runningMinutes)
    timeEstimateMin = try? c.decode(Int.self, forKey: .timeEstimateMin)
  }

  /// Argon serves `due` as a date, sometimes with a time on it. Only the day
  /// matters for grouping, and parsing the whole thing to throw the time away
  /// is how a timezone bug gets in.
  var dueDay: Date? {
    guard let due, due.count >= 10 else { return nil }
    let formatter = DateFormatter()
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = .autoupdatingCurrent
    formatter.dateFormat = "yyyy-MM-dd"
    return formatter.date(from: String(due.prefix(10)))
  }

  var isRunning: Bool { running == true }
}

struct ArgonTasksState: Codable, Equatable {
  let mode: String
  let currentTask: String?
  let workSessionMinutes: Int
  let lockInMinutes: Int

  enum CodingKeys: String, CodingKey {
    case mode
    case currentTask = "current_task"
    case workSessionMinutes = "work_session_minutes"
    case lockInMinutes = "lock_in_minutes"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    mode = (try? c.decode(String.self, forKey: .mode)) ?? "idle"
    currentTask = try? c.decode(String.self, forKey: .currentTask)
    workSessionMinutes = (try? c.decode(Int.self, forKey: .workSessionMinutes)) ?? 0
    lockInMinutes = (try? c.decode(Int.self, forKey: .lockInMinutes)) ?? 0
  }
}

struct ArgonTasksResponse: Codable {
  let tasks: [ArgonTask]
  let state: ArgonTasksState
}

struct ArgonInboxAction: Codable, Equatable, Identifiable {
  let label: String
  let action: String
  let taskId: String?
  let title: String?

  var id: String { "\(action):\(taskId ?? "-")" }

  enum CodingKeys: String, CodingKey {
    case label, action, title
    case taskId = "task_id"
  }
}

struct ArgonInboxItem: Codable, Equatable, Identifiable {
  let id: String
  let text: String
  let actions: [ArgonInboxAction]
  let answered: AnswerRecord?

  struct AnswerRecord: Codable, Equatable {
    let verb: String
  }

  var isWaiting: Bool { answered == nil && !actions.isEmpty }
}

struct ArgonInboxResponse: Codable {
  let items: [ArgonInboxItem]
  let unanswered: Int
}

struct ArgonAgendaEvent: Codable, Equatable, Identifiable {
  let id: String
  let summary: String
  let when: String
}

struct ArgonFocus: Codable, Equatable {
  let mode: String
  let reason: String
  let shielded: Bool
}

/// Everything the menu bar and the widgets render, in one object.
///
/// Kept as a single value so the two surfaces cannot disagree — the same bug
/// the Python readout solved by sharing `build_view` between SwiftBar and
/// Übersicht. The lesson survives the rewrite even though the code does not.
struct ArgonSnapshot: Codable, Equatable {
  var capturedAt: Date
  var mode: String
  var currentTask: String?
  var workMinutes: Int
  var shielded: Bool
  var focusReason: String
  var overdue: [ArgonTask]
  var today: [ArgonTask]
  var laterCount: Int
  var waiting: [ArgonInboxItem]
  var nextEvent: ArgonAgendaEvent?
  var error: String?

  static let empty = ArgonSnapshot(
    capturedAt: .distantPast,
    mode: "offline",
    currentTask: nil,
    workMinutes: 0,
    shielded: false,
    focusReason: "",
    overdue: [],
    today: [],
    laterCount: 0,
    waiting: [],
    nextEvent: nil,
    error: nil
  )

  var runningTask: ArgonTask? { (overdue + today).first(where: \.isRunning) }
  var dueNow: [ArgonTask] { overdue + today }
  var hasNeverSynced: Bool { capturedAt == .distantPast }
  var isStale: Bool { Date().timeIntervalSince(capturedAt) > 10 * 60 }
}
