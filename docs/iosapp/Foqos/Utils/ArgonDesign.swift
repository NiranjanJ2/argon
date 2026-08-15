import SwiftUI

enum ArgonPalette {
  static let canvas = Color(hex: "#040812")
  static let canvasLifted = Color(hex: "#081326")
  static let surface = Color(hex: "#0C1729")
  static let surfaceRaised = Color(hex: "#12213A")
  static let electricBlue = Color(hex: "#5DA9FF")
  static let iceBlue = Color(hex: "#A9DDFF")
  static let cobalt = Color(hex: "#275DFF")
  static let cyan = Color(hex: "#65D8FF")
  static let ink = Color(hex: "#F4F8FF")
  static let mutedInk = Color(hex: "#9BAAC0")
}

extension Font {
  static func argonDisplay(_ size: CGFloat, weight: Font.Weight = .semibold) -> Font {
    .system(size: size, weight: weight, design: .serif)
  }
}

struct ArgonBackdrop: View {
  var accentColor = ArgonPalette.electricBlue

  var body: some View {
    GeometryReader { geometry in
      ZStack {
        LinearGradient(
          colors: [
            ArgonPalette.canvas,
            ArgonPalette.canvasLifted,
            ArgonPalette.canvas,
          ],
          startPoint: .topLeading,
          endPoint: .bottomTrailing
        )

        RadialGradient(
          colors: [
            accentColor.opacity(0.20),
            accentColor.opacity(0.04),
            .clear,
          ],
          center: .topTrailing,
          startRadius: 0,
          endRadius: geometry.size.width * 0.92
        )
        .offset(x: geometry.size.width * 0.16, y: -geometry.size.height * 0.08)

        RadialGradient(
          colors: [
            ArgonPalette.cobalt.opacity(0.12),
            .clear,
          ],
          center: .bottomLeading,
          startRadius: 0,
          endRadius: geometry.size.width * 0.82
        )

        LinearGradient(
          colors: [
            .white.opacity(0.035),
            .clear,
            ArgonPalette.cyan.opacity(0.025),
          ],
          startPoint: .top,
          endPoint: .bottom
        )
      }
    }
    .ignoresSafeArea()
  }
}

struct ArgonOrb: View {
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var size: CGFloat = 176
  var accentColor = ArgonPalette.electricBlue
  var showsOrbit = true

  @State private var isFloating = false

  var body: some View {
    ZStack {
      Circle()
        .fill(accentColor.opacity(0.25))
        .frame(width: size * 0.92, height: size * 0.92)
        .blur(radius: size * 0.22)

      if showsOrbit {
        Ellipse()
          .stroke(
            LinearGradient(
              colors: [
                .clear,
                ArgonPalette.iceBlue.opacity(0.58),
                .clear,
              ],
              startPoint: .leading,
              endPoint: .trailing
            ),
            lineWidth: 1
          )
          .frame(width: size * 1.28, height: size * 0.46)
          .rotationEffect(.degrees(-14))

        Circle()
          .fill(ArgonPalette.iceBlue)
          .frame(width: size * 0.035, height: size * 0.035)
          .shadow(color: ArgonPalette.iceBlue, radius: 8)
          .offset(x: size * 0.48, y: -size * 0.19)
      }

      Circle()
        .fill(
          RadialGradient(
            colors: [
              ArgonPalette.iceBlue,
              accentColor,
              ArgonPalette.cobalt,
              ArgonPalette.canvasLifted,
            ],
            center: UnitPoint(x: 0.34, y: 0.25),
            startRadius: 0,
            endRadius: size * 0.66
          )
        )
        .frame(width: size * 0.72, height: size * 0.72)
        .overlay(alignment: .topLeading) {
          Ellipse()
            .fill(
              LinearGradient(
                colors: [
                  .white.opacity(0.78),
                  ArgonPalette.iceBlue.opacity(0.08),
                  .clear,
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
              )
            )
            .frame(width: size * 0.34, height: size * 0.18)
            .blur(radius: size * 0.025)
            .rotationEffect(.degrees(-28))
            .offset(x: size * 0.14, y: size * 0.12)
        }
        .overlay {
          Circle()
            .stroke(
              LinearGradient(
                colors: [
                  .white.opacity(0.52),
                  .white.opacity(0.04),
                  ArgonPalette.cyan.opacity(0.28),
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
              ),
              lineWidth: 1
            )
        }
        .shadow(color: accentColor.opacity(0.60), radius: size * 0.16, y: size * 0.08)
        .shadow(color: .black.opacity(0.70), radius: size * 0.09, y: size * 0.10)
    }
    .frame(width: size * 1.35, height: size * 1.2)
    .offset(y: isFloating ? -4 : 4)
    .rotation3DEffect(
      .degrees(isFloating ? 4 : -4),
      axis: (x: 1, y: 0.35, z: 0)
    )
    .animation(
      reduceMotion
        ? nil
        : .easeInOut(duration: 3.8).repeatForever(autoreverses: true),
      value: isFloating
    )
    .onAppear {
      isFloating = true
    }
    .accessibilityHidden(true)
  }
}

private struct ArgonGlassPanelModifier: ViewModifier {
  let cornerRadius: CGFloat
  let strokeOpacity: Double

  func body(content: Content) -> some View {
    content
      .background(
        LinearGradient(
          colors: [
            Color.white.opacity(0.085),
            ArgonPalette.surface.opacity(0.86),
            ArgonPalette.surfaceRaised.opacity(0.48),
          ],
          startPoint: .topLeading,
          endPoint: .bottomTrailing
        ),
        in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
      )
      .overlay {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
          .stroke(
            LinearGradient(
              colors: [
                .white.opacity(strokeOpacity),
                ArgonPalette.electricBlue.opacity(strokeOpacity * 0.72),
                .white.opacity(0.025),
              ],
              startPoint: .topLeading,
              endPoint: .bottomTrailing
            ),
            lineWidth: 1
          )
      }
      .shadow(color: .black.opacity(0.30), radius: 22, y: 14)
  }
}

extension View {
  func argonGlassPanel(
    cornerRadius: CGFloat = 24,
    strokeOpacity: Double = 0.18
  ) -> some View {
    modifier(
      ArgonGlassPanelModifier(
        cornerRadius: cornerRadius,
        strokeOpacity: strokeOpacity
      )
    )
  }
}
