import AppIntents
import Foundation

/// Shortcuts entry points for the air conditioner.
///
/// These exist so the Action Button can reach it: the button runs a Shortcut,
/// a Shortcut runs an App Intent, and the intent posts to Argon's server, which
/// is the thing on the same LAN as the unit. That indirection is also what
/// makes it work away from home — the phone never speaks to the AC directly.
///
/// `openAppWhenRun` is false throughout. A button press that launches an app to
/// turn on the AC is worse than the remote it replaces.

private func acMac() -> String {
  // Which unit the button drives. Stored by the AC screen; falls back to the
  // one adopted first so the intent works before he has chosen.
  UserDefaults.standard.string(forKey: "argon.ac.primaryMac") ?? ""
}

@MainActor
private func applyPower(_ power: Bool?) async throws -> String {
  let bridge = ArgonBridge.shared
  var mac = acMac()
  if mac.isEmpty {
    mac = await bridge.acUnits().first?.mac ?? ""
  }
  guard !mac.isEmpty else {
    throw ACIntentError.noUnit
  }
  guard let unit = await bridge.setAC(mac: mac, power: power) else {
    throw ACIntentError.unreachable
  }
  let temperature = unit.targetF.map { " · \($0)°F" } ?? ""
  return unit.on ? "AC on\(temperature)" : "AC off"
}

enum ACIntentError: Error, CustomLocalizedStringResourceConvertible {
  case noUnit
  case unreachable

  var localizedStringResource: LocalizedStringResource {
    switch self {
    case .noUnit: return "No air conditioner is set up in Argon yet."
    case .unreachable: return "Argon could not reach the air conditioner."
    }
  }
}

struct ToggleACIntent: AppIntent {
  static var title: LocalizedStringResource = "Toggle AC"
  static var description = IntentDescription("Turn the air conditioner on if it is off, or off if it is on.")
  static var openAppWhenRun = false

  @MainActor
  func perform() async throws -> some IntentResult & ProvidesDialog {
    // nil means toggle, resolved server-side against the unit's real state.
    let summary = try await applyPower(nil)
    return .result(dialog: IntentDialog(stringLiteral: summary))
  }
}

struct TurnACOnIntent: AppIntent {
  static var title: LocalizedStringResource = "Turn AC on"
  static var openAppWhenRun = false

  @MainActor
  func perform() async throws -> some IntentResult & ProvidesDialog {
    .result(dialog: IntentDialog(stringLiteral: try await applyPower(true)))
  }
}

struct TurnACOffIntent: AppIntent {
  static var title: LocalizedStringResource = "Turn AC off"
  static var openAppWhenRun = false

  @MainActor
  func perform() async throws -> some IntentResult & ProvidesDialog {
    .result(dialog: IntentDialog(stringLiteral: try await applyPower(false)))
  }
}

struct SetACTemperatureIntent: AppIntent {
  static var title: LocalizedStringResource = "Set AC temperature"
  static var openAppWhenRun = false

  @Parameter(title: "Fahrenheit", default: 74)
  var fahrenheit: Int

  @MainActor
  func perform() async throws -> some IntentResult & ProvidesDialog {
    var mac = acMac()
    if mac.isEmpty { mac = await ArgonBridge.shared.acUnits().first?.mac ?? "" }
    guard !mac.isEmpty else { throw ACIntentError.noUnit }

    // The protocol speaks celsius even though the unit displays fahrenheit.
    let celsius = Int(((Double(fahrenheit) - 32.0) * 5.0 / 9.0).rounded())
    guard let unit = await ArgonBridge.shared.setAC(mac: mac, power: true, targetC: celsius)
    else {
      throw ACIntentError.unreachable
    }
    return .result(dialog: IntentDialog(stringLiteral: "AC set to \(unit.targetF ?? fahrenheit)°F"))
  }
}

/// Offered to Shortcuts automatically, so there is something to bind the Action
/// Button to without building a Shortcut by hand first.
struct ArgonACShortcuts: AppShortcutsProvider {
  static var appShortcuts: [AppShortcut] {
    AppShortcut(
      intent: ToggleACIntent(),
      phrases: ["Toggle the AC in \(.applicationName)", "\(.applicationName) AC"],
      shortTitle: "Toggle AC",
      systemImageName: "snowflake"
    )
    AppShortcut(
      intent: TurnACOnIntent(),
      phrases: ["Turn on the AC in \(.applicationName)"],
      shortTitle: "AC on",
      systemImageName: "snowflake"
    )
    AppShortcut(
      intent: TurnACOffIntent(),
      phrases: ["Turn off the AC in \(.applicationName)"],
      shortTitle: "AC off",
      systemImageName: "power"
    )
  }
}
