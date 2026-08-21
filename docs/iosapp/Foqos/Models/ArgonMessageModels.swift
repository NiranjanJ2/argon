import Foundation

/// One line of the conversation, as the server holds it.
struct ArgonMessage: Codable, Identifiable, Equatable {
  let role: String
  let text: String
  let at: String?

  /// Role and timestamp together: the server has no id for these, and two
  /// identical lines a minute apart are genuinely two messages.
  var id: String { "\(role):\(at ?? "")\(text.prefix(24))" }

  var isFromArgon: Bool { role == "assistant" }

  var sentDate: Date? {
    guard let at else { return nil }
    return ArgonServerDate.parse(at)
  }
}

struct ArgonMessagesResponse: Codable {
  let messages: [ArgonMessage]
  let unread: Int
}
