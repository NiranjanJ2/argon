import SwiftUI
import WidgetKit

struct ArgonTodayEntry: TimelineEntry {
  let date: Date
  let snapshot: ArgonWidgetSnapshot
}

struct ArgonTodayProvider: TimelineProvider {
  func placeholder(in context: Context) -> ArgonTodayEntry {
    ArgonTodayEntry(date: Date(), snapshot: .preview)
  }

  func getSnapshot(in context: Context, completion: @escaping (ArgonTodayEntry) -> Void) {
    let stored = ArgonWidgetStore.read()
    completion(
      ArgonTodayEntry(
        date: Date(),
        // The gallery preview must never be an empty box, or the widget looks
        // broken to someone deciding whether to add it.
        snapshot: context.isPreview && stored.hasNeverSynced ? .preview : stored
      )
    )
  }

  func getTimeline(in context: Context, completion: @escaping (Timeline<ArgonTodayEntry>) -> Void) {
    let entry = ArgonTodayEntry(date: Date(), snapshot: ArgonWidgetStore.read())
    // A refresh request, not a guarantee — WidgetKit budgets these. The app
    // reloads timelines directly whenever it syncs, which is what actually
    // keeps this current; the interval is only the floor.
    completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(15 * 60))))
  }
}

struct ArgonTodayWidget: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: "ArgonToday", provider: ArgonTodayProvider()) { entry in
      ArgonTodayView(snapshot: entry.snapshot)
        .containerBackground(for: .widget) { ArgonWidgetPalette.canvas }
    }
    .configurationDisplayName("Today")
    .description("What is left today, what you are on, and what is next.")
    .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
  }
}

extension ArgonWidgetSnapshot {
  /// Only for the gallery and Xcode previews. Never rendered from real state.
  static let preview = ArgonWidgetSnapshot(
    capturedAt: Date(),
    mode: "working",
    currentTask: "APUSH reading",
    shielded: true,
    items: [
      Item(
        id: "1", title: "APUSH reading", priority: "high", dueLabel: "Today",
        overdue: false, running: true
      ),
      Item(
        id: "2", title: "Calc problem set", priority: "high", dueLabel: "Yesterday",
        overdue: true, running: false
      ),
      Item(
        id: "3", title: "Physics lab writeup", priority: "medium", dueLabel: "Today",
        overdue: false, running: false
      ),
    ],
    nextEvent: Event(summary: "Robotics", start: Date().addingTimeInterval(5400)),
    waitingMessages: 1
  )
}
