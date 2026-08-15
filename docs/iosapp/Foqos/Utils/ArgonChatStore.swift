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
