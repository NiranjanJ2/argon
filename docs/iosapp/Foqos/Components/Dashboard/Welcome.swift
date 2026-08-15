import SwiftUI

struct Welcome: View {
  @EnvironmentObject var themeManager: ThemeManager
  let onGuidedTap: () -> Void
  let onAdvancedTap: () -> Void

  var body: some View {
    VStack(spacing: 22) {
      ArgonOrb(size: 154, accentColor: themeManager.themeColor)
        .padding(.top, 6)

      VStack(spacing: 12) {
        Text("FOCUS, WITHOUT FRICTION")
          .font(.caption2.weight(.bold))
          .tracking(2.2)
          .foregroundStyle(ArgonPalette.iceBlue.opacity(0.82))

        Text("Make space for\nwhat matters.")
          .font(.argonDisplay(39))
          .tracking(-1.0)
          .foregroundStyle(ArgonPalette.ink)
          .multilineTextAlignment(.center)

        Text("Create a focus profile once. Argon can take the wheel whenever it’s time to lock in.")
          .font(.subheadline)
          .foregroundStyle(ArgonPalette.mutedInk)
          .multilineTextAlignment(.center)
          .lineSpacing(4)
          .fixedSize(horizontal: false, vertical: true)
          .padding(.horizontal, 8)

        ShimmerLauncherButton(
          title: "Create your first profile",
          iconName: "brain.head.profile",
          height: 56,
          accessibilityLabel: "Start guided profile setup",
          action: onGuidedTap
        )
        .padding(.top, 6)

        Button(action: onAdvancedTap) {
          Text("Use the full profile editor")
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(ArgonPalette.iceBlue)
        }
        .buttonStyle(.plain)
      }
    }
    .frame(maxWidth: .infinity)
    .padding(.horizontal, 18)
    .padding(.vertical, 24)
    .argonGlassPanel(cornerRadius: 32, strokeOpacity: 0.22)
  }
}

#Preview {
  ZStack {
    Color.gray.opacity(0.1).ignoresSafeArea()

    Welcome(
      onGuidedTap: { print("Guided tapped") },
      onAdvancedTap: { print("Advanced tapped") }
    )
    .padding(.horizontal)
    .environmentObject(ThemeManager.shared)
  }
}
