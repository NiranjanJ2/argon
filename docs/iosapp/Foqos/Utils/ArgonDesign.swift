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

/// Light that comes from inside the button, not off it.
///
/// The obvious way to show a live control is a coloured drop shadow, which
/// throws light outward onto the page. It reads as the button hovering above
/// the screen, and on a dark ground it smears into whatever is behind it.
///
/// This does the opposite. The footprint never changes — nothing reflows and
/// nothing jumps — but the face insets, so the control looks pressed into the
/// surface. The lip that reveals is where the light sits, and the glow is
/// masked to the face so it falls inward across the button rather than out
/// into the page.
struct ArgonInsetGlow: ViewModifier {
  var isActive: Bool
  var cornerRadius: CGFloat = 12
  /// How far the face sits below the rim. Enough that the lip reads as a
  /// recess rather than as a slightly thick border.
  var inset: CGFloat = 4
  var tint: Color = ArgonPalette.electricBlue

  private var faceRadius: CGFloat { max(2, cornerRadius - inset) }

  func body(content: Content) -> some View {
    content
      // Deliberately no padding on the content: padding it would grow the
      // whole control by twice the inset the moment it lit up, which is the
      // layout jump this effect exists to avoid. The face insets instead, so
      // the footprint is identical lit or dark.
      .background {
        ZStack {
          // The well. Darker than the page, so the lip around the face reads
          // as depth rather than as a gap someone forgot to fill.
          RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
            .fill(isActive ? Color.black.opacity(0.45) : Color.clear)

          faceShape
            .fill(isActive ? ArgonPalette.surfaceRaised : ArgonPalette.surface)
            .overlay { innerGlow }
            .overlay {
              // strokeBorder, not stroke: stroke straddles the path and spills
              // half its width into the lip, filling the recess it is meant to
              // sit inside.
              faceShape.strokeBorder(
                isActive ? tint.opacity(0.95) : ArgonPalette.hairline,
                lineWidth: isActive ? 1 : 1
              )
            }
            .padding(isActive ? inset : 0)
        }
      }
      .animation(.easeOut(duration: 0.16), value: isActive)
  }

  private var faceShape: RoundedRectangle {
    RoundedRectangle(cornerRadius: isActive ? faceRadius : cornerRadius, style: .continuous)
  }

  /// Light entering from the rim and falling off toward the middle.
  ///
  /// Three borders of increasing width and decreasing opacity, each blurred and
  /// all clipped to the face. The clip is the whole trick: without it the blur
  /// spills past the rim and becomes the outward halo this exists to avoid.
  /// strokeBorder keeps every pass inside the face to begin with, so the mask
  /// is trimming the blur rather than half the stroke.
  @ViewBuilder
  private var innerGlow: some View {
    if isActive {
      ZStack {
        faceShape.strokeBorder(tint.opacity(0.85), lineWidth: 2).blur(radius: 2)
        faceShape.strokeBorder(tint.opacity(0.40), lineWidth: 6).blur(radius: 6)
        faceShape.strokeBorder(tint.opacity(0.16), lineWidth: 14).blur(radius: 12)
      }
      .mask(faceShape.fill())
      .allowsHitTesting(false)
    }
  }
}

extension View {
  /// Light from the rim, inward. Use for a control that is live or chosen.
  func argonInsetGlow(
    _ isActive: Bool,
    cornerRadius: CGFloat = 12,
    inset: CGFloat = 3,
    tint: Color = ArgonPalette.electricBlue
  ) -> some View {
    modifier(
      ArgonInsetGlow(
        isActive: isActive, cornerRadius: cornerRadius, inset: inset, tint: tint
      )
    )
  }

  /// Mark this as pickable, and show whether it is picked.
  ///
  /// Selection uses the same inward light as a live button, so "this one" looks
  /// the same wherever it appears.
  func argonSelectable(_ isSelected: Bool, cornerRadius: CGFloat = 12) -> some View {
    argonInsetGlow(isSelected, cornerRadius: cornerRadius)
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
///
/// Lit while it is actually actionable, dark while it is not, so "can I press
/// this yet" is answered by looking rather than by pressing and seeing nothing
/// happen. Pressing sinks it further rather than flashing a highlight — the
/// control already reads as recessed, so going deeper is the honest gesture.
struct ArgonPrimaryButtonStyle: ButtonStyle {
  func makeBody(configuration: Configuration) -> some View {
    Face(configuration: configuration)
  }

  private struct Face: View {
    @Environment(\.isEnabled) private var isEnabled
    let configuration: Configuration

    var body: some View {
      configuration.label
        .font(.system(size: 16, weight: .semibold))
        .foregroundStyle(isEnabled ? ArgonPalette.ink : ArgonPalette.mutedInk)
        .frame(maxWidth: .infinity)
        .frame(height: 50)
        .argonInsetGlow(
          isEnabled,
          inset: configuration.isPressed ? 5 : 3
        )
    }
  }
}

/// A secondary action. Never lit, so it cannot be mistaken for the primary one
/// at a glance even when both are available.
struct ArgonSecondaryButtonStyle: ButtonStyle {
  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .font(.system(size: 16, weight: .medium))
      .foregroundStyle(ArgonPalette.mutedInk)
      .frame(maxWidth: .infinity)
      .frame(height: 50)
      .argonInsetGlow(false)
      .opacity(configuration.isPressed ? 0.7 : 1)
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
