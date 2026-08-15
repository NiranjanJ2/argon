import SwiftUI
import WidgetKit

enum ArgonWidgetPalette {
  static let canvas = Color(red: 0.016, green: 0.031, blue: 0.071)
  static let ink = Color(red: 0.957, green: 0.973, blue: 1.0)
  static let muted = Color(red: 0.608, green: 0.667, blue: 0.753)
  static let ice = Color(red: 0.663, green: 0.867, blue: 1.0)
  static let warn = Color(red: 1.0, green: 0.573, blue: 0.322)
}

struct ArgonTodayView: View {
  @Environment(\.widgetFamily) private var family
  let snapshot: ArgonWidgetSnapshot

  private var remaining: [ArgonWidgetSnapshot.Item] { snapshot.items }
  private var overdueCount: Int { remaining.filter(\.overdue).count }

  var body: some View {
    switch family {
    case .systemSmall: small
    case .systemLarge: large
    default: medium
    }
  }

  // MARK: - Small

  private var small: some View {
    VStack(alignment: .leading, spacing: 6) {
      header
      Spacer(minLength: 0)
      Text("\(remaining.count)")
        .font(.system(size: 40, weight: .bold, design: .rounded))
        .foregroundStyle(ArgonWidgetPalette.ink)
      Text(remaining.count == 1 ? "task left" : "tasks left")
        .font(.caption2)
        .foregroundStyle(ArgonWidgetPalette.muted)
      if let running = remaining.first(where: \.running) {
        Text(running.title)
          .font(.caption2.weight(.semibold))
          .foregroundStyle(ArgonWidgetPalette.ice)
          .lineLimit(1)
      } else if overdueCount > 0 {
        Text("\(overdueCount) overdue")
          .font(.caption2.weight(.semibold))
          .foregroundStyle(ArgonWidgetPalette.warn)
      }
      staleNote
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  // MARK: - Medium / Large

  private var medium: some View { list(limit: 3) }
  private var large: some View { list(limit: 7) }

  private func list(limit: Int) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      header

      if remaining.isEmpty {
        Spacer(minLength: 0)
        Text(snapshot.hasNeverSynced ? "Open Argon to sync" : "Nothing left today")
          .font(.caption)
          .foregroundStyle(ArgonWidgetPalette.muted)
        Spacer(minLength: 0)
      } else {
        VStack(alignment: .leading, spacing: 6) {
          ForEach(remaining.prefix(limit)) { item in
            row(item)
          }
        }
        if remaining.count > limit {
          Text("+\(remaining.count - limit) more")
            .font(.caption2)
            .foregroundStyle(ArgonWidgetPalette.muted)
        }
        Spacer(minLength: 0)
      }

      footer
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  private func row(_ item: ArgonWidgetSnapshot.Item) -> some View {
    HStack(spacing: 7) {
      Circle()
        .strokeBorder(
          item.running ? ArgonWidgetPalette.ice : ArgonWidgetPalette.muted.opacity(0.6),
          lineWidth: 1.5
        )
        .frame(width: 11, height: 11)

      Text(item.title)
        .font(.caption.weight(item.running ? .semibold : .regular))
        .foregroundStyle(item.running ? ArgonWidgetPalette.ice : ArgonWidgetPalette.ink)
        .lineLimit(1)

      Spacer(minLength: 4)

      if let dueLabel = item.dueLabel {
        Text(dueLabel)
          .font(.caption2)
          .foregroundStyle(item.overdue ? ArgonWidgetPalette.warn : ArgonWidgetPalette.muted)
      }
    }
  }

  // MARK: - Chrome

  private var header: some View {
    HStack(spacing: 5) {
      Text("Argon")
        .font(.caption2.weight(.bold))
        .foregroundStyle(ArgonWidgetPalette.ice)
      if snapshot.shielded {
        Image(systemName: "shield.lefthalf.filled")
          .font(.system(size: 9))
          .foregroundStyle(ArgonWidgetPalette.ice)
      }
      Spacer(minLength: 0)
      if snapshot.waitingMessages > 0 {
        Text("\(snapshot.waitingMessages)")
          .font(.system(size: 9, weight: .bold))
          .foregroundStyle(ArgonWidgetPalette.canvas)
          .padding(.horizontal, 5)
          .padding(.vertical, 1)
          .background(ArgonWidgetPalette.ice, in: Capsule())
      }
    }
  }

  @ViewBuilder
  private var footer: some View {
    if let event = snapshot.nextEvent {
      HStack(spacing: 4) {
        Image(systemName: "calendar")
          .font(.system(size: 9))
        Text(event.summary)
          .lineLimit(1)
        Text(event.start, style: .time)
      }
      .font(.caption2)
      .foregroundStyle(ArgonWidgetPalette.muted)
    } else {
      staleNote
    }
  }

  /// Shown rather than hidden. A widget that silently renders a stale list is
  /// worse than one that says it has not heard from Argon lately — the first
  /// gets trusted and acted on, the second gets opened.
  @ViewBuilder
  private var staleNote: some View {
    if snapshot.hasNeverSynced {
      Text("Not synced")
        .font(.caption2)
        .foregroundStyle(ArgonWidgetPalette.muted)
    } else if snapshot.isStale {
      Text("As of \(snapshot.capturedAt, style: .time)")
        .font(.caption2)
        .foregroundStyle(ArgonWidgetPalette.warn)
    }
  }
}

#Preview(as: .systemMedium) {
  ArgonTodayWidget()
} timeline: {
  ArgonTodayEntry(date: .now, snapshot: .preview)
}
