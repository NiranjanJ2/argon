import SwiftUI

/// Slate, with blue reserved for one job: showing what is selected.
///
/// The previous palette was near-black navy under four stacked gradients, a
/// floating orb and glass panels with gradient strokes. It looked considered
/// and it read badly: body text sat on a moving background, every surface
/// glowed slightly, so nothing glowed *meaningfully*, and the decoration took
/// the top third of the screen before a single task appeared.
///
/// Slate gives text a flat, even ground. Blue then means exactly one thing —
/// this is the thing you picked — which is why selection is the only place a
/// glow survives.
enum ArgonPalette {
  /// The page. Flat on purpose; nothing is painted over it.
  static let canvas = Color(hex: "#14171C")
  static let canvasLifted = Color(hex: "#1A1E25")
  /// Cards and rows. One step up from the page, no gradient.
  static let surface = Color(hex: "#21262E")
  static let surfaceRaised = Color(hex: "#2A313A")
  /// Hairlines. Visible against surface without drawing the eye.
  static let hairline = Color(hex: "#39414C")

  /// The one accent. Used for selection and for nothing decorative.
  static let electricBlue = Color(hex: "#4D9EFF")
  static let iceBlue = Color(hex: "#8FC2FF")
  static let cobalt = Color(hex: "#2F6FD0")
  static let cyan = Color(hex: "#63C8E8")

  /// Body text at full strength, and the quieter tier for captions.
  static let ink = Color(hex: "#E9EDF2")
  static let mutedInk = Color(hex: "#98A2B0")

  static let warning = Color(hex: "#EDA24C")
  static let danger = Color(hex: "#E4645E")
}

extension Font {
  /// Was serif. A display serif at 23pt on a phone, over a gradient, is harder
  /// to read than the system face at the same size — and every other line in
  /// the app was already system, so it read as a different app's heading.
  static func argonDisplay(_ size: CGFloat, weight: Font.Weight = .semibold) -> Font {
    .system(size: size, weight: weight, design: .default)
  }
}

/// The page behind everything. Flat slate.
///
/// Deliberately not a gradient. The old one moved under scrolling text and
/// forced every card to fight it for contrast.
struct ArgonBackdrop: View {
  var accentColor = ArgonPalette.electricBlue

  var body: some View {
    ArgonPalette.canvas.ignoresSafeArea()
  }
}

/// Selection, as a blue outline that glows.
///
/// One treatment, used everywhere something can be picked, so "selected" looks
/// the same on a task row, a wizard option and a mode toggle. Unselected is a
/// plain hairline — the difference has to be obvious at a glance, and colour
/// alone is not enough, so the border also thickens.
struct ArgonSelectable: ViewModifier {
  let isSelected: Bool
  var cornerRadius: CGFloat = 12

  func body(content: Content) -> some View {
    content
      .background(
        isSelected
          ? ArgonPalette.electricBlue.opacity(0.12)
          : ArgonPalette.surface,
        in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
      )
      .overlay {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
          .stroke(
            isSelected ? ArgonPalette.electricBlue : ArgonPalette.hairline,
            lineWidth: isSelected ? 1.5 : 1
          )
      }
      .shadow(
        color: isSelected ? ArgonPalette.electricBlue.opacity(0.45) : .clear,
        radius: isSelected ? 10 : 0
      )
      .animation(.easeOut(duration: 0.15), value: isSelected)
  }
}

extension View {
  /// Mark this as pickable, and show whether it is picked.
  func argonSelectable(_ isSelected: Bool, cornerRadius: CGFloat = 12) -> some View {
    modifier(ArgonSelectable(isSelected: isSelected, cornerRadius: cornerRadius))
  }
}

/// A tappable chip that glows when chosen. The wizard's basic unit.
struct ArgonChoiceButton: View {
  let title: String
  var caption: String? = nil
  let isSelected: Bool
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(spacing: 10) {
        Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
          .font(.system(size: 17))
          .foregroundStyle(isSelected ? ArgonPalette.electricBlue : ArgonPalette.mutedInk)

        VStack(alignment: .leading, spacing: 2) {
          Text(title)
            .font(.system(size: 15))
            .foregroundStyle(ArgonPalette.ink)
            .multilineTextAlignment(.leading)
          if let caption, !caption.isEmpty {
            Text(caption)
              .font(.system(size: 12))
              .foregroundStyle(ArgonPalette.mutedInk)
          }
        }
        Spacer(minLength: 0)
      }
      .padding(.horizontal, 14)
      // 52pt: comfortably past the 44pt minimum, because these get tapped in
      // a hurry and a miss here costs a whole step of the wizard.
      .frame(minHeight: 52)
      .frame(maxWidth: .infinity, alignment: .leading)
      .argonSelectable(isSelected)
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
  }
}

/// The primary action on a screen.
struct ArgonPrimaryButtonStyle: ButtonStyle {
  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .font(.system(size: 16, weight: .semibold))
      .foregroundStyle(ArgonPalette.canvas)
      .frame(maxWidth: .infinity)
      .frame(height: 50)
      .background(ArgonPalette.electricBlue, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
      .opacity(configuration.isPressed ? 0.75 : 1)
  }
}

/// A secondary action: outlined, never filled, so it cannot be mistaken for
/// the primary one at a glance.
struct ArgonSecondaryButtonStyle: ButtonStyle {
  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .font(.system(size: 16, weight: .medium))
      .foregroundStyle(ArgonPalette.ink)
      .frame(maxWidth: .infinity)
      .frame(height: 50)
      .background(ArgonPalette.surface, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
      .overlay {
        RoundedRectangle(cornerRadius: 12, style: .continuous)
          .stroke(ArgonPalette.hairline, lineWidth: 1)
      }
      .opacity(configuration.isPressed ? 0.75 : 1)
  }
}

/// Kept so existing call sites compile, but reduced to a small status dot.
///
/// It used to be a 176pt animated orb with an orbit ring and a 3D float. On the
/// dashboard it pushed the first task below the fold, which is a lot to pay for
/// an ornament.
struct ArgonOrb: View {
  var size: CGFloat = 176
  var accentColor = ArgonPalette.electricBlue
  var showsOrbit = true

  var body: some View {
    Circle()
      .fill(accentColor.opacity(0.16))
      .overlay {
        Circle().stroke(accentColor.opacity(0.55), lineWidth: 1.5)
      }
      .frame(width: min(size, 44), height: min(size, 44))
      .accessibilityHidden(true)
  }
}

private struct ArgonGlassPanelModifier: ViewModifier {
  let cornerRadius: CGFloat
  let strokeOpacity: Double

  func body(content: Content) -> some View {
    content
      .background(
        ArgonPalette.surface,
        in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
      )
      .overlay {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
          .stroke(ArgonPalette.hairline, lineWidth: 1)
      }
  }
}

extension View {
  /// Name kept; it is a plain slate card now. Nothing glass, no gradient
  /// stroke, no drop shadow — a card's job is to group things, not to be seen.
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
