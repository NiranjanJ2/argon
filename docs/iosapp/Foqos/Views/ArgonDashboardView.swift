import SwiftUI

struct ArgonDashboardView: View {
  @EnvironmentObject private var bridge: ArgonBridge
  @State private var showingAddTask = false

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
        .refreshable { await bridge.refreshTasks() }
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
          ArgonTaskRow(task: task, isMutating: bridge.taskMutationIDs.contains(task.id))
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
            .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
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

private struct ArgonTaskRow: View {
  let task: ArgonTask
  let isMutating: Bool

  var body: some View {
    HStack(spacing: 13) {
      ZStack {
        Circle()
          .stroke(priorityColor.opacity(0.58), lineWidth: 1.5)
          .frame(width: 28, height: 28)

        if task.isStarted {
          Image(systemName: "play.fill")
            .font(.system(size: 9, weight: .bold))
            .foregroundStyle(ArgonPalette.iceBlue)
        }
      }
      .shadow(color: task.isStarted ? ArgonPalette.electricBlue.opacity(0.55) : .clear, radius: 7)

      VStack(alignment: .leading, spacing: 5) {
        Text(task.title)
          .font(.system(size: 16, weight: .medium, design: .serif))
          .foregroundStyle(ArgonPalette.ink)
          .lineLimit(2)

        HStack(spacing: 7) {
          if let subject = task.subject, !subject.isEmpty {
            Text(subject)
              .lineLimit(1)
          }
          if let due = task.dueDay {
            Label(due.formatted(date: .abbreviated, time: .omitted), systemImage: "calendar")
          }
          if let estimate = task.timeEstimateMinutes {
            Label("\(estimate)m", systemImage: "hourglass")
          }
        }
        .font(.caption2.weight(.medium))
        .foregroundStyle(ArgonPalette.mutedInk)
      }

      Spacer(minLength: 8)

      if isMutating {
        ProgressView()
          .tint(ArgonPalette.iceBlue)
      } else {
        Text(task.priority.uppercased())
          .font(.system(size: 8, weight: .bold))
          .tracking(0.9)
          .foregroundStyle(priorityColor)
          .padding(.horizontal, 7)
          .padding(.vertical, 5)
          .background(priorityColor.opacity(0.10), in: Capsule())
      }
    }
    .padding(.horizontal, 15)
    .padding(.vertical, 14)
    .background(ArgonPalette.surface.opacity(0.82), in: RoundedRectangle(cornerRadius: 20))
    .overlay {
      RoundedRectangle(cornerRadius: 20)
        .stroke(Color.white.opacity(0.07), lineWidth: 1)
    }
  }

  private var priorityColor: Color {
    switch task.priority {
    case "high": return .orange
    case "low": return ArgonPalette.mutedInk
    default: return ArgonPalette.iceBlue
    }
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
