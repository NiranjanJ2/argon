import Foundation

/// One thing the planning screen can ask about.
struct ArgonPlannerItem: Codable, Identifiable, Equatable {
  let id: String
  let title: String
  let subject: String
  let due: String?
  let daysOverdue: Int?

  enum CodingKeys: String, CodingKey {
    case id, title, subject, due
    case daysOverdue = "days_overdue"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
    title = (try? c.decode(String.self, forKey: .title)) ?? "(untitled)"
    subject = (try? c.decode(String.self, forKey: .subject)) ?? ""
    due = try? c.decode(String.self, forKey: .due)
    daysOverdue = try? c.decode(Int.self, forKey: .daysOverdue)
  }

  var staleness: String? {
    guard let daysOverdue, daysOverdue > 0 else { return nil }
    return daysOverdue == 1 ? "1 day late" : "\(daysOverdue) days late"
  }
}

/// Something no source can see, that only he can confirm.
struct ArgonPlannerSuggestion: Codable, Identifiable, Equatable {
  let kind: String
  let title: String
  let subject: String?
  let prompt: String?
  let estimateMin: Int?
  /// Never true for Chem. Ticking it by default invents work; omitting it
  /// asserts a free night. Argon knows neither.
  let isDefault: Bool

  var id: String { "\(kind):\(title)" }

  enum CodingKeys: String, CodingKey {
    case kind, title, subject, prompt
    case estimateMin = "estimate_min"
    case isDefault = "default"
  }

  init(
    kind: String,
    title: String,
    subject: String?,
    prompt: String?,
    estimateMin: Int?,
    isDefault: Bool
  ) {
    self.kind = kind
    self.title = title
    self.subject = subject
    self.prompt = prompt
    self.estimateMin = estimateMin
    self.isDefault = isDefault
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    kind = (try? c.decode(String.self, forKey: .kind)) ?? "other"
    title = (try? c.decode(String.self, forKey: .title)) ?? ""
    subject = try? c.decode(String.self, forKey: .subject)
    prompt = try? c.decode(String.self, forKey: .prompt)
    estimateMin = try? c.decode(Int.self, forKey: .estimateMin)
    isDefault = (try? c.decode(Bool.self, forKey: .isDefault)) ?? false
  }
}

struct ArgonPlannerPayload: Codable, Equatable, Identifiable {
  let needed: Bool
  let opensAfter: String
  let lastPlanned: String?
  let overdue: [ArgonPlannerItem]
  let today: [ArgonPlannerItem]
  let suggestions: [ArgonPlannerSuggestion]

  enum CodingKeys: String, CodingKey {
    case needed, overdue, today, suggestions
    case opensAfter = "opens_after"
    case lastPlanned = "last_planned"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    needed = (try? c.decode(Bool.self, forKey: .needed)) ?? false
    opensAfter = (try? c.decode(String.self, forKey: .opensAfter)) ?? "15:36"
    lastPlanned = try? c.decode(String.self, forKey: .lastPlanned)
    overdue = (try? c.decode([ArgonPlannerItem].self, forKey: .overdue)) ?? []
    today = (try? c.decode([ArgonPlannerItem].self, forKey: .today)) ?? []
    suggestions = (try? c.decode([ArgonPlannerSuggestion].self, forKey: .suggestions)) ?? []
  }

  static let empty = ArgonPlannerPayload()

  private init() {
    needed = false
    opensAfter = "15:36"
    lastPlanned = nil
    overdue = []
    today = []
    suggestions = []
  }

  var id: String { lastPlanned ?? opensAfter }

  var hasAnythingToDecide: Bool {
    !overdue.isEmpty || !suggestions.isEmpty
  }
}
