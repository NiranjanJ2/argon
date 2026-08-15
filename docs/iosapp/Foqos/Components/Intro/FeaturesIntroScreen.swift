import SwiftUI

struct FeaturesIntroScreen: View {
  @EnvironmentObject private var themeManager: ThemeManager

  @State private var selectedFeature: Int = 0
  @State private var showContent: Bool = false

  let features = [
    Feature(
      systemName: "bolt.horizontal.circle.fill",
      title: "Direct Control",
      description:
        "Argon sends focus commands straight to this app. No email rules, carrier gateways, or Shortcuts automations."
    ),
    Feature(
      systemName: "shield.lefthalf.filled",
      title: "Screen Time Engine",
      description:
        "Choose the apps and sites you want out of reach. The open-source Foqos engine applies Apple's Screen Time restrictions on-device."
    ),
    Feature(
      systemName: "rectangle.3.group.bubble.left.fill",
      title: "One Dashboard",
      description:
        "See Argon's live mode, current task, focus time, and Screen Time state—and talk to your assistant from the same place."
    ),
  ]

  var body: some View {
    VStack(spacing: 0) {
      // Header
      VStack(spacing: 8) {
        Text("Powerful Features")
          .font(.argonDisplay(38))
          .foregroundStyle(ArgonPalette.ink)
          .opacity(showContent ? 1 : 0)
          .offset(y: showContent ? 0 : -20)

        Text("Everything you need to stay focused")
          .font(.system(size: 16))
          .foregroundStyle(ArgonPalette.mutedInk)
          .opacity(showContent ? 1 : 0)
          .offset(y: showContent ? 0 : -20)
      }

      Spacer()

      // Feature selector and display
      VStack(spacing: 0) {
        // Icon selector
        HStack(spacing: 20) {
          ForEach(0..<features.count, id: \.self) { index in
            Button(action: {
              withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                selectedFeature = index
              }
            }) {
              Image(systemName: features[index].systemName)
                .font(.system(size: 30, weight: .semibold))
                .foregroundStyle(ArgonPalette.iceBlue)
                .frame(width: 64, height: 64)
                .background(.white.opacity(0.055), in: Circle())
                .overlay {
                  Circle()
                    .stroke(ArgonPalette.electricBlue.opacity(0.22), lineWidth: 1)
                }
                .opacity(selectedFeature == index ? 1.0 : 0.4)
                .scaleEffect(selectedFeature == index ? 1.12 : 1.0)
            }
          }
        }
        .opacity(showContent ? 1 : 0)
        .padding(.bottom, 50)

        // Feature content
        VStack(spacing: 30) {
          // Feature icon
          Image(systemName: features[selectedFeature].systemName)
            .font(.system(size: 58, weight: .medium))
            .foregroundStyle(ArgonPalette.iceBlue)
            .frame(width: 124, height: 124)
            .background(ArgonPalette.electricBlue.opacity(0.10), in: Circle())
            .shadow(color: themeManager.themeColor.opacity(0.2), radius: 12, y: 6)
            .transition(.scale.combined(with: .opacity))
            .id("icon-\(selectedFeature)")

          // Feature text
          VStack(spacing: 12) {
            Text(features[selectedFeature].title)
              .font(.argonDisplay(29))
              .foregroundStyle(ArgonPalette.ink)
              .multilineTextAlignment(.center)
              .transition(.opacity)
              .id("title-\(selectedFeature)")

            Text(features[selectedFeature].description)
              .font(.system(size: 17))
              .foregroundStyle(ArgonPalette.mutedInk)
              .multilineTextAlignment(.center)
              .lineSpacing(4)
              .padding(.horizontal, 32)
              .transition(.opacity)
              .id("description-\(selectedFeature)")
          }
        }
        .padding(.vertical, 20)
        .argonGlassPanel(cornerRadius: 28)
        .opacity(showContent ? 1 : 0)
      }

      Spacer()

      // Tap indicator
      HStack(spacing: 6) {
        Image(systemName: "hand.tap.fill")
          .font(.system(size: 14))
          .foregroundColor(.secondary)
        Text("Tap icons to explore features")
          .font(.system(size: 14))
          .foregroundColor(.secondary)
      }
      .opacity(showContent ? 0.7 : 0)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .onAppear {
      withAnimation(.easeOut(duration: 0.6).delay(0.2)) {
        showContent = true
      }
    }
  }
}

struct Feature: Identifiable {
  let id = UUID()
  let systemName: String
  let title: String
  let description: String
}

#Preview {
  FeaturesIntroScreen()
    .background(Color(.systemBackground))
    .environmentObject(ThemeManager.shared)
}
