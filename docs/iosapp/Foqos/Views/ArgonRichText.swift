import SwiftUI

/// One line of an Argon message, already classified.
///
/// Argon wrote in one voice before: a paragraph, however long, however much of
/// it was really a list. Everything it says is either prose, a set of things, or
/// a set of things to tick off, and a wall of text makes all three look the same.
///
/// Parsing is line-based on purpose. A full Markdown engine is a dependency and
/// a rendering surface, and the only constructs worth having in a chat bubble
/// are these — inline styling comes free from `AttributedString(markdown:)`.
enum ArgonBlock: Equatable, Identifiable {
  case heading(String)
  case paragraph(String)
  case bullet(String)
  case numbered(index: Int, text: String)
  case checkbox(id: String, text: String, checked: Bool)
  /// A line that is nothing but `argon:` links, rendered as a row of buttons.
  case actions([ArgonAction])
  case divider

  var id: String {
    switch self {
    case .heading(let t): return "h:\(t)"
    case .paragraph(let t): return "p:\(t)"
    case .bullet(let t): return "b:\(t)"
    case .numbered(let i, let t): return "n:\(i):\(t)"
    case .checkbox(let id, _, _): return "c:\(id)"
    case .actions(let a): return "a:" + a.map(\.label).joined(separator: "|")
    case .divider: return "hr:\(UUID().uuidString)"
    }
  }
}

/// A button Argon offered, written as a link so no payload had to grow.
///
/// `[Start HW 9](argon:start/8c45122ea6b542c6)` — the verb and the task id are
/// already in the message, and the model already knows task ids from
/// `list_tasks`. Encoding these as a structured `actions` array would have meant
/// a field on the mailbox, a field on the response, a field on the model, and a
/// tool parameter for the model to populate, to carry two strings a link holds.
struct ArgonAction: Equatable {
  let label: String
  let verb: String
  let taskID: String

  /// Parses `argon:<verb>/<id>`. Anything else is not an action and stays a link.
  init?(label: String, url: String) {
    guard url.hasPrefix("argon:") else { return nil }
    let parts = url.dropFirst("argon:".count).split(separator: "/", maxSplits: 1)
    guard parts.count == 2, !parts[0].isEmpty, !parts[1].isEmpty else { return nil }
    self.label = label
    self.verb = String(parts[0])
    self.taskID = String(parts[1])
  }
}

enum ArgonMarkdown {
  private static let bullet = /^\s*[-*•]\s+(.*)$/
  private static let numbered = /^\s*(\d+)[.)]\s+(.*)$/
  private static let checkbox = /^\s*[-*]\s+\[([ xX])\]\s+(.*)$/
  private static let heading = /^\s*#{1,6}\s+(.*)$/
  private static let divider = /^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/
  private static let link = /\[([^\]]+)\]\(([^)]+)\)/

  /// Every `argon:` link on the line, but only if the line is *nothing else*.
  /// A sentence that happens to contain one is prose with a link in it.
  static func actionRow(_ line: String) -> [ArgonAction]? {
    var found: [ArgonAction] = []
    var rest = line
    for m in line.matches(of: link) {
      guard let action = ArgonAction(label: String(m.1), url: String(m.2)) else { return nil }
      found.append(action)
      rest = rest.replacingOccurrences(of: String(m.0), with: "")
    }
    guard !found.isEmpty, rest.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
    return found
  }

  /// Split a message into renderable blocks. Never throws: an unparseable line
  /// is a paragraph, because dropping something Argon said is worse than
  /// rendering it plainly.
  static func blocks(_ text: String, messageID: String = "") -> [ArgonBlock] {
    var out: [ArgonBlock] = []
    for (offset, raw) in text.components(separatedBy: .newlines).enumerated() {
      let line = raw.trimmingCharacters(in: .whitespaces)
      if line.isEmpty { continue }

      // Checkbox before bullet: "- [ ] x" matches both, and the checkbox is
      // the more specific reading.
      if let row = actionRow(line) {
        out.append(.actions(row))
      } else if let m = line.firstMatch(of: checkbox) {
        let checked = String(m.1).lowercased() == "x"
        out.append(.checkbox(id: "\(messageID):\(offset)", text: String(m.2), checked: checked))
      } else if line.firstMatch(of: divider) != nil {
        out.append(.divider)
      } else if let m = line.firstMatch(of: heading) {
        out.append(.heading(String(m.1)))
      } else if let m = line.firstMatch(of: numbered) {
        out.append(.numbered(index: Int(m.1) ?? 1, text: String(m.2)))
      } else if let m = line.firstMatch(of: bullet) {
        out.append(.bullet(String(m.1)))
      } else {
        out.append(.paragraph(line))
      }
    }
    return out
  }

  /// Inline styling — bold, italic, code, links. Falls back to the raw string,
  /// so a stray asterisk shows as an asterisk rather than eating the message.
  static func inline(_ text: String) -> AttributedString {
    (try? AttributedString(
      markdown: text,
      options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
    )) ?? AttributedString(text)
  }
}

/// Where a viewer's ticks live.
///
/// A checkbox in a message is his scratchpad, not the task board: Argon does not
/// know which commitment a line refers to, and guessing by title match is how a
/// tick against "read chapter 3" would close the wrong assignment. Ticking a
/// real task is what the dashboard is for.
enum ArgonCheckboxState {
  private static let key = "argon.message.checkboxes"

  static func checked(_ id: String) -> Bool? {
    (UserDefaults.standard.dictionary(forKey: key) as? [String: Bool])?[id]
  }

  static func set(_ id: String, _ value: Bool) {
    var all = (UserDefaults.standard.dictionary(forKey: key) as? [String: Bool]) ?? [:]
    all[id] = value
    UserDefaults.standard.set(all, forKey: key)
  }
}

struct ArgonRichText: View {
  let text: String
  let messageID: String
  var tint: Color = ArgonPalette.iceBlue
  /// Runs a button. Nil in previews and wherever nothing can act on it, which
  /// is why the buttons render disabled rather than lying about what they do.
  var onAction: ((ArgonAction) -> Void)? = nil

  @State private var ticks: [String: Bool] = [:]

  private var blocks: [ArgonBlock] { ArgonMarkdown.blocks(text, messageID: messageID) }

  var body: some View {
    VStack(alignment: .leading, spacing: 7) {
      ForEach(blocks) { block in
        switch block {
        case .heading(let t):
          Text(ArgonMarkdown.inline(t))
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(ArgonPalette.ink)
            .padding(.top, 2)

        case .paragraph(let t):
          Text(ArgonMarkdown.inline(t))

        case .bullet(let t):
          row(marker: Text("•").foregroundStyle(tint), text: t)

        case .numbered(let i, let t):
          row(
            marker: Text("\(i).").foregroundStyle(tint).monospacedDigit(),
            text: t
          )

        case .checkbox(let id, let t, let initial):
          checkbox(id: id, text: t, initial: initial)

        case .actions(let row):
          buttons(row)

        case .divider:
          Rectangle()
            .fill(ArgonPalette.ink.opacity(0.12))
            .frame(height: 1)
            .padding(.vertical, 3)
        }
      }
    }
    .textSelection(.enabled)
  }

  private func row(marker: some View, text: String) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: 8) {
      marker.font(.system(size: 15, weight: .semibold))
      Text(ArgonMarkdown.inline(text))
      Spacer(minLength: 0)
    }
  }

  private func buttons(_ row: [ArgonAction]) -> some View {
    HStack(spacing: 8) {
      ForEach(row, id: \.label) { action in
        Button { onAction?(action) } label: {
          Text(action.label)
            .font(.system(size: 14, weight: .medium))
            .foregroundStyle(onAction == nil ? ArgonPalette.mutedInk : tint)
            .padding(.horizontal, 13)
            .padding(.vertical, 7)
            .background(
              tint.opacity(onAction == nil ? 0.06 : 0.14),
              in: Capsule()
            )
        }
        .buttonStyle(.plain)
        .disabled(onAction == nil)
      }
      Spacer(minLength: 0)
    }
    .padding(.top, 2)
  }

  private func checkbox(id: String, text: String, initial: Bool) -> some View {
    let on = ticks[id] ?? ArgonCheckboxState.checked(id) ?? initial
    return Button {
      ticks[id] = !on
      ArgonCheckboxState.set(id, !on)
    } label: {
      HStack(alignment: .firstTextBaseline, spacing: 8) {
        Image(systemName: on ? "checkmark.circle.fill" : "circle")
          .font(.system(size: 15))
          .foregroundStyle(on ? tint : ArgonPalette.mutedInk)
        Text(ArgonMarkdown.inline(text))
          .strikethrough(on, color: ArgonPalette.mutedInk)
          .foregroundStyle(on ? ArgonPalette.mutedInk : ArgonPalette.ink)
        Spacer(minLength: 0)
      }
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
  }
}
