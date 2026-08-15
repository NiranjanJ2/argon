import SwiftUI

@main
struct ArgonMacApp: App {
  @StateObject private var store = ArgonStore()

  var body: some Scene {
    // `.window` rather than the default menu: the panel shows Argon's questions
    // with real buttons, and a plain NSMenu cannot render those.
    MenuBarExtra {
      MenuPanel()
        .environmentObject(store)
        .task { store.start() }
    } label: {
      HStack(spacing: 3) {
        Image(systemName: store.barIcon)
        Text(store.barTitle)
      }
    }
    .menuBarExtraStyle(.window)
  }
}

struct MenuPanel: View {
  @EnvironmentObject private var store: ArgonStore

  private var snapshot: ArgonSnapshot { store.snapshot }

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      header

      Divider().padding(.vertical, 6)

      ScrollView {
        VStack(alignment: .leading, spacing: 14) {
          if !snapshot.waiting.isEmpty { questions }
          if let running = snapshot.runningTask { nowSection(running) }
          taskSection
          if let event = snapshot.nextEvent { nextUp(event) }
        }
        .padding(.horizontal, 2)
      }
      .frame(maxHeight: 420)

      Divider().padding(.vertical, 6)
      footer
    }
    .padding(12)
    .frame(width: 320)
  }

  // MARK: - Sections

  private var header: some View {
    HStack(spacing: 6) {
      Text(snapshot.currentTask ?? "Argon")
        .font(.system(size: 13, weight: .semibold))
        .lineLimit(1)
      if snapshot.shielded {
        Image(systemName: "shield.lefthalf.filled")
          .font(.system(size: 10))
          .foregroundStyle(.tint)
      }
      Spacer()
      if snapshot.workMinutes > 0 {
        Text("\(snapshot.workMinutes)m")
          .font(.system(size: 11, design: .rounded))
          .foregroundStyle(.secondary)
      }
    }
  }

  /// Argon's open questions, above everything. This is the one thing on screen
  /// that is waiting on him rather than merely informing him.
  private var questions: some View {
    VStack(alignment: .leading, spacing: 8) {
      Label("Argon asked", systemImage: "bubble.left.fill")
        .font(.system(size: 10, weight: .semibold))
        .foregroundStyle(.secondary)

      ForEach(snapshot.waiting) { item in
        VStack(alignment: .leading, spacing: 6) {
          Text(item.text)
            .font(.system(size: 12))
            .fixedSize(horizontal: false, vertical: true)
          HStack(spacing: 6) {
            ForEach(item.actions) { action in
              Button(action.label) { store.answer(item, action: action) }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(store.busyIDs.contains("\(item.id):\(action.id)"))
            }
          }
        }
        .padding(8)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
      }
    }
  }

  private func nowSection(_ task: ArgonTask) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      Label("Working on", systemImage: "record.circle")
        .font(.system(size: 10, weight: .semibold))
        .foregroundStyle(.secondary)
      Text(task.title)
        .font(.system(size: 13, weight: .medium))
        .lineLimit(2)
      HStack(spacing: 6) {
        Button("Done") { store.act(task, action: "complete") }
          .buttonStyle(.borderedProminent)
          .controlSize(.small)
        Button("Put down") { store.act(task, action: "stop") }
          .buttonStyle(.bordered)
          .controlSize(.small)
      }
      .disabled(store.busyIDs.contains(task.id))
    }
  }

  /// One list. The Python readout showed the same five tasks under "Start
  /// working on", "Due" and "Later" in a single menu, which is how a readout
  /// stops being read.
  private var taskSection: some View {
    VStack(alignment: .leading, spacing: 6) {
      Label(
        snapshot.overdue.isEmpty ? "Today" : "Overdue and today",
        systemImage: "checklist"
      )
      .font(.system(size: 10, weight: .semibold))
      .foregroundStyle(.secondary)

      if snapshot.dueNow.isEmpty {
        Text(snapshot.error.map { "Can't reach Argon — \($0)" } ?? "Nothing due today")
          .font(.system(size: 12))
          .foregroundStyle(.secondary)
      } else {
        ForEach(snapshot.dueNow.filter { !$0.isRunning }) { task in
          TaskRow(task: task)
        }
      }

      if snapshot.laterCount > 0 {
        Text("+\(snapshot.laterCount) later")
          .font(.system(size: 11))
          .foregroundStyle(.tertiary)
      }
    }
  }

  private func nextUp(_ event: ArgonAgendaEvent) -> some View {
    HStack(spacing: 6) {
      Image(systemName: "calendar")
        .font(.system(size: 10))
      Text(event.summary).lineLimit(1)
      Spacer()
      Text(event.when)
        .foregroundStyle(.secondary)
    }
    .font(.system(size: 11))
  }

  private var footer: some View {
    HStack(spacing: 8) {
      if snapshot.hasNeverSynced {
        Text("Not synced").foregroundStyle(.secondary)
      } else if snapshot.isStale {
        Text("As of \(snapshot.capturedAt, style: .time)").foregroundStyle(.orange)
      } else {
        Text("Updated \(snapshot.capturedAt, style: .time)").foregroundStyle(.secondary)
      }
      Spacer()
      Button("Refresh") { Task { await store.refresh() } }
        .buttonStyle(.link)
      Button("Quit") { NSApplication.shared.terminate(nil) }
        .buttonStyle(.link)
    }
    .font(.system(size: 10))
  }
}

private struct TaskRow: View {
  @EnvironmentObject private var store: ArgonStore
  let task: ArgonTask

  @State private var hovering = false

  var body: some View {
    HStack(spacing: 7) {
      Button {
        store.act(task, action: "complete")
      } label: {
        Image(systemName: "circle")
          .font(.system(size: 12))
          .foregroundStyle(task.priority == "high" ? .orange : .secondary)
      }
      .buttonStyle(.plain)
      .help("Mark done")

      Text(task.title)
        .font(.system(size: 12))
        .lineLimit(1)

      Spacer(minLength: 4)

      if hovering {
        Button("Start") { store.act(task, action: "start") }
          .buttonStyle(.bordered)
          .controlSize(.mini)
      } else if let subject = task.subject {
        Text(subject)
          .font(.system(size: 10))
          .foregroundStyle(.tertiary)
          .lineLimit(1)
      }
    }
    .opacity(store.busyIDs.contains(task.id) ? 0.4 : 1)
    .padding(.vertical, 1)
    .onHover { hovering = $0 }
  }
}
