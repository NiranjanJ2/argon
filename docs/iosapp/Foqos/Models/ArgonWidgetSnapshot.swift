import Foundation

/// What the widgets render, written by the app into the shared app group.
///
/// The widget does not fetch. Reaching the server from a timeline provider would
/// mean putting the API token somewhere the extension can read it, and the token
/// currently lives in the app's own keychain with no shared access group — worth
/// keeping that way for a widget. The app already polls; it writes what it
/// learned here and reloads the timelines.
///
/// The cost is staleness when the app has not run, which is why `capturedAt` is
/// part of the payload and shown in the UI. A widget that quietly displays
/// yesterday's list is worse than one that admits it is out of date.
struct ArgonWidgetSnapshot: Codable, Equatable {
  struct Item: Codable, Equatable, Identifiable {
    let id: String
    let title: String
    let priority: String
    /// Already reduced to what the widget shows. Date maths does not belong in
    /// a timeline provider that may render hours after the data was captured.
    let dueLabel: String?
    let overdue: Bool
    let running: Bool
  }

  struct Event: Codable, Equatable {
    let summary: String
    let start: Date
  }

  var capturedAt: Date
  var mode: String
  var currentTask: String?
  var shielded: Bool
  /// Today and overdue only. "Relevant" is the whole point of a widget — the
  /// full backlog is what the app is for.
  var items: [Item]
  var nextEvent: Event?
  var waitingMessages: Int

  static let empty = ArgonWidgetSnapshot(
    capturedAt: .distantPast,
    mode: "offline",
    currentTask: nil,
    shielded: false,
    items: [],
    nextEvent: nil,
    waitingMessages: 0
  )

  /// Older than this and the widget says so rather than implying it is current.
  var isStale: Bool {
    Date().timeIntervalSince(capturedAt) > 45 * 60
  }

  var hasNeverSynced: Bool { capturedAt == .distantPast }
}

enum ArgonWidgetStore {
  private static let suite = UserDefaults(suiteName: "group.com.niranjanj.argon")
  private static let key = "argon.widget.snapshot"

  static func read() -> ArgonWidgetSnapshot {
    guard let data = suite?.data(forKey: key),
      let snapshot = try? JSONDecoder().decode(ArgonWidgetSnapshot.self, from: data)
    else {
      return .empty
    }
    return snapshot
  }

  static func write(_ snapshot: ArgonWidgetSnapshot) {
    guard let data = try? JSONEncoder().encode(snapshot) else { return }
    suite?.set(data, forKey: key)
  }
}
