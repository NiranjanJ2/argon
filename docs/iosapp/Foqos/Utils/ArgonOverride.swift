import Foundation

/// The local half of the emergency release.
///
/// The server has an override too, but reaching it needs a network, a running
/// gateway and a phone that is allowed to talk to it. An escape hatch with
/// dependencies is not an escape hatch, so this one lives entirely on the
/// device: while it is engaged the reconciler refuses to apply any block, no
/// matter what the server asks for. It works in airplane mode.
enum ArgonOverride {
  private static let key = "argon.overrideUntil"

  /// Nil when no override is engaged or it has expired.
  static var activeUntil: Date? {
    let stamp = UserDefaults.standard.double(forKey: key)
    guard stamp > 0 else { return nil }
    let until = Date(timeIntervalSinceReferenceDate: stamp)
    return until > Date() ? until : nil
  }

  static var isActive: Bool { activeUntil != nil }

  static func engage(minutes: Int) {
    let until = Date().addingTimeInterval(TimeInterval(max(1, minutes) * 60))
    UserDefaults.standard.set(until.timeIntervalSinceReferenceDate, forKey: key)
  }

  static func clear() {
    UserDefaults.standard.removeObject(forKey: key)
  }
}
