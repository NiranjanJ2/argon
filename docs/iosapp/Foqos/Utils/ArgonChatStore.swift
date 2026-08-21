import Foundation

@MainActor
final class ArgonChatStore: ObservableObject {
  @Published private(set) var messages: [ArgonChatMessage]

  private let defaults: UserDefaults
  private let storageKey = "argon.chat.messages"

  init(defaults: UserDefaults = .standard) {
    self.defaults = defaults
    if let data = defaults.data(forKey: storageKey),
      let stored = try? JSONDecoder().decode([ArgonChatMessage].self, from: data)
    {
      messages = stored
    } else {
      messages = []
    }
  }

  func send(_ text: String, through bridge: ArgonBridge) async {
    let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !value.isEmpty, !bridge.isSendingMessage else { return }

    let message = ArgonChatMessage(
      id: UUID(),
      role: .user,
      text: value,
      sentAt: Date(),
      delivered: false
    )
    messages.append(message)
    persist()
    await deliver(message.id, through: bridge)
  }

  func retry(_ id: UUID, through bridge: ArgonBridge) async {
    guard !bridge.isSendingMessage else { return }
    await deliver(id, through: bridge)
  }

  /// Fold in anything Argon said while the app was closed.
  ///
  /// A push notification is gone once dismissed, so messages Argon started
  /// live on the server. Without this the chat only ever showed conversations
  /// he began, and everything Argon opened existed nowhere after the banner
  /// disappeared.
  func merge(_ incoming: [ArgonMessage]) {
    // Matched on text: the local copy has no server id, and Argon does not
    // repeat itself verbatim within a conversation. Timestamps cannot be the
    // key because the local copy is stamped when it arrived, not when it was
    // sent.
    let known = Set(messages.map(\.text))
    let additions = incoming
      .filter { $0.isFromArgon && !known.contains($0.text) }
      .map {
        ArgonChatMessage(
          id: UUID(),
          role: .argon,
          text: $0.text,
          sentAt: $0.sentDate ?? Date(),
          delivered: true
        )
      }
    guard !additions.isEmpty else { return }

    messages.append(contentsOf: additions)
    messages.sort { $0.sentAt < $1.sentAt }
    messages = Array(messages.suffix(100))
    persist()
  }

  func clear() {
    messages = []
    persist()
  }

  private func deliver(_ id: UUID, through bridge: ArgonBridge) async {
    guard let index = messages.firstIndex(where: { $0.id == id }),
      messages[index].role == .user
    else {
      return
    }

    let text = messages[index].text
    guard let reply = await bridge.sendMessage(text) else {
      return
    }

    if let deliveredIndex = messages.firstIndex(where: { $0.id == id }) {
      messages[deliveredIndex].delivered = true
    }
    messages.append(
      ArgonChatMessage(
        id: UUID(),
        role: .argon,
        text: reply,
        sentAt: Date(),
        delivered: true
      )
    )
    messages = Array(messages.suffix(100))
    persist()
  }

  private func persist() {
    guard let data = try? JSONEncoder().encode(messages) else { return }
    defaults.set(data, forKey: storageKey)
  }
}
