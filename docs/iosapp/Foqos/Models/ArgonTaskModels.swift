import Foundation

struct ArgonTask: Codable, Identifiable, Equatable {
  var id: String
  var title: String
  var done: Bool
  var priority: String
  var source: String
  var subject: String?
  var notes: String?
  var due: String?
  var classroomID: String?
  var timeEstimateMinutes: Int?
  var timeActualMinutes: Int?
  var startedAt: String?

  enum CodingKeys: String, CodingKey {
    case id
    case title
    case done
    case priority
    case source
    case subject
    case notes
    case due
    case classroomID = "classroom_id"
    case timeEstimateMinutes = "time_estimate_min"
    case timeActualMinutes = "time_actual_min"
    case startedAt = "started_at"
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    id = try container.decode(String.self, forKey: .id)
    title = try container.decode(String.self, forKey: .title)
    done = try container.decodeIfPresent(Bool.self, forKey: .done) ?? false
    priority = try container.decodeIfPresent(String.self, forKey: .priority) ?? "medium"
    source = try container.decodeIfPresent(String.self, forKey: .source) ?? "argon"
    subject = try container.decodeIfPresent(String.self, forKey: .subject)
    notes = try container.decodeIfPresent(String.self, forKey: .notes)
    due = try container.decodeIfPresent(String.self, forKey: .due)
    classroomID = try container.decodeIfPresent(String.self, forKey: .classroomID)
    timeEstimateMinutes = try container.decodeIfPresent(Int.self, forKey: .timeEstimateMinutes)
    timeActualMinutes = try container.decodeIfPresent(Int.self, forKey: .timeActualMinutes)
    startedAt = try container.decodeIfPresent(String.self, forKey: .startedAt)
  }

  var dueDay: Date? {
    guard let due, due.count >= 10 else { return nil }
    let formatter = DateFormatter()
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "yyyy-MM-dd"
    return formatter.date(from: String(due.prefix(10)))
  }

  var isStarted: Bool {
    startedAt != nil
  }
}

struct ArgonTaskDashboardState: Codable, Equatable {
  var mode: String
  var currentTask: String?
  var workSessionMinutes: Int
  var lockInMinutes: Int

  enum CodingKeys: String, CodingKey {
    case mode
    case currentTask = "current_task"
    case workSessionMinutes = "work_session_minutes"
    case lockInMinutes = "lock_in_minutes"
  }

  init(
    mode: String,
    currentTask: String?,
    workSessionMinutes: Int,
    lockInMinutes: Int
  ) {
    self.mode = mode
    self.currentTask = currentTask
    self.workSessionMinutes = workSessionMinutes
    self.lockInMinutes = lockInMinutes
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    mode = try container.decodeIfPresent(String.self, forKey: .mode) ?? "idle"
    currentTask = try container.decodeIfPresent(String.self, forKey: .currentTask)
    workSessionMinutes =
      try container.decodeIfPresent(Int.self, forKey: .workSessionMinutes) ?? 0
    lockInMinutes = try container.decodeIfPresent(Int.self, forKey: .lockInMinutes) ?? 0
  }

  static let empty = ArgonTaskDashboardState(
    mode: "idle",
    currentTask: nil,
    workSessionMinutes: 0,
    lockInMinutes: 0
  )
}

struct ArgonTasksResponse: Codable, Equatable {
  var tasks: [ArgonTask]
  var state: ArgonTaskDashboardState

  private enum CodingKeys: String, CodingKey {
    case tasks
    case state
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    tasks = try container.decodeIfPresent([ArgonTask].self, forKey: .tasks) ?? []
    state = try container.decodeIfPresent(ArgonTaskDashboardState.self, forKey: .state) ?? .empty
  }
}

struct ArgonChatMessage: Codable, Identifiable, Equatable {
  enum Role: String, Codable {
    case user
    case argon
  }

  var id: UUID
  var role: Role
  var text: String
  var sentAt: Date
  var delivered: Bool
}
