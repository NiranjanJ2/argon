import SwiftUI

struct ArgonDashboardView: View {
  @EnvironmentObject private var bridge: ArgonBridge
  @State private var showingAddTask = false

  /// What Argon has said lately, unanswered first.
  ///
  /// This deliberately shows answered messages too. Rendering only open
  /// questions sounded right and was wrong in practice: Argon speaks unprompted
  /// about twice a day, so the section was empty almost always and appeared
  /// three minutes out of every twenty-four hours — which reads as broken
  /// rather than as "nothing is waiting on you". An answered message still says
  /// what he was asked and what he replied, which is worth the space.
  private var recentMessages: [ArgonInboxItem] {
    let waiting = bridge.inbox.filter(\.isWaiting)
    let answered = bridge.inbox.filter { !$0.isWaiting }
    return Array((waiting + answered).prefix(3))
  }

  private var pendingTasks: [ArgonTask] {
    bridge.tasks.filter { !$0.done }
  }

  private var overdueTasks: [ArgonTask] {
    sorted(
      pendingTasks.filter { task in
        guard let due = task.dueDay else { return false }
        return due < Calendar.autoupdatingCurrent.startOfDay(for: Date())
      })
  }

  private var todayTasks: [ArgonTask] {
    sorted(
      pendingTasks.filter { task in
        guard let due = task.dueDay else { return false }
        return Calendar.autoupdatingCurrent.isDateInToday(due)
      })
  }

  private var laterTasks: [ArgonTask] {
    sorted(
      pendingTasks.filter { task in
        guard let due = task.dueDay else { return true }
        return due > Calendar.autoupdatingCurrent.startOfDay(for: Date())
          && !Calendar.autoupdatingCurrent.isDateInToday(due)
      })
  }

  var body: some View {
    NavigationStack {
      ZStack {
        ArgonBackdrop()

        List {
          dashboardHero
            .listRowInsets(EdgeInsets(top: 16, leading: 16, bottom: 18, trailing: 16))
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)

          if !recentMessages.isEmpty {
            Section {
              ForEach(recentMessages) { item in
                ArgonMessageCard(item: item) { action in
                  Task { await bridge.answerInbox(item, with: action) }
                }
                .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
                .listRowBackground(Color.clear)
                .listRowSeparator(.hidden)
              }
            } header: {
              Text("From Argon")
                .font(.caption.weight(.semibold))
                .foregroundStyle(ArgonPalette.mutedInk)
                .textCase(nil)
            }
          }

          if pendingTasks.isEmpty, !bridge.isLoadingTasks {
            emptyState
              .listRowInsets(EdgeInsets(top: 28, leading: 16, bottom: 28, trailing: 16))
              .listRowBackground(Color.clear)
              .listRowSeparator(.hidden)
          } else {
            taskSection("Overdue", tasks: overdueTasks, tint: .orange)
            taskSection("Today", tasks: todayTasks, tint: ArgonPalette.iceBlue)
            taskSection("Later", tasks: laterTasks, tint: ArgonPalette.mutedInk)
          }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .refreshable {
          await bridge.refreshTasks()
          await bridge.refreshInbox()
        }
        .task { await bridge.refreshInbox() }
        .overlay {
          if bridge.isLoadingTasks, bridge.tasks.isEmpty {
            ProgressView()
              .tint(ArgonPalette.iceBlue)
              .controlSize(.large)
          }
        }
      }
      .navigationTitle("Today")
      .navigationBarTitleDisplayMode(.large)
      .toolbarBackground(ArgonPalette.canvasLifted.opacity(0.82), for: .navigationBar)
      .toolbarBackground(.visible, for: .navigationBar)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button {
            showingAddTask = true
          } label: {
            Image(systemName: "plus")
              .font(.system(size: 15, weight: .bold))
              .foregroundStyle(ArgonPalette.canvas)
              .frame(width: 32, height: 32)
              .background(ArgonPalette.iceBlue, in: Circle())
              .shadow(color: ArgonPalette.electricBlue.opacity(0.45), radius: 9)
          }
          .accessibilityLabel("Add task")
        }
      }
      .sheet(isPresented: $showingAddTask) {
        ArgonAddTaskView()
          .presentationDetents([.large])
      }
    }
  }

  private var dashboardHero: some View {
    VStack(alignment: .leading, spacing: 17) {
      HStack(alignment: .top, spacing: 16) {
        VStack(alignment: .leading, spacing: 6) {
          Text(modeLabel.uppercased())
            .font(.caption2.weight(.bold))
            .tracking(1.8)
            .foregroundStyle(ArgonPalette.iceBlue)

          Text(bridge.taskDashboardState.currentTask ?? "Ready when you are")
            .font(.argonDisplay(25))
            .foregroundStyle(ArgonPalette.ink)
            .lineLimit(2)

          Text(currentTaskCaption)
            .font(.caption)
            .foregroundStyle(ArgonPalette.mutedInk)
        }

        Spacer(minLength: 6)
        ArgonOrb(size: 72, showsOrbit: false)
          .frame(width: 78, height: 70)
      }

      HStack(spacing: 10) {
        dashboardMetric(
          "\(bridge.taskDashboardState.workSessionMinutes)m",
          label: "focus",
          icon: "timer"
        )
        dashboardMetric(
          "\(pendingTasks.count)",
          label: "open",
          icon: "checklist"
        )
        dashboardMetric(
          connectionValue,
          label: "argon",
          icon: "link"
        )
      }
    }
    .padding(20)
    .argonGlassPanel(cornerRadius: 28, strokeOpacity: 0.25)
    .overlay(alignment: .topTrailing) {
      Circle()
        .fill(ArgonPalette.electricBlue.opacity(0.20))
        .frame(width: 110, height: 110)
        .blur(radius: 42)
        .offset(x: 28, y: -40)
        .allowsHitTesting(false)
    }
  }

  private var emptyState: some View {
    VStack(spacing: 12) {
      Image(systemName: "checkmark.seal.fill")
        .font(.system(size: 32))
        .foregroundStyle(ArgonPalette.iceBlue)
        .shadow(color: ArgonPalette.electricBlue.opacity(0.46), radius: 12)
      Text("Clear runway")
        .font(.argonDisplay(23))
        .foregroundStyle(ArgonPalette.ink)
      Text("When you or Argon adds something, it will show up here on the same shared list.")
        .font(.subheadline)
        .foregroundStyle(ArgonPalette.mutedInk)
        .multilineTextAlignment(.center)
        .lineSpacing(2)
    }
    .frame(maxWidth: .infinity)
    .padding(28)
    .argonGlassPanel(cornerRadius: 24)
  }

  @ViewBuilder
  private func taskSection(_ title: String, tasks: [ArgonTask], tint: Color) -> some View {
    if !tasks.isEmpty {
      Section {
        ForEach(tasks) { task in
          ArgonTaskRow(
            task: task,
            isMutating: bridge.taskMutationIDs.contains(task.id),
            onToggle: { Task { await bridge.completeTask(task) } }
          )
          .contentShape(Rectangle())
          .onTapGesture {
            guard !task.isStarted else { return }
            Task { await bridge.startTask(task) }
          }
            // Swipe left to tick it off, swipe right to push it to tomorrow.
            // His call on which way round: the muscle memory that matters is
            // his, not a usability argument. Completing stays first on the
            // trailing edge so a full swipe does the obvious thing.
            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
              Button {
                Task { await bridge.completeTask(task) }
              } label: {
                Label("Done", systemImage: "checkmark.circle.fill")
              }
              .tint(.green)

              Button {
                Task { await bridge.updateTask(task, due: tomorrowString) }
              } label: {
                Label("Tomorrow", systemImage: "moon.zzz.fill")
              }
              .tint(ArgonPalette.cobalt)
            }
            .swipeActions(edge: .leading, allowsFullSwipe: true) {
              Button {
                Task { await bridge.updateTask(task, due: tomorrowString) }
              } label: {
                Label("Tomorrow", systemImage: "moon.zzz.fill")
              }
              .tint(ArgonPalette.cobalt)
            }
            .contextMenu {
              Button("Start", systemImage: "play.fill") {
                Task { await bridge.startTask(task) }
              }
              Button("Due tomorrow", systemImage: "calendar.badge.clock") {
                Task { await bridge.updateTask(task, due: tomorrowString) }
              }
              Menu("Priority") {
                ForEach(["high", "medium", "low"], id: \.self) { priority in
                  Button(priority.capitalized) {
                    Task { await bridge.updateTask(task, priority: priority) }
                  }
                }
              }
              Button("Complete", systemImage: "checkmark.circle") {
                Task { await bridge.completeTask(task) }
              }
            }
            .listRowInsets(EdgeInsets(top: 2, leading: 12, bottom: 2, trailing: 12))
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)
        }
      } header: {
        HStack(spacing: 8) {
          Circle()
            .fill(tint)
            .frame(width: 6, height: 6)
            .shadow(color: tint.opacity(0.7), radius: 5)
          Text(title)
            .font(.argonDisplay(18))
            .foregroundStyle(ArgonPalette.ink)
          Text("\(tasks.count)")
            .font(.caption.weight(.semibold))
            .foregroundStyle(ArgonPalette.mutedInk)
        }
        .textCase(nil)
        .padding(.top, 7)
      }
    }
  }

  private func dashboardMetric(_ value: String, label: String, icon: String) -> some View {
    VStack(alignment: .leading, spacing: 7) {
      Image(systemName: icon)
        .font(.caption.weight(.semibold))
        .foregroundStyle(ArgonPalette.iceBlue)
      Text(value)
        .font(.argonDisplay(19))
        .foregroundStyle(ArgonPalette.ink)
        .lineLimit(1)
      Text(label.uppercased())
        .font(.system(size: 8, weight: .bold))
        .tracking(1)
        .foregroundStyle(ArgonPalette.mutedInk)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(12)
    .background(.black.opacity(0.19), in: RoundedRectangle(cornerRadius: 15))
    .overlay {
      RoundedRectangle(cornerRadius: 15)
        .stroke(Color.white.opacity(0.055), lineWidth: 1)
    }
  }

  private func sorted(_ tasks: [ArgonTask]) -> [ArgonTask] {
    tasks.sorted { lhs, rhs in
      if lhs.isStarted != rhs.isStarted { return lhs.isStarted }
      let priorities = ["high": 0, "medium": 1, "low": 2]
      let lhsPriority = priorities[lhs.priority] ?? 3
      let rhsPriority = priorities[rhs.priority] ?? 3
      if lhsPriority != rhsPriority { return lhsPriority < rhsPriority }
      if lhs.dueDay != rhs.dueDay {
        return (lhs.dueDay ?? .distantFuture) < (rhs.dueDay ?? .distantFuture)
      }
      return lhs.title.localizedStandardCompare(rhs.title) == .orderedAscending
    }
  }

  private var modeLabel: String {
    switch bridge.taskDashboardState.mode {
    case "working": return "In motion"
    case "lock_in": return "Locked in"
    case "napping": return "Recharging"
    case "done": return "Day complete"
    default: return "At ease"
    }
  }

  private var currentTaskCaption: String {
    bridge.taskDashboardState.currentTask == nil
      ? "Tap a task to start it with Argon"
      : "Your active task · tap another to switch"
  }

  private var connectionValue: String {
    bridge.connectionState == "Connected" ? "live" : "cached"
  }

  private var tomorrowString: String {
    let tomorrow = Calendar.autoupdatingCurrent.date(byAdding: .day, value: 1, to: Date()) ?? Date()
    return Self.dayFormatter.string(from: tomorrow)
  }

  private static let dayFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = .autoupdatingCurrent
    formatter.dateFormat = "yyyy-MM-dd"
    return formatter
  }()
}

/// One line of the checklist.
///
/// Built like a to-do list rather than a dashboard card: the circle on the left
/// is the whole point, and it is a real tap target that completes the item
/// rather than decoration that reports its priority. Everything else is one
/// title and one quiet line under it — a list you can run your eye down and a
/// thumb along, which is what a checklist is for.
/// A message Argon sent unprompted, with the buttons it offered.
///
/// The buttons are the point. A tap carries a verb and a task id both chosen on
/// the server, so "starting now" cannot be misread — where a typed "yeah in a
/// bit" has to be interpreted, and interpretation is what previously had Argon
/// convinced he had begun work he had not begun.
private struct ArgonMessageCard: View {
  let item: ArgonInboxItem
  let onAction: (ArgonInboxAction) -> Void

  @EnvironmentObject private var bridge: ArgonBridge

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      Text(item.text)
        .font(.subheadline)
        .foregroundStyle(ArgonPalette.ink)
        .fixedSize(horizontal: false, vertical: true)

      if let sent = item.sentDate {
        Text(sent.formatted(date: .omitted, time: .shortened))
          .font(.caption2)
          .foregroundStyle(ArgonPalette.mutedInk)
      }

      if let answered = item.answered {
        // Already dealt with. Shows what he chose rather than dead buttons,
        // so the card reads as a record instead of a question asked twice.
        Label(
          answered.result?.isEmpty == false ? answered.result! : answered.verb.capitalized,
          systemImage: "checkmark.circle.fill"
        )
        .font(.caption2.weight(.medium))
        .foregroundStyle(ArgonPalette.mutedInk)
      } else if !item.actions.isEmpty {
        // Wraps rather than scrolls: three verbs at a readable size do not fit
        // one line on a small phone, and a button you have to scroll to find is
        // one you will not press.
        FlowRow(spacing: 8) {
          ForEach(item.actions) { action in
            Button {
              onAction(action)
            } label: {
              Text(action.label)
                .font(.caption.weight(.semibold))
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .frame(minHeight: 36)
                .background(
                  ArgonPalette.surfaceRaised,
                  in: RoundedRectangle(cornerRadius: 10, style: .continuous)
                )
                .foregroundStyle(ArgonPalette.iceBlue)
            }
            .buttonStyle(.plain)
            .disabled(isBusy(action))
            .opacity(isBusy(action) ? 0.5 : 1)
          }
        }
      }
    }
    .padding(14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(
      ArgonPalette.surface,
      in: RoundedRectangle(cornerRadius: 14, style: .continuous)
    )
    .overlay(
      RoundedRectangle(cornerRadius: 14, style: .continuous)
        .stroke(
          // A live question is worth looking at; an answered one is a receipt.
          ArgonPalette.electricBlue.opacity(item.isWaiting ? 0.22 : 0.08),
          lineWidth: 1
        )
    )
    .opacity(item.isWaiting ? 1 : 0.62)
  }

  private func isBusy(_ action: ArgonInboxAction) -> Bool {
    bridge.inboxActionIDs.contains("\(item.id):\(action.id)")
  }
}

/// Minimal wrapping stack. SwiftUI has no first-party flow layout below iOS 16,
/// and `Layout` is the whole feature here — a few buttons that wrap.
private struct FlowRow: Layout {
  var spacing: CGFloat = 8

  func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
    let maxWidth = proposal.width ?? .infinity
    var x: CGFloat = 0
    var y: CGFloat = 0
    var rowHeight: CGFloat = 0

    for subview in subviews {
      let size = subview.sizeThatFits(.unspecified)
      if x > 0, x + size.width > maxWidth {
        x = 0
        y += rowHeight + spacing
        rowHeight = 0
      }
      x += size.width + spacing
      rowHeight = max(rowHeight, size.height)
    }
    return CGSize(width: maxWidth == .infinity ? x : maxWidth, height: y + rowHeight)
  }

  func placeSubviews(
    in bounds: CGRect,
    proposal: ProposedViewSize,
    subviews: Subviews,
    cache: inout ()
  ) {
    var x = bounds.minX
    var y = bounds.minY
    var rowHeight: CGFloat = 0

    for subview in subviews {
      let size = subview.sizeThatFits(.unspecified)
      if x > bounds.minX, x + size.width > bounds.maxX {
        x = bounds.minX
        y += rowHeight + spacing
        rowHeight = 0
      }
      subview.place(at: CGPoint(x: x, y: y), anchor: .topLeading, proposal: .unspecified)
      x += size.width + spacing
      rowHeight = max(rowHeight, size.height)
    }
  }
}

private struct ArgonTaskRow: View {
  let task: ArgonTask
  let isMutating: Bool
  /// Ticking the circle. Separate from tapping the row, which starts the task.
  var onToggle: () -> Void = {}

  var body: some View {
    HStack(alignment: .top, spacing: 12) {
      Button(action: onToggle) {
        ZStack {
          Circle()
            .stroke(circleColor, lineWidth: 1.6)
            .frame(width: 24, height: 24)

          if task.isStarted {
            Circle()
              .fill(ArgonPalette.iceBlue)
              .frame(width: 10, height: 10)
          }
        }
        // A 24pt circle is too small to hit reliably; the padding gives it a
        // 44pt target without moving anything visually.
        .padding(10)
        .contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .disabled(isMutating)
      .accessibilityLabel(task.isStarted ? "Complete \(task.title)" : "Complete \(task.title)")

      VStack(alignment: .leading, spacing: 3) {
        Text(task.title)
          .font(.system(size: 16, weight: .regular))
          .foregroundStyle(ArgonPalette.ink)
          .lineLimit(2)

        HStack(spacing: 6) {
          if task.isStarted {
            Label("In progress", systemImage: "play.fill")
              .foregroundStyle(ArgonPalette.iceBlue)
          }
          if let subject = task.subject, !subject.isEmpty {
            Text(subject).lineLimit(1)
          }
          if let due = task.dueDay {
            Text(dueLabel(due))
              .foregroundStyle(isOverdue(due) ? .orange : ArgonPalette.mutedInk)
          }
          if let estimate = task.timeEstimateMinutes {
            Text("\(estimate)m")
          }
        }
        .font(.system(size: 12))
        .foregroundStyle(ArgonPalette.mutedInk)
      }
      .padding(.top, 11)

      Spacer(minLength: 4)

      if isMutating {
        ProgressView()
          .tint(ArgonPalette.iceBlue)
          .padding(.top, 11)
      } else if task.priority == "high" {
        // Importance reads as one mark, the way a starred item does, instead of
        // a label on every row repeating what "medium" means.
        Image(systemName: "star.fill")
          .font(.system(size: 12))
          .foregroundStyle(.orange)
          .padding(.top, 13)
      }
    }
    .padding(.trailing, 14)
    .background(ArgonPalette.surface.opacity(0.55), in: RoundedRectangle(cornerRadius: 12))
  }

  private var circleColor: Color {
    if task.isStarted { return ArgonPalette.iceBlue }
    return task.priority == "high" ? .orange.opacity(0.75) : ArgonPalette.mutedInk.opacity(0.55)
  }

  private func isOverdue(_ day: Date) -> Bool {
    Calendar.autoupdatingCurrent.startOfDay(for: day)
      < Calendar.autoupdatingCurrent.startOfDay(for: Date())
  }

  /// "Today" and "Tomorrow" rather than a date he has to convert in his head.
  private func dueLabel(_ day: Date) -> String {
    let cal = Calendar.autoupdatingCurrent
    if cal.isDateInToday(day) { return "Today" }
    if cal.isDateInTomorrow(day) { return "Tomorrow" }
    if cal.isDateInYesterday(day) { return "Yesterday" }
    return day.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())
  }
}

private struct ArgonAddTaskView: View {
  @Environment(\.dismiss) private var dismiss
  @EnvironmentObject private var bridge: ArgonBridge

  @State private var title = ""
  @State private var subject = ""
  @State private var priority = "medium"
  @State private var hasDueDate = true
  @State private var dueDate = Date()
  @State private var hasEstimate = true
  @State private var estimateMinutes = 30
  @State private var isSaving = false

  var body: some View {
    NavigationStack {
      ZStack {
        ArgonBackdrop()

        Form {
          Section("Task") {
            TextField("What needs doing?", text: $title, axis: .vertical)
              .lineLimit(1...3)
            TextField("Subject or project (optional)", text: $subject)
          }

          Section("Plan") {
            Picker("Priority", selection: $priority) {
              Text("Low").tag("low")
              Text("Medium").tag("medium")
              Text("High").tag("high")
            }
            .pickerStyle(.segmented)

            Toggle("Due date", isOn: $hasDueDate.animation())
            if hasDueDate {
              DatePicker("Due", selection: $dueDate, displayedComponents: .date)
            }

            Toggle("Time estimate", isOn: $hasEstimate.animation())
            if hasEstimate {
              Stepper("\(estimateMinutes) minutes", value: $estimateMinutes, in: 5...240, step: 5)
            }
          }

          Section {
            Text(
              "This joins Argon's existing task list. Changes made here and changes made by the agent stay on the same source of truth."
            )
            .font(.caption)
            .foregroundStyle(ArgonPalette.mutedInk)
          }
        }
        .scrollContentBackground(.hidden)
      }
      .navigationTitle("New task")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button("Cancel") { dismiss() }
        }
        ToolbarItem(placement: .confirmationAction) {
          Button {
            save()
          } label: {
            if isSaving {
              ProgressView().tint(ArgonPalette.iceBlue)
            } else {
              Text("Add").fontWeight(.semibold)
            }
          }
          .disabled(trimmedTitle.isEmpty || isSaving)
        }
      }
    }
  }

  private var trimmedTitle: String {
    title.trimmingCharacters(in: .whitespacesAndNewlines)
  }

  private func save() {
    guard !trimmedTitle.isEmpty, !isSaving else { return }
    isSaving = true
    Task {
      let succeeded = await bridge.addTask(
        title: trimmedTitle,
        priority: priority,
        subject: subject.trimmingCharacters(in: .whitespacesAndNewlines),
        due: hasDueDate ? dueDate : nil,
        estimateMinutes: hasEstimate ? estimateMinutes : nil
      )
      isSaving = false
      if succeeded { dismiss() }
    }
  }
}
