import DeviceActivity
import FamilyControls
import ManagedSettings
import SwiftUI

class RequestAuthorizer: ObservableObject {
  @Published var isAuthorized = false

  func refreshAuthorizationStatus() {
    let isApproved = getAuthorizationStatus() == .approved

    Task { @MainActor in
      self.isAuthorized = isApproved
    }
  }

  /// Ask for Screen Time authorization. `completion` runs either way.
  ///
  /// The caller needs to know the attempt *finished*, not that it succeeded.
  /// The intro was a `fullScreenCover` with `interactiveDismissDisabled` that
  /// closed only when authorization was granted, so anyone who declined — or
  /// any build without the Family Controls entitlement — was locked in it with
  /// no way forward and no explanation.
  func requestAuthorization(completion: (() -> Void)? = nil) {
    Task {
      do {
        try await AuthorizationCenter.shared.requestAuthorization(for: .individual)
        print("Individual authorization successful")

        // Dispatch the update to the main thread
        await MainActor.run {
          self.isAuthorized = true
        }
      } catch {
        print("Error requesting authorization: \(error)")
        await MainActor.run {
          self.isAuthorized = false
        }
      }
      await MainActor.run { completion?() }
    }
  }

  func getAuthorizationStatus() -> AuthorizationStatus {
    return AuthorizationCenter.shared.authorizationStatus
  }
}
