import SwiftUI

struct ArgonStatusCard: View {
  @EnvironmentObject private var bridge: ArgonBridge

  let onSettingsTapped: () -> Void

  private var modeLabel: String {
    switch bridge.mode {
    case "lock_in": return "LOCKED IN"
    case "working": return "WORKING"
    case "idle": return "AT EASE"
    default: return "OFFLINE"
    }
  }

  private var modeIcon: String {
    switch bridge.mode {
    case "lock_in": return "lock.fill"
    case "working": return "sparkles"
    case "idle": return "moon.stars.fill"
    default: return "bolt.slash.fill"
    }
  }

  private var taskLabel: String {
    guard let currentTask = bridge.currentTask, !currentTask.isEmpty else {
      return "Argon is standing by"
    }
    return currentTask
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 20) {
      HStack(alignment: .center, spacing: 14) {
        ZStack {
          Circle()
            .fill(ArgonPalette.electricBlue.opacity(0.15))
            .frame(width: 48, height: 48)
            .blur(radius: 7)

          Image(systemName: modeIcon)
            .font(.system(size: 17, weight: .semibold))
            .foregroundStyle(ArgonPalette.iceBlue)
            .frame(width: 42, height: 42)
            .background(.white.opacity(0.055), in: Circle())
            .overlay {
              Circle()
                .stroke(ArgonPalette.electricBlue.opacity(0.28), lineWidth: 1)
            }
        }

        VStack(alignment: .leading, spacing: 3) {
          Text(modeLabel)
            .font(.caption.weight(.bold))
            .tracking(1.8)
            .foregroundStyle(ArgonPalette.iceBlue)

          Text(taskLabel)
            .font(.argonDisplay(21))
            .foregroundStyle(ArgonPalette.ink)
            .lineLimit(2)
        }

        Spacer(minLength: 8)

        Button(action: onSettingsTapped) {
          Image(systemName: bridge.connectionState == "Connected" ? "link" : "link.badge.plus")
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(ArgonPalette.iceBlue)
            .frame(width: 36, height: 36)
            .background(.white.opacity(0.06), in: Circle())
        }
        .accessibilityLabel("Argon connection settings")
      }

      HStack(spacing: 0) {
        metric(value: "\(bridge.workMinutes)m", label: "FOCUS")
        divider
        metric(value: "\(bridge.lockMinutes)m", label: "LOCKED")
        divider
        metric(
          value: bridge.connectionState == "Connected" ? "LIVE" : "SETUP",
          label: "ARGON"
        )
      }

      if bridge.desiredMode != "off", !bridge.desiredReason.isEmpty {
        HStack(alignment: .top, spacing: 9) {
          Image(systemName: "quote.opening")
            .font(.caption)
            .foregroundStyle(ArgonPalette.iceBlue)
          Text(bridge.desiredReason)
            .font(.caption)
            .foregroundStyle(ArgonPalette.mutedInk)
            .lineLimit(3)
        }
        .padding(.horizontal, 4)
      }

      if let error = bridge.lastError, bridge.connectionState != "Connected" {
        Button(action: onSettingsTapped) {
          Label(error, systemImage: "exclamationmark.circle")
            .font(.caption)
            .foregroundStyle(ArgonPalette.mutedInk)
            .lineLimit(2)
        }
      }
    }
    .padding(20)
    .argonGlassPanel(cornerRadius: 28, strokeOpacity: 0.24)
    .overlay(alignment: .topTrailing) {
      Circle()
        .fill(ArgonPalette.electricBlue.opacity(0.24))
        .frame(width: 90, height: 90)
        .blur(radius: 36)
        .offset(x: 24, y: -30)
        .allowsHitTesting(false)
    }
  }

  private var divider: some View {
    Rectangle()
      .fill(.white.opacity(0.08))
      .frame(width: 1, height: 34)
  }

  private func metric(value: String, label: String) -> some View {
    VStack(spacing: 3) {
      Text(value)
        .font(.argonDisplay(18))
        .foregroundStyle(ArgonPalette.ink)
      Text(label)
        .font(.system(size: 9, weight: .bold))
        .tracking(1.2)
        .foregroundStyle(ArgonPalette.mutedInk)
    }
    .frame(maxWidth: .infinity)
  }
}
