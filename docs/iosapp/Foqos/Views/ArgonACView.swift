import SwiftUI

/// The air conditioner screen.
///
/// Everything here is fahrenheit, because that is what the unit's own display
/// shows. The protocol carries celsius and the conversion happens at the edge —
/// a screen that said 26 while the wall said 79 would be read as broken.
struct ArgonACView: View {
  @EnvironmentObject private var bridge: ArgonBridge

  @State private var units: [ArgonACUnit] = []
  @State private var busyMac: String?
  @State private var isLoading = true
  @AppStorage("argon.ac.primaryMac") private var primaryMac = ""

  var body: some View {
    NavigationStack {
      ZStack {
        ArgonBackdrop()

        if isLoading && units.isEmpty {
          ProgressView().tint(ArgonPalette.iceBlue).controlSize(.large)
        } else if units.isEmpty {
          empty
        } else {
          ScrollView {
            VStack(spacing: 14) {
              ForEach(units) { unit in
                card(unit)
              }
              actionButtonHint
            }
            .padding(16)
          }
        }
      }
      .navigationTitle("Air")
      .toolbarBackground(ArgonPalette.canvasLifted.opacity(0.82), for: .navigationBar)
      .toolbarBackground(.visible, for: .navigationBar)
      .refreshable { await load() }
      .task { await load() }
    }
  }

  private var empty: some View {
    VStack(spacing: 8) {
      Image(systemName: "snowflake")
        .font(.system(size: 30))
        .foregroundStyle(ArgonPalette.mutedInk)
      Text("No air conditioner set up")
        .font(.headline)
        .foregroundStyle(ArgonPalette.ink)
      Text(bridge.lastError ?? "Argon has not adopted a unit yet.")
        .font(.caption)
        .foregroundStyle(ArgonPalette.mutedInk)
        .multilineTextAlignment(.center)
    }
    .padding(32)
  }

  private func card(_ unit: ArgonACUnit) -> some View {
    VStack(alignment: .leading, spacing: 14) {
      HStack {
        VStack(alignment: .leading, spacing: 2) {
          Text(unit.displayName)
            .font(.headline)
            .foregroundStyle(ArgonPalette.ink)
          Text(subtitle(unit))
            .font(.caption)
            .foregroundStyle(ArgonPalette.mutedInk)
        }
        Spacer()
        Toggle(
          "",
          isOn: Binding(
            get: { unit.on },
            set: { on in act(unit) { await bridge.setAC(mac: unit.mac, power: on) } }
          )
        )
        .labelsHidden()
        .tint(ArgonPalette.electricBlue)
        .disabled(busyMac == unit.mac || unit.reachable == false)
      }

      if unit.on, let target = unit.targetF {
        HStack(spacing: 12) {
          stepper(unit, target: target, delta: -1, symbol: "minus")
          Text("\(target)°F")
            .font(.system(size: 34, weight: .semibold, design: .rounded))
            .foregroundStyle(ArgonPalette.ink)
            .frame(maxWidth: .infinity)
          stepper(unit, target: target, delta: 1, symbol: "plus")
        }

        Picker(
          "Mode",
          selection: Binding(
            get: { unit.mode },
            set: { mode in act(unit) { await bridge.setAC(mac: unit.mac, mode: mode) } }
          )
        ) {
          ForEach(["cool", "dry", "fan", "heat", "auto"], id: \.self) {
            Text($0.capitalized).tag($0)
          }
        }
        .pickerStyle(.segmented)
        .disabled(busyMac == unit.mac)
      }

      if unit.reachable == false {
        Label("Not answering", systemImage: "exclamationmark.triangle")
          .font(.caption)
          .foregroundStyle(.orange)
      }

      Button {
        primaryMac = unit.mac
      } label: {
        Label(
          primaryMac == unit.mac ? "Action Button controls this" : "Use for the Action Button",
          systemImage: primaryMac == unit.mac ? "checkmark.circle.fill" : "circle"
        )
        .font(.caption)
        .foregroundStyle(primaryMac == unit.mac ? ArgonPalette.iceBlue : ArgonPalette.mutedInk)
      }
      .buttonStyle(.plain)
    }
    .padding(16)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(ArgonPalette.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    .overlay(
      RoundedRectangle(cornerRadius: 18, style: .continuous)
        .stroke(ArgonPalette.electricBlue.opacity(unit.on ? 0.28 : 0.10), lineWidth: 1)
    )
    .opacity(busyMac == unit.mac ? 0.55 : 1)
  }

  private func stepper(_ unit: ArgonACUnit, target: Int, delta: Int, symbol: String) -> some View {
    Button {
      // Converted back to celsius here because the protocol has no other unit;
      // stepping in fahrenheit and rounding keeps the number he sees stable.
      let wanted = target + delta
      let celsius = Int(((Double(wanted) - 32.0) * 5.0 / 9.0).rounded())
      act(unit) { await bridge.setAC(mac: unit.mac, targetC: celsius) }
    } label: {
      Image(systemName: symbol)
        .font(.system(size: 15, weight: .bold))
        .foregroundStyle(ArgonPalette.iceBlue)
        .frame(width: 44, height: 44)
        .background(ArgonPalette.surfaceRaised, in: Circle())
    }
    .buttonStyle(.plain)
    .disabled(busyMac == unit.mac)
  }

  private var actionButtonHint: some View {
    Text(
      "Settings ▸ Action Button ▸ Shortcut ▸ Toggle AC. It works away from home "
        + "too — the phone asks Argon, and Argon is the one on the same network."
    )
    .font(.caption2)
    .foregroundStyle(ArgonPalette.mutedInk)
    .multilineTextAlignment(.center)
    .padding(.top, 4)
  }

  private func subtitle(_ unit: ArgonACUnit) -> String {
    guard unit.on else { return "Off" }
    var parts = [unit.mode.capitalized]
    if let room = unit.roomF { parts.append("room \(room)°F") }
    return parts.joined(separator: " · ")
  }

  private func act(_ unit: ArgonACUnit, _ work: @escaping () async -> ArgonACUnit?) {
    guard busyMac == nil else { return }
    busyMac = unit.mac
    Task {
      // The reply carries the unit's real state, so the row corrects itself
      // rather than showing what was asked for.
      if let updated = await work() {
        units = units.map { $0.mac == updated.mac ? updated : $0 }
      } else {
        await load()
      }
      busyMac = nil
    }
  }

  private func load() async {
    isLoading = true
    units = await bridge.acUnits()
    if primaryMac.isEmpty, let first = units.first { primaryMac = first.mac }
    isLoading = false
  }
}
