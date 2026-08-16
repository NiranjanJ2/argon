import Foundation

/// When this Mac is allowed to poll Argon.
///
/// Reads and writes the same `~/.config/argon/activity.json` the Übersicht
/// readout honours, so pausing from the menu bar pauses everything rather than
/// one of two things that are both polling.
///
/// The saving is smaller than it looks and worth being honest about: a paused
/// refresh of the Python readout still costs an interpreter start, about 0.05s
/// of the 0.06s an active one costs. Pausing stops *this* app's polling
/// completely, because it is one long-lived process — the readout's cost is the
/// spawn, which is why its refresh interval matters more than its gate.
struct ArgonActivity: Equatable {
  var activeFrom: String
  var activeTo: String
  var pausedUntil: Date?
  var scheduleEnabled: Bool

  static let path = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".config/argon/activity.json")

  static let defaultFrom = "16:00"
  static let defaultTo = "07:30"

  static func load() -> ArgonActivity {
    let object =
      (try? Data(contentsOf: path))
      .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]

    var paused: Date?
    if let stamp = object["paused_until"] as? String {
      paused = Self.parseStamp(stamp)
    }
    return ArgonActivity(
      activeFrom: (object["active_from"] as? String) ?? defaultFrom,
      activeTo: (object["active_to"] as? String) ?? defaultTo,
      pausedUntil: paused,
      scheduleEnabled: (object["schedule_enabled"] as? Bool) ?? true
    )
  }

  func save() {
    var object: [String: Any] = [
      "active_from": activeFrom,
      "active_to": activeTo,
      "schedule_enabled": scheduleEnabled,
    ]
    object["paused_until"] =
      pausedUntil.map { ISO8601DateFormatter.argonLocal.string(from: $0) } ?? NSNull()

    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted])
    else { return }
    try? FileManager.default.createDirectory(
      at: Self.path.deletingLastPathComponent(), withIntermediateDirectories: true)
    try? data.write(to: Self.path, options: .atomic)
  }

  /// Whether to poll, and what to say when not.
  func status(at now: Date = Date()) -> (active: Bool, reason: String) {
    if let pausedUntil, pausedUntil > now {
      return (false, "Paused until \(Self.shortTime.string(from: pausedUntil))")
    }
    guard scheduleEnabled else { return (true, "") }

    let minutes = Self.minutes(of: now)
    guard let start = Self.minutes(hhmm: activeFrom), let end = Self.minutes(hhmm: activeTo)
    else {
      return (true, "")
    }
    // 16:00–07:30 wraps past midnight, which is the normal case here rather
    // than the edge case — a plain `start <= now < end` is false all evening.
    let inside = start == end || (start < end
      ? (minutes >= start && minutes < end)
      : (minutes >= start || minutes < end))
    return inside ? (true, "") : (false, "Asleep until \(activeFrom)")
  }

  /// Parse a stamp written by either side.
  ///
  /// Python now writes an offset so `ISO8601DateFormatter` can read it, but a
  /// file written before that is naive local time, which it refuses outright.
  /// Failing to parse would read as "not paused" and quietly resume polling.
  static func parseStamp(_ value: String) -> Date? {
    if let date = ISO8601DateFormatter.argonLocal.date(from: value) { return date }
    let naive = DateFormatter()
    naive.calendar = Calendar(identifier: .gregorian)
    naive.locale = Locale(identifier: "en_US_POSIX")
    naive.timeZone = .autoupdatingCurrent
    naive.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    return naive.date(from: value)
  }

  private static func minutes(of date: Date) -> Int {
    let parts = Calendar.current.dateComponents([.hour, .minute], from: date)
    return (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
  }

  private static func minutes(hhmm: String) -> Int? {
    let parts = hhmm.split(separator: ":")
    guard parts.count == 2, let h = Int(parts[0]), let m = Int(parts[1]) else { return nil }
    return h * 60 + m
  }

  private static let shortTime: DateFormatter = {
    let formatter = DateFormatter()
    formatter.dateFormat = "h:mm a"
    return formatter
  }()
}

extension ISO8601DateFormatter {
  /// Matches what Python's `datetime.isoformat()` writes: local time, offset,
  /// no fractional seconds.
  static let argonLocal: ISO8601DateFormatter = {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    return formatter
  }()
}
