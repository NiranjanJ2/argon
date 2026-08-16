import Foundation
import SwiftData

extension Notification.Name {
  static let argonStateApplied = Notification.Name("argonStateApplied")
  static let argonStateFailed = Notification.Name("argonStateFailed")
}

struct ArgonReconcileResult {
  let mode: String
  let version: Int
  let shielded: Bool
  let message: String
  /// nil on success. Set when the desired state could not be applied, so the
  /// server can tell "tried and failed" from "phone is switched off".
  let error: String?

  init(
    mode: String,
    version: Int,
    shielded: Bool,
    message: String,
    error: String? = nil
  ) {
    self.mode = mode
    self.version = version
    self.shielded = shielded
    self.message = message
    self.error = error
  }
}

@MainActor
final class ArgonReconciler {
  static let shared = ArgonReconciler()

  private let lastVersionKey = "argon.lastAppliedVersion"
  private var container: ModelContainer?

  private init() {}

  func configure(container: ModelContainer) {
    self.container = container
  }

  /// Never returns nil: a failure the server does not hear about is
  /// indistinguishable from a phone that is switched off, which would leave
  /// Argon believing it had locked a device that is wide open.
  func reconcile(
    _ desired: ArgonDesiredMode,
    profileName: String
  ) -> ArgonReconcileResult {
    guard let container else {
      return failure(desired, "Argon's Screen Time engine is not ready yet.")
    }

    do {
      let message: String
      let shielded: Bool

      if let until = ArgonOverride.activeUntil, desired.mode != "off" {
        // Emergency release. Refuse the block locally, with no network needed,
        // and keep refusing until the window ends.
        _ = StrategyManager.shared.applyArgonUnlock(context: container.mainContext)
        return failure(
          desired,
          "Emergency override active until \(until.formatted(date: .omitted, time: .shortened))"
        )
      }

      if desired.mode == "off" {
        if let profileName = StrategyManager.shared.applyArgonUnlock(
          context: container.mainContext
        ) {
          message = "\(profileName) is off"
        } else {
          message = "Screen Time restrictions are off"
        }
        shielded = false
      } else if !desired.hasValidHardExpiry {
        _ = StrategyManager.shared.applyArgonUnlock(context: container.mainContext)
        // A refusal is not an application: report the version still in force,
        // or the server reads a matching version and calls this converged.
        return failure(
          desired,
          "Argon refused a focus state with a missing or invalid hard expiry"
        )
      } else if let expiry = desired.expiryDate, expiry <= Date() {
        _ = StrategyManager.shared.applyArgonUnlock(context: container.mainContext)
        message = "The Argon focus window expired"
        shielded = false
      } else if let allowance = desired.allowance {
        // Metered mode is Argon's own, not a foqos block. There is no session
        // and no profile to start: Screen Time meters the apps directly and the
        // monitor extension raises the shield when the budget is spent, so it
        // keeps working with the app killed and the server unreachable.
        let applied = try StrategyManager.shared.applyArgonMeteredMode(
          profileName: profileName,
          minutes: allowance.minutes,
          perHours: allowance.perHours,
          context: container.mainContext
        )
        let window = allowance.perHours == 1 ? "hour" : "\(allowance.perHours)h"
        message = "\(applied): \(allowance.minutes)m of use per \(window)"
        // Not shielded *yet* — the budget starts full. Reported as shielded
        // because the mode is in force, which is what convergence asks.
        shielded = true
      } else {
        let profileName = try StrategyManager.shared.applyArgonLock(
          profileName: profileName,
          expiresAt: desired.expiryDate,
          context: container.mainContext
        )
        message = "\(profileName) is active"
        shielded = true
      }

      UserDefaults.standard.set(desired.version, forKey: lastVersionKey)
      NotificationCenter.default.post(
        name: .argonStateApplied,
        object: nil,
        userInfo: ["message": message]
      )
      return ArgonReconcileResult(
        mode: shielded ? desired.mode : "off",
        version: desired.version,
        shielded: shielded,
        message: message
      )
    } catch {
      return failure(desired, error.localizedDescription)
    }
  }

  /// Report the version still actually in force, not the one that failed, so a
  /// plain version comparison on the server shows the phone has not converged.
  private func failure(
    _ desired: ArgonDesiredMode,
    _ message: String
  ) -> ArgonReconcileResult {
    publishFailure(message)
    return ArgonReconcileResult(
      mode: "off",
      version: UserDefaults.standard.integer(forKey: lastVersionKey),
      shielded: false,
      message: message,
      error: message
    )
  }

  private func publishFailure(_ message: String) {
    NotificationCenter.default.post(
      name: .argonStateFailed,
      object: nil,
      userInfo: ["message": message]
    )
  }
}
