import SwiftUI

/// The afternoon planning moment.
///
/// Argon used to assume anything past its due date was still outstanding, with
/// no way to learn otherwise — so work he had finished sat on the board for
/// days and got asked about every evening. This is where it asks instead.
///
/// Deliberately a sheet he must answer rather than a banner he can scroll past:
/// the whole value is one pass over everything, once, instead of being nagged
/// item by item for the rest of the night.
struct ArgonPlannerView: View {
  @EnvironmentObject private var bridge: ArgonBridge
  @Environment(\.dismiss) private var dismiss

  let payload: ArgonPlannerPayload

  @State private var done: Set<String> = []
  @State private var carry: Set<String> = []
  @State private var extras: [ArgonPlannerSuggestion] = []
  @State private var accepted: Set<String> = []
  @State private var newTitle = ""
  @State private var isSaving = false

  var body: some View {
    NavigationStack {
      Form {
        if !payload.overdue.isEmpty { overdueSection }
        if !payload.today.isEmpty { todaySection }
        suggestionsSection
        addSection
      }
      .navigationTitle("Plan the afternoon")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button(isSaving ? "Saving…" : "Done") { save() }
            .disabled(isSaving)
            .fontWeight(.semibold)
        }
      }
    }
    .interactiveDismissDisabled(isSaving)
  }

  // MARK: - Sections

  private var overdueSection: some View {
    Section {
      ForEach(payload.overdue) { item in
        VStack(alignment: .leading, spacing: 8) {
          HStack {
            VStack(alignment: .leading, spacing: 2) {
              Text(item.title).font(.subheadline)
              if let stale = item.staleness {
                Text([item.subject, stale].filter { !$0.isEmpty }.joined(separator: " · "))
                  .font(.caption2)
                  .foregroundStyle(.orange)
              }
            }
            Spacer()
          }
          Picker("", selection: choice(for: item.id)) {
            Text("Still to do").tag(Choice.leave)
            Text("Already done").tag(Choice.done)
            Text("Do today").tag(Choice.carry)
          }
          .pickerStyle(.segmented)
        }
        .padding(.vertical, 2)
      }
    } header: {
      Text("Past due")
    } footer: {
      Text(
        "Argon has no way to know which of these you already finished. "
          + "Whatever you mark done here comes off for good."
      )
    }
  }

  private var todaySection: some View {
    Section("Due today") {
      ForEach(payload.today) { item in
        HStack {
          Text(item.title).font(.subheadline)
          Spacer()
          if !item.subject.isEmpty {
            Text(item.subject).font(.caption2).foregroundStyle(.secondary)
          }
        }
      }
    }
  }

  private var suggestionsSection: some View {
    Section {
      ForEach(payload.suggestions) { suggestion in
        Toggle(isOn: acceptance(for: suggestion)) {
          VStack(alignment: .leading, spacing: 2) {
            Text(suggestion.title).font(.subheadline)
            if let prompt = suggestion.prompt {
              Text(prompt).font(.caption2).foregroundStyle(.secondary)
            }
          }
        }
      }
      ForEach(extras) { extra in
        HStack {
          Image(systemName: "plus.circle.fill").foregroundStyle(.green)
          Text(extra.title).font(.subheadline)
        }
      }
    } header: {
      Text("Anything Argon can't see")
    } footer: {
      Text("AP Chem is never assumed either way — it is only here because nothing else can tell.")
    }
  }

  private var addSection: some View {
    Section("Add anything else") {
      HStack {
        TextField("Something else due today", text: $newTitle)
          .submitLabel(.done)
          .onSubmit(addTyped)
        Button(action: addTyped) { Image(systemName: "plus.circle.fill") }
          .disabled(newTitle.trimmingCharacters(in: .whitespaces).isEmpty)
      }
    }
  }

  // MARK: - State

  private enum Choice: Hashable { case leave, done, carry }

  private func choice(for id: String) -> Binding<Choice> {
    Binding(
      get: {
        if done.contains(id) { return .done }
        if carry.contains(id) { return .carry }
        return .leave
      },
      set: { picked in
        done.remove(id)
        carry.remove(id)
        if picked == .done { done.insert(id) }
        if picked == .carry { carry.insert(id) }
      }
    )
  }

  private func acceptance(for suggestion: ArgonPlannerSuggestion) -> Binding<Bool> {
    Binding(
      get: { accepted.contains(suggestion.id) },
      set: { on in
        if on { accepted.insert(suggestion.id) } else { accepted.remove(suggestion.id) }
      }
    )
  }

  private func addTyped() {
    let title = newTitle.trimmingCharacters(in: .whitespaces)
    guard !title.isEmpty else { return }
    extras.append(ArgonPlannerSuggestion(manualTitle: title))
    newTitle = ""
  }

  private func save() {
    isSaving = true
    let chosen = payload.suggestions.filter { accepted.contains($0.id) }
    let chem = chosen.contains { $0.kind == "chem" }
    let add: [[String: Any]] = (chosen.filter { $0.kind != "chem" } + extras).map {
      var row: [String: Any] = ["title": $0.title]
      if let subject = $0.subject { row["subject"] = subject }
      if let minutes = $0.estimateMin { row["estimate_min"] = minutes }
      return row
    }

    Task {
      await bridge.submitPlan(
        done: Array(done), carry: Array(carry), add: add, chem: chem
      )
      isSaving = false
      dismiss()
    }
  }
}

extension ArgonPlannerSuggestion {
  /// Something he typed in, which never came from the server.
  init(manualTitle: String) {
    self.init(
      kind: "manual", title: manualTitle, subject: nil,
      prompt: nil, estimateMin: nil, isDefault: true
    )
  }
}
