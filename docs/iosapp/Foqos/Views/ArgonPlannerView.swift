import SwiftUI

/// The afternoon planning moment, as four questions rather than one form.
///
/// Argon used to assume anything past its due date was still outstanding and
/// had no way to learn otherwise, so finished work sat on the board for days
/// and got asked about every evening. This is where it asks instead.
///
/// Four steps rather than one screen because they are four different questions
/// and one of them — long-term work — loses every time it shares space with
/// something on fire. Asked first, before the deadlines are even visible, it
/// gets a fair hearing.
struct ArgonPlannerView: View {
  @EnvironmentObject private var bridge: ArgonBridge
  @Environment(\.dismiss) private var dismiss

  let payload: ArgonPlannerPayload

  @State private var step = 0
  @State private var longTermPicked: Set<String> = []
  @State private var stillToDo: Set<String> = []
  @State private var alreadyDone: Set<String> = []
  @State private var nothingOverdue = false
  @State private var accepted: Set<String> = []
  @State private var extras: [String] = []
  @State private var newTitle = ""
  @State private var startAt = Calendar.current.date(
    bySettingHour: 17, minute: 0, second: 0, of: Date()) ?? Date()
  @State private var wantsStartTime = true
  @State private var isSaving = false

  private var steps: [String] { ["Long-term", "Past due", "Anything else", "Your day"] }

  var body: some View {
    NavigationStack {
      VStack(spacing: 0) {
        progress
        Divider()

        Group {
          switch step {
          case 0: longTermStep
          case 1: overdueStep
          case 2: extrasStep
          default: dayStep
          }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)

        Divider()
        controls
      }
      .navigationTitle(steps[min(step, steps.count - 1)])
      .navigationBarTitleDisplayMode(.inline)
    }
    .interactiveDismissDisabled(isSaving)
  }

  // MARK: - Chrome

  private var progress: some View {
    HStack(spacing: 6) {
      ForEach(0..<steps.count, id: \.self) { index in
        Capsule()
          .fill(index <= step ? ArgonPalette.iceBlue : ArgonPalette.mutedInk.opacity(0.25))
          .frame(height: 3)
      }
    }
    .padding(.horizontal, 16)
    .padding(.vertical, 10)
  }

  private var controls: some View {
    HStack {
      if step > 0 {
        Button("Back") { withAnimation { step -= 1 } }
          .buttonStyle(ArgonSecondaryButtonStyle())
          .frame(maxWidth: 110)
      }
      Spacer()
      Button(step == steps.count - 1 ? (isSaving ? "Saving…" : "Start the day") : "Next") {
        if step == steps.count - 1 { save() } else { withAnimation { step += 1 } }
      }
      .buttonStyle(ArgonPrimaryButtonStyle())
      .disabled(isSaving || !canAdvance)
      .opacity(canAdvance ? 1 : 0.45)
    }
    .padding(16)
  }

  /// The only gate: past due must be answered, and "none of it" counts.
  private var canAdvance: Bool {
    guard step == 1, !payload.overdue.isEmpty else { return true }
    return nothingOverdue || !(stillToDo.isEmpty && alreadyDone.isEmpty)
  }

  // MARK: - Steps

  private var longTermStep: some View {
    stepBody(
      question: "What long-term work do you want to touch today?",
      note: "Nothing forces these, so they lose to whatever is due. Pick them first."
    ) {
      if payload.longTerm.isEmpty {
        emptyLine("No long-term work on the board.")
      } else {
        ForEach(payload.longTerm) { item in
          selectRow(
            title: item.title,
            caption: item.subject,
            isOn: binding(item.id, in: $longTermPicked)
          )
        }
      }
    }
  }

  private var overdueStep: some View {
    stepBody(
      question: "Which of these still actually exist?",
      note: "Argon has no way to know what you already finished. Anything you mark done comes off for good."
    ) {
      if payload.overdue.isEmpty {
        emptyLine("Nothing is past due.")
      } else {
        ArgonChoiceButton(
          title: "None of it — it's all done",
          caption: "Clears every item below",
          isSelected: nothingOverdue
        ) {
          nothingOverdue.toggle()
        }
          .onChange(of: nothingOverdue) { _, on in
            if on {
              alreadyDone = Set(payload.overdue.map(\.id))
              stillToDo.removeAll()
            } else {
              alreadyDone.removeAll()
            }
          }

        ForEach(payload.overdue) { item in
          VStack(alignment: .leading, spacing: 6) {
            Text(item.title).font(.subheadline)
            if let stale = item.staleness {
              Text([item.subject, stale].filter { !$0.isEmpty }.joined(separator: " · "))
                .font(.caption2).foregroundStyle(.orange)
            }
            HStack(spacing: 8) {
              ForEach(
                [
                  (OverdueChoice.todo, "Still to do"),
                  (OverdueChoice.done, "Done"),
                  (OverdueChoice.skip, "Skip"),
                ],
                id: \.0
              ) { choice, label in
                Button {
                  overdueChoice(item.id).wrappedValue = choice
                } label: {
                  Text(label)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(
                      overdueChoice(item.id).wrappedValue == choice
                        ? ArgonPalette.ink : ArgonPalette.mutedInk
                    )
                    .frame(maxWidth: .infinity)
                    .frame(height: 44)
                    .argonSelectable(overdueChoice(item.id).wrappedValue == choice, cornerRadius: 10)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
              }
            }
            .opacity(nothingOverdue ? 0.4 : 1)
            .disabled(nothingOverdue)
          }
          .padding(.vertical, 4)
        }
      }
    }
  }

  private var extrasStep: some View {
    stepBody(
      question: "Anything else on today?",
      note: "AP Chem is here because nothing else can see it — it is never assumed either way."
    ) {
      ForEach(payload.suggestions) { suggestion in
        selectRow(
          title: suggestion.title,
          caption: suggestion.prompt ?? suggestion.subject ?? "",
          isOn: binding(suggestion.id, in: $accepted)
        )
      }
      ForEach(extras, id: \.self) { title in
        Label(title, systemImage: "checkmark.circle.fill")
          .font(.subheadline)
          .foregroundStyle(.green)
      }
      HStack {
        TextField("Something else", text: $newTitle)
          .submitLabel(.done)
          .onSubmit(addTyped)
        Button(action: addTyped) { Image(systemName: "plus.circle.fill") }
          .disabled(newTitle.trimmingCharacters(in: .whitespaces).isEmpty)
      }
      .padding(.top, 4)
    }
  }

  private var dayStep: some View {
    stepBody(
      question: "Here's your day. When do you want to start?",
      note: "You'll get a notification \(payload.warningMinutes) minutes before, and your phone locks down at that time until you start something."
    ) {
      ForEach(plannedTitles, id: \.self) { title in
        Label(title, systemImage: "circle")
          .font(.subheadline)
      }
      if plannedTitles.isEmpty {
        emptyLine("Nothing planned — a clear evening.")
      }

      Divider().padding(.vertical, 6)

      Toggle("Set a start time", isOn: $wantsStartTime)
      if wantsStartTime {
        DatePicker(
          "Start at", selection: $startAt, displayedComponents: .hourAndMinute
        )
        .datePickerStyle(.compact)
      }
    }
  }

  // MARK: - Pieces

  private func stepBody<Content: View>(
    question: String, note: String, @ViewBuilder content: () -> Content
  ) -> some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 14) {
        Text(question)
          .font(.title3.weight(.semibold))
          .fixedSize(horizontal: false, vertical: true)
        Text(note)
          .font(.caption)
          .foregroundStyle(.secondary)
          .fixedSize(horizontal: false, vertical: true)
        VStack(alignment: .leading, spacing: 10) { content() }
          .padding(.top, 4)
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(20)
    }
  }

  private func selectRow(title: String, caption: String, isOn: Binding<Bool>) -> some View {
    ArgonChoiceButton(
      title: title,
      caption: caption,
      isSelected: isOn.wrappedValue
    ) {
      isOn.wrappedValue.toggle()
    }
  }

  private func emptyLine(_ text: String) -> some View {
    Text(text).font(.subheadline).foregroundStyle(.secondary)
  }

  private var plannedTitles: [String] {
    payload.today.map(\.title)
      + payload.longTerm.filter { longTermPicked.contains($0.id) }.map(\.title)
      + payload.overdue.filter { stillToDo.contains($0.id) }.map(\.title)
      + payload.suggestions.filter { accepted.contains($0.id) }.map(\.title)
      + extras
  }

  // MARK: - State

  private enum OverdueChoice: Hashable { case todo, done, skip }

  private func overdueChoice(_ id: String) -> Binding<OverdueChoice> {
    Binding(
      get: {
        if alreadyDone.contains(id) { return .done }
        if stillToDo.contains(id) { return .todo }
        return .skip
      },
      set: { picked in
        alreadyDone.remove(id)
        stillToDo.remove(id)
        if picked == .done { alreadyDone.insert(id) }
        if picked == .todo { stillToDo.insert(id) }
      }
    )
  }

  private func binding(_ id: String, in set: Binding<Set<String>>) -> Binding<Bool> {
    Binding(
      get: { set.wrappedValue.contains(id) },
      set: { on in
        if on { set.wrappedValue.insert(id) } else { set.wrappedValue.remove(id) }
      }
    )
  }

  private func addTyped() {
    let title = newTitle.trimmingCharacters(in: .whitespaces)
    guard !title.isEmpty else { return }
    extras.append(title)
    newTitle = ""
  }

  private func save() {
    isSaving = true
    let chosen = payload.suggestions.filter { accepted.contains($0.id) }
    let chem = chosen.contains { $0.kind == "chem" }

    var add: [[String: Any]] = chosen.filter { $0.kind != "chem" }.map {
      var row: [String: Any] = ["title": $0.title]
      if let subject = $0.subject { row["subject"] = subject }
      if let minutes = $0.estimateMin { row["estimate_min"] = minutes }
      return row
    }
    add += extras.map { ["title": $0] }

    let formatter = DateFormatter()
    formatter.dateFormat = "HH:mm"
    let time = wantsStartTime ? formatter.string(from: startAt) : nil

    Task {
      await bridge.submitPlan(
        done: Array(alreadyDone),
        // Long-term work and still-outstanding overdue both mean "today", so
        // they carry to today's date together.
        carry: Array(stillToDo) + Array(longTermPicked),
        add: add,
        chem: chem,
        startAt: time
      )
      isSaving = false
      dismiss()
    }
  }
}
