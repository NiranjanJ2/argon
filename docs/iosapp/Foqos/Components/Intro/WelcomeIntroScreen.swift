import SwiftUI

struct WelcomeIntroScreen: View {
  @EnvironmentObject private var themeManager: ThemeManager

  @State private var showContent = false

  var body: some View {
    VStack(spacing: 0) {
      VStack(spacing: 12) {
        Text("WELCOME TO ARGON")
          .font(.caption2.weight(.bold))
          .tracking(2.4)
          .foregroundStyle(ArgonPalette.iceBlue.opacity(0.82))

        Text("Attention is your\nmost finite resource.")
          .font(.argonDisplay(40))
          .tracking(-1.0)
          .foregroundStyle(ArgonPalette.ink)
          .multilineTextAlignment(.center)
      }
      .opacity(showContent ? 1 : 0)
      .offset(y: showContent ? 0 : -18)

      Spacer()

      ArgonOrb(size: 224, accentColor: themeManager.themeColor)
        .scaleEffect(showContent ? 1 : 0.72)
        .opacity(showContent ? 1 : 0)

      Spacer()

      VStack(spacing: 12) {
        Text("One system. One command.")
          .font(.system(size: 24, weight: .semibold, design: .serif))
          .foregroundStyle(ArgonPalette.ink)

        Text(
          "Your Argon assistant can move you into focus and this app applies the boundary instantly."
        )
        .font(.system(size: 16))
        .foregroundStyle(ArgonPalette.mutedInk)
        .multilineTextAlignment(.center)
        .lineSpacing(4)
      }
      .padding(.horizontal, 20)
      .opacity(showContent ? 1 : 0)
      .offset(y: showContent ? 0 : 18)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .onAppear {
      withAnimation(.easeOut(duration: 0.8).delay(0.15)) {
        showContent = true
      }
    }
  }
}

#Preview {
  ZStack {
    ArgonBackdrop()
    WelcomeIntroScreen()
      .environmentObject(ThemeManager.shared)
  }
  .preferredColorScheme(.dark)
}
