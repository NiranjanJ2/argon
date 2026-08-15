import SwiftUI

struct ArgonChatView: View {
  @EnvironmentObject private var bridge: ArgonBridge
  @EnvironmentObject private var chat: ArgonChatStore

  @State private var draft = ""
  @FocusState private var composerFocused: Bool

  var body: some View {
    NavigationStack {
      ZStack {
        ArgonBackdrop()

        VStack(spacing: 0) {
          conversation
          composer
        }
      }
      .navigationTitle("Argon")
      .navigationBarTitleDisplayMode(.inline)
      .toolbarBackground(ArgonPalette.canvasLifted.opacity(0.82), for: .navigationBar)
      .toolbarBackground(.visible, for: .navigationBar)
      .toolbar {
        ToolbarItem(placement: .principal) {
          VStack(spacing: 1) {
            Text("Argon")
              .font(.argonDisplay(20))
              .foregroundStyle(ArgonPalette.ink)
            Label(connectionLabel, systemImage: "circle.fill")
              .font(.system(size: 10, weight: .medium))
              .foregroundStyle(connectionColor)
              .labelStyle(.titleAndIcon)
          }
        }

        if !chat.messages.isEmpty {
          ToolbarItem(placement: .topBarTrailing) {
            Menu {
              Button("Clear conversation", systemImage: "trash", role: .destructive) {
                chat.clear()
              }
            } label: {
              Image(systemName: "ellipsis.circle")
                .foregroundStyle(ArgonPalette.iceBlue)
            }
          }
        }
      }
    }
  }

  private var conversation: some View {
    ScrollViewReader { proxy in
      ScrollView {
        LazyVStack(spacing: 14) {
          if chat.messages.isEmpty {
            emptyConversation
              .padding(.top, 54)
          }

          ForEach(chat.messages) { message in
            messageBubble(message)
              .id(message.id)
          }

          if bridge.isSendingMessage {
            typingIndicator
              .id("typing")
          }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 18)
      }
      .scrollDismissesKeyboard(.interactively)
      .onChange(of: chat.messages.count) { _, _ in
        scrollToBottom(proxy)
      }
      .onChange(of: bridge.isSendingMessage) { _, _ in
        scrollToBottom(proxy)
      }
    }
  }

  private var emptyConversation: some View {
    VStack(spacing: 18) {
      ArgonOrb(size: 116)

      VStack(spacing: 7) {
        Text("What are we doing?")
          .font(.argonDisplay(27))
          .foregroundStyle(ArgonPalette.ink)
        Text(
          "Talk to the same Argon running your day. Ask about your tasks, change the plan, or tell it to lock you in."
        )
        .font(.subheadline)
        .foregroundStyle(ArgonPalette.mutedInk)
        .multilineTextAlignment(.center)
        .lineSpacing(3)
      }
      .padding(.horizontal, 24)
    }
  }

  private func messageBubble(_ message: ArgonChatMessage) -> some View {
    HStack(alignment: .bottom, spacing: 9) {
      if message.role == .user { Spacer(minLength: 46) }

      if message.role == .argon {
        Image(systemName: "sparkles")
          .font(.system(size: 12, weight: .bold))
          .foregroundStyle(ArgonPalette.iceBlue)
          .frame(width: 28, height: 28)
          .background(ArgonPalette.electricBlue.opacity(0.14), in: Circle())
      }

      VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 5) {
        Text(message.text)
          .font(.system(size: 16, design: message.role == .argon ? .serif : .default))
          .foregroundStyle(ArgonPalette.ink)
          .textSelection(.enabled)
          .padding(.horizontal, 15)
          .padding(.vertical, 11)
          .background(
            message.role == .user
              ? ArgonPalette.cobalt.opacity(0.72)
              : ArgonPalette.surfaceRaised.opacity(0.90),
            in: RoundedRectangle(cornerRadius: 19, style: .continuous)
          )
          .overlay {
            RoundedRectangle(cornerRadius: 19, style: .continuous)
              .stroke(
                message.role == .user
                  ? ArgonPalette.iceBlue.opacity(0.18)
                  : Color.white.opacity(0.08),
                lineWidth: 1
              )
          }

        if message.role == .user, !message.delivered {
          Button {
            Task { await chat.retry(message.id, through: bridge) }
          } label: {
            Label("Not sent · Tap to retry", systemImage: "arrow.clockwise")
              .font(.caption2.weight(.medium))
              .foregroundStyle(.orange)
          }
          .disabled(bridge.isSendingMessage)
        }
      }

      if message.role == .argon { Spacer(minLength: 46) }
    }
  }

  private var typingIndicator: some View {
    HStack(spacing: 9) {
      Image(systemName: "sparkles")
        .font(.system(size: 12, weight: .bold))
        .foregroundStyle(ArgonPalette.iceBlue)
        .frame(width: 28, height: 28)
        .background(ArgonPalette.electricBlue.opacity(0.14), in: Circle())

      HStack(spacing: 5) {
        ForEach(0..<3, id: \.self) { index in
          Circle()
            .fill(ArgonPalette.mutedInk)
            .frame(width: 6, height: 6)
            .opacity(index == 1 ? 1 : 0.52)
        }
      }
      .padding(.horizontal, 15)
      .padding(.vertical, 13)
      .background(ArgonPalette.surfaceRaised.opacity(0.9), in: Capsule())

      Spacer()
    }
  }

  private var composer: some View {
    HStack(alignment: .bottom, spacing: 10) {
      TextField("Message Argon…", text: $draft, axis: .vertical)
        .focused($composerFocused)
        .font(.body)
        .foregroundStyle(ArgonPalette.ink)
        .lineLimit(1...5)
        .textInputAutocapitalization(.sentences)
        .submitLabel(.send)
        .onSubmit(send)
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
        .background(.black.opacity(0.24), in: RoundedRectangle(cornerRadius: 22))
        .overlay {
          RoundedRectangle(cornerRadius: 22)
            .stroke(ArgonPalette.electricBlue.opacity(0.18), lineWidth: 1)
        }

      Button(action: send) {
        Group {
          if bridge.isSendingMessage {
            ProgressView().tint(ArgonPalette.canvas)
          } else {
            Image(systemName: "arrow.up")
              .font(.system(size: 15, weight: .bold))
          }
        }
        .frame(width: 43, height: 43)
        .foregroundStyle(ArgonPalette.canvas)
        .background(ArgonPalette.iceBlue, in: Circle())
        .shadow(color: ArgonPalette.electricBlue.opacity(0.48), radius: 12)
      }
      .disabled(trimmedDraft.isEmpty || bridge.isSendingMessage)
      .opacity(trimmedDraft.isEmpty ? 0.48 : 1)
      .accessibilityLabel("Send to Argon")
    }
    .padding(.horizontal, 14)
    .padding(.top, 10)
    .padding(.bottom, 9)
    .background(.ultraThinMaterial)
  }

  private var trimmedDraft: String {
    draft.trimmingCharacters(in: .whitespacesAndNewlines)
  }

  private var connectionLabel: String {
    bridge.connectionState == "Connected" ? "Connected to your agent" : bridge.connectionState
  }

  private var connectionColor: Color {
    bridge.connectionState == "Connected" ? .green : ArgonPalette.mutedInk
  }

  private func send() {
    let value = trimmedDraft
    guard !value.isEmpty, !bridge.isSendingMessage else { return }
    draft = ""
    Task { await chat.send(value, through: bridge) }
  }

  private func scrollToBottom(_ proxy: ScrollViewProxy) {
    withAnimation(.easeOut(duration: 0.22)) {
      if bridge.isSendingMessage {
        proxy.scrollTo("typing", anchor: .bottom)
      } else if let last = chat.messages.last {
        proxy.scrollTo(last.id, anchor: .bottom)
      }
    }
  }
}
